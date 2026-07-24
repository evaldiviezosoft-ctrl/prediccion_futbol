from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

from app.core.config import get_settings
from app.services.baseline_model_service import BASELINE_FINAL_STATUSES
from app.services.feature_builder import TEAM_ALIASES, load_profiles
from app.services.fixture_service import (
    CALENDAR_ONLY_LEAGUE_IDS,
)


_HISTORY_QUERY_CHUNK_SIZE = 100
_HISTORY_PAGE_SIZE = 1000
_MIN_HISTORY_MATCHES = 5
_FRIENDLY_LEAGUE_ID = 667
_NON_ALPHANUMERIC = re.compile(r'[^a-z0-9]+')
_SAFE_CLUB_DESIGNATORS = frozenset({'fc', 'sc', 'vfl'})
_LOCAL_PROFILE_COUNTRIES = {
    'D1': 'Germany',
    'E0': 'England',
    'F1': 'France',
    'I1': 'Italy',
    'SP1': 'Spain',
}


@dataclass(frozen=True, slots=True)
class LocalTeamProfile:
    league_code: str
    profile_name: str
    values: Mapping[str, Any]


def _normalized_team_name(value: Any) -> str:
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    ascii_text = ''.join(
        character for character in text
        if not unicodedata.combining(character)
    )
    tokens = _NON_ALPHANUMERIC.sub(' ', ascii_text).split()
    if tokens and tokens[0] in _SAFE_CLUB_DESIGNATORS:
        tokens.pop(0)
    if tokens and tokens[-1] in _SAFE_CLUB_DESIGNATORS:
        tokens.pop()
    return ' '.join(tokens)


@lru_cache(maxsize=1)
def local_team_profile_catalog() -> dict[str, LocalTeamProfile]:
    """Index local profiles by safe canonical name and explicit provider alias."""

    catalog: dict[str, LocalTeamProfile] = {}
    profile_root = get_settings().team_profile_root
    for path in sorted(profile_root.glob('*.json')):
        league_code = path.stem
        try:
            profiles = load_profiles(league_code)
        except (OSError, ValueError):
            continue
        for profile_name, values in profiles.items():
            if not isinstance(values, Mapping):
                continue
            match = LocalTeamProfile(
                league_code=league_code,
                profile_name=str(profile_name),
                values=dict(values),
            )
            for name in (profile_name, values.get('team_name')):
                normalized = _normalized_team_name(name)
                if normalized:
                    catalog.setdefault(normalized, match)
        for provider_name, profile_name in TEAM_ALIASES.get(league_code, {}).items():
            match = catalog.get(_normalized_team_name(profile_name))
            provider_key = _normalized_team_name(provider_name)
            if match is not None and provider_key:
                catalog[provider_key] = match
    return catalog


@lru_cache(maxsize=1)
def local_profile_team_names() -> frozenset[str]:
    """Return exact/canonical names backed by local profiles."""

    return frozenset(local_team_profile_catalog())


def local_team_profile(team_name: Any) -> LocalTeamProfile | None:
    return local_team_profile_catalog().get(_normalized_team_name(team_name))


def local_team_country(team_name: Any) -> str | None:
    """Return the club country implied by a validated local league profile."""

    profile = local_team_profile(team_name)
    return _LOCAL_PROFILE_COUNTRIES.get(profile.league_code) if profile else None


def calendar_fixture_has_local_profile(row: Mapping[str, Any]) -> bool:
    return any(
        local_team_profile(row.get(column)) is not None
        for column in ('home_team_name', 'away_team_name')
    )


def _chunks(values: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _historical_competitive_team_ids(
    database: Any,
    candidate_team_ids: Iterable[int],
    *,
    before_kickoff: str,
) -> set[int]:
    """Find candidate clubs with enough completed competitive stored fixtures.

    The query is limited to candidate ids, requests only team columns and
    paginates to avoid Supabase's default 1,000-row response cap. Targeted
    history may belong to a dynamically created domestic competition, so the
    lookup deliberately accepts every league except the friendly calendar.
    """

    candidates = sorted({int(team_id) for team_id in candidate_team_ids})
    if not candidates or not before_kickoff:
        return set()
    known: set[int] = set()
    for chunk in _chunks(candidates, _HISTORY_QUERY_CHUNK_SIZE):
        wanted = set(chunk)
        fixture_ids_by_team = {team_id: set() for team_id in wanted}
        encoded_ids = ','.join(str(team_id) for team_id in chunk)
        offset = 0
        while wanted - known:
            query = (
                database.table('fixtures')
                .select('id,home_team_id,away_team_id,home_goals,away_goals')
                .neq('league_id', _FRIENDLY_LEAGUE_ID)
                .in_('status_short', sorted(BASELINE_FINAL_STATUSES))
                .lt('kickoff', before_kickoff)
                .or_(
                    f'home_team_id.in.({encoded_ids}),'
                    f'away_team_id.in.({encoded_ids})'
                )
                .order('id')
            )
            response = (
                query
                .range(offset, offset + _HISTORY_PAGE_SIZE - 1)
                .execute()
            )
            page = [dict(row) for row in (response.data or [])]
            for row in page:
                if row.get('home_goals') is None or row.get('away_goals') is None:
                    continue
                fixture_id = row.get('id')
                if fixture_id is None:
                    continue
                for column in ('home_team_id', 'away_team_id'):
                    team_id = row.get(column)
                    if team_id is not None and int(team_id) in wanted:
                        parsed_team_id = int(team_id)
                        fixture_ids_by_team[parsed_team_id].add(int(fixture_id))
                        if (
                            len(fixture_ids_by_team[parsed_team_id])
                            >= _MIN_HISTORY_MATCHES
                        ):
                            known.add(parsed_team_id)
            if len(page) < _HISTORY_PAGE_SIZE:
                break
            offset += _HISTORY_PAGE_SIZE
    return known


def filter_visible_calendar_fixtures(
    database: Any,
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Filter calendar-only competitions to clubs backed by usable local data.

    Modeled leagues are never filtered. A calendar-only match is eligible when
    either club has a local model profile (exact/canonical name or configured
    alias) or has at least five completed competitive fixtures in Supabase.
    The latter also
    covers targeted team history stored under dynamically created leagues.
    """

    values = [dict(row) for row in rows]
    profile_names = local_profile_team_names()
    has_profile: dict[int, bool] = {}
    unresolved_team_ids: set[int] = set()
    calendar_kickoffs = [
        str(row['kickoff'])
        for row in values
        if (
            int(row.get('league_id') or 0) in CALENDAR_ONLY_LEAGUE_IDS
            and row.get('kickoff')
        )
    ]

    for row in values:
        if int(row.get('league_id') or 0) not in CALENDAR_ONLY_LEAGUE_IDS:
            continue
        fixture_id = int(row['id'])
        matched = any(
            _normalized_team_name(row.get(column)) in profile_names
            for column in ('home_team_name', 'away_team_name')
        )
        has_profile[fixture_id] = matched
        if not matched:
            unresolved_team_ids.update(
                (int(row['home_team_id']), int(row['away_team_id']))
            )

    historical_team_ids = _historical_competitive_team_ids(
        database,
        unresolved_team_ids,
        before_kickoff=min(calendar_kickoffs) if calendar_kickoffs else '',
    )
    visible: list[dict[str, Any]] = []
    for row in values:
        league_id = int(row.get('league_id') or 0)
        is_calendar_only = league_id in CALENDAR_ONLY_LEAGUE_IDS
        fallback_available = (
            is_calendar_only
            and (
                has_profile.get(int(row['id']), False)
                or int(row['home_team_id']) in historical_team_ids
                or int(row['away_team_id']) in historical_team_ids
            )
        )
        if not is_calendar_only or fallback_available:
            # Preserve the eligibility decision so callers do not repeat the
            # paginated history lookup merely to explain why a row is visible.
            row['prediction_fallback_available'] = fallback_available
            visible.append(row)
    return visible

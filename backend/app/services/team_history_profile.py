from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping


FINAL_STATUSES = frozenset({'FT', 'AET', 'PEN'})
_STATISTIC_FIELDS = {
    'corners': ('season_corners_pg', 'corners5'),
    'total_shots': ('season_shots_pg', 'shots5'),
    'shots_on_goal': ('season_sot_pg', 'sot5'),
}


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _nonnegative_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 and math.isfinite(parsed) else None


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def _fixture_id(row: Mapping[str, Any]) -> int | None:
    return _positive_int(row.get('id') or row.get('api_fixture_id'))


def _side_for_team(
    row: Mapping[str, Any],
    api_team_id: int,
) -> tuple[str, int | None] | None:
    home_api_id = _positive_int(row.get('home_team_id'))
    away_api_id = _positive_int(row.get('away_team_id'))
    if home_api_id == api_team_id and away_api_id != api_team_id:
        return 'home', _positive_int(row.get('home_team_ref_id'))
    if away_api_id == api_team_id and home_api_id != api_team_id:
        return 'away', _positive_int(row.get('away_team_ref_id'))
    return None


def _eligible_fixture(
    row: Mapping[str, Any],
    *,
    api_team_id: int,
    cutoff: datetime,
) -> dict[str, Any] | None:
    fixture_id = _fixture_id(row)
    side_value = _side_for_team(row, api_team_id)
    if fixture_id is None or side_value is None:
        return None
    if str(row.get('status_short') or '').upper() not in FINAL_STATUSES:
        return None
    try:
        kickoff = _utc_datetime(row.get('kickoff') or row.get('fixture_date_utc'))
    except (TypeError, ValueError):
        return None
    if kickoff >= cutoff:
        return None

    home_goals = _nonnegative_int(row.get('home_goals'))
    away_goals = _nonnegative_int(row.get('away_goals'))
    if home_goals is None or away_goals is None:
        return None
    side, surrogate_id = side_value
    goals_for, goals_against = (
        (home_goals, away_goals)
        if side == 'home'
        else (away_goals, home_goals)
    )
    points = 3 if goals_for > goals_against else 1 if goals_for == goals_against else 0
    return {
        'fixture_id': fixture_id,
        'league_id': _positive_int(row.get('league_id')),
        'kickoff': kickoff,
        'season': _nonnegative_int(row.get('season')),
        'side': side,
        'surrogate_team_id': surrogate_id,
        'goals_for': goals_for,
        'goals_against': goals_against,
        'points': points,
    }


def _deduplicate_fixtures(
    rows: Iterable[Mapping[str, Any]],
    *,
    api_team_id: int,
    cutoff: datetime,
) -> tuple[list[dict[str, Any]], int]:
    fixtures: dict[int, dict[str, Any]] = {}
    eligible_rows = 0
    for row in rows:
        fixture = _eligible_fixture(row, api_team_id=api_team_id, cutoff=cutoff)
        if fixture is None:
            continue
        eligible_rows += 1
        fixtures.setdefault(int(fixture['fixture_id']), fixture)
    values = sorted(
        fixtures.values(),
        key=lambda item: (item['kickoff'], item['fixture_id']),
    )
    return values, eligible_rows - len(values)


def _resolve_surrogate_team_id(
    fixtures: Iterable[Mapping[str, Any]],
    explicit_team_ref_id: int | None,
) -> tuple[int | None, bool]:
    if explicit_team_ref_id is not None:
        parsed = _positive_int(explicit_team_ref_id)
        if parsed is None:
            raise ValueError('team_ref_id must be a positive integer.')
        return parsed, False
    inferred = {
        int(value)
        for fixture in fixtures
        if (value := fixture.get('surrogate_team_id')) is not None
    }
    if len(inferred) == 1:
        return next(iter(inferred)), False
    return None, len(inferred) > 1


def _statistics_by_fixture(
    rows: Iterable[Mapping[str, Any]],
    *,
    fixture_ids: set[int],
    surrogate_team_id: int | None,
) -> tuple[dict[int, dict[str, float]], int]:
    if surrogate_team_id is None:
        return {}, 0
    statistics: dict[int, dict[str, float]] = {}
    duplicate_rows = 0
    for row in rows:
        fixture_id = _positive_int(row.get('fixture_id'))
        row_team_id = _positive_int(row.get('team_id'))
        if fixture_id not in fixture_ids or row_team_id != surrogate_team_id:
            continue
        values = {
            field: value
            for field in _STATISTIC_FIELDS
            if (value := _nonnegative_float(row.get(field))) is not None
        }
        if not values:
            continue
        if fixture_id in statistics:
            duplicate_rows += 1
            continue
        statistics[fixture_id] = values
    return statistics, duplicate_rows


def build_team_history_profile(
    *,
    api_team_id: int,
    team_name: str,
    fixture_rows: Iterable[Mapping[str, Any]],
    team_statistics_rows: Iterable[Mapping[str, Any]],
    cutoff: datetime | str,
    team_ref_id: int | None = None,
    min_matches: int = 5,
) -> dict[str, Any] | None:
    """Build a leakage-safe profile from already-loaded historical rows.

    ``fixtures.home_team_id`` and ``fixtures.away_team_id`` are API-Football
    identifiers. In contrast, ``fixture_team_statistics.team_id`` references
    the internal ``public.teams.id`` surrogate. The surrogate can be supplied
    explicitly or inferred from the matching fixture side; it is never assumed
    to equal ``api_team_id``.

    Only unique FT/AET/PEN fixtures strictly before ``cutoff`` are eligible.
    ``None`` is returned when fewer than ``min_matches`` scored fixtures remain.
    Missing detailed statistics remain ``None`` instead of being replaced with
    a league or cross-team prior.
    """

    parsed_api_team_id = _positive_int(api_team_id)
    if parsed_api_team_id is None:
        raise ValueError('api_team_id must be a positive integer.')
    normalized_name = str(team_name or '').strip()
    if not normalized_name:
        raise ValueError('team_name must not be blank.')
    if isinstance(min_matches, bool) or int(min_matches) < 1:
        raise ValueError('min_matches must be a positive integer.')
    minimum = int(min_matches)
    parsed_team_ref_id: int | None = None
    if team_ref_id is not None:
        parsed_team_ref_id = _positive_int(team_ref_id)
        if parsed_team_ref_id is None:
            raise ValueError('team_ref_id must be a positive integer.')
    try:
        cutoff_utc = _utc_datetime(cutoff)
    except (TypeError, ValueError) as exc:
        raise ValueError('cutoff must be a valid ISO datetime.') from exc

    fixtures, duplicate_fixture_rows = _deduplicate_fixtures(
        fixture_rows,
        api_team_id=parsed_api_team_id,
        cutoff=cutoff_utc,
    )
    if len(fixtures) < minimum:
        return None

    surrogate_team_id, surrogate_conflict = _resolve_surrogate_team_id(
        fixtures,
        parsed_team_ref_id,
    )
    fixture_ids = {int(row['fixture_id']) for row in fixtures}
    statistics, duplicate_statistic_rows = _statistics_by_fixture(
        team_statistics_rows,
        fixture_ids=fixture_ids,
        surrogate_team_id=surrogate_team_id,
    )
    recent = fixtures[-5:]
    recent_ids = {int(row['fixture_id']) for row in recent}
    league_counts: dict[int, int] = {}
    for fixture in fixtures:
        league_id = fixture.get('league_id')
        if league_id is not None:
            league_counts[int(league_id)] = (
                league_counts.get(int(league_id), 0) + 1
            )
    dominant_league_id = (
        min(
            league_counts,
            key=lambda league_id: (-league_counts[league_id], league_id),
        )
        if league_counts
        else None
    )

    def fixture_average(key: str, values: Iterable[Mapping[str, Any]]) -> float:
        selected = list(values)
        return sum(float(row[key]) for row in selected) / len(selected)

    result: dict[str, Any] = {
        'api_team_id': parsed_api_team_id,
        'team_name': normalized_name,
        'last_match_date': fixtures[-1]['kickoff'].date().isoformat(),
        'season': max(
            (int(row['season']) for row in fixtures if row.get('season') is not None),
            default=None,
        ),
        'season_mp': len(fixtures),
        'season_ppg': fixture_average('points', fixtures),
        'season_gfpg': fixture_average('goals_for', fixtures),
        'season_gapg': fixture_average('goals_against', fixtures),
        'form_ppg5': fixture_average('points', recent),
        'gf5': fixture_average('goals_for', recent),
        'ga5': fixture_average('goals_against', recent),
    }

    metric_samples: dict[str, dict[str, int]] = {}
    for source_field, (season_field, recent_field) in _STATISTIC_FIELDS.items():
        season_values = [
            row[source_field]
            for fixture_id, row in statistics.items()
            if fixture_id in fixture_ids and source_field in row
        ]
        recent_values = [
            row[source_field]
            for fixture_id, row in statistics.items()
            if fixture_id in recent_ids and source_field in row
        ]
        result[season_field] = _mean(season_values)
        result[recent_field] = _mean(recent_values)
        metric_samples[source_field] = {
            'season': len(season_values),
            'last_five': len(recent_values),
        }

    result['metadata'] = {
        'source': 'loaded_finished_fixtures_and_team_statistics',
        'cutoff_kickoff': cutoff_utc.isoformat(),
        'cutoff_rule': 'status in FT/AET/PEN and kickoff < cutoff',
        'minimum_matches': minimum,
        'api_team_id_domain': 'api_football_team_id',
        'statistics_team_id_domain': 'public.teams.id',
        'surrogate_team_id': surrogate_team_id,
        'surrogate_conflict': surrogate_conflict,
        'sample_sizes': {
            'finished_matches': len(fixtures),
            'last_five_matches': len(recent),
            'statistics_matches': len(statistics),
            'metrics': metric_samples,
        },
        'fixture_ids': [int(row['fixture_id']) for row in fixtures],
        'last_five_fixture_ids': [int(row['fixture_id']) for row in recent],
        'dominant_league_id': dominant_league_id,
        'competitions': [
            {'league_id': league_id, 'matches': league_counts[league_id]}
            for league_id in sorted(league_counts)
        ],
        'seasons': sorted({
            int(row['season'])
            for row in fixtures
            if row.get('season') is not None
        }),
        'first_kickoff': fixtures[0]['kickoff'].isoformat(),
        'last_kickoff': fixtures[-1]['kickoff'].isoformat(),
        'deduplicated_fixture_rows': duplicate_fixture_rows,
        'deduplicated_statistic_rows': duplicate_statistic_rows,
    }
    return result

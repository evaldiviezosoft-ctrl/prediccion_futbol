from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import DatabaseError, PredictionInputError, ProviderError
from app.db.supabase_client import get_supabase
from app.services.api_football_client import ApiFootballClient
from app.services.baseline_model_service import BASELINE_LEAGUE_IDS
from app.services.supabase_repository import SupabaseRepository


LEAGUE_ID_TO_CODE = {39: 'E0', 61: 'F1', 78: 'D1', 135: 'I1', 140: 'SP1'}
BUNDLED_LEAGUE_IDS = frozenset(LEAGUE_ID_TO_CODE)
SUPPORTED_LEAGUE_IDS = BUNDLED_LEAGUE_IDS | BASELINE_LEAGUE_IDS
SYNC_LEAGUE_IDS = SUPPORTED_LEAGUE_IDS


@dataclass(frozen=True)
class FixtureSyncResult:
    rows: list[dict[str, Any]]
    rate_limit: dict[str, Any] | None

    @property
    def synced(self) -> int:
        return len(self.rows)


def validate_timezone(timezone_name: str) -> str:
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise PredictionInputError(f'Unknown timezone: {timezone_name}') from exc
    return timezone_name


def fixture_row_from_api(item: dict[str, Any]) -> dict[str, Any]:
    try:
        league = item['league']
        fixture = item['fixture']
        teams = item['teams']
        home = teams['home']
        away = teams['away']
        return {
            'id': int(fixture['id']),
            'league_id': int(league['id']),
            'season': int(league['season']),
            'round': league.get('round'),
            'kickoff': fixture['date'],
            'timezone': fixture.get('timezone'),
            'venue_name': fixture.get('venue', {}).get('name'),
            'status_short': fixture.get('status', {}).get('short'),
            'home_team_id': int(home['id']),
            'away_team_id': int(away['id']),
            'home_team_name': str(home['name']),
            'away_team_name': str(away['name']),
            'raw_payload': item,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError('API-Football returned an incomplete fixture payload.') from exc


def _upsert_rows(rows: list[dict[str, Any]], db_client: Any) -> None:
    if not rows:
        return
    try:
        db_client.table('fixtures').upsert(rows, on_conflict='id').execute()
    except Exception as exc:
        raise DatabaseError('Could not persist fixtures.') from exc


def upsert_fixture_item(item: dict[str, Any], db_client: Any | None = None) -> dict[str, Any]:
    row = fixture_row_from_api(item)
    client = db_client if db_client is not None else get_supabase()
    _upsert_rows([row], client)
    return row


async def sync_fixtures_by_date(
    fixture_date: date | str,
    timezone_name: str,
    *,
    api_client: ApiFootballClient | None = None,
    db_client: Any | None = None,
) -> FixtureSyncResult:
    validate_timezone(timezone_name)
    try:
        normalized_date = date.fromisoformat(str(fixture_date)).isoformat()
    except ValueError as exc:
        raise PredictionInputError('Fixture date must use YYYY-MM-DD.') from exc

    client = db_client if db_client is not None else get_supabase()
    owns_api = api_client is None
    api = api_client or ApiFootballClient(
        request_log_sink=SupabaseRepository(client=client)
    )
    try:
        payload = await api.fixtures_by_date(
            normalized_date,
            timezone_name=timezone_name,
        )
        if isinstance(payload, dict):
            # Keep compatibility with injected clients that implement the old
            # envelope-shaped interface.
            items = payload.get('response', []) or []
            rate_limit = payload.get('_rate_limit')
        else:
            items = payload
            rate_limit = _rate_limit_payload(api)
        if not isinstance(items, list):
            raise ProviderError('API-Football returned an invalid fixtures response.')
        rows = []
        for item in items:
            if not isinstance(item, dict):
                raise ProviderError('API-Football returned an invalid fixture payload.')
            try:
                league_id = int(item['league']['id'])
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError('API-Football returned an invalid league payload.') from exc
            if league_id in SYNC_LEAGUE_IDS:
                rows.append(fixture_row_from_api(item))

        _upsert_rows(rows, client)
        return FixtureSyncResult(rows=rows, rate_limit=rate_limit)
    finally:
        if owns_api:
            await api.close()


def _rate_limit_payload(api_client: Any) -> dict[str, Any] | None:
    manager = getattr(api_client, 'rate_limit', None)
    snapshot = getattr(manager, 'snapshot', None)
    if snapshot is None:
        return None
    if callable(snapshot):
        snapshot = snapshot()
    if hasattr(snapshot, 'model_dump'):
        value = snapshot.model_dump(mode='json')
    elif isinstance(snapshot, dict):
        value = dict(snapshot)
    else:
        return None
    # Preserve the legacy keys consumed by /fixtures/sync-date while exposing
    # the new per-run/reserve fields alongside them.
    return {
        'remaining_day': value.get('daily_remaining'),
        'limit_day': value.get('daily_limit'),
        'remaining_minute': value.get('minute_remaining'),
        'limit_minute': value.get('minute_limit'),
        'requests_this_run': value.get('requests_this_run'),
        'daily_safety_reserve': value.get('daily_safety_reserve'),
        'can_continue': value.get('can_continue'),
        'stop_reason': value.get('stop_reason'),
    }

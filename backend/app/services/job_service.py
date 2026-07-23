from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.errors import (
    BackendError,
    DatabaseError,
    PredictionInputError,
    ProviderDateAccessError,
    ProviderRateLimitError,
)
from app.db.supabase_client import get_supabase
from app.services.api_football_client import ApiFootballClient
from app.services.baseline_model_service import (
    BASELINE_LEAGUE_IDS,
    BASELINE_UPCOMING_STATUSES,
)
from app.services.fixture_service import (
    SUPPORTED_LEAGUE_IDS,
    sync_fixtures_by_date,
    validate_timezone,
)
from app.services.prediction_service import refresh_prediction
from app.services.supabase_repository import SupabaseRepository


logger = logging.getLogger(__name__)
NOT_STARTED_STATUSES = {None, '', 'NS', 'TBD', 'PST'}


def _kickoff_utc(row: dict[str, Any]) -> datetime:
    value = str(row['kickoff']).replace('Z', '+00:00')
    kickoff = datetime.fromisoformat(value)
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return kickoff.astimezone(timezone.utc)


def _public_failure(exc: Exception) -> dict[str, str]:
    if isinstance(exc, BackendError):
        return {'code': exc.code, 'detail': exc.public_detail}
    return {'code': 'prediction_failed', 'detail': 'No se pudo generar esta predicción.'}


async def predict_stored_baselines(
    *,
    horizon_days: int = 30,
    max_matches: int = 25,
    now: datetime | None = None,
    db_client: Any | None = None,
) -> dict[str, Any]:
    """Publish predictions from stored fixtures without constructing a provider client."""

    if not 1 <= horizon_days <= 30:
        raise PredictionInputError('horizon_days must be between 1 and 30.')
    if not 1 <= max_matches <= 100:
        raise PredictionInputError('max_matches must be between 1 and 100.')
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    start = clock.astimezone(timezone.utc)
    end = start + timedelta(days=horizon_days)
    database = db_client if db_client is not None else get_supabase()
    repository = SupabaseRepository(client=database)
    try:
        fixtures = await repository.stored_upcoming_fixtures(
            league_ids=BASELINE_LEAGUE_IDS,
            start_kickoff=start.isoformat(),
            end_kickoff=end.isoformat(),
            statuses=BASELINE_UPCOMING_STATUSES,
            limit=max_matches,
        )
    except Exception as exc:
        raise DatabaseError('Could not read stored upcoming fixtures.') from exc

    results: list[dict[str, Any]] = []
    for row in fixtures:
        fixture_id = int(row['id'])
        try:
            prediction = await refresh_prediction(fixture_id, db_client=database)
            results.append({
                'fixture_id': fixture_id,
                'league_id': int(row['league_id']),
                'status': 'predicted',
                'stage': prediction['stage'],
                'model_type': prediction['model_metadata']['model_type'],
            })
        except Exception as exc:
            logger.warning(
                'Stored baseline prediction failed for fixture %s: %s',
                fixture_id,
                type(exc).__name__,
            )
            results.append({
                'fixture_id': fixture_id,
                'league_id': int(row['league_id']),
                'status': 'error',
                'error': _public_failure(exc),
            })

    succeeded = sum(result['status'] == 'predicted' for result in results)
    return {
        'mode': 'db_only_statistical_baseline',
        'provider_requests': 0,
        'horizon_days': horizon_days,
        'max_matches': max_matches,
        'fixtures_found': len(fixtures),
        'predictions_attempted': len(results),
        'predictions_succeeded': succeeded,
        'predictions_failed': len(results) - succeeded,
        'prediction_results': results,
    }


async def sync_and_predict(
    *,
    horizon_days: int | None = None,
    max_matches: int | None = None,
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    horizon = horizon_days or settings.scheduler_horizon_days
    limit = max_matches or settings.max_matches_per_scheduler_cycle
    if not 1 <= horizon <= 30:
        raise PredictionInputError('horizon_days must be between 1 and 30.')
    if not 1 <= limit <= 25:
        raise PredictionInputError('max_matches must be between 1 and 25.')

    tz_name = validate_timezone(timezone_name or settings.default_timezone)
    local_tz = ZoneInfo(tz_name)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    clock_utc = clock.astimezone(timezone.utc)
    first_date = clock_utc.astimezone(local_tz).date()

    db = get_supabase()
    api = ApiFootballClient(
        request_log_sink=SupabaseRepository(client=db)
    )
    sync_results: list[dict[str, Any]] = []
    sync_stop: dict[str, str] | None = None
    fixtures: dict[int, dict[str, Any]] = {}
    try:
        for offset in range(horizon):
            fixture_date = first_date + timedelta(days=offset)
            try:
                result = await sync_fixtures_by_date(
                    fixture_date,
                    tz_name,
                    api_client=api,
                    db_client=db,
                )
            except ProviderDateAccessError as exc:
                # A date-window restriction is a partial success only after at
                # least one day completed. On the first request it must remain a
                # visible error instead of masquerading as an empty job result.
                if not sync_results:
                    raise
                logger.info(
                    'Fixture sync horizon stopped at %s due to provider date access.',
                    fixture_date.isoformat(),
                )
                sync_stop = {
                    'date': fixture_date.isoformat(),
                    'code': exc.code,
                    'detail': exc.public_detail,
                }
                break
            sync_results.append({'date': fixture_date.isoformat(), 'synced': result.synced})
            for row in result.rows:
                fixtures[int(row['id'])] = row

        eligible = [
            row
            for row in fixtures.values()
            if row.get('status_short') in NOT_STARTED_STATUSES
            and int(row.get('league_id') or 0) in SUPPORTED_LEAGUE_IDS
            and _kickoff_utc(row) > clock_utc
        ]
        eligible.sort(key=_kickoff_utc)

        prediction_results: list[dict[str, Any]] = []
        for row in eligible[:limit]:
            fixture_id = int(row['id'])
            try:
                prediction = await refresh_prediction(
                    fixture_id,
                    api_client=api,
                    db_client=db,
                )
                prediction_results.append({
                    'fixture_id': fixture_id,
                    'status': 'predicted',
                    'stage': prediction['stage'],
                })
            except Exception as exc:
                logger.warning('Prediction failed for fixture %s: %s', fixture_id, type(exc).__name__)
                prediction_results.append({
                    'fixture_id': fixture_id,
                    'status': 'error',
                    'error': _public_failure(exc),
                })
                if isinstance(exc, ProviderRateLimitError):
                    break
    finally:
        await api.close()

    predicted = sum(item['status'] == 'predicted' for item in prediction_results)
    return {
        'horizon_days': horizon,
        'horizon_days_completed': len(sync_results),
        'horizon_truncated': sync_stop is not None,
        'max_matches': limit,
        'timezone': tz_name,
        'fixtures_synced': len(fixtures),
        'eligible_fixtures': len(eligible),
        'predictions_attempted': len(prediction_results),
        'predictions_succeeded': predicted,
        'predictions_failed': len(prediction_results) - predicted,
        'sync_results': sync_results,
        'sync_stop': sync_stop,
        'prediction_results': prediction_results,
    }

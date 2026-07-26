from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.db.supabase_client import get_supabase
from app.services.ai_calibration_service import (
    calibrate_stored_predictions,
    refresh_ai_calibration,
)
from app.services.api_football_client import ApiFootballClient
from app.services.job_service import predict_stored_baselines, sync_and_predict
from app.services.optional_fixture_sync_service import (
    LINEUPS_EARLIEST_WINDOW,
    OptionalFixtureSyncOptions,
    OptionalFixtureSyncService,
)
from app.services.supabase_repository import SupabaseRepository


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_scheduler: AsyncIOScheduler | None = None
LINEUP_POLL_INTERVAL_MINUTES = 15
LINEUP_POLL_LIMIT = 100


async def _scheduled_cycle() -> None:
    try:
        result = await sync_and_predict()
        logger.info(
            'Scheduled prediction cycle completed: attempted=%s succeeded=%s failed=%s',
            result['predictions_attempted'],
            result['predictions_succeeded'],
            result['predictions_failed'],
        )
    except Exception as exc:
        logger.error('Scheduled prediction cycle failed: %s', type(exc).__name__)

    try:
        settings = get_settings()
        catch_up = await predict_stored_baselines(
            horizon_days=settings.scheduler_prediction_horizon_days,
            max_matches=100,
        )
        logger.info(
            'Scheduled DB-only prediction catch-up completed: '
            'found=%s attempted=%s succeeded=%s failed=%s provider_requests=%s',
            catch_up['fixtures_found'],
            catch_up['predictions_attempted'],
            catch_up['predictions_succeeded'],
            catch_up['predictions_failed'],
            catch_up['provider_requests'],
        )
    except Exception as exc:
        logger.error(
            'Scheduled DB-only prediction catch-up failed: %s',
            type(exc).__name__,
        )

    try:
        settings = get_settings()
        if getattr(settings, 'openai_configured', False):
            calibrated = await calibrate_stored_predictions(
                horizon_days=getattr(
                    settings,
                    'ai_calibration_horizon_days',
                    settings.scheduler_prediction_horizon_days,
                ),
                max_matches=getattr(
                    settings,
                    'ai_calibration_max_per_cycle',
                    10,
                ),
                settings=settings,
            )
            logger.info(
                'Scheduled AI calibration completed: attempted=%s '
                'updated=%s failed=%s',
                calibrated['attempted'],
                calibrated['updated'],
                calibrated['failed'],
            )
    except Exception as exc:
        logger.error(
            'Scheduled AI calibration failed: %s',
            type(exc).__name__,
        )


async def _scheduled_lineup_cycle() -> None:
    """Poll only stored, predicted fixtures close enough to publish lineups."""

    settings = get_settings()
    database = get_supabase()
    repository = SupabaseRepository(client=database)
    api = ApiFootballClient(request_log_sink=repository)
    now = datetime.now(timezone.utc)
    try:
        candidates = await repository.list_optional_fixture_candidates(
            starts_at=now,
            ends_at=now + LINEUPS_EARLIEST_WINDOW,
            limit=LINEUP_POLL_LIMIT,
        )
        published_ids = await repository.published_prediction_fixture_ids(
            int(row['id']) for row in candidates
        )
        predicted_candidates = [
            row for row in candidates if int(row['id']) in published_ids
        ]
        result = await OptionalFixtureSyncService(api, repository).sync_many(
            predicted_candidates,
            options=OptionalFixtureSyncOptions(lineups=True),
            now=now,
        )
    except Exception as exc:
        logger.error(
            'Scheduled lineup sync failed: %s',
            type(exc).__name__,
        )
        return
    finally:
        await api.close()

    logger.info(
        'Scheduled lineup sync completed: candidates=%s downloaded=%s '
        'confirmed=%s skipped=%s',
        len(predicted_candidates),
        result.lineups_downloaded,
        len(result.confirmed_fixture_ids),
        result.skipped,
    )
    if not getattr(settings, 'openai_configured', False):
        return

    for fixture_id in result.confirmed_fixture_ids:
        try:
            await refresh_ai_calibration(
                fixture_id,
                repository=repository,
                db_client=database,
                settings=settings,
            )
        except Exception as exc:
            logger.error(
                'Final lineup calibration failed for fixture %s: %s',
                fixture_id,
                type(exc).__name__,
            )


def start_scheduler() -> AsyncIOScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.enable_scheduler:
        return None
    if not settings.api_football_configured or not settings.supabase_configured:
        logger.warning('Scheduler is enabled but provider or database configuration is incomplete.')
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    try:
        local_timezone = ZoneInfo(settings.default_timezone)
    except ZoneInfoNotFoundError:
        logger.warning('Scheduler timezone is invalid.')
        return None

    scheduler = AsyncIOScheduler(timezone=local_timezone)
    trigger = CronTrigger(
        hour=settings.scheduler_daily_hour,
        minute=settings.scheduler_daily_minute,
        timezone=local_timezone,
    )
    job_options: dict[str, object] = {
        'id': 'sync-and-predict',
        'replace_existing': True,
        'coalesce': True,
        'max_instances': 1,
    }
    if settings.scheduler_run_on_startup:
        job_options['next_run_time'] = datetime.now(local_timezone)
    scheduler.add_job(
        _scheduled_cycle,
        trigger=trigger,
        **job_options,
    )
    scheduler.add_job(
        _scheduled_lineup_cycle,
        trigger=IntervalTrigger(
            minutes=LINEUP_POLL_INTERVAL_MINUTES,
            timezone=local_timezone,
        ),
        id='sync-confirmed-lineups',
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        'Prediction scheduler started: daily at %02d:%02d %s; run_on_startup=%s.',
        settings.scheduler_daily_hour,
        settings.scheduler_daily_minute,
        settings.default_timezone,
        settings.scheduler_run_on_startup,
    )
    return scheduler


def stop_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    global _scheduler
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
    if scheduler is _scheduler:
        _scheduler = None

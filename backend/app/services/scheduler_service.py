from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.services.job_service import predict_stored_baselines, sync_and_predict


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_scheduler: AsyncIOScheduler | None = None


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

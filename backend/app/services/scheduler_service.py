from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import get_settings
from app.services.job_service import sync_and_predict


logger = logging.getLogger(__name__)
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
    scheduler.add_job(
        _scheduled_cycle,
        trigger='interval',
        minutes=settings.scheduler_interval_minutes,
        id='sync-and-predict',
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        next_run_time=datetime.now(local_timezone),
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info('Prediction scheduler started.')
    return scheduler


def stop_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    global _scheduler
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
    if scheduler is _scheduler:
        _scheduler = None

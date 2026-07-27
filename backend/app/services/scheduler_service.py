from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.db.supabase_client import get_supabase
from app.services.api_football_client import ApiFootballClient
from app.services.data_retention_service import run_safe_data_retention
from app.services.job_service import predict_stored_baselines, sync_and_predict
from app.services.optional_fixture_sync_service import (
    LINEUPS_EARLIEST_WINDOW,
    OptionalFixtureSyncOptions,
    OptionalFixtureSyncService,
)
from app.services.prediction_evaluation_service import (
    sync_and_evaluate_published_predictions,
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


async def _scheduled_postmatch_cycle() -> None:
    """Refresh finished fixtures and settle their stored prediction snapshot."""

    try:
        settings = get_settings()
        evaluated = await sync_and_evaluate_published_predictions(
            lookback_days=settings.postmatch_lookback_days,
            max_matches=settings.postmatch_max_matches,
        )
        logger.info(
            'Scheduled post-match evaluation completed: candidates=%s '
            'refreshed=%s evaluated=%s partial=%s void=%s legacy=%s',
            evaluated['candidates'],
            evaluated['details_refreshed'],
            evaluated['evaluated'],
            evaluated['partial'],
            evaluated['void'],
            evaluated['legacy_unscored'],
        )
    except Exception as exc:
        logger.error(
            'Scheduled post-match evaluation failed: %s',
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


async def _scheduled_retention_cycle() -> None:
    """Bound storage without deleting normalized training or audit history."""

    try:
        result = await run_safe_data_retention()
        logger.info(
            'Scheduled data retention completed: dry_run=%s batches=%s '
            'fixtures_compacted=%s api_logs_deleted=%s batch_limit_reached=%s',
            result['dry_run'],
            result['batches'],
            result['fixtures_compacted'],
            result['api_logs_deleted'],
            result['batch_limit_reached'],
        )
    except Exception as exc:
        logger.error(
            'Scheduled data retention failed: %s',
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
    scheduler.add_job(
        _scheduled_postmatch_cycle,
        trigger=IntervalTrigger(
            minutes=settings.postmatch_poll_interval_minutes,
            timezone=local_timezone,
        ),
        id='evaluate-postmatch-results',
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        next_run_time=datetime.now(local_timezone),
    )
    if settings.retention_enabled:
        scheduler.add_job(
            _scheduled_retention_cycle,
            trigger=CronTrigger(
                day_of_week=settings.retention_weekday,
                hour=settings.retention_hour,
                minute=settings.retention_minute,
                timezone=local_timezone,
            ),
            id='safe-data-retention',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        'Prediction scheduler started: daily at %02d:%02d %s; '
        'post-match every %d minutes; retention_enabled=%s; run_on_startup=%s.',
        settings.scheduler_daily_hour,
        settings.scheduler_daily_minute,
        settings.default_timezone,
        settings.postmatch_poll_interval_minutes,
        settings.retention_enabled,
        settings.scheduler_run_on_startup,
    )
    return scheduler


def stop_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    global _scheduler
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
    if scheduler is _scheduler:
        _scheduler = None

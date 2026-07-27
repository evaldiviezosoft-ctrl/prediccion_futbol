from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services import scheduler_service


class FakeAsyncIOScheduler:
    instances: list['FakeAsyncIOScheduler'] = []

    def __init__(self, *, timezone):
        self.timezone = timezone
        self.running = False
        self.jobs: list[tuple[object, dict[str, object]]] = []
        self.shutdown_wait: bool | None = None
        self.instances.append(self)

    def add_job(self, function, **kwargs):
        self.jobs.append((function, kwargs))

    def start(self):
        self.running = True

    def shutdown(self, *, wait):
        self.shutdown_wait = wait
        self.running = False


@pytest.fixture(autouse=True)
def reset_scheduler_singleton():
    scheduler_service._scheduler = None
    FakeAsyncIOScheduler.instances.clear()
    yield
    scheduler_service._scheduler = None
    FakeAsyncIOScheduler.instances.clear()


def _configured_settings(**overrides):
    values = {
        'enable_scheduler': True,
        'api_football_configured': True,
        'supabase_configured': True,
        'scheduler_run_on_startup': False,
        'default_timezone': 'America/Lima',
        'scheduler_daily_hour': 0,
        'scheduler_daily_minute': 5,
        'scheduler_prediction_horizon_days': 14,
        'postmatch_lookback_days': 7,
        'postmatch_max_matches': 100,
        'postmatch_poll_interval_minutes': 30,
        'retention_enabled': True,
        'retention_dry_run': False,
        'retention_raw_payload_days': 1825,
        'retention_api_log_days': 90,
        'retention_fixture_batch_size': 500,
        'retention_api_log_batch_size': 5000,
        'retention_max_batches': 10,
        'retention_weekday': 6,
        'retention_hour': 3,
        'retention_minute': 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_daily_scheduler_runs_immediately_and_then_uses_lima_cron(monkeypatch):
    monkeypatch.setattr(scheduler_service, 'AsyncIOScheduler', FakeAsyncIOScheduler)
    monkeypatch.setattr(
        scheduler_service,
        'get_settings',
        lambda: _configured_settings(scheduler_run_on_startup=True),
    )

    scheduler = scheduler_service.start_scheduler()

    assert scheduler is not None
    assert scheduler.running is True
    assert scheduler.timezone == ZoneInfo('America/Lima')
    assert len(scheduler.jobs) == 4
    function, job_options = scheduler.jobs[0]
    assert function is scheduler_service._scheduled_cycle
    assert job_options['id'] == 'sync-and-predict'
    assert job_options['coalesce'] is True
    assert job_options['max_instances'] == 1
    assert job_options['replace_existing'] is True

    immediate_run = job_options['next_run_time']
    assert isinstance(immediate_run, datetime)
    assert immediate_run.tzinfo == ZoneInfo('America/Lima')

    trigger = job_options['trigger']
    next_daily_run = trigger.get_next_fire_time(
        None,
        datetime(2026, 7, 23, 0, 6, tzinfo=ZoneInfo('America/Lima')),
    )
    assert next_daily_run == datetime(2026, 7, 24, 0, 5, tzinfo=ZoneInfo('America/Lima'))
    lineup_function, lineup_options = scheduler.jobs[1]
    assert lineup_function is scheduler_service._scheduled_lineup_cycle
    assert lineup_options['id'] == 'sync-confirmed-lineups'
    assert lineup_options['coalesce'] is True
    assert lineup_options['max_instances'] == 1
    assert lineup_options['replace_existing'] is True
    postmatch_function, postmatch_options = scheduler.jobs[2]
    assert postmatch_function is scheduler_service._scheduled_postmatch_cycle
    assert postmatch_options['id'] == 'evaluate-postmatch-results'
    assert postmatch_options['coalesce'] is True
    assert postmatch_options['max_instances'] == 1
    assert postmatch_options['replace_existing'] is True
    assert postmatch_options['trigger'].interval == timedelta(minutes=30)
    immediate_postmatch_run = postmatch_options['next_run_time']
    assert isinstance(immediate_postmatch_run, datetime)
    assert immediate_postmatch_run.tzinfo == ZoneInfo('America/Lima')
    retention_function, retention_options = scheduler.jobs[3]
    assert retention_function is scheduler_service._scheduled_retention_cycle
    assert retention_options['id'] == 'safe-data-retention'
    assert retention_options['coalesce'] is True
    assert retention_options['max_instances'] == 1
    assert retention_options['misfire_grace_time'] == 3600
    assert 'next_run_time' not in retention_options
    next_retention_run = retention_options['trigger'].get_next_fire_time(
        None,
        datetime(2026, 7, 27, 4, 0, tzinfo=ZoneInfo('America/Lima')),
    )
    assert next_retention_run == datetime(
        2026, 8, 2, 3, 30, tzinfo=ZoneInfo('America/Lima')
    )


def test_daily_scheduler_waits_for_cron_by_default(monkeypatch):
    monkeypatch.setattr(scheduler_service, 'AsyncIOScheduler', FakeAsyncIOScheduler)
    monkeypatch.setattr(scheduler_service, 'get_settings', _configured_settings)

    scheduler = scheduler_service.start_scheduler()

    assert scheduler is not None
    _, job_options = scheduler.jobs[0]
    assert 'next_run_time' not in job_options


def test_scheduled_cycle_runs_db_only_catch_up_with_prediction_horizon(
    monkeypatch,
    caplog,
):
    calls: list[tuple[str, dict[str, int]]] = []

    async def fake_sync_and_predict():
        calls.append(('sync', {}))
        return {
            'predictions_attempted': 2,
            'predictions_succeeded': 1,
            'predictions_failed': 1,
        }

    async def fake_predict_stored_baselines(**kwargs):
        calls.append(('catch_up', kwargs))
        return {
            'fixtures_found': 8,
            'predictions_attempted': 8,
            'predictions_succeeded': 7,
            'predictions_failed': 1,
            'provider_requests': 0,
        }

    monkeypatch.setattr(scheduler_service, 'sync_and_predict', fake_sync_and_predict)
    monkeypatch.setattr(
        scheduler_service,
        'predict_stored_baselines',
        fake_predict_stored_baselines,
    )
    monkeypatch.setattr(
        scheduler_service,
        'get_settings',
        lambda: _configured_settings(scheduler_prediction_horizon_days=9),
    )
    caplog.set_level(logging.INFO, logger=scheduler_service.__name__)

    asyncio.run(scheduler_service._scheduled_cycle())

    assert calls == [
        ('sync', {}),
        ('catch_up', {'horizon_days': 9, 'max_matches': 100}),
    ]
    assert 'provider_requests=0' in caplog.text


def test_scheduled_cycle_still_runs_db_only_catch_up_when_sync_fails(
    monkeypatch,
    caplog,
):
    catch_up_calls = []

    async def failing_sync_and_predict():
        raise RuntimeError('private provider details')

    async def fake_predict_stored_baselines(**kwargs):
        catch_up_calls.append(kwargs)
        return {
            'fixtures_found': 1,
            'predictions_attempted': 1,
            'predictions_succeeded': 1,
            'predictions_failed': 0,
            'provider_requests': 0,
        }

    monkeypatch.setattr(scheduler_service, 'sync_and_predict', failing_sync_and_predict)
    monkeypatch.setattr(
        scheduler_service,
        'predict_stored_baselines',
        fake_predict_stored_baselines,
    )
    monkeypatch.setattr(scheduler_service, 'get_settings', _configured_settings)
    caplog.set_level(logging.INFO, logger=scheduler_service.__name__)

    asyncio.run(scheduler_service._scheduled_cycle())

    assert catch_up_calls == [{'horizon_days': 14, 'max_matches': 100}]
    assert 'Scheduled prediction cycle failed: RuntimeError' in caplog.text
    assert 'Scheduled DB-only prediction catch-up completed' in caplog.text
    assert 'private provider details' not in caplog.text


def test_scheduled_cycle_contains_db_only_failure_after_successful_sync(
    monkeypatch,
    caplog,
):
    sync_calls = 0

    async def fake_sync_and_predict():
        nonlocal sync_calls
        sync_calls += 1
        return {
            'predictions_attempted': 1,
            'predictions_succeeded': 1,
            'predictions_failed': 0,
        }

    async def failing_predict_stored_baselines(**_kwargs):
        raise RuntimeError('private database details')

    monkeypatch.setattr(scheduler_service, 'sync_and_predict', fake_sync_and_predict)
    monkeypatch.setattr(
        scheduler_service,
        'predict_stored_baselines',
        failing_predict_stored_baselines,
    )
    monkeypatch.setattr(scheduler_service, 'get_settings', _configured_settings)
    caplog.set_level(logging.INFO, logger=scheduler_service.__name__)

    asyncio.run(scheduler_service._scheduled_cycle())

    assert sync_calls == 1
    assert 'Scheduled prediction cycle completed' in caplog.text
    assert 'Scheduled DB-only prediction catch-up failed: RuntimeError' in caplog.text
    assert 'private database details' not in caplog.text


def test_scheduled_postmatch_cycle_uses_configured_limits(monkeypatch, caplog):
    calls: list[dict[str, int]] = []

    async def fake_postmatch(**kwargs):
        calls.append(kwargs)
        return {
            'candidates': 3,
            'details_refreshed': 2,
            'evaluated': 1,
            'partial': 1,
            'void': 0,
            'legacy_unscored': 1,
        }

    monkeypatch.setattr(
        scheduler_service,
        'sync_and_evaluate_published_predictions',
        fake_postmatch,
    )
    monkeypatch.setattr(
        scheduler_service,
        'get_settings',
        lambda: _configured_settings(
            postmatch_lookback_days=5,
        ),
    )
    caplog.set_level(logging.INFO, logger=scheduler_service.__name__)

    asyncio.run(scheduler_service._scheduled_postmatch_cycle())

    assert calls == [{'lookback_days': 5, 'max_matches': 100}]
    assert 'candidates=3 refreshed=2 evaluated=1 partial=1' in caplog.text


def test_scheduled_postmatch_cycle_contains_failure_details(monkeypatch, caplog):
    async def failing_postmatch(**_kwargs):
        raise RuntimeError('private provider details')

    monkeypatch.setattr(
        scheduler_service,
        'sync_and_evaluate_published_predictions',
        failing_postmatch,
    )
    monkeypatch.setattr(scheduler_service, 'get_settings', _configured_settings)
    caplog.set_level(logging.INFO, logger=scheduler_service.__name__)

    asyncio.run(scheduler_service._scheduled_postmatch_cycle())

    assert 'Scheduled post-match evaluation failed: RuntimeError' in caplog.text
    assert 'private provider details' not in caplog.text


def test_scheduled_retention_cycle_logs_only_bounded_summary(monkeypatch, caplog):
    async def fake_retention():
        return {
            'dry_run': False,
            'batches': 2,
            'fixtures_compacted': 15,
            'api_logs_deleted': 250,
            'batch_limit_reached': False,
        }

    monkeypatch.setattr(
        scheduler_service,
        'run_safe_data_retention',
        fake_retention,
    )
    caplog.set_level(logging.INFO, logger=scheduler_service.__name__)

    asyncio.run(scheduler_service._scheduled_retention_cycle())

    assert 'fixtures_compacted=15 api_logs_deleted=250' in caplog.text


def test_scheduler_can_disable_only_the_retention_job(monkeypatch):
    monkeypatch.setattr(scheduler_service, 'AsyncIOScheduler', FakeAsyncIOScheduler)
    monkeypatch.setattr(
        scheduler_service,
        'get_settings',
        lambda: _configured_settings(retention_enabled=False),
    )

    scheduler = scheduler_service.start_scheduler()

    assert scheduler is not None
    assert [options['id'] for _, options in scheduler.jobs] == [
        'sync-and-predict',
        'sync-confirmed-lineups',
        'evaluate-postmatch-results',
    ]


def test_lineup_cycle_filters_to_predictions_and_downloads_only_confirmed_window(
    monkeypatch,
):
    calls: dict[str, object] = {}

    class Repository:
        async def list_optional_fixture_candidates(self, **kwargs):
            calls['window'] = kwargs
            return [{'id': 11}, {'id': 22}]

        async def published_prediction_fixture_ids(self, fixture_ids):
            calls['candidate_ids'] = list(fixture_ids)
            return {22}

    class Api:
        def __init__(self, *, request_log_sink):
            calls['request_log_sink'] = request_log_sink

        async def close(self):
            calls['closed'] = True

    class OptionalService:
        def __init__(self, api, repository):
            calls['service'] = (api, repository)

        async def sync_many(self, fixtures, *, options, now):
            calls['fixtures'] = fixtures
            calls['lineups_enabled'] = options.lineups
            calls['now'] = now
            return SimpleNamespace(
                lineups_downloaded=1,
                confirmed_fixture_ids=[22],
                skipped=0,
            )

    settings = _configured_settings()
    repository = Repository()
    database = object()
    monkeypatch.setattr(scheduler_service, 'get_settings', lambda: settings)
    monkeypatch.setattr(scheduler_service, 'get_supabase', lambda: database)
    monkeypatch.setattr(
        scheduler_service,
        'SupabaseRepository',
        lambda **_kwargs: repository,
    )
    monkeypatch.setattr(scheduler_service, 'ApiFootballClient', Api)
    monkeypatch.setattr(
        scheduler_service,
        'OptionalFixtureSyncService',
        OptionalService,
    )
    before = datetime.now(timezone.utc)
    asyncio.run(scheduler_service._scheduled_lineup_cycle())
    after = datetime.now(timezone.utc)

    assert calls['candidate_ids'] == [11, 22]
    assert calls['fixtures'] == [{'id': 22}]
    assert calls['lineups_enabled'] is True
    assert calls['closed'] is True
    window = calls['window']
    assert before <= window['starts_at'] <= after
    assert window['ends_at'] - window['starts_at'] == timedelta(minutes=90)


def test_start_scheduler_reuses_the_single_running_instance(monkeypatch):
    monkeypatch.setattr(scheduler_service, 'AsyncIOScheduler', FakeAsyncIOScheduler)
    monkeypatch.setattr(scheduler_service, 'get_settings', _configured_settings)

    first = scheduler_service.start_scheduler()
    second = scheduler_service.start_scheduler()

    assert second is first
    assert len(FakeAsyncIOScheduler.instances) == 1

    scheduler_service.stop_scheduler(first)

    assert first.shutdown_wait is False
    assert scheduler_service._scheduler is None


@pytest.mark.parametrize(
    ('field_name', 'invalid_value'),
    (
        ('scheduler_daily_hour', -1),
        ('scheduler_daily_hour', 24),
        ('scheduler_daily_minute', -1),
        ('scheduler_daily_minute', 60),
    ),
)
def test_daily_scheduler_time_settings_reject_invalid_values(field_name, invalid_value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: invalid_value})


def test_db_only_prediction_horizon_defaults_to_fourteen_days():
    assert Settings(_env_file=None).scheduler_prediction_horizon_days == 14


@pytest.mark.parametrize('invalid_value', (0, 31))
def test_db_only_prediction_horizon_rejects_invalid_values(invalid_value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            scheduler_prediction_horizon_days=invalid_value,
        )


@pytest.mark.parametrize('invalid_value', (9, 121))
def test_postmatch_poll_interval_rejects_invalid_values(invalid_value):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            postmatch_poll_interval_minutes=invalid_value,
        )


@pytest.mark.parametrize(
    ('field_name', 'invalid_value'),
    (
        ('retention_raw_payload_days', 364),
        ('retention_api_log_days', 6),
        ('retention_fixture_batch_size', 2001),
        ('retention_api_log_batch_size', 10001),
        ('retention_max_batches', 51),
        ('retention_weekday', 7),
        ('retention_hour', 24),
        ('retention_minute', 60),
    ),
)
def test_retention_settings_reject_unsafe_values(field_name, invalid_value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: invalid_value})


def test_scheduler_does_not_start_with_invalid_timezone(monkeypatch):
    monkeypatch.setattr(scheduler_service, 'AsyncIOScheduler', FakeAsyncIOScheduler)
    monkeypatch.setattr(
        scheduler_service,
        'get_settings',
        lambda: _configured_settings(default_timezone='Mars/Olympus'),
    )

    assert scheduler_service.start_scheduler() is None
    assert FakeAsyncIOScheduler.instances == []

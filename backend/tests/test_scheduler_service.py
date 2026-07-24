from __future__ import annotations

from datetime import datetime
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
    assert len(scheduler.jobs) == 1
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


def test_daily_scheduler_waits_for_cron_by_default(monkeypatch):
    monkeypatch.setattr(scheduler_service, 'AsyncIOScheduler', FakeAsyncIOScheduler)
    monkeypatch.setattr(scheduler_service, 'get_settings', _configured_settings)

    scheduler = scheduler_service.start_scheduler()

    assert scheduler is not None
    _, job_options = scheduler.jobs[0]
    assert 'next_run_time' not in job_options


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


def test_scheduler_does_not_start_with_invalid_timezone(monkeypatch):
    monkeypatch.setattr(scheduler_service, 'AsyncIOScheduler', FakeAsyncIOScheduler)
    monkeypatch.setattr(
        scheduler_service,
        'get_settings',
        lambda: _configured_settings(default_timezone='Mars/Olympus'),
    )

    assert scheduler_service.start_scheduler() is None
    assert FakeAsyncIOScheduler.instances == []

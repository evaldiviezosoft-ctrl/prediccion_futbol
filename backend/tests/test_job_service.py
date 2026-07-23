import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.core.errors import PredictionInputError, ProviderDateAccessError
from app.services import job_service
from app.services.api_football_client import ApiFootballClient as BudgetedApiFootballClient
from app.services.fixture_service import FixtureSyncResult


def fixture_row(fixture_id: int, kickoff: datetime, league_id: int = 39) -> dict:
    return {
        'id': fixture_id,
        'league_id': league_id,
        'kickoff': kickoff.isoformat(),
        'status_short': 'NS',
    }


class SharedApi:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_job_syncs_horizon_limits_predictions_and_reports_each_result(monkeypatch):
    now = datetime(2099, 8, 22, 12, tzinfo=timezone.utc)
    api = SharedApi()
    db = object()
    sink = object()
    api_factory_arguments = {}
    calls = {'sync': 0, 'predictions': []}
    settings = Settings(
        _env_file=None,
        scheduler_horizon_days=2,
        max_matches_per_scheduler_cycle=2,
        default_timezone='UTC',
    )

    async def fake_sync(*_args, **_kwargs):
        calls['sync'] += 1
        if calls['sync'] == 1:
            rows = [
                fixture_row(1, now + timedelta(hours=1)),
                fixture_row(2, now + timedelta(hours=2)),
                fixture_row(3, now + timedelta(hours=3)),
                fixture_row(4, now - timedelta(hours=1)),
                fixture_row(5, now + timedelta(hours=1), league_id=281),
            ]
        else:
            rows = []
        return FixtureSyncResult(rows=rows, rate_limit=None)

    async def fake_refresh(fixture_id, **_kwargs):
        calls['predictions'].append(fixture_id)
        if fixture_id == 5:
            raise PredictionInputError('profile unavailable')
        return {'stage': 'prematch'}

    monkeypatch.setattr(job_service, 'get_settings', lambda: settings)
    def api_factory(**kwargs):
        api_factory_arguments.update(kwargs)
        return api

    assert job_service.ApiFootballClient is BudgetedApiFootballClient
    monkeypatch.setattr(job_service, 'get_supabase', lambda: db)
    monkeypatch.setattr(
        job_service,
        'SupabaseRepository',
        lambda *, client: sink if client is db else None,
    )
    monkeypatch.setattr(job_service, 'ApiFootballClient', api_factory)
    monkeypatch.setattr(job_service, 'sync_fixtures_by_date', fake_sync)
    monkeypatch.setattr(job_service, 'refresh_prediction', fake_refresh)

    result = asyncio.run(job_service.sync_and_predict(now=now))

    assert calls['sync'] == 2
    assert calls['predictions'] == [1, 5]
    assert result['eligible_fixtures'] == 4
    assert result['predictions_attempted'] == 2
    assert result['predictions_succeeded'] == 1
    assert result['predictions_failed'] == 1
    assert result['prediction_results'][1]['error']['code'] == 'prediction_input_error'
    assert api_factory_arguments == {'request_log_sink': sink}
    assert api.closed is True


def test_job_keeps_synced_days_and_predicts_when_date_window_stops_horizon(monkeypatch):
    now = datetime(2099, 8, 22, 12, tzinfo=timezone.utc)
    api = SharedApi()
    sync_dates = []
    predicted = []
    settings = Settings(
        _env_file=None,
        scheduler_horizon_days=4,
        max_matches_per_scheduler_cycle=5,
        default_timezone='UTC',
    )

    async def fake_sync(fixture_date, *_args, **_kwargs):
        sync_dates.append(fixture_date.isoformat())
        if len(sync_dates) == 1:
            return FixtureSyncResult(
                rows=[fixture_row(101, now + timedelta(hours=2))],
                rate_limit=None,
            )
        raise ProviderDateAccessError('provider diagnostic containing private account data')

    async def fake_refresh(fixture_id, **_kwargs):
        predicted.append(fixture_id)
        return {'stage': 'prematch'}

    monkeypatch.setattr(job_service, 'get_settings', lambda: settings)
    monkeypatch.setattr(job_service, 'get_supabase', lambda: object())
    monkeypatch.setattr(job_service, 'ApiFootballClient', lambda **_kwargs: api)
    monkeypatch.setattr(job_service, 'sync_fixtures_by_date', fake_sync)
    monkeypatch.setattr(job_service, 'refresh_prediction', fake_refresh)

    result = asyncio.run(job_service.sync_and_predict(now=now))

    assert sync_dates == ['2099-08-22', '2099-08-23']
    assert predicted == [101]
    assert result['horizon_days'] == 4
    assert result['horizon_days_completed'] == 1
    assert result['horizon_truncated'] is True
    assert result['fixtures_synced'] == 1
    assert result['predictions_succeeded'] == 1
    assert result['sync_results'] == [{'date': '2099-08-22', 'synced': 1}]
    assert result['sync_stop']['date'] == '2099-08-23'
    assert result['sync_stop']['code'] == 'provider_date_access_restricted'
    assert 'private account data' not in str(result)
    assert api.closed is True


def test_job_does_not_hide_date_access_error_on_first_sync_day(monkeypatch):
    now = datetime(2099, 8, 22, 12, tzinfo=timezone.utc)
    api = SharedApi()
    settings = Settings(
        _env_file=None,
        scheduler_horizon_days=2,
        max_matches_per_scheduler_cycle=2,
        default_timezone='UTC',
    )

    async def fake_sync(*_args, **_kwargs):
        raise ProviderDateAccessError('first request failed')

    monkeypatch.setattr(job_service, 'get_settings', lambda: settings)
    monkeypatch.setattr(job_service, 'get_supabase', lambda: object())
    monkeypatch.setattr(job_service, 'ApiFootballClient', lambda **_kwargs: api)
    monkeypatch.setattr(job_service, 'sync_fixtures_by_date', fake_sync)

    with pytest.raises(ProviderDateAccessError):
        asyncio.run(job_service.sync_and_predict(now=now))

    assert api.closed is True


def test_db_only_baseline_job_never_constructs_provider_or_request_log(monkeypatch):
    now = datetime(2099, 8, 22, 12, tzinfo=timezone.utc)
    database = object()
    repository_calls = []
    refresh_calls = []

    class StoredRepository:
        def __init__(self, *, client):
            assert client is database

        async def stored_upcoming_fixtures(self, **kwargs):
            repository_calls.append(kwargs)
            return [
                fixture_row(501, now + timedelta(hours=1), league_id=281),
                fixture_row(502, now + timedelta(hours=2), league_id=71),
            ]

    async def fake_refresh(fixture_id, **kwargs):
        refresh_calls.append((fixture_id, kwargs))
        return {
            'stage': 'prematch',
            'model_metadata': {'model_type': 'statistical_baseline'},
        }

    def forbidden_provider(**_kwargs):
        raise AssertionError('DB-only publication cannot construct ApiFootballClient.')

    monkeypatch.setattr(job_service, 'SupabaseRepository', StoredRepository)
    monkeypatch.setattr(job_service, 'refresh_prediction', fake_refresh)
    monkeypatch.setattr(job_service, 'ApiFootballClient', forbidden_provider)

    result = asyncio.run(job_service.predict_stored_baselines(
        horizon_days=2,
        max_matches=5,
        now=now,
        db_client=database,
    ))

    assert result['mode'] == 'db_only_statistical_baseline'
    assert result['provider_requests'] == 0
    assert result['predictions_succeeded'] == 2
    assert [fixture_id for fixture_id, _kwargs in refresh_calls] == [501, 502]
    assert all(kwargs == {'db_client': database} for _fixture_id, kwargs in refresh_calls)
    assert repository_calls[0]['start_kickoff'] == now.isoformat()
    assert repository_calls[0]['end_kickoff'] == (now + timedelta(days=2)).isoformat()
    assert 'api_request_logs' not in str(repository_calls)

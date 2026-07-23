import argparse
import asyncio
from types import SimpleNamespace

import pytest

from backend.scripts import backfill_market_statistics as script


def test_cli_requires_explicit_request_cap_and_accepts_short_brazil_alias():
    parser = script.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(['--competition', 'brazil'])

    args = parser.parse_args([
        '--competition', 'brazil',
        '--max-requests', '2',
        '--max-fixtures', '100',
    ])
    assert args.competitions == ['brazil']
    # El plan real no admite `ids`: dos llamadas cubren como máximo dos fixtures.
    assert script._fixture_limit(args) == 2
    assert args.execute is False

    batch_args = parser.parse_args([
        '--competition', 'brazil',
        '--max-requests', '2',
        '--max-fixtures', '100',
        '--allow-batches',
    ])
    assert script._fixture_limit(batch_args) == 40


def test_plan_mode_never_constructs_api_client_or_writes(monkeypatch):
    calls = {}

    class Repository:
        async def list_enabled_competitions(self, codes):
            calls['codes'] = tuple(codes)
            return [{'id': 7, 'internal_code': 'brazil_serie_a'}]

        async def list_pending_market_fixture_details(self, **kwargs):
            calls['selection'] = kwargs
            return [{
                'api_fixture_id': 300,
                'priority_current_team': True,
            }]

    monkeypatch.setattr(script, 'get_settings', lambda: object())
    monkeypatch.setattr(script, 'SupabaseRepository', Repository)
    monkeypatch.setattr(
        script,
        'ApiFootballClient',
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('plan mode must not construct the provider client')
        ),
    )
    monkeypatch.setattr(script, 'print_summary', lambda value: calls.setdefault('summary', value))
    args = script.build_parser().parse_args([
        '--competition', 'brazil',
        '--max-requests', '2',
    ])

    asyncio.run(script.run(args))

    assert calls['codes'] == ('brazil_serie_a',)
    assert calls['selection'] == {
        'competition_ids': [7],
        'limit': 2,
        'max_attempts': 3,
    }
    assert calls['summary']['mode'] == 'plan_only'
    assert calls['summary']['fixture_ids'] == [300]


def test_execute_mode_injects_strict_per_run_request_manager(monkeypatch):
    calls = {}

    class Client:
        def __init__(self, **kwargs):
            calls['rate_limit'] = kwargs['rate_limit']

        async def close(self):
            calls['closed'] = True

    class Service:
        def __init__(self, client, repository, *, timezone):
            calls['timezone'] = timezone

        async def backfill_market_statistics(self, **kwargs):
            calls['backfill'] = kwargs
            return {'messages': [], 'details_complete': 0}

    settings = SimpleNamespace(
        api_daily_safety_reserve=15,
        api_timezone='America/Lima',
        default_timezone='America/Lima',
    )
    monkeypatch.setattr(script, 'get_settings', lambda: settings)
    monkeypatch.setattr(script, 'SupabaseRepository', lambda: object())
    monkeypatch.setattr(script, 'ApiFootballClient', Client)
    monkeypatch.setattr(script, 'HistoricalSyncService', Service)
    monkeypatch.setattr(script, 'print_summary', lambda _value: None)
    args = script.build_parser().parse_args([
        '--competition', 'argentina',
        '--max-requests', '3',
        '--execute',
    ])

    asyncio.run(script.run(args))

    assert calls['rate_limit'].max_requests_per_run == 3
    assert calls['rate_limit'].daily_safety_reserve == 15
    assert calls['backfill'] == {
        'competitions': ('argentina_liga_profesional',),
        'max_fixtures': 3,
        'max_attempts': 3,
        'force_singular_details': True,
    }
    assert calls['closed'] is True

import asyncio
from types import SimpleNamespace

from backend.scripts import backfill_team_metadata as script


def test_metadata_plan_is_read_only_and_counts_unique_teams(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        script,
        'ApiFootballClient',
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('plan mode must not construct the provider client')
        ),
    )
    monkeypatch.setattr(
        script,
        'SupabaseRepository',
        lambda: (_ for _ in ()).throw(
            AssertionError('plan mode must not construct a repository')
        ),
    )
    monkeypatch.setattr(
        script,
        'print_summary',
        lambda value: captured.setdefault('summary', value),
    )
    args = script.build_parser().parse_args([
        '--team-id', '69',
        '--team-id', '76',
        '--team-id', '69',
        '--max-requests', '2',
    ])

    asyncio.run(script.run(args))

    assert captured['summary']['team_ids'] == [69, 76]
    assert captured['summary']['metadata_requests_planned'] == 2
    assert captured['summary']['max_requests'] == 2


def test_metadata_execute_injects_strict_request_manager(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured['rate_limit'] = kwargs['rate_limit']

        async def close(self):
            captured['closed'] = True

    class Service:
        def __init__(self, client, repository, *, timezone):
            captured['timezone'] = timezone

        async def backfill_team_metadata(self, **kwargs):
            captured['backfill'] = kwargs
            return {'messages': []}

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
        '--team-id', '69',
        '--max-requests', '1',
        '--execute',
    ])

    asyncio.run(script.run(args))

    assert captured['rate_limit'].max_requests_per_run == 1
    assert captured['rate_limit'].daily_safety_reserve == 15
    assert captured['backfill'] == {'team_ids': (69,)}
    assert captured['closed'] is True

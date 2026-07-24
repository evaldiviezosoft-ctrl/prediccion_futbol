import asyncio
from types import SimpleNamespace

from backend.scripts import backfill_team_history as script


def test_team_history_plan_deduplicates_ids_and_reserves_listing_requests(monkeypatch):
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
        '--max-requests', '4',
    ])

    asyncio.run(script.run(args))

    assert captured['summary']['team_ids'] == [69, 76]
    assert captured['summary']['team_fixture_requests'] == 2
    assert captured['summary']['metadata_requests_planned'] == 0
    assert captured['summary']['minimum_requests_planned'] == 2
    assert captured['summary']['max_detail_fixtures'] == 40
    assert (
        captured['summary']['request_mode']
        == 'batch_with_automatic_singular_fallback'
    )


def test_team_history_execute_injects_quota_and_uses_batch_fallback(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured['rate_limit'] = kwargs['rate_limit']

        async def close(self):
            captured['closed'] = True

    class Service:
        def __init__(self, client, repository, *, timezone):
            captured['timezone'] = timezone

        async def backfill_team_history(self, **kwargs):
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
        '--team-id', '104',
        '--max-requests', '3',
        '--max-detail-fixtures', '10',
        '--season', '2024',
        '--execute',
    ])

    asyncio.run(script.run(args))

    assert captured['rate_limit'].max_requests_per_run == 3
    assert captured['backfill'] == {
        'team_ids': (104,),
        'max_fixtures_per_team': 20,
        'max_detail_fixtures': 10,
        'force_singular_details': False,
        'season': 2024,
    }
    assert captured['closed'] is True


def test_team_history_metadata_plan_reserves_calls_and_remains_read_only(
    monkeypatch,
):
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
        '--max-requests', '6',
        '--with-team-metadata',
    ])

    asyncio.run(script.run(args))

    assert captured['summary']['metadata_requests_planned'] == 2
    assert captured['summary']['minimum_requests_planned'] == 4
    assert captured['summary']['max_detail_fixtures'] == 40


def test_team_history_metadata_requires_nominal_budget_for_both_phases():
    args = script.build_parser().parse_args([
        '--team-id', '69',
        '--team-id', '76',
        '--max-requests', '3',
        '--with-team-metadata',
    ])

    try:
        asyncio.run(script.run(args))
    except ValueError as exc:
        assert '--max-requests 4' in str(exc)
    else:
        raise AssertionError('Expected insufficient metadata budget to fail.')


def test_team_history_execute_with_metadata_prioritizes_metadata(monkeypatch):
    captured = {'calls': []}

    class Client:
        def __init__(self, **kwargs):
            captured['rate_limit'] = kwargs['rate_limit']

        async def close(self):
            captured['closed'] = True

    class Service:
        def __init__(self, client, repository, *, timezone):
            captured['timezone'] = timezone

        async def backfill_team_metadata(self, **kwargs):
            captured['calls'].append(('metadata', kwargs))
            return {
                'team_metadata_updated': 1,
                'messages': ['metadata complete'],
            }

        async def backfill_team_history(self, **kwargs):
            captured['calls'].append(('history', kwargs))
            return {
                'fixtures_downloaded': 20,
                'messages': ['history complete'],
            }

    settings = SimpleNamespace(
        api_daily_safety_reserve=15,
        api_timezone='America/Lima',
        default_timezone='America/Lima',
    )
    monkeypatch.setattr(script, 'get_settings', lambda: settings)
    monkeypatch.setattr(script, 'SupabaseRepository', lambda: object())
    monkeypatch.setattr(script, 'ApiFootballClient', Client)
    monkeypatch.setattr(script, 'HistoricalSyncService', Service)
    monkeypatch.setattr(
        script,
        'print_summary',
        lambda value: captured.setdefault('summary', value),
    )
    args = script.build_parser().parse_args([
        '--team-id', '104',
        '--max-requests', '3',
        '--max-detail-fixtures', '10',
        '--season', '2024',
        '--with-team-metadata',
        '--execute',
    ])

    asyncio.run(script.run(args))

    assert captured['calls'] == [
        ('metadata', {'team_ids': (104,)}),
        (
            'history',
            {
                'team_ids': (104,),
                'max_fixtures_per_team': 20,
                'max_detail_fixtures': 10,
                'force_singular_details': False,
                'season': 2024,
            },
        ),
    ]
    assert captured['summary']['mode'] == 'executed_with_team_metadata'
    assert captured['summary']['metadata']['team_metadata_updated'] == 1
    assert captured['summary']['history']['fixtures_downloaded'] == 20
    assert captured['closed'] is True

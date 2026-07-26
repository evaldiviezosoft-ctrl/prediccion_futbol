import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services.supabase_repository import (
    SupabaseRepository,
    _apply_cup_aggregate_scores,
)


class _InjuryRepository(SupabaseRepository):
    def __init__(self) -> None:
        self.upserts: list[tuple[str, object, str]] = []
        self.status_updates: list[tuple[int, dict]] = []

    async def _id_map(self, *_args, **_kwargs):
        return {}

    async def _select(self, table, **_kwargs):
        assert table == 'fixture_injuries'
        return [
            {
                'fixture_id': 100,
                'source_key': 'no-longer-returned',
                'active': True,
            }
        ]

    async def _upsert(self, table, rows, *, on_conflict, **_kwargs):
        self.upserts.append((table, rows, on_conflict))
        return []

    async def update_optional_sync_status(self, fixture_id, changes):
        self.status_updates.append((fixture_id, changes))


def test_empty_injury_snapshot_marks_previous_rows_inactive_without_deleting_history():
    repository = _InjuryRepository()

    asyncio.run(
        repository.persist_injuries(
            100,
            [],
            fetched_at='2026-07-22T22:30:00+00:00',
        )
    )

    assert repository.upserts == [
        (
            'fixture_injuries',
            [
                {
                    'fixture_id': 100,
                    'source_key': 'no-longer-returned',
                    'active': False,
                }
            ],
            'fixture_id,source_key',
        )
    ]
    assert repository.status_updates[0][0] == 100


class _Response:
    def __init__(self, data):
        self.data = data


class _PaginatedQuery:
    def __init__(self, client):
        self.client = client
        self.start = 0
        self.end = len(client.rows) - 1

    def select(self, _columns):
        return self

    def range(self, start, end):
        self.start = start
        self.end = end
        return self

    def execute(self):
        self.client.ranges.append((self.start, self.end))
        return _Response(self.client.rows[self.start : self.end + 1])


class _PaginatedClient:
    def __init__(self, rows):
        self.rows = rows
        self.ranges = []

    def table(self, _table):
        return _PaginatedQuery(self)


def test_select_without_limit_reads_every_postgrest_page():
    expected = [{'id': value} for value in range(2_105)]
    client = _PaginatedClient(expected)
    repository = SupabaseRepository(client=client)

    actual = asyncio.run(repository._select('large_table'))

    assert actual == expected
    assert client.ranges == [(0, 999), (1000, 1999), (2000, 2999)]


def test_ai_calibration_candidates_prioritize_never_calibrated_fixtures(
    monkeypatch,
):
    repository = SupabaseRepository(client=object())
    predictions = [
        {
            'fixture_id': 1,
            'kickoff': '2026-07-26T10:00:00+00:00',
            'updated_at': '2026-07-25T10:00:00+00:00',
        },
        {
            'fixture_id': 2,
            'kickoff': '2026-07-26T11:00:00+00:00',
            'updated_at': '2026-07-25T11:00:00+00:00',
        },
    ]
    attempts = [{
        'fixture_id': 1,
        'attempt_number': 1,
        'status': 'updated',
        'retry_after': None,
        'base_prediction_updated_at': '2026-07-25T09:00:00+00:00',
        'model': 'gpt-5.6-sol',
        'reasoning_effort': 'max',
        'prompt_version': 'football-calibrator-1.1',
        'schema_version': 'ai-calibration-1.0',
    }]

    async def select(table, **_kwargs):
        if table == 'predictions':
            return predictions
        if table == 'prediction_calibrations':
            return attempts
        raise AssertionError(table)

    monkeypatch.setattr(repository, '_select', select)

    rows = asyncio.run(repository.ai_calibration_candidates(
        starts_at='2026-07-26T00:00:00+00:00',
        ends_at='2026-07-27T00:00:00+00:00',
        limit=1,
        model='gpt-5.6-sol',
        reasoning_effort='max',
        prompt_version='football-calibrator-1.1',
        schema_version='ai-calibration-1.0',
    ))

    assert [row['fixture_id'] for row in rows] == [2]


class _FilteredQuery:
    def __init__(self, client):
        self.client = client

    def select(self, columns):
        self.client.columns = columns
        return self

    def eq(self, column, value):
        self.client.filters.append(('eq', column, value))
        return self

    def in_(self, column, values):
        self.client.filters.append(('in', column, tuple(values)))
        return self

    def lt(self, column, value):
        self.client.filters.append(('lt', column, value))
        return self

    def order(self, column, desc=False):
        self.client.ordering = (column, desc)
        return self

    def range(self, start, end):
        self.client.ranges.append((start, end))
        return self

    def execute(self):
        return _Response(self.client.rows)


class _FilteredClient:
    def __init__(self, rows):
        self.rows = rows
        self.columns = None
        self.filters = []
        self.ordering = None
        self.ranges = []

    def table(self, table):
        assert table == 'fixtures'
        return _FilteredQuery(self)


def test_historical_fixture_query_enforces_strict_database_cutoff():
    client = _FilteredClient([{'id': 1}])
    repository = SupabaseRepository(client=client)

    rows = asyncio.run(repository.historical_finished_fixtures_before(
        league_id=71,
        kickoff='2026-07-23T00:30:00+00:00',
        statuses={'PEN', 'FT', 'AET'},
    ))

    assert rows == [{'id': 1}]
    assert ('eq', 'league_id', 71) in client.filters
    assert ('in', 'status_short', ('AET', 'FT', 'PEN')) in client.filters
    assert ('lt', 'kickoff', '2026-07-23T00:30:00+00:00') in client.filters
    assert client.ordering == ('kickoff', False)
    assert client.ranges == [(0, 999)]


def test_market_detail_candidates_prioritize_current_teams_then_newest(monkeypatch):
    repository = SupabaseRepository(client=object())
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)

    async def select(table, **kwargs):
        if table == 'api_sync_status':
            return [
                {'competition_id': 7, 'season': 2024, 'api_fixture_id': 100, 'attempts': 0},
                {'competition_id': 7, 'season': 2023, 'api_fixture_id': 200, 'attempts': 0},
                {'competition_id': 7, 'season': 2024, 'api_fixture_id': 300, 'attempts': 1},
                {'competition_id': 7, 'season': 2024, 'api_fixture_id': 400, 'attempts': 3},
                {'competition_id': 7, 'season': 2024, 'api_fixture_id': 600, 'attempts': 0},
                {
                    'competition_id': 7,
                    'season': 2024,
                    'api_fixture_id': 500,
                    'attempts': 0,
                    'retry_after': '2026-07-24T00:00:00+00:00',
                },
            ]
        if table == 'fixtures' and 'id' in (kwargs.get('in_values') or {}):
            return [
                {'id': 100, 'api_fixture_id': 100, 'competition_id': 7, 'season': 2024, 'fixture_date_utc': '2024-12-01T00:00:00+00:00', 'status_short': 'FT', 'home_team_id': 90, 'away_team_id': 91},
                {'id': 200, 'api_fixture_id': 200, 'competition_id': 7, 'season': 2023, 'fixture_date_utc': '2023-12-01T00:00:00+00:00', 'status_short': 'FT', 'home_team_id': 10, 'away_team_id': 92},
                {'id': 300, 'api_fixture_id': 300, 'competition_id': 7, 'season': 2024, 'fixture_date_utc': '2024-11-01T00:00:00+00:00', 'status_short': 'PEN', 'home_team_id': 10, 'away_team_id': 93},
                {'id': 600, 'api_fixture_id': 600, 'competition_id': 7, 'season': 2024, 'fixture_date_utc': '2024-12-30T00:00:00+00:00', 'status_short': 'CANC', 'home_team_id': 10, 'away_team_id': 94},
            ]
        if table == 'fixtures':
            assert kwargs['gte_values']['fixture_date_utc'].startswith('2025-07-18')
            return [{'competition_id': 7, 'home_team_id': 10, 'away_team_id': 11}]
        raise AssertionError(table)

    monkeypatch.setattr(repository, '_select', select)
    rows = asyncio.run(repository.list_pending_market_fixture_details(
        competition_ids=[7],
        limit=10,
        max_attempts=3,
        now=now,
    ))

    assert [row['api_fixture_id'] for row in rows] == [300, 200, 100]
    assert [row['priority_current_team'] for row in rows] == [True, True, False]


def test_mark_sync_component_pending_resets_statistics_and_increments_attempts():
    repository = SupabaseRepository(client=object())
    repository._select = AsyncMock(return_value=[{
        'attempts': 1,
        'statistics_downloaded': True,
        'fixture_details_downloaded': True,
    }])
    repository._upsert_sync_status = AsyncMock()

    asyncio.run(repository.mark_sync_component_pending(
        7,
        2024,
        7104,
        'statistics',
        'empty normalized statistics',
    ))

    changes = repository._upsert_sync_status.await_args.args[3]
    assert changes['statistics_downloaded'] is False
    assert changes['fixture_details_downloaded'] is False
    assert changes['completed_at'] is None
    assert changes['attempts'] == 2
    assert changes['last_error'] == 'empty normalized statistics'


def test_targeted_competition_is_disabled_and_deterministic():
    repository = SupabaseRepository(client=object())
    repository._select = AsyncMock(return_value=[])
    repository._upsert = AsyncMock(return_value=[{
        'id': 40,
        'api_league_id': 40,
        'internal_code': 'api_40',
        'name': 'Championship',
        'country': 'England',
        'competition_type': 'league',
        'enabled': False,
    }])

    stored = asyncio.run(repository.ensure_targeted_competition({
        'id': 40,
        'name': 'Championship',
        'country': 'England',
        'logo': 'league.png',
    }))

    assert stored['internal_code'] == 'api_40'
    written = repository._upsert.await_args.args[1]
    assert written['api_league_id'] == 40
    assert written['enabled'] is False
    assert repository._upsert.await_args.kwargs['on_conflict'] == 'api_league_id'


def test_team_metadata_persists_country_and_existing_venue_shape():
    repository = SupabaseRepository(client=object())
    calls = []

    async def upsert(table, rows, **kwargs):
        calls.append((table, rows, kwargs))
        return [{'id': 76}] if table == 'teams' else []

    repository._upsert = upsert
    result = asyncio.run(repository.persist_team_metadata({
        'team': {
            'id': 76,
            'name': 'Team 76',
            'code': 'T76',
            'country': 'England',
            'founded': 1901,
            'national': False,
            'logo': 'team.png',
        },
        'venue': {
            'id': 9076,
            'name': 'Ground',
            'city': 'City',
            'capacity': 12_000,
            'surface': 'grass',
            'image': 'ground.png',
        },
    }))

    assert calls[0][0] == 'teams'
    assert calls[0][1]['country'] == 'England'
    assert calls[1][0] == 'venues'
    assert calls[1][1]['api_venue_id'] == 9076
    assert result == {
        'api_team_id': 76,
        'team_ref_id': 76,
        'api_venue_id': 9076,
    }


def test_fixture_statistics_completion_uses_normalized_rows_not_sync_flag():
    repository = SupabaseRepository(client=object())
    repository._select = AsyncMock(return_value=[
        {'fixture_id': 8001},
        {'fixture_id': 8001},
    ])

    result = asyncio.run(repository.fixture_ids_with_team_statistics(
        [8001, 8002, 8001]
    ))

    assert result == {8001}
    assert repository._select.await_args.kwargs == {
        'columns': 'fixture_id',
        'in_values': {'fixture_id': [8001, 8002]},
    }


def test_targeted_team_history_query_is_not_restricted_to_modeled_leagues():
    repository = SupabaseRepository(client=object())
    calls = []

    async def select(table, **kwargs):
        calls.append((table, kwargs))
        team_column = next(iter(kwargs['equals']))
        return [{
            'id': 9001 if team_column == 'home_team_id' else 9002,
            'kickoff': (
                '2026-07-20T00:00:00+00:00'
                if team_column == 'home_team_id'
                else '2026-07-21T00:00:00+00:00'
            ),
        }]

    repository._select = select
    rows = asyncio.run(repository.historical_finished_fixtures_for_team(
        api_team_id=69,
        kickoff='2026-07-24T00:00:00+00:00',
        limit=10,
    ))

    assert [row['id'] for row in rows] == [9002, 9001]
    assert {next(iter(kwargs['equals'])) for _, kwargs in calls} == {
        'home_team_id',
        'away_team_id',
    }
    assert all('league_id' not in kwargs['in_values'] for _, kwargs in calls)
    assert all(
        kwargs['lt_values'] == {'kickoff': '2026-07-24T00:00:00+00:00'}
        for _, kwargs in calls
    )


def test_mark_sync_component_pending_rejects_unknown_component():
    repository = SupabaseRepository(client=object())
    with pytest.raises(ValueError, match='Unsupported sync component'):
        asyncio.run(repository.mark_sync_component_pending(
            7, 2024, 7104, 'odds', 'unsupported'
        ))


def test_sync_progress_counts_only_the_2021_to_2026_target_seasons():
    repository = SupabaseRepository(client=object())
    target_seasons = [
        {
            'id': competition_id * 100 + season,
            'competition_id': competition_id,
            'season': season,
            'availability_status': 'available' if season <= 2023 else 'unavailable',
        }
        for competition_id in range(1, 11)
        for season in range(2021, 2027)
    ]
    historical_catalog = [
        {
            'id': 10_000 + value,
            'competition_id': value % 10 + 1,
            'season': 2000 + value % 21,
            'availability_status': 'available',
        }
        for value in range(81)
    ]
    all_seasons = historical_catalog + target_seasons
    calls = []

    async def select(table, **kwargs):
        calls.append((table, kwargs))
        if table == 'competition_seasons':
            minimum = kwargs['gte_values']['season']
            maximum = kwargs['lte_values']['season']
            return [
                row for row in all_seasons
                if minimum <= row['season'] <= maximum
            ]
        if table == 'api_sync_status':
            return [
                {
                    'fixture_basic_downloaded': True,
                    'fixture_details_downloaded': False,
                    'statistics_downloaded': False,
                    'last_error': None,
                }
            ]
        if table == 'api_request_logs':
            return []
        raise AssertionError(f'unexpected table: {table}')

    repository._select = select
    progress = asyncio.run(repository.sync_progress())

    assert len(all_seasons) == 141
    assert progress['seasons_total'] == 60
    assert progress['seasons_available'] == 30
    assert progress['seasons_unavailable'] == 30
    scoped_calls = {
        table: kwargs
        for table, kwargs in calls
        if table in {'competition_seasons', 'api_sync_status'}
    }
    assert scoped_calls['competition_seasons']['gte_values'] == {'season': 2021}
    assert scoped_calls['competition_seasons']['lte_values'] == {'season': 2026}
    assert scoped_calls['api_sync_status']['gte_values'] == {'season': 2021}
    assert scoped_calls['api_sync_status']['lte_values'] == {'season': 2026}


def test_competition_resolution_persists_exactly_six_target_seasons():
    repository = SupabaseRepository(client=object())
    writes = []

    async def ensure_legacy_league(_competition):
        return None

    async def upsert(table, rows, **kwargs):
        writes.append((table, rows, kwargs))
        if table == 'competitions':
            return [{'id': 77, **rows}]
        return []

    repository.ensure_legacy_league = ensure_legacy_league
    repository._upsert = upsert
    asyncio.run(repository.upsert_competition_resolution({
        'internal_code': 'peru_liga_1',
        'api_league_id': 281,
        'name': 'Primera Division',
        'country': 'Peru',
        'competition_type': 'league',
        'seasons': [
            {'year': 2019, 'coverage': {'fixtures': {'events': True}}},
            {
                'year': 2022,
                'start': '2022-02-01',
                'end': '2022-11-30',
                'coverage': {'fixtures': {'events': True}},
            },
            {'year': 2024, 'coverage': {'fixtures': {'events': True}}},
            {'year': 2027, 'coverage': {'fixtures': {'events': True}}},
        ],
    }))

    season_rows = [
        row
        for table, row, _kwargs in writes
        if table == 'competition_seasons'
    ]
    assert [row['season'] for row in season_rows] == list(range(2021, 2027))
    assert {
        row['season']: row['availability_status']
        for row in season_rows
    } == {
        2021: 'unavailable',
        2022: 'available',
        2023: 'unavailable',
        2024: 'available',
        2025: 'unavailable',
        2026: 'unavailable',
    }
    season_2022 = next(row for row in season_rows if row['season'] == 2022)
    assert season_2022['competition_id'] == 77
    assert season_2022['start_date'] == '2022-02-01'
    assert season_2022['coverage_json'] == {'fixtures': {'events': True}}


def test_two_finished_cup_legs_receive_relative_aggregate_scores():
    first_leg = {
        'api_fixture_id': 1,
        'competition_id': 13,
        'season': 2024,
        'round': 'Semi-finals',
        'fixture_date_utc': '2024-10-01T00:00:00+00:00',
        'status_short': 'FT',
        'home_team_id': 10,
        'away_team_id': 20,
        'home_goals': 2,
        'away_goals': 1,
        'leg': None,
    }
    second_leg = {
        'api_fixture_id': 2,
        'competition_id': 13,
        'season': 2024,
        'round': 'Semi-finals',
        'fixture_date_utc': '2024-10-08T00:00:00+00:00',
        'status_short': 'PEN',
        'home_team_id': 20,
        'away_team_id': 10,
        'home_goals': 3,
        'away_goals': 1,
        'leg': None,
    }

    _apply_cup_aggregate_scores([first_leg, second_leg])

    assert (first_leg['aggregate_home'], first_leg['aggregate_away']) == (3, 4)
    assert (second_leg['aggregate_home'], second_leg['aggregate_away']) == (4, 3)
    assert first_leg['leg'] == 'first'
    assert second_leg['leg'] == 'second'

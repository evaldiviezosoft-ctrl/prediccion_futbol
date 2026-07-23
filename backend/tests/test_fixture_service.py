import asyncio
from types import SimpleNamespace

import pytest

from app.core.errors import PredictionInputError
from app.services import fixture_service
from app.services.api_football_client import ApiFootballClient as BudgetedApiFootballClient
from app.services.fixture_service import fixture_row_from_api, sync_fixtures_by_date


def fixture_item(fixture_id: int, league_id: int = 39) -> dict:
    return {
        'fixture': {
            'id': fixture_id,
            'date': '2099-08-22T18:00:00+00:00',
            'timezone': 'UTC',
            'venue': {'name': 'Test Stadium'},
            'status': {'short': 'NS'},
        },
        'league': {'id': league_id, 'season': 2099, 'round': 'Round 1'},
        'teams': {
            'home': {'id': 10, 'name': 'Arsenal'},
            'away': {'id': 20, 'name': 'Aston Villa'},
        },
    }


class RecordingTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def upsert(self, payload, on_conflict=None):
        self.db.operations.append(('upsert', self.name, payload, on_conflict))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class RecordingDb:
    def __init__(self):
        self.operations = []

    def table(self, name):
        return RecordingTable(self, name)


class FixtureApi:
    def __init__(self, items):
        self.items = items
        self.closed = False

    async def fixtures_by_date(self, fixture_date, *, timezone_name):
        return {
            'response': self.items,
            '_rate_limit': {'remaining_day': '88'},
        }

    async def close(self):
        self.closed = True


def test_fixture_payload_is_normalized():
    row = fixture_row_from_api(fixture_item(123))

    assert row['id'] == 123
    assert row['league_id'] == 39
    assert row['home_team_name'] == 'Arsenal'
    assert row['raw_payload']['fixture']['id'] == 123


def test_sync_filters_unsupported_leagues_and_upserts_once():
    api = FixtureApi([
        fixture_item(1),
        fixture_item(2, league_id=281),
        fixture_item(3, league_id=999),
    ])
    db = RecordingDb()

    result = asyncio.run(
        sync_fixtures_by_date('2099-08-22', 'UTC', api_client=api, db_client=db)
    )

    assert result.synced == 2
    assert result.rows[0]['id'] == 1
    assert result.rows[1]['league_id'] == 281
    assert db.operations[0][0:2] == ('upsert', 'fixtures')
    assert api.closed is False


def test_sync_rejects_an_invalid_timezone_before_calling_provider():
    api = FixtureApi([])

    with pytest.raises(PredictionInputError):
        asyncio.run(
            sync_fixtures_by_date('2099-08-22', 'Not/A-Timezone', api_client=api, db_client=RecordingDb())
        )


def test_default_sync_client_uses_request_log_sink_and_is_closed(monkeypatch):
    db = RecordingDb()
    api = FixtureApi([fixture_item(77)])
    sink = object()
    captured = {}

    def repository_factory(*, client):
        assert client is db
        return sink

    def api_factory(*, request_log_sink):
        captured['sink'] = request_log_sink
        return api

    assert fixture_service.ApiFootballClient is BudgetedApiFootballClient
    monkeypatch.setattr(fixture_service, 'get_supabase', lambda: db)
    monkeypatch.setattr(fixture_service, 'SupabaseRepository', repository_factory)
    monkeypatch.setattr(fixture_service, 'ApiFootballClient', api_factory)

    result = asyncio.run(sync_fixtures_by_date('2099-08-22', 'UTC'))

    assert result.synced == 1
    assert captured['sink'] is sink
    assert api.closed is True

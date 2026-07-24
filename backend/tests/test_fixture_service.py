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
            'venue': {'id': 300, 'name': 'Test Stadium'},
            'status': {'short': 'NS'},
        },
        'league': {'id': league_id, 'season': 2099, 'round': 'Round 1'},
        'teams': {
            'home': {
                'id': 10,
                'name': 'Arsenal',
                'logo': 'https://example.test/arsenal.png',
            },
            'away': {
                'id': 20,
                'name': 'Aston Villa',
                'logo': 'https://example.test/villa.png',
            },
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


def competition(league_id: int, *, competition_id: int | None = None) -> dict:
    return {
        'id': competition_id or league_id,
        'api_league_id': league_id,
        'internal_code': f'league_{league_id}',
        'name': f'League {league_id}',
        'country': 'World',
        'competition_type': 'cup' if league_id in {3, 667} else 'league',
        'enabled': True,
    }


class RecordingRepository:
    def __init__(self, competitions):
        self.competitions = competitions
        self.persisted = []

    async def list_enabled_competitions(self):
        return self.competitions

    async def persist_fixtures_basic(self, fixtures, *, competition):
        self.persisted.append((competition, list(fixtures)))
        return {fixture.api_fixture_id: True for fixture in fixtures}


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


def test_sync_filters_unconfigured_leagues_and_persists_normalized_groups(monkeypatch):
    api = FixtureApi([
        fixture_item(1),
        fixture_item(2, league_id=281),
        fixture_item(3, league_id=3),
        fixture_item(4, league_id=667),
        fixture_item(5, league_id=999),
    ])
    db = RecordingDb()
    repository = RecordingRepository([
        competition(39),
        competition(281),
        competition(3),
        competition(667),
    ])
    monkeypatch.setattr(
        fixture_service,
        'SupabaseRepository',
        lambda *, client: repository,
    )

    result = asyncio.run(
        sync_fixtures_by_date('2099-08-22', 'UTC', api_client=api, db_client=db)
    )

    assert result.synced == 4
    assert result.rows[0]['id'] == 1
    assert result.rows[1]['league_id'] == 281
    assert result.rows[2]['league_id'] == 3
    assert result.rows[3]['league_id'] == 667
    assert 3 not in fixture_service.SUPPORTED_LEAGUE_IDS
    assert 667 not in fixture_service.SUPPORTED_LEAGUE_IDS
    assert [group[0]['api_league_id'] for group in repository.persisted] == [
        39,
        281,
        3,
        667,
    ]
    first_fixture = repository.persisted[0][1][0]
    assert first_fixture.fixture['api_fixture_id'] == 1
    assert first_fixture.fixture['competition_id'] == 39
    assert first_fixture.fixture['fixture_date_lima'] is not None
    assert first_fixture.teams[0]['logo_url'] == 'https://example.test/arsenal.png'
    assert first_fixture.teams[1]['logo_url'] == 'https://example.test/villa.png'
    assert first_fixture.venue['api_venue_id'] == 300
    assert db.operations == []
    assert api.closed is False


def test_sync_safely_skips_a_syncable_but_unresolved_competition(monkeypatch):
    api = FixtureApi([fixture_item(4, league_id=667)])
    repository = RecordingRepository([
        {
            'id': 12,
            'api_league_id': None,
            'internal_code': 'friendlies_clubs',
            'name': 'Friendlies Clubs',
            'country': 'World',
            'competition_type': 'cup',
            'enabled': True,
        },
    ])
    monkeypatch.setattr(
        fixture_service,
        'SupabaseRepository',
        lambda *, client: repository,
    )

    result = asyncio.run(
        sync_fixtures_by_date(
            '2099-08-22',
            'UTC',
            api_client=api,
            db_client=RecordingDb(),
        )
    )

    assert result.synced == 0
    assert repository.persisted == []


def test_sync_rejects_an_invalid_timezone_before_calling_provider():
    api = FixtureApi([])

    with pytest.raises(PredictionInputError):
        asyncio.run(
            sync_fixtures_by_date('2099-08-22', 'Not/A-Timezone', api_client=api, db_client=RecordingDb())
        )


def test_default_sync_client_uses_request_log_sink_and_is_closed(monkeypatch):
    db = RecordingDb()
    api = FixtureApi([fixture_item(77)])
    repository = RecordingRepository([competition(39)])
    captured = {}

    def repository_factory(*, client):
        assert client is db
        return repository

    def api_factory(*, request_log_sink):
        captured['sink'] = request_log_sink
        return api

    assert fixture_service.ApiFootballClient is BudgetedApiFootballClient
    monkeypatch.setattr(fixture_service, 'get_supabase', lambda: db)
    monkeypatch.setattr(fixture_service, 'SupabaseRepository', repository_factory)
    monkeypatch.setattr(fixture_service, 'ApiFootballClient', api_factory)

    result = asyncio.run(sync_fixtures_by_date('2099-08-22', 'UTC'))

    assert result.synced == 1
    assert captured['sink'] is repository
    assert len(repository.persisted) == 1
    assert api.closed is True

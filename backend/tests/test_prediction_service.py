import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.errors import PredictionInputError, ProviderAccessRestrictionError
from app.services import prediction_service


def fixture_item(fixture_id: int) -> dict:
    return {
        'fixture': {
            'id': fixture_id,
            'date': '2099-08-22T18:00:00+00:00',
            'timezone': 'UTC',
            'venue': {'name': 'Test Stadium'},
            'status': {'short': 'NS'},
        },
        'league': {'id': 39, 'season': 2099, 'round': 'Round 1'},
        'teams': {
            'home': {'id': 10, 'name': 'Arsenal'},
            'away': {'id': 20, 'name': 'Aston Villa'},
        },
    }


def stored_fixture_row(fixture_id: int, *, league_id: int = 39) -> dict:
    item = fixture_item(fixture_id)
    return {
        'id': fixture_id,
        'league_id': league_id,
        'competition_id': league_id,
        'season': item['league']['season'],
        'kickoff': item['fixture']['date'],
        'fixture_date_utc': item['fixture']['date'],
        'status_short': item['fixture']['status']['short'],
        'home_team_id': item['teams']['home']['id'],
        'away_team_id': item['teams']['away']['id'],
        'home_team_name': item['teams']['home']['name'],
        'away_team_name': item['teams']['away']['name'],
    }


class PredictionApi:
    async def fixture(self, fixture_id):
        return {'response': [fixture_item(fixture_id)]}

    async def odds(self, fixture_id):
        raise AssertionError('Prediction refresh must not request odds automatically.')

    async def lineups(self, fixture_id):
        raise AssertionError('Far-future fixtures must not request lineups.')

    async def close(self):
        raise AssertionError('A shared API client must not be closed by refresh_prediction.')


class RecordingTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.action = None
        self.filters = {}

    def select(self, _columns):
        self.action = 'select'
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def limit(self, _value):
        return self

    def upsert(self, payload, on_conflict=None):
        self.db.operations.append(('upsert', self.name, payload))
        return self

    def insert(self, payload):
        self.db.operations.append(('insert', self.name, payload))
        return self

    def execute(self):
        if self.action == 'select' and self.name == 'fixtures':
            fixture_id = int(self.filters['id'])
            row = self.db.fixtures.get(fixture_id)
            return SimpleNamespace(data=[row] if row else [])
        return SimpleNamespace(data=[])


class RecordingDb:
    def __init__(self):
        self.operations = []
        self.fixtures = {
            fixture_id: stored_fixture_row(fixture_id)
            for fixture_id in (123, 321)
        }

    def table(self, name):
        return RecordingTable(self, name)


def model_output() -> dict:
    return {
        'probabilities': {
            'home_win': 0.5,
            'draw': 0.3,
            'away_win': 0.2,
            'over_2_5': 0.55,
            'btts': 0.45,
        },
        'expected': {'home_goals': 1.5, 'away_goals': 1.0},
        'goal_lines': [
            {'line': 0.5, 'probability': 0.9179},
            {'line': 1.5, 'probability': 0.7127},
            {'line': 2.5, 'probability': 0.4562},
            {'line': 3.5, 'probability': 0.2424},
            {'line': 4.5, 'probability': 0.1088},
        ],
        'likely_scores': [{'score': '1-0', 'probability': 0.2}],
        'model': {'league': 'Premier League'},
    }


def test_refresh_upserts_fixture_before_prediction(monkeypatch):
    db = RecordingDb()
    monkeypatch.setattr(prediction_service, 'build_features', lambda **_kwargs: {'HomeElo': 1500.0})
    monkeypatch.setattr(prediction_service, 'predict', lambda *_args: model_output())

    record = asyncio.run(
        prediction_service.refresh_prediction(
            123,
            api_client=PredictionApi(),
            db_client=db,
        )
    )

    assert record['fixture_id'] == 123
    assert record['stage'] == 'initial'
    assert [(operation, table) for operation, table, _payload in db.operations] == [
        ('upsert', 'fixtures'),
        ('upsert', 'predictions'),
        ('insert', 'prediction_versions'),
    ]


class RestrictedOptionalApi:
    def __init__(self, *, fixture_restricted=False):
        self.fixture_restricted = fixture_restricted
        self.optional_calls = []

    async def fixture(self, fixture_id):
        if self.fixture_restricted:
            raise ProviderAccessRestrictionError('mandatory fixture unavailable')
        item = fixture_item(fixture_id)
        item['fixture']['date'] = (
            datetime.now(timezone.utc) + timedelta(minutes=30)
        ).isoformat()
        return {'response': [item]}

    async def odds(self, fixture_id):
        raise AssertionError('Prediction refresh must not request odds automatically.')

    async def lineups(self, fixture_id):
        self.optional_calls.append(('lineups', fixture_id))
        raise ProviderAccessRestrictionError('lineups not included')


def test_refresh_uses_empty_odds_and_degrades_plan_restricted_lineups(monkeypatch):
    db = RecordingDb()
    api = RestrictedOptionalApi()
    captured = {}

    def fake_build_features(**kwargs):
        captured['odds'] = kwargs['odds']
        return {'HomeElo': 1500.0}

    monkeypatch.setattr(prediction_service, 'build_features', fake_build_features)
    monkeypatch.setattr(prediction_service, 'predict', lambda *_args: model_output())

    record = asyncio.run(
        prediction_service.refresh_prediction(321, api_client=api, db_client=db)
    )

    assert api.optional_calls == [('lineups', 321)]
    assert all(value is None for value in captured['odds'].values())
    assert record['lineups_confirmed'] is False
    assert record['stage'] == 'waiting_lineups'
    assert record['possible_scorers'] == []


def test_refresh_does_not_hide_access_error_for_required_fixture(monkeypatch):
    db = RecordingDb()
    api = RestrictedOptionalApi(fixture_restricted=True)

    with pytest.raises(ProviderAccessRestrictionError):
        asyncio.run(
            prediction_service.refresh_prediction(321, api_client=api, db_client=db)
        )

    assert api.optional_calls == []
    assert db.operations == []


class BaselineRepository:
    def __init__(self, fixture):
        self.fixture = fixture
        self.history_calls = []

    async def prediction_fixture(self, fixture_id):
        return self.fixture if fixture_id == self.fixture['id'] else None

    async def historical_finished_fixtures_before(self, **kwargs):
        self.history_calls.append(kwargs)
        cutoff = datetime.fromisoformat(kwargs['kickoff'])
        return [
            {
                'id': index + 1,
                'league_id': self.fixture['league_id'],
                'season': 2098,
                'kickoff': (cutoff - timedelta(days=30 - index)).isoformat(),
                'status_short': 'FT',
                'home_team_id': 10 if index < 8 else 100 + index,
                'away_team_id': 20 if 8 <= index < 15 else 200 + index,
                'home_goals': index % 4,
                'away_goals': index % 3,
            }
            for index in range(30)
        ]

    async def historical_finished_fixtures_before_many(self, **kwargs):
        self.market_history_call = kwargs
        rows = await self.historical_finished_fixtures_before(
            league_id=self.fixture['league_id'],
            kickoff=kwargs['kickoff'],
            statuses=kwargs['statuses'],
        )
        self.history_calls.pop()
        return rows

    async def team_statistics_for_fixtures(self, fixture_ids):
        return [
            {
                'fixture_id': fixture_id,
                'team_id': 10,
                'is_home': True,
                'corners': 5,
                'total_shots': 12,
                'shots_on_goal': 4,
            }
            for fixture_id in fixture_ids
        ]

    async def player_statistics_for_fixtures(self, **_kwargs):
        return []

    async def players_by_ids(self, _player_ids):
        return {}


def test_south_american_refresh_is_db_only_and_persists_existing_contract(monkeypatch):
    db = RecordingDb()
    fixture = stored_fixture_row(777, league_id=281)
    fixture['home_team_name'] = 'Sporting Cristal'
    fixture['away_team_name'] = 'Alianza Lima'
    repository = BaselineRepository(fixture)

    monkeypatch.setattr(prediction_service, 'SupabaseRepository', lambda **_kwargs: repository)

    def forbidden_api_client(**_kwargs):
        raise AssertionError('The DB-only baseline must not construct ApiFootballClient.')

    monkeypatch.setattr(prediction_service, 'ApiFootballClient', forbidden_api_client)

    record = asyncio.run(prediction_service.refresh_prediction(777, db_client=db))

    assert repository.history_calls == [{
        'league_id': 281,
        'kickoff': '2099-08-22T18:00:00+00:00',
        'statuses': frozenset({'FT', 'AET', 'PEN'}),
    }]
    assert [(operation, table) for operation, table, _payload in db.operations] == [
        ('upsert', 'predictions'),
        ('insert', 'prediction_versions'),
    ]
    assert record['league_code'] == 'peru_liga_1'
    assert record['model_metadata']['model_type'] == 'statistical_baseline'
    assert record['model_metadata']['trained_rows'] == 30
    assert record['likely_scores'] == []
    assert record['model_metadata']['goal_lines'][2]['line'] == 2.5
    assert record['model_metadata']['possible_assistants'] == []
    assert record['expected']['home_corners'] == 5
    assert record['possible_scorers'] == []


@pytest.mark.parametrize(
    ('status_short', 'kickoff'),
    [
        ('FT', '2099-08-22T18:00:00+00:00'),
        ('NS', '2000-08-22T18:00:00+00:00'),
    ],
)
def test_south_american_refresh_rejects_non_upcoming_fixture(
    monkeypatch,
    status_short,
    kickoff,
):
    db = RecordingDb()
    fixture = stored_fixture_row(778, league_id=281)
    fixture['status_short'] = status_short
    fixture['kickoff'] = kickoff
    fixture['fixture_date_utc'] = kickoff
    repository = BaselineRepository(fixture)
    monkeypatch.setattr(prediction_service, 'SupabaseRepository', lambda **_kwargs: repository)

    with pytest.raises(PredictionInputError, match='no longer upcoming'):
        asyncio.run(prediction_service.refresh_prediction(778, db_client=db))

    assert repository.history_calls == []
    assert db.operations == []

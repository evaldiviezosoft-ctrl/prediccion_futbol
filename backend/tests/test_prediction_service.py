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
        if self.name == 'predictions':
            self.db.predictions[int(payload['fixture_id'])] = dict(payload)
        return self

    def insert(self, payload):
        self.db.operations.append(('insert', self.name, payload))
        return self

    def execute(self):
        if self.action == 'select' and self.name == 'fixtures':
            fixture_id = int(self.filters['id'])
            row = self.db.fixtures.get(fixture_id)
            return SimpleNamespace(data=[row] if row else [])
        if self.action == 'select' and self.name == 'predictions':
            fixture_id = int(self.filters['fixture_id'])
            row = self.db.predictions.get(fixture_id)
            return SimpleNamespace(data=[dict(row)] if row else [])
        return SimpleNamespace(data=[])


class RecordingDb:
    def __init__(self):
        self.operations = []
        self.predictions = {}
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


def test_db_only_refresh_reuses_semantically_identical_persisted_prediction(
    monkeypatch,
):
    db = RecordingDb()
    fixture = stored_fixture_row(779, league_id=281)
    fixture['home_team_name'] = 'Sporting Cristal'
    fixture['away_team_name'] = 'Alianza Lima'
    repository = BaselineRepository(fixture)
    monkeypatch.setattr(
        prediction_service,
        'SupabaseRepository',
        lambda **_kwargs: repository,
    )

    first = asyncio.run(
        prediction_service.refresh_prediction(779, db_client=db)
    )
    original_updated_at = first['updated_at']
    db.predictions[779]['kickoff'] = '2099-08-22T18:00:00Z'

    second = asyncio.run(
        prediction_service.refresh_prediction(779, db_client=db)
    )

    assert [
        (operation, table)
        for operation, table, _payload in db.operations
    ] == [
        ('upsert', 'predictions'),
        ('insert', 'prediction_versions'),
    ]
    assert second == db.predictions[779]
    assert second['updated_at'] == original_updated_at
    assert second['kickoff'] == '2099-08-22T18:00:00Z'


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


class CalendarFallbackRepository:
    def __init__(self, fixture):
        self.fixture = fixture
        self.targeted_history_calls = []
        self.targeted_history = []
        self.team_statistics_call = None
        self.targeted_team_statistics = []
        self.player_call = None
        kickoff = datetime.fromisoformat(fixture['kickoff'])
        self.known_history = [{
            'id': 900,
            'league_id': 39,
            'season': 2098,
            'kickoff': (kickoff - timedelta(days=30)).isoformat(),
            'status_short': 'FT',
            'home_team_id': 999,
            'away_team_id': self.fixture['away_team_id'],
            'home_team_ref_id': 1999,
            'away_team_ref_id': (
                self.fixture.get('away_team_ref_id')
                or self.fixture['away_team_id']
            ),
            'home_goals': 0,
            'away_goals': 2,
        }]

    async def prediction_fixture(self, fixture_id):
        return self.fixture if fixture_id == self.fixture['id'] else None

    async def historical_finished_fixtures_for_team(self, **kwargs):
        self.targeted_history_calls.append(kwargs)
        return [
            row for row in [*self.known_history, *self.targeted_history]
            if int(kwargs['api_team_id']) in {
                int(row['home_team_id']),
                int(row['away_team_id']),
            }
        ]

    async def team_statistics_for_fixtures(self, fixture_ids):
        self.team_statistics_call = set(fixture_ids)
        return [
            row for row in self.targeted_team_statistics
            if int(row['fixture_id']) in self.team_statistics_call
        ]

    async def historical_finished_fixtures_before_many(self, **kwargs):
        raise AssertionError(
            'Calendar fallback must query exact teams, not complete leagues.'
        )

    async def player_statistics_for_fixtures(self, **kwargs):
        self.player_call = kwargs
        return [
            {
                'fixture_id': 900,
                'player_id': 501,
                'team_id': self.fixture['away_team_id'],
                'starter': True,
                'substitute': False,
                'minutes': 90,
                'goals': 1,
                'assists': 0,
            },
            {
                'fixture_id': 900,
                'player_id': 777,
                'team_id': self.fixture['home_team_id'],
                'starter': True,
                'substitute': False,
                'minutes': 90,
                'goals': 2,
                'assists': 1,
            },
        ]

    async def players_by_ids(self, _player_ids):
        return {
            501: {'id': 501, 'name': 'Known United scorer'},
            777: {'id': 777, 'name': 'Unknown rival player'},
        }


def test_calendar_fallback_is_db_only_and_never_invents_unknown_rival_players(
    monkeypatch,
):
    db = RecordingDb()
    fixture = stored_fixture_row(880, league_id=667)
    fixture.update({
        'home_team_name': 'Rosenborg',
        'away_team_name': 'Manchester United',
        'home_team_ref_id': fixture['home_team_id'],
        'away_team_ref_id': fixture['away_team_id'],
    })
    repository = CalendarFallbackRepository(fixture)
    monkeypatch.setattr(
        prediction_service,
        'SupabaseRepository',
        lambda **_kwargs: repository,
    )

    def forbidden_api_client(**_kwargs):
        raise AssertionError('Calendar fallback must not construct ApiFootballClient.')

    monkeypatch.setattr(
        prediction_service,
        'ApiFootballClient',
        forbidden_api_client,
    )

    record = asyncio.run(
        prediction_service.refresh_prediction(880, db_client=db)
    )

    assert record['model_metadata']['model_type'] == 'calendar_profile_fallback'
    assert record['model_metadata']['method'] == 'calendar_profile_poisson'
    assert record['model_metadata']['confidence'] == 'low'
    assert record['model_metadata']['single_team_profile'] is True
    assert [market['line'] for market in record['model_metadata']['goal_lines']] == [
        0.5, 1.5, 2.5, 3.5, 4.5,
    ]
    assert 'home_shots' not in record['expected']
    assert record['possible_scorers'][0]['player'] == 'Known United scorer'
    assert all(
        player['team_id'] == fixture['away_team_id']
        for player in record['possible_scorers']
    )
    assert record['model_metadata']['possible_assistants'] == []
    assert repository.player_call['team_ids'] == {fixture['away_team_id']}
    assert {
        call['api_team_id'] for call in repository.targeted_history_calls
    } == {fixture['home_team_id'], fixture['away_team_id']}
    assert [(operation, table) for operation, table, _payload in db.operations] == [
        ('upsert', 'predictions'),
        ('insert', 'prediction_versions'),
    ]


def test_calendar_fallback_promotes_targeted_team_history_to_a_profile(monkeypatch):
    db = RecordingDb()
    fixture = stored_fixture_row(881, league_id=667)
    fixture.update({
        'home_team_name': 'Rosenborg',
        'away_team_name': 'Manchester United',
        'home_team_ref_id': None,
        'away_team_ref_id': fixture['away_team_id'],
    })
    repository = CalendarFallbackRepository(fixture)
    kickoff = datetime.fromisoformat(fixture['kickoff'])
    repository.targeted_history = [
        {
            'id': 1000 + index,
            'league_id': 103 if index < 3 else 104,
            'season': 2098,
            'kickoff': (kickoff - timedelta(days=70 - index)).isoformat(),
            'status_short': 'FT',
            'home_team_id': fixture['home_team_id'],
            'away_team_id': 3000 + index,
            'home_team_ref_id': 1010,
            'away_team_ref_id': 4000 + index,
            'home_goals': 1 + (index % 2),
            'away_goals': index % 2,
        }
        for index in range(6)
    ]
    repository.targeted_team_statistics = [
        {
            'fixture_id': row['id'],
            'team_id': 1010,
            'is_home': True,
            'corners': 6,
            'total_shots': 13,
            'shots_on_goal': 5,
        }
        for row in repository.targeted_history
    ]
    monkeypatch.setattr(
        prediction_service,
        'SupabaseRepository',
        lambda **_kwargs: repository,
    )

    def forbidden_api_client(**_kwargs):
        raise AssertionError('Calendar fallback must remain DB-only.')

    monkeypatch.setattr(
        prediction_service,
        'ApiFootballClient',
        forbidden_api_client,
    )

    record = asyncio.run(
        prediction_service.refresh_prediction(881, db_client=db)
    )

    metadata = record['model_metadata']
    assert metadata['known_profile_sides'] == ['home', 'away']
    assert metadata['single_team_profile'] is False
    assert metadata['data_source'] == (
        'local_profiles_and_supabase_team_history'
    )
    assert record['features_snapshot']['profiles']['home']['source_kind'] == (
        'supabase_team_history'
    )
    assert 0 < record['expected']['home_corners'] <= 6.0
    assert 0 < record['expected']['home_shots'] <= 13.0
    assert 0 < record['expected']['home_shots_on_target'] <= 5.0
    home_metrics = metadata['market_statistics']['teams']['home']['metrics']
    assert all(
        metric['team_sample_used'] is True
        for metric in home_metrics.values()
    )
    assert repository.team_statistics_call == {
        row['id'] for row in repository.targeted_history
    }
    assert 1010 in repository.player_call['team_ids']
    assert {
        row['id'] for row in repository.targeted_history
    } <= set(repository.player_call['fixture_ids'])
    home_source = metadata['history_sources']['home']
    assert home_source['source_league_id'] == 103
    assert home_source['eligible_team_matches'] == 6


def test_calendar_fallback_supports_two_teams_with_targeted_history(monkeypatch):
    db = RecordingDb()
    fixture = stored_fixture_row(882, league_id=667)
    fixture.update({
        'home_team_name': 'Rosenborg',
        'away_team_name': 'Galatasaray',
        'home_team_ref_id': 1010,
        'away_team_ref_id': 2020,
    })
    repository = CalendarFallbackRepository(fixture)
    repository.known_history = []
    kickoff = datetime.fromisoformat(fixture['kickoff'])
    repository.targeted_history = [
        {
            'id': 1100 + index,
            'league_id': 103,
            'season': 2098,
            'kickoff': (kickoff - timedelta(days=80 - index)).isoformat(),
            'status_short': 'FT',
            'home_team_id': fixture['home_team_id'],
            'away_team_id': 3100 + index,
            'home_team_ref_id': fixture['home_team_ref_id'],
            'away_team_ref_id': 4100 + index,
            'home_goals': 2,
            'away_goals': index % 2,
        }
        for index in range(5)
    ] + [
        {
            'id': 1200 + index,
            'league_id': 203,
            'season': 2098,
            'kickoff': (kickoff - timedelta(days=75 - index)).isoformat(),
            'status_short': 'FT',
            'home_team_id': 3200 + index,
            'away_team_id': fixture['away_team_id'],
            'home_team_ref_id': 4200 + index,
            'away_team_ref_id': fixture['away_team_ref_id'],
            'home_goals': index % 2,
            'away_goals': 1,
        }
        for index in range(5)
    ]
    monkeypatch.setattr(
        prediction_service,
        'SupabaseRepository',
        lambda **_kwargs: repository,
    )

    record = asyncio.run(
        prediction_service.refresh_prediction(882, db_client=db)
    )

    assert record['model_metadata']['known_profile_sides'] == [
        'home',
        'away',
    ]
    assert record['model_metadata']['single_team_profile'] is False
    assert record['model_metadata']['data_source'] == 'supabase_team_history'
    profiles = record['features_snapshot']['profiles']
    assert profiles['home']['source_kind'] == 'supabase_team_history'
    assert profiles['away']['source_kind'] == 'supabase_team_history'
    assert profiles['home']['league_code'] is None
    assert profiles['away']['league_code'] is None


def test_calendar_fallback_preserves_semantic_error_without_team_history(
    monkeypatch,
):
    db = RecordingDb()
    fixture = stored_fixture_row(883, league_id=667)
    fixture.update({
        'home_team_name': 'Unknown Home XI',
        'away_team_name': 'Unknown Away XI',
        'home_team_ref_id': 1010,
        'away_team_ref_id': 2020,
    })
    repository = CalendarFallbackRepository(fixture)
    repository.known_history = []
    monkeypatch.setattr(
        prediction_service,
        'SupabaseRepository',
        lambda **_kwargs: repository,
    )

    with pytest.raises(PredictionInputError, match='At least one team'):
        asyncio.run(
            prediction_service.refresh_prediction(883, db_client=db)
        )

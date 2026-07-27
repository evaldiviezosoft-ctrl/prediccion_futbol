from unittest.mock import AsyncMock
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.routes import admin, dependencies, fixtures, health, predictions
from app.services.api_football import ApiFootballError
from app.services.probable_forecast_service import build_market_forecast


VALID_ADMIN_TOKEN = 'valid_admin_token_0123456789'


class ReadQuery:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.in_filters = []

    def select(self, columns):
        self.database.selects.append((self.table, columns))
        return self

    def gte(self, *_args):
        return self

    def lt(self, *_args):
        return self

    def order(self, *_args):
        return self

    def in_(self, column, values):
        normalized = tuple(values)
        self.in_filters.append((column, normalized))
        self.database.in_filters.append((self.table, column, normalized))
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        rows = self.database.rows[self.table]
        for column, values in self.in_filters:
            rows = [row for row in rows if row.get(column) in values]
        return SimpleNamespace(data=rows)


class UpcomingDatabase:
    def __init__(self):
        self.selects = []
        self.in_filters = []
        self.rows = {
            'fixtures': [{
                'id': 123,
                'league_id': 39,
                'season': 2026,
                'round': 'Round 1',
                'kickoff': '2099-08-22T18:00:00+00:00',
                'fixture_date_lima': '2099-08-22T13:00:00',
                'timezone': 'UTC',
                'venue_name': 'Test Stadium',
                'status_short': 'NS',
                'home_team_id': 10,
                'away_team_id': 20,
                'home_team_name': 'Arsenal',
                'away_team_name': 'Aston Villa',
                'created_at': '2099-01-01T00:00:00+00:00',
                'updated_at': '2099-01-01T00:00:00+00:00',
            }],
            'leagues': [{
                'id': 39,
                'code': 'E0',
                'name': 'Premier League',
                'country': 'England',
            }],
            'predictions': [{'fixture_id': 123, 'stage': 'prematch'}],
            'teams': [
                {
                    'api_team_id': 10,
                    'country': 'England',
                    'logo_url': 'https://example.test/arsenal.png',
                },
                {
                    'api_team_id': 20,
                    'country': 'England',
                    'logo_url': 'https://example.test/villa.png',
                },
            ],
        }

    def table(self, name):
        return ReadQuery(self, name)


class UpcomingHistoryQuery(ReadQuery):
    def __init__(self, database, table):
        super().__init__(database, table)
        self.selected_columns = ''
        self.not_equal_filters = []
        self.less_than_filters = []
        self.candidate_ids = set()
        self.start = 0
        self.end = None

    def select(self, columns):
        self.selected_columns = columns
        return super().select(columns)

    def neq(self, column, value):
        self.not_equal_filters.append((column, value))
        return self

    def lt(self, column, value):
        if self.selected_columns == (
            'id,home_team_id,away_team_id,home_goals,away_goals'
        ):
            self.less_than_filters.append((column, value))
        return self

    def or_(self, filters):
        encoded_values = filters.split('(', 1)[1].split(')', 1)[0]
        self.candidate_ids = {
            int(value) for value in encoded_values.split(',') if value
        }
        return self

    def range(self, start, end):
        self.start = start
        self.end = end
        return self

    def execute(self):
        rows = list(self.database.rows[self.table])
        for column, values in self.in_filters:
            rows = [row for row in rows if row.get(column) in values]
        for column, value in self.not_equal_filters:
            rows = [row for row in rows if row.get(column) != value]
        for column, value in self.less_than_filters:
            rows = [row for row in rows if str(row.get(column) or '') < value]
        if self.candidate_ids:
            rows = [
                row for row in rows
                if (
                    row.get('home_team_id') in self.candidate_ids
                    or row.get('away_team_id') in self.candidate_ids
                )
            ]
        end = self.end + 1 if self.end is not None else None
        return SimpleNamespace(data=rows[self.start:end])


class UpcomingHistoryDatabase(UpcomingDatabase):
    def table(self, name):
        return UpcomingHistoryQuery(self, name)


class MissingPredictionQuery:
    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return None


class MissingPredictionDatabase:
    def table(self, _name):
        return MissingPredictionQuery()


class PublishedPredictionQuery(MissingPredictionQuery):
    def execute(self):
        payload = {
            'fixture_id': 1492292,
            'published': True,
            'home_team_id': 177,
            'away_team_id': 127,
            'home_team_name': 'Chapecoense-SC',
            'away_team_name': 'Flamengo',
            'league_id': 71,
            'expected': {'home_goals': 1.2, 'away_goals': 1.4},
            'likely_scores': [{'score': '1-2', 'probability': 0.12}],
            'model_metadata': {
                'model_type': 'statistical_baseline',
                'method': 'poisson_empirical_bayes',
                'goal_lines': [{'line': 1.5, 'probability': 0.73}],
                'possible_assistants': [
                    {'player': 'Jugador A', 'team': 'Flamengo', 'probability': 0.2}
                ],
            },
        }
        payload['market_forecast'] = build_market_forecast(payload)
        return SimpleNamespace(data=payload)


class PublishedPredictionDatabase(UpcomingDatabase):
    def __init__(self):
        super().__init__()
        self.rows['teams'] = [
            {'api_team_id': 177, 'country': 'Brazil'},
            {'api_team_id': 127, 'country': 'Brazil'},
        ]

    def table(self, name):
        if name == 'predictions':
            return PublishedPredictionQuery()
        return ReadQuery(self, name)


def settings_with_admin(token=VALID_ADMIN_TOKEN) -> Settings:
    return Settings(_env_file=None, admin_token=token)


def test_app_starts_without_secrets_and_live_endpoint_works():
    with TestClient(app) as client:
        response = client.get('/health/live')

    assert response.status_code == 200
    assert response.json() == {'status': 'alive'}


def test_readiness_reports_boolean_checks_without_secret_values(monkeypatch):
    monkeypatch.setattr(health, 'get_settings', lambda: Settings(_env_file=None))
    monkeypatch.setattr(health, '_artifacts_ready', lambda: (True, True))

    with TestClient(app) as client:
        response = client.get('/health/ready')

    assert response.status_code == 503
    assert response.json()['status'] == 'not_ready'
    assert response.json()['checks']['api_football_configured'] is False
    assert 'secret' not in response.text.lower()


def test_upcoming_fixture_contract_includes_league_and_prediction_state(monkeypatch):
    database = UpcomingDatabase()
    monkeypatch.setattr(fixtures, 'get_supabase', lambda: database)

    with TestClient(app) as client:
        response = client.get('/fixtures/upcoming')

    assert response.status_code == 200
    item = response.json()[0]
    assert item['league_name'] == 'Premier League'
    assert item['league_code'] == 'E0'
    assert item['prediction_available'] is True
    assert item['prediction_stage'] == 'prematch'
    assert item['prediction_model_available'] is True
    assert item['prediction_fallback_available'] is False
    assert item['fixture_date_lima'] == '2099-08-22T13:00:00'
    assert item['home_team_country'] == 'England'
    assert item['away_team_country'] == 'England'
    assert item['home_team_logo_url'] == 'https://example.test/arsenal.png'
    assert item['away_team_logo_url'] == 'https://example.test/villa.png'
    assert item['home_team_logo_proxy_path'] == '/fixtures/team-logo/10'
    assert item['away_team_logo_proxy_path'] == '/fixtures/team-logo/20'
    fixture_columns = dict(database.selects)['fixtures']
    assert 'raw_payload' not in fixture_columns
    assert dict(database.selects)['predictions'] == 'fixture_id,stage'


def test_upcoming_uses_modeled_league_country_when_team_metadata_is_missing(
    monkeypatch,
):
    database = UpcomingDatabase()
    for team in database.rows['teams']:
        team['country'] = None
    monkeypatch.setattr(fixtures, 'get_supabase', lambda: database)

    with TestClient(app) as client:
        response = client.get('/fixtures/upcoming')

    assert response.status_code == 200
    item = response.json()[0]
    assert item['home_team_country'] == 'England'
    assert item['away_team_country'] == 'England'


def test_upcoming_marks_profile_backed_calendar_fallback_separately(monkeypatch):
    database = UpcomingDatabase()
    database.rows['fixtures'][0].update({
        'id': 667001,
        'league_id': 667,
        'home_team_name': 'Barcelona',
        'away_team_name': 'Europa FC',
    })
    database.rows['leagues'] = [{
        'id': 667,
        'code': 'friendlies_clubs',
        'name': 'Friendlies Clubs',
        'country': 'World',
    }]
    database.rows['predictions'] = []
    for team in database.rows['teams']:
        team['country'] = None
    monkeypatch.setattr(fixtures, 'get_supabase', lambda: database)

    with TestClient(app) as client:
        response = client.get('/fixtures/upcoming')

    assert response.status_code == 200
    item = response.json()[0]
    assert item['prediction_model_available'] is False
    assert item['prediction_fallback_available'] is True
    assert item['home_team_country'] == 'Spain'
    assert item['away_team_country'] is None


def test_upcoming_marks_history_only_calendar_fixture_as_fallback(monkeypatch):
    database = UpcomingHistoryDatabase()
    upcoming = database.rows['fixtures'][0]
    upcoming.update({
        'id': 667002,
        'league_id': 667,
        'home_team_id': 100,
        'away_team_id': 200,
        'home_team_name': 'Historical Club',
        'away_team_name': 'Unknown XI',
    })
    database.rows['fixtures'].extend([
        {
            'id': 800 + index,
            'league_id': 40,
            'status_short': 'FT',
            'kickoff': '2098-08-22T18:00:00+00:00',
            'home_team_id': 100,
            'away_team_id': 900 + index,
            'home_goals': 2,
            'away_goals': 1,
        }
        for index in range(5)
    ])
    database.rows['leagues'] = [{
        'id': 667,
        'code': 'friendlies_clubs',
        'name': 'Friendlies Clubs',
        'country': 'World',
    }]
    database.rows['predictions'] = []
    database.rows['teams'] = [
        {'api_team_id': 100, 'country': 'Portugal', 'logo_url': None},
        {'api_team_id': 200, 'country': None, 'logo_url': None},
    ]
    monkeypatch.setattr(fixtures, 'get_supabase', lambda: database)

    with TestClient(app) as client:
        response = client.get('/fixtures/upcoming')

    assert response.status_code == 200
    items = response.json()
    assert [item['id'] for item in items] == [667002]
    assert items[0]['prediction_model_available'] is False
    assert items[0]['prediction_fallback_available'] is True


def test_upcoming_fixture_endpoint_excludes_terminal_states(monkeypatch):
    database = UpcomingDatabase()
    cancelled = {
        **database.rows['fixtures'][0],
        'id': 124,
        'status_short': 'CANC',
    }
    database.rows['fixtures'].append(cancelled)
    monkeypatch.setattr(fixtures, 'get_supabase', lambda: database)

    with TestClient(app) as client:
        response = client.get('/fixtures/upcoming')

    assert response.status_code == 200
    assert [item['status_short'] for item in response.json()] == ['NS']
    assert (
        'fixtures',
        'status_short',
        ('NS', 'PST', 'TBD'),
    ) in database.in_filters


def test_team_logo_proxy_returns_cached_image_contract(monkeypatch):
    fetch = AsyncMock(return_value=(b'fake-png', 'image/png'))
    monkeypatch.setattr(fixtures, '_fetch_team_logo', fetch)

    with TestClient(app) as client:
        response = client.get('/fixtures/team-logo/10')

    assert response.status_code == 200
    assert response.content == b'fake-png'
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['cache-control'] == 'public, max-age=86400'
    fetch.assert_awaited_once_with(10)


def test_public_database_route_maps_missing_configuration_to_503(monkeypatch):
    from app.core.errors import ConfigurationError

    monkeypatch.setattr(
        fixtures,
        'get_supabase',
        lambda: (_ for _ in ()).throw(ConfigurationError('missing database secret')),
    )

    with TestClient(app) as client:
        response = client.get('/fixtures/upcoming')

    assert response.status_code == 503
    assert response.json()['code'] == 'configuration_unavailable'
    assert 'secret' not in response.text.lower()


def test_missing_prediction_returns_404_when_maybe_single_returns_none(monkeypatch):
    monkeypatch.setattr(predictions, 'get_supabase', lambda: MissingPredictionDatabase())

    with TestClient(app) as client:
        response = client.get('/predictions/999')

    assert response.status_code == 404


def test_published_prediction_contract_returns_model_metadata(monkeypatch):
    monkeypatch.setattr(predictions, 'get_supabase', lambda: PublishedPredictionDatabase())

    with TestClient(app) as client:
        response = client.get('/predictions/1492292')

    assert response.status_code == 200
    assert response.json()['fixture_id'] == 1492292
    assert response.json()['model_metadata'] == {
        'model_type': 'statistical_baseline',
        'method': 'poisson_empirical_bayes',
        'goal_lines': [{'line': 1.5, 'probability': 0.73}],
        'possible_assistants': [
            {'player': 'Jugador A', 'team': 'Flamengo', 'probability': 0.2}
        ],
    }
    assert response.json()['goal_lines'] == [{'line': 1.5, 'probability': 0.73}]
    assert response.json()['possible_assistants'][0]['player'] == 'Jugador A'
    assert [
        item['category'] for item in response.json()['probable_forecast']
    ] == ['goals', 'half_goals']
    assert response.json()['probable_forecast'][0] == {
        'category': 'goals',
        'title': 'Goles totales',
        'prediction': 'Más de 1.5',
        'probability': 0.73,
        'confidence': 'medium',
    }
    market_forecast = response.json()['market_forecast']
    assert market_forecast['version'] == 'deterministic_lines_v1'
    assert [market['category'] for market in market_forecast['markets']] == [
        'goals',
    ]
    assert market_forecast['markets'][0]['title'] == 'Goles totales'
    assert len(market_forecast['markets'][0]['lines']) == 5
    one_point_five = market_forecast['markets'][0]['lines'][1]
    assert one_point_five['line'] == 1.5
    assert one_point_five['selection'] == 'over'
    assert one_point_five['selection_probability'] == 0.73
    assert response.json()['home_team_country'] == 'Brazil'
    assert response.json()['away_team_country'] == 'Brazil'
    assert 'likely_scores' not in response.json()


def test_prediction_performance_endpoint_returns_stored_outcomes(monkeypatch):
    class Repository:
        async def prediction_performance_summary(self):
            return {
                'evaluated_fixtures': 2,
                'scored_selections': 8,
                'correct_selections': 6,
                'accuracy': 0.75,
                'by_market': {},
            }

    monkeypatch.setattr(predictions, 'SupabaseRepository', Repository)

    with TestClient(app) as client:
        response = client.get('/predictions/performance/summary')

    assert response.status_code == 200
    assert response.json()['accuracy'] == 0.75


def test_admin_endpoint_is_disabled_when_token_is_missing_or_placeholder(monkeypatch):
    with TestClient(app) as client:
        monkeypatch.setattr(dependencies, 'get_settings', lambda: Settings(_env_file=None))
        missing = client.post('/admin/jobs/sync-and-predict')

        monkeypatch.setattr(
            dependencies,
            'get_settings',
            lambda: Settings(_env_file=None, admin_token='CAMBIAR_POR_UN_TOKEN_LARGO'),
        )
        placeholder = client.post(
            '/admin/jobs/sync-and-predict',
            headers={'X-Admin-Token': 'CAMBIAR_POR_UN_TOKEN_LARGO'},
        )

    assert missing.status_code == 503
    assert placeholder.status_code == 503


def test_admin_endpoint_rejects_wrong_token_and_runs_with_valid_token(monkeypatch):
    monkeypatch.setattr(dependencies, 'get_settings', lambda: settings_with_admin())
    job_result = {
        'predictions_attempted': 0,
        'predictions_succeeded': 0,
        'predictions_failed': 0,
    }
    mocked_job = AsyncMock(return_value=job_result)
    monkeypatch.setattr(admin, 'sync_and_predict', mocked_job)

    with TestClient(app) as client:
        wrong = client.post(
            '/admin/jobs/sync-and-predict',
            headers={'X-Admin-Token': 'wrong_admin_token_0123456789'},
        )
        valid = client.post(
            '/admin/jobs/sync-and-predict?horizon_days=2&max_matches=1',
            headers={'X-Admin-Token': VALID_ADMIN_TOKEN},
        )

    assert wrong.status_code == 401
    assert valid.status_code == 200
    assert valid.json() == job_result
    mocked_job.assert_awaited_once_with(horizon_days=2, max_matches=1, timezone_name=None)


def test_admin_postmatch_defaults_to_one_hundred_and_rejects_more(monkeypatch):
    monkeypatch.setattr(dependencies, 'get_settings', lambda: settings_with_admin())
    mocked_job = AsyncMock(return_value={'candidates': 0})
    monkeypatch.setattr(
        admin,
        'sync_and_evaluate_published_predictions',
        mocked_job,
    )

    with TestClient(app) as client:
        valid = client.post(
            '/admin/jobs/evaluate-postmatch',
            headers={'X-Admin-Token': VALID_ADMIN_TOKEN},
        )
        invalid = client.post(
            '/admin/jobs/evaluate-postmatch?max_matches=101',
            headers={'X-Admin-Token': VALID_ADMIN_TOKEN},
        )

    assert valid.status_code == 200
    assert invalid.status_code == 422
    mocked_job.assert_awaited_once_with(lookback_days=7, max_matches=100)


def test_provider_errors_are_mapped_without_exposing_internal_details(monkeypatch):
    monkeypatch.setattr(dependencies, 'get_settings', lambda: settings_with_admin())
    monkeypatch.setattr(
        predictions,
        'refresh_prediction',
        AsyncMock(side_effect=ApiFootballError('vendor payload with sensitive diagnostics')),
    )

    with TestClient(app) as client:
        response = client.post(
            '/predictions/123/refresh',
            headers={'X-Admin-Token': VALID_ADMIN_TOKEN},
        )

    assert response.status_code == 502
    assert response.json()['code'] == 'provider_error'
    assert 'sensitive' not in response.text.lower()

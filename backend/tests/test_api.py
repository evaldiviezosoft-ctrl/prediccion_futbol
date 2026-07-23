from unittest.mock import AsyncMock
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.routes import admin, dependencies, fixtures, health, predictions
from app.services.api_football import ApiFootballError


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
            'leagues': [{'id': 39, 'code': 'E0', 'name': 'Premier League'}],
            'predictions': [{'fixture_id': 123, 'stage': 'prematch'}],
            'teams': [
                {'api_team_id': 10, 'logo_url': 'https://example.test/arsenal.png'},
                {'api_team_id': 20, 'logo_url': 'https://example.test/villa.png'},
            ],
        }

    def table(self, name):
        return ReadQuery(self, name)


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
        return SimpleNamespace(data={
            'fixture_id': 1492292,
            'published': True,
            'home_team_name': 'Chapecoense-SC',
            'away_team_name': 'Flamengo',
            'likely_scores': [{'score': '1-2', 'probability': 0.12}],
            'model_metadata': {
                'model_type': 'statistical_baseline',
                'method': 'poisson_empirical_bayes',
                'goal_lines': [{'line': 0.5, 'probability': 0.9}],
                'possible_assistants': [
                    {'player': 'Jugador A', 'team': 'Flamengo', 'probability': 0.2}
                ],
            },
        })


class PublishedPredictionDatabase:
    def table(self, name):
        assert name == 'predictions'
        return PublishedPredictionQuery()


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
    assert item['fixture_date_lima'] == '2099-08-22T13:00:00'
    assert item['home_team_logo_url'] == 'https://example.test/arsenal.png'
    assert item['away_team_logo_url'] == 'https://example.test/villa.png'
    assert item['home_team_logo_proxy_path'] == '/fixtures/team-logo/10'
    assert item['away_team_logo_proxy_path'] == '/fixtures/team-logo/20'
    fixture_columns = dict(database.selects)['fixtures']
    assert 'raw_payload' not in fixture_columns
    assert dict(database.selects)['predictions'] == 'fixture_id,stage'


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
        'goal_lines': [{'line': 0.5, 'probability': 0.9}],
        'possible_assistants': [
            {'player': 'Jugador A', 'team': 'Flamengo', 'probability': 0.2}
        ],
    }
    assert response.json()['goal_lines'] == [{'line': 0.5, 'probability': 0.9}]
    assert response.json()['possible_assistants'][0]['player'] == 'Jugador A'
    assert 'likely_scores' not in response.json()


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

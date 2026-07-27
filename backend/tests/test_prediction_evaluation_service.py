from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services import prediction_evaluation_service
from app.services.prediction_evaluation_service import (
    actual_market_values,
    evaluate_market_forecast,
    sync_and_evaluate_published_predictions,
)
from tests.test_fixture_normalizer import fixture_payload


@pytest.fixture(autouse=True)
def _clear_detail_access_cache():
    prediction_evaluation_service.reset_detail_access_cache()
    yield
    prediction_evaluation_service.reset_detail_access_cache()


def _forecast():
    return {
        'version': 'deterministic_lines_v1',
        'method': 'poisson_mean_approximation',
        'markets': [
            {
                'category': 'goals',
                'scope': 'match_total',
                'lines': [
                    {
                        'line': 1.5,
                        'selection': 'over',
                        'selection_probability': 0.76,
                    },
                ],
            },
            {
                'category': 'corners',
                'scope': 'match_total',
                'lines': [
                    {
                        'line': 10.5,
                        'selection': 'under',
                        'selection_probability': 0.69,
                    },
                ],
            },
            {
                'category': 'shots',
                'scope': 'match_total',
                'lines': [
                    {
                        'line': 20.5,
                        'selection': 'over',
                        'selection_probability': 0.67,
                    },
                ],
            },
        ],
    }


def _team_statistics():
    return [
        {
            'fixture_id': 7,
            'is_home': True,
            'corners': 5,
            'yellow_cards': 2,
            'total_shots': 12,
            'shots_on_goal': 4,
        },
        {
            'fixture_id': 7,
            'is_home': False,
            'corners': 4,
            'yellow_cards': 3,
            'total_shots': 10,
            'shots_on_goal': 3,
        },
    ]


def _candidate(
    fixture_id: int,
    kickoff: str,
    *,
    status: str = 'NS',
) -> dict:
    return {
        'id': fixture_id,
        'api_fixture_id': fixture_id,
        'competition_id': 71,
        'season': 2026,
        'kickoff': kickoff,
        'fixture_date_utc': kickoff,
        'status_short': status,
        'home_goals': None,
        'away_goals': None,
        'fulltime_home': None,
        'fulltime_away': None,
    }


def _provider_payload(
    fixture_id: int,
    kickoff: str,
    *,
    include_statistics: bool = False,
) -> dict:
    payload = fixture_payload(
        'FT',
        fixture_id=fixture_id,
        include_statistics=include_statistics,
    )
    parsed = datetime.fromisoformat(kickoff.replace('Z', '+00:00'))
    payload['fixture']['date'] = parsed.isoformat()
    payload['fixture']['timestamp'] = int(parsed.timestamp())
    payload['fixture']['timezone'] = 'UTC'
    return payload


def test_actual_market_values_sum_both_teams():
    actual = actual_market_values(
        {
            'status_short': 'FT',
            'home_goals': 2,
            'away_goals': 1,
        },
        _team_statistics(),
    )

    assert actual == {
        'goals': 3.0,
        'corners': 9.0,
        'yellow_cards': 5.0,
        'shots': 22.0,
        'shots_on_target': 7.0,
    }


def test_evaluation_scores_wins_and_keeps_missing_statistics_pending():
    summary, rows = evaluate_market_forecast(
        fixture_id=7,
        prediction_version_id='9edda6ad-a34a-4faa-a803-34d8289e52c7',
        forecast=_forecast(),
        actual={'goals': 3.0, 'corners': 9.0, 'shots': None},
        fixture_status='FT',
        evaluated_at='2026-07-27T03:00:00+00:00',
    )

    assert summary['status'] == 'partial'
    assert summary['scored_selections'] == 2
    assert summary['correct_selections'] == 2
    assert summary['accuracy'] == 1.0
    assert [row['outcome'] for row in rows] == ['won', 'won', 'pending']


def test_extra_time_settles_regulation_goals_and_voids_aggregate_statistics():
    actual = actual_market_values(
        {
            'status_short': 'AET',
            'home_goals': 3,
            'away_goals': 2,
            'fulltime_home': 1,
            'fulltime_away': 1,
        },
        _team_statistics(),
    )
    summary, rows = evaluate_market_forecast(
        fixture_id=7,
        prediction_version_id='9edda6ad-a34a-4faa-a803-34d8289e52c7',
        forecast=_forecast(),
        actual=actual,
        fixture_status='AET',
        evaluated_at='2026-07-27T03:00:00+00:00',
    )

    assert actual['goals'] == 2.0
    assert actual['corners'] is None
    assert summary['status'] == 'completed'
    assert rows[0]['outcome'] == 'won'
    assert rows[1]['outcome'] == 'void'
    assert rows[1]['reason_code'] == 'extra_time_not_separable'


def test_service_evaluates_a_complete_stored_fixture_without_provider_request(
    monkeypatch,
):
    fixture = {
        'id': 7,
        'competition_id': 71,
        'season': 2026,
        'kickoff': '2026-07-26T20:00:00+00:00',
        'status_short': 'FT',
        'home_goals': 2,
        'away_goals': 1,
        'fulltime_home': 2,
        'fulltime_away': 1,
    }

    class Repository:
        def __init__(self):
            self.saved = []

        async def prediction_evaluation_candidates(self, **_kwargs):
            return [fixture]

        async def team_statistics_for_fixtures(self, _fixture_ids):
            return _team_statistics()

        async def latest_prediction_version_before_kickoff(
            self,
            _fixture_id,
            _kickoff,
        ):
            return {
                'id': '9edda6ad-a34a-4faa-a803-34d8289e52c7',
                'payload': {'market_forecast': _forecast()},
            }

        async def save_prediction_evaluation(self, summary, results):
            self.saved.append((summary, results))

    class Api:
        async def fixture_details(self, _ids):
            pytest.fail('The provider must not be called for complete data.')

    repository = Repository()
    monkeypatch.setattr(
        prediction_evaluation_service,
        'SupabaseRepository',
        lambda client: repository,
    )

    result = asyncio.run(sync_and_evaluate_published_predictions(
        now=datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
        api_client=Api(),
        db_client=object(),
    ))

    assert result['evaluated'] == 1
    assert result['details_refreshed'] == 0
    assert repository.saved[0][0]['status'] == 'completed'


def test_service_uses_only_current_utc_date_and_caches_detail_restriction(
    monkeypatch,
):
    clock = datetime(2026, 7, 27, 3, tzinfo=timezone.utc)
    fixtures = {
        7: _candidate(7, '2026-07-27T00:00:00+00:00'),
        8: _candidate(8, '2026-07-27T01:00:00+00:00'),
        9: _candidate(9, '2026-07-26T20:00:00+00:00'),
    }

    class Repository:
        def __init__(self):
            self.saved = []
            self.persisted = []
            self.candidate_queries = []

        async def prediction_evaluation_candidates(self, **kwargs):
            self.candidate_queries.append(kwargs)
            return list(fixtures.values())

        async def team_statistics_for_fixtures(self, _fixture_ids):
            return []

        async def get_competitions_by_ids(self, competition_ids):
            assert competition_ids == [71]
            return {71: {'id': 71, 'api_league_id': 281}}

        async def persist_fixture(
            self,
            normalized,
            *,
            competition,
            details,
            coverage=None,
        ):
            assert competition['id'] == 71
            fixture_id = normalized.api_fixture_id
            self.persisted.append((fixture_id, details))
            if not details:
                fixtures[fixture_id].update(normalized.fixture)
            return True

        async def latest_prediction_version_before_kickoff(
            self,
            _fixture_id,
            _kickoff,
        ):
            return {
                'id': '9edda6ad-a34a-4faa-a803-34d8289e52c7',
                'payload': {'market_forecast': _forecast()},
            }

        async def save_prediction_evaluation(self, summary, results):
            self.saved.append((summary, results))

    class ApiFootballAccessRestrictionError(Exception):
        pass

    class Api:
        def __init__(self):
            self.date_calls = []
            self.detail_calls = []

        async def fixtures_by_date(self, fixture_date, *, timezone_name):
            self.date_calls.append((fixture_date, timezone_name))
            return [
                _provider_payload(7, '2026-07-27T00:00:00+00:00'),
                _provider_payload(8, '2026-07-27T01:00:00+00:00'),
            ]

        async def fixture_details(self, fixture_ids):
            self.detail_calls.append(list(fixture_ids))
            raise ApiFootballAccessRestrictionError()

    repository = Repository()
    api = Api()
    monkeypatch.setattr(
        prediction_evaluation_service,
        'SupabaseRepository',
        lambda client: repository,
    )

    first = asyncio.run(sync_and_evaluate_published_predictions(
        now=clock,
        api_client=api,
        db_client=object(),
    ))
    second = asyncio.run(sync_and_evaluate_published_predictions(
        now=clock,
        api_client=api,
        db_client=object(),
    ))

    assert repository.candidate_queries[0]['ends_at'] == clock
    assert api.date_calls == [
        ('2026-07-27', 'UTC'),
        ('2026-07-27', 'UTC'),
    ]
    assert api.detail_calls == [[7, 8], [7]]
    assert 9 not in {
        fixture_id
        for fixture_id, _details in repository.persisted
    }
    assert first['provider_fixture_requests'] == 3
    assert first['provider_date_requests'] == 1
    assert first['provider_detail_requests'] == 2
    assert first['basic_refreshed'] == 2
    assert first['details_refreshed'] == 0
    assert first['refresh_errors'] == 2
    assert first['partial'] == 2
    assert second['provider_fixture_requests'] == 1
    assert second['provider_detail_requests'] == 0
    assert second['basic_refreshed'] == 2


def test_successful_single_detail_probe_continues_and_persists_immediately():
    candidates = [
        _candidate(7, '2026-07-27T00:00:00+00:00'),
        _candidate(8, '2026-07-27T00:30:00+00:00'),
        _candidate(9, '2026-07-27T01:00:00+00:00'),
    ]

    class Repository:
        def __init__(self):
            self.persisted = []

        async def get_competitions_by_ids(self, competition_ids):
            assert competition_ids == [71]
            return {71: {'id': 71, 'api_league_id': 281}}

        async def persist_fixture(
            self,
            normalized,
            *,
            competition,
            details,
            coverage=None,
        ):
            self.persisted.append((normalized.api_fixture_id, details))
            return True

    class ApiFootballAccessRestrictionError(Exception):
        pass

    repository = Repository()

    class Api:
        def __init__(self):
            self.detail_calls = []

        async def fixtures_by_date(self, fixture_date, *, timezone_name):
            assert (fixture_date, timezone_name) == ('2026-07-27', 'UTC')
            return [
                _provider_payload(
                    candidate['id'],
                    candidate['kickoff'],
                )
                for candidate in candidates
            ]

        async def fixture_details(self, fixture_ids):
            fixture_ids = list(fixture_ids)
            self.detail_calls.append(fixture_ids)
            if len(fixture_ids) > 1:
                raise ApiFootballAccessRestrictionError()
            fixture_id = fixture_ids[0]
            previously_persisted_details = [
                persisted_id
                for persisted_id, details in repository.persisted
                if details
            ]
            assert previously_persisted_details == list(range(7, fixture_id))
            return [
                _provider_payload(
                    fixture_id,
                    next(
                        candidate['kickoff']
                        for candidate in candidates
                        if candidate['id'] == fixture_id
                    ),
                    include_statistics=True,
                ),
            ]

    api = Api()
    counters = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=candidates,
            clock=datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
        ),
    )

    assert api.detail_calls == [[7, 8, 9], [7], [8], [9]]
    assert counters == {
        'provider_fixture_requests': 5,
        'provider_date_requests': 1,
        'provider_detail_requests': 4,
        'basic_refreshed': 3,
        'details_refreshed': 3,
        'refresh_errors': 1,
    }
    assert repository.persisted == [
        (7, False),
        (8, False),
        (9, False),
        (7, True),
        (8, True),
        (9, True),
    ]


def test_date_access_restriction_is_cached_until_the_next_utc_day():
    class Repository:
        async def get_competitions_by_ids(self, _competition_ids):
            return {71: {'id': 71, 'api_league_id': 281}}

    class ApiFootballDateAccessError(Exception):
        pass

    class Api:
        def __init__(self):
            self.date_calls = []

        async def fixtures_by_date(self, fixture_date, *, timezone_name):
            self.date_calls.append((fixture_date, timezone_name))
            raise ApiFootballDateAccessError()

        async def fixture_details(self, _fixture_ids):
            pytest.fail('Details must not be queried after a date restriction.')

    repository = Repository()
    api = Api()
    first = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=[
                _candidate(7, '2026-07-27T00:00:00+00:00'),
            ],
            clock=datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
        ),
    )
    second = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=[
                _candidate(7, '2026-07-27T00:00:00+00:00'),
            ],
            clock=datetime(2026, 7, 27, 4, tzinfo=timezone.utc),
        ),
    )
    next_day = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=[
                _candidate(8, '2026-07-28T00:00:00+00:00'),
            ],
            clock=datetime(2026, 7, 28, 1, tzinfo=timezone.utc),
        ),
    )

    assert first['provider_fixture_requests'] == 1
    assert first['provider_date_requests'] == 1
    assert first['refresh_errors'] == 1
    assert second['provider_fixture_requests'] == 0
    assert second['provider_date_requests'] == 0
    assert second['refresh_errors'] == 0
    assert next_day['provider_fixture_requests'] == 1
    assert api.date_calls == [
        ('2026-07-27', 'UTC'),
        ('2026-07-28', 'UTC'),
    ]


def test_generic_date_failure_is_not_cached():
    class Repository:
        async def get_competitions_by_ids(self, _competition_ids):
            return {71: {'id': 71, 'api_league_id': 281}}

    class Api:
        def __init__(self):
            self.date_calls = 0

        async def fixtures_by_date(self, _fixture_date, *, timezone_name):
            assert timezone_name == 'UTC'
            self.date_calls += 1
            raise RuntimeError('temporary upstream failure')

        async def fixture_details(self, _fixture_ids):
            pytest.fail('A non-final fixture does not need details.')

    api = Api()
    repository = Repository()
    for hour in (3, 4):
        counters = asyncio.run(
            prediction_evaluation_service._refresh_current_utc_fixtures(
                api=api,
                repository=repository,
                candidates=[
                    _candidate(7, '2026-07-27T00:00:00+00:00'),
                ],
                clock=datetime(2026, 7, 27, hour, tzinfo=timezone.utc),
            ),
        )
        assert counters['provider_fixture_requests'] == 1
        assert counters['refresh_errors'] == 1

    assert api.date_calls == 2


def test_historical_final_detail_catch_up_does_not_query_historical_date():
    candidates = [
        _candidate(
            7,
            '2026-07-26T20:00:00+00:00',
            status='FT',
        ),
    ]

    class Repository:
        def __init__(self):
            self.persisted = []

        async def get_competitions_by_ids(self, _competition_ids):
            return {71: {'id': 71, 'api_league_id': 281}}

        async def persist_fixture(
            self,
            normalized,
            *,
            competition,
            details,
            coverage=None,
        ):
            self.persisted.append((normalized.api_fixture_id, details))
            return True

    class Api:
        def __init__(self):
            self.detail_calls = []

        async def fixtures_by_date(self, _fixture_date, *, timezone_name):
            pytest.fail('Historical dates must not use fixtures_by_date.')

        async def fixture_details(self, fixture_ids):
            fixture_ids = list(fixture_ids)
            self.detail_calls.append(fixture_ids)
            return [
                _provider_payload(
                    fixture_id,
                    '2026-07-26T20:00:00+00:00',
                    include_statistics=True,
                )
                for fixture_id in fixture_ids
            ]

    repository = Repository()
    api = Api()
    counters = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=candidates,
            clock=datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
        ),
    )

    assert api.detail_calls == [[7]]
    assert repository.persisted == [(7, True)]
    assert counters['provider_date_requests'] == 0
    assert counters['provider_detail_requests'] == 1
    assert counters['details_refreshed'] == 1


def test_detail_catch_up_chunks_more_than_twenty_fixture_ids():
    candidates = [
        _candidate(
            fixture_id,
            '2026-07-26T20:00:00+00:00',
            status='FT',
        )
        for fixture_id in range(100, 145)
    ]

    class Repository:
        def __init__(self):
            self.persisted = []

        async def get_competitions_by_ids(self, _competition_ids):
            return {71: {'id': 71, 'api_league_id': 281}}

        async def persist_fixture(
            self,
            normalized,
            *,
            competition,
            details,
            coverage=None,
        ):
            assert details is True
            self.persisted.append(normalized.api_fixture_id)
            return True

    class Api:
        def __init__(self):
            self.detail_calls = []

        async def fixtures_by_date(self, _fixture_date, *, timezone_name):
            pytest.fail('Historical dates must not use fixtures_by_date.')

        async def fixture_details(self, fixture_ids):
            fixture_ids = list(fixture_ids)
            assert 1 <= len(fixture_ids) <= 20
            self.detail_calls.append(fixture_ids)
            return [
                _provider_payload(
                    fixture_id,
                    '2026-07-26T20:00:00+00:00',
                    include_statistics=True,
                )
                for fixture_id in fixture_ids
            ]

    repository = Repository()
    api = Api()
    counters = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=candidates,
            clock=datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
        ),
    )

    assert [len(batch) for batch in api.detail_calls] == [20, 20, 5]
    assert repository.persisted == list(range(100, 145))
    assert counters['provider_fixture_requests'] == 3
    assert counters['provider_date_requests'] == 0
    assert counters['provider_detail_requests'] == 3
    assert counters['details_refreshed'] == 45
    assert counters['refresh_errors'] == 0


def test_date_block_does_not_prevent_one_bounded_historical_detail_probe():
    candidates = [
        _candidate(7, '2026-07-27T00:00:00+00:00'),
        _candidate(
            8,
            '2026-07-26T20:00:00+00:00',
            status='FT',
        ),
        _candidate(
            9,
            '2026-07-26T21:00:00+00:00',
            status='FT',
        ),
    ]

    class Repository:
        async def get_competitions_by_ids(self, _competition_ids):
            return {71: {'id': 71, 'api_league_id': 281}}

    class ApiFootballDateAccessError(Exception):
        pass

    class ApiFootballAccessRestrictionError(Exception):
        pass

    class Api:
        def __init__(self):
            self.date_calls = 0
            self.detail_calls = []

        async def fixtures_by_date(self, _fixture_date, *, timezone_name):
            assert timezone_name == 'UTC'
            self.date_calls += 1
            raise ApiFootballDateAccessError()

        async def fixture_details(self, fixture_ids):
            self.detail_calls.append(list(fixture_ids))
            raise ApiFootballAccessRestrictionError()

    repository = Repository()
    api = Api()
    first = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=candidates,
            clock=datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
        ),
    )
    second = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=candidates,
            clock=datetime(2026, 7, 27, 4, tzinfo=timezone.utc),
        ),
    )

    assert api.date_calls == 1
    assert api.detail_calls == [[8, 9], [8]]
    assert first['provider_fixture_requests'] == 3
    assert first['provider_date_requests'] == 1
    assert first['provider_detail_requests'] == 2
    assert first['refresh_errors'] == 3
    assert second['provider_fixture_requests'] == 0
    assert second['provider_detail_requests'] == 0


def test_max_matches_validation_allows_up_to_one_hundred():
    with pytest.raises(ValueError, match='between 1 and 100'):
        asyncio.run(sync_and_evaluate_published_predictions(
            max_matches=101,
            api_client=object(),
            db_client=object(),
        ))


def test_each_detail_id_is_attempted_once_per_utc_day_even_when_empty():
    candidates = [
        _candidate(
            7,
            '2026-07-26T20:00:00+00:00',
            status='FT',
        ),
        _candidate(
            8,
            '2026-07-26T21:00:00+00:00',
            status='FT',
        ),
    ]

    class Repository:
        async def get_competitions_by_ids(self, _competition_ids):
            return {71: {'id': 71, 'api_league_id': 281}}

    class Api:
        def __init__(self):
            self.detail_calls = []

        async def fixtures_by_date(self, _fixture_date, *, timezone_name):
            pytest.fail('Historical dates must not use fixtures_by_date.')

        async def fixture_details(self, fixture_ids):
            self.detail_calls.append(list(fixture_ids))
            return []

    repository = Repository()
    api = Api()
    first = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=candidates,
            clock=datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
        ),
    )
    second = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=[
                *candidates,
                _candidate(
                    9,
                    '2026-07-26T22:00:00+00:00',
                    status='FT',
                ),
            ],
            clock=datetime(2026, 7, 27, 4, tzinfo=timezone.utc),
        ),
    )
    third = asyncio.run(
        prediction_evaluation_service._refresh_current_utc_fixtures(
            api=api,
            repository=repository,
            candidates=[
                *candidates,
                _candidate(
                    9,
                    '2026-07-26T22:00:00+00:00',
                    status='FT',
                ),
            ],
            clock=datetime(2026, 7, 27, 5, tzinfo=timezone.utc),
        ),
    )

    assert api.detail_calls == [[7, 8], [9]]
    assert first['provider_detail_requests'] == 1
    assert first['refresh_errors'] == 2
    assert second['provider_detail_requests'] == 1
    assert second['refresh_errors'] == 1
    assert third['provider_fixture_requests'] == 0
    assert third['provider_detail_requests'] == 0

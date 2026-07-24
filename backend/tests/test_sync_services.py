from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.api_football import RateLimitSnapshot
from app.services.api_football import (
    ApiFootballAccessRestrictionError,
    ApiFootballDateAccessError,
)
from app.services.fixture_normalizer import normalize_fixture
from app.services.historical_sync_service import HistoricalSyncService
from app.services.optional_fixture_sync_service import OptionalFixtureSyncOptions
from app.services.rate_limit_manager import RateLimitExhaustedError
from app.services.upcoming_sync_service import UpcomingSyncService
from app.services.supabase_repository import SupabaseRepository
from tests.test_fixture_normalizer import fixture_payload


class FakeRepository:
    def __init__(self, *, completed: set[int] | None = None) -> None:
        self.competition = {
            'id': 9,
            'api_league_id': 281,
            'internal_code': 'peru_liga_1',
            'name': 'Liga 1',
            'country': 'Peru',
            'enabled': True,
        }
        self.completed = completed or set()
        self.availability: list[tuple[int, str]] = []
        self.persisted_basic = []
        self.persisted_details = []
        self.errors = []

    async def list_enabled_competitions(self, codes=None):
        if codes and self.competition['internal_code'] not in codes:
            return []
        return [self.competition]

    async def ensure_legacy_league(self, competition):
        return None

    async def list_competition_seasons(self, competition_id, **_kwargs):
        return [
            {'season': year, 'availability_status': 'available', 'coverage_json': {}, 'is_current': year == 2026}
            for year in range(2021, 2027)
        ]

    async def mark_season_availability(self, competition_id, season, status, **_kwargs):
        self.availability.append((season, status))

    async def persist_fixtures_basic(self, fixtures, *, competition):
        self.persisted_basic.extend(fixtures)
        return {item.api_fixture_id: True for item in fixtures}

    async def completed_fixture_ids(self, competition_id, season):
        return set(self.completed)

    async def persist_fixture(self, fixture, *, competition, details, coverage=None):
        self.persisted_details.append(fixture)
        return True

    async def mark_sync_error(self, *args, **kwargs):
        self.errors.append((args, kwargs))

    async def mark_sync_component_pending(self, *args, **kwargs):
        self.errors.append((args, kwargs))

    async def list_pending_fixture_details(self, **_kwargs):
        return []

    async def optional_sync_status(self, fixture_id):
        return {'fixture_id': fixture_id}


class FakeClient:
    def __init__(self, fixtures_by_season=None, *, detail_items=None):
        self.fixtures_by_season = fixtures_by_season or {}
        self.detail_items = detail_items or {}
        self.fixture_calls = []
        self.detail_calls = []
        self.rate_limit = SimpleNamespace(snapshot=RateLimitSnapshot(
            daily_limit=100,
            daily_remaining=80,
            minute_limit=10,
            minute_remaining=9,
            requests_this_run=2,
            max_requests_per_run=80,
            daily_safety_reserve=15,
        ))

    async def fixtures(self, league, season, **kwargs):
        self.fixture_calls.append((season, kwargs))
        value = self.fixtures_by_season.get(season, [])
        if isinstance(value, Exception):
            raise value
        return value

    async def fixture_details(self, ids, timezone_name=None):
        self.detail_calls.append(list(ids))
        return [self.detail_items[fixture_id] for fixture_id in ids if fixture_id in self.detail_items]


def test_historical_prioritizes_newest_and_marks_real_access_failure_unavailable():
    payload = fixture_payload(fixture_id=2025)
    payload['league']['season'] = 2025
    client = FakeClient({
        2026: ApiFootballAccessRestrictionError('not in plan'),
        2025: [payload],
    })
    repository = FakeRepository(completed={2025})
    summary = asyncio.run(HistoricalSyncService(client, repository).sync(
        from_season=2025, to_season=2026
    ))
    assert [call[0] for call in client.fixture_calls] == [2026, 2025]
    assert (2026, 'unavailable') in repository.availability
    assert summary.seasons_unavailable == 1
    assert summary.fixtures_downloaded == 1


def test_details_are_batched_at_twenty_and_resume_skips_completed_ids():
    basics = []
    details = {}
    for fixture_id in range(1, 22):
        item = fixture_payload(fixture_id=fixture_id)
        item['league']['season'] = 2026
        basics.append(item)
        details[fixture_id] = item
    client = FakeClient({2026: basics}, detail_items=details)
    repository = FakeRepository(completed={1})
    summary = asyncio.run(HistoricalSyncService(client, repository).sync(
        from_season=2026, to_season=2026
    ))
    assert client.detail_calls == [list(range(2, 22))]
    assert all(len(batch) <= 20 for batch in client.detail_calls)
    assert summary.details_complete == 20


def test_detail_batches_fall_back_to_singular_ids_when_plan_rejects_ids():
    basics = []
    details = {}
    for fixture_id in (71, 72, 73):
        item = fixture_payload(fixture_id=fixture_id)
        item['league']['season'] = 2024
        basics.append(item)
        details[fixture_id] = item

    class SingularOnlyClient(FakeClient):
        async def fixture_details(self, ids, timezone_name=None):
            self.detail_calls.append(list(ids))
            if len(ids) > 1:
                raise ApiFootballAccessRestrictionError('ids is not available for this plan')
            return [self.detail_items[ids[0]]]

    client = SingularOnlyClient({2024: basics}, detail_items=details)
    repository = FakeRepository()
    summary = asyncio.run(HistoricalSyncService(client, repository).sync(
        from_season=2024, to_season=2024
    ))

    assert client.detail_calls == [[71, 72, 73], [71], [72], [73]]
    assert summary.details_complete == 3
    assert len(repository.persisted_details) == 3


def test_daily_budget_exhaustion_stops_safely_after_saved_progress():
    snapshot = RateLimitSnapshot(
        daily_limit=100,
        daily_remaining=15,
        requests_this_run=3,
        max_requests_per_run=80,
        daily_safety_reserve=15,
    )
    client = FakeClient({2026: RateLimitExhaustedError('daily_safety_reserve', snapshot)})
    client.rate_limit.snapshot = snapshot
    repository = FakeRepository()
    summary = asyncio.run(HistoricalSyncService(client, repository).sync(
        from_season=2025, to_season=2026
    ))
    assert summary.stopped_safely is True
    assert summary.requests_remaining == 15
    assert [call[0] for call in client.fixture_calls] == [2026]


def test_upcoming_keeps_only_scheduled_or_postponed_states():
    items = []
    for fixture_id, status in enumerate(('NS', 'TBD', 'PST', 'FT', 'CANC'), start=1):
        item = fixture_payload(status, fixture_id=fixture_id)
        item['fixture']['date'] = '2026-07-23T12:00:00-05:00'
        items.append(item)
    client = FakeClient({2026: items})
    repository = FakeRepository()
    summary = asyncio.run(UpcomingSyncService(client, repository).sync(
        days=30,
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    ))
    assert [item.fixture['status_short'] for item in repository.persisted_basic] == ['NS', 'TBD', 'PST']
    assert summary.fixtures_downloaded == 3


def test_upcoming_uses_date_fallback_when_current_season_is_plan_restricted():
    item = fixture_payload('NS', fixture_id=880)
    item['fixture']['date'] = '2026-07-22T19:00:00-05:00'

    class UnavailableRepository(FakeRepository):
        async def list_competition_seasons(self, competition_id, **_kwargs):
            return [{
                'season': 2026,
                'availability_status': 'unavailable',
                'coverage_json': {},
                'is_current': True,
            }]

    class DateFallbackClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.date_calls = []

        async def fixtures_by_date(self, fixture_date, *, timezone_name=None):
            self.date_calls.append(fixture_date)
            if len(self.date_calls) == 1:
                return [item]
            raise ApiFootballDateAccessError('future date unavailable')

    client = DateFallbackClient()
    repository = UnavailableRepository()
    summary = asyncio.run(UpcomingSyncService(client, repository).sync(
        days=1,
        now=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
    ))

    assert client.fixture_calls == []
    assert client.date_calls == ['2026-07-22', '2026-07-23']
    assert summary.fixtures_downloaded == 1
    assert len(repository.persisted_basic) == 1
    assert any('truncado' in message for message in summary.messages)


@pytest.mark.parametrize(
    ('optional_error', 'stopped_safely', 'error_count', 'expected_date_calls'),
    [
        (
            RateLimitExhaustedError(
                'daily_safety_reserve',
                RateLimitSnapshot(
                    daily_limit=100,
                    daily_remaining=15,
                    requests_this_run=3,
                    max_requests_per_run=80,
                    daily_safety_reserve=15,
                ),
            ),
            True,
            0,
            ['2026-07-22'],
        ),
        (
            RuntimeError('optional provider failure'),
            False,
            1,
            ['2026-07-22', '2026-07-23'],
        ),
    ],
)
def test_upcoming_date_fallback_handles_optional_failures_safely(
    optional_error,
    stopped_safely,
    error_count,
    expected_date_calls,
):
    item = fixture_payload('NS', fixture_id=881)
    item['fixture']['date'] = '2026-07-22T19:00:00-05:00'

    class UnavailableRepository(FakeRepository):
        async def list_competition_seasons(self, competition_id, **_kwargs):
            return [{
                'season': 2026,
                'availability_status': 'unavailable',
                'coverage_json': {},
                'is_current': True,
            }]

    class DateFallbackClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.date_calls = []

        async def fixtures_by_date(self, fixture_date, *, timezone_name=None):
            self.date_calls.append(fixture_date)
            return [item] if len(self.date_calls) == 1 else []

    client = DateFallbackClient()
    if isinstance(optional_error, RateLimitExhaustedError):
        client.rate_limit.snapshot = optional_error.snapshot
    repository = UnavailableRepository()
    service = UpcomingSyncService(client, repository)
    service.optional_sync.sync_many = AsyncMock(side_effect=optional_error)

    summary = asyncio.run(service.sync(
        days=1,
        now=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
        optional=OptionalFixtureSyncOptions(odds=True),
    ))

    assert summary.stopped_safely is stopped_safely
    assert summary.errors == error_count
    assert summary.fixtures_downloaded == 1
    assert len(repository.persisted_basic) == 1
    assert client.date_calls == expected_date_calls
    service.optional_sync.sync_many.assert_awaited_once()
    if stopped_safely:
        assert summary.requests_remaining == 15
        assert any('detenido de forma segura' in message for message in summary.messages)


def test_completed_fixtures_prevent_duplicate_detail_requests():
    item = fixture_payload(fixture_id=55)
    client = FakeClient({2026: [item]}, detail_items={55: item})
    repository = FakeRepository(completed={55})
    asyncio.run(HistoricalSyncService(client, repository).sync(
        from_season=2026, to_season=2026
    ))
    assert client.detail_calls == []
    assert len(repository.persisted_basic) == 1


def test_market_backfill_uses_only_pending_rows_and_requires_statistics():
    detail = fixture_payload(fixture_id=7101)
    detail['league']['id'] = 71
    detail['league']['season'] = 2024
    second_detail = fixture_payload(fixture_id=7103)
    second_detail['league']['id'] = 71
    second_detail['league']['season'] = 2024

    class MarketRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.competition.update({
                'api_league_id': 71,
                'internal_code': 'brazil_serie_a',
                'name': 'Brasileirão Série A',
                'country': 'Brazil',
            })

        async def list_pending_market_fixture_details(self, **kwargs):
            assert kwargs == {
                'competition_ids': [9],
                'limit': 20,
                'max_attempts': 3,
            }
            return [
                {
                    'competition_id': 9,
                    'season': 2024,
                    'api_fixture_id': 7101,
                    'priority_current_team': True,
                },
                {
                    'competition_id': 9,
                    'season': 2024,
                    'api_fixture_id': 7103,
                    'priority_current_team': False,
                },
            ]

    repository = MarketRepository()
    client = FakeClient(detail_items={7101: detail, 7103: second_detail})
    summary = asyncio.run(HistoricalSyncService(client, repository).backfill_market_statistics(
        competitions=['brazil_serie_a'],
        max_fixtures=20,
        max_attempts=3,
    ))

    assert client.fixture_calls == []
    assert client.detail_calls == [[7101], [7103]]
    assert [item.api_fixture_id for item in repository.persisted_details] == [7101, 7103]
    assert summary.details_complete == 2
    assert summary.details_incomplete == 0


def test_market_backfill_counts_missing_statistics_as_retryable_failure():
    detail = fixture_payload(fixture_id=7102, include_statistics=False)
    detail['league']['id'] = 71
    detail['league']['season'] = 2024

    class MarketRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.competition.update({
                'api_league_id': 71,
                'internal_code': 'brazil_serie_a',
                'name': 'Brasileirão Série A',
                'country': 'Brazil',
            })

        async def list_pending_market_fixture_details(self, **_kwargs):
            return [{
                'competition_id': 9,
                'season': 2024,
                'api_fixture_id': 7102,
                'priority_current_team': False,
            }]

    repository = MarketRepository()
    client = FakeClient(detail_items={7102: detail})
    summary = asyncio.run(HistoricalSyncService(client, repository).backfill_market_statistics(
        competitions=['brazil_serie_a'], max_fixtures=1
    ))

    assert summary.details_complete == 0
    assert summary.details_incomplete == 1
    assert repository.errors
    assert 'Required component has no normalized rows: statistics' in repository.errors[0][0][-1]


def test_market_backfill_rejects_present_but_empty_statistics_array():
    detail = fixture_payload(fixture_id=7104)
    detail['league']['id'] = 71
    detail['league']['season'] = 2024
    detail['statistics'] = []

    class MarketRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.competition.update({
                'api_league_id': 71,
                'internal_code': 'brazil_serie_a',
                'name': 'Brasileirão Série A',
                'country': 'Brazil',
            })

        async def list_pending_market_fixture_details(self, **_kwargs):
            return [{
                'competition_id': 9,
                'season': 2024,
                'api_fixture_id': 7104,
                'priority_current_team': True,
            }]

    repository = MarketRepository()
    client = FakeClient(detail_items={7104: detail})
    summary = asyncio.run(HistoricalSyncService(client, repository).backfill_market_statistics(
        competitions=['brazil_serie_a'], max_fixtures=1
    ))

    assert summary.details_complete == 0
    assert summary.details_incomplete == 1
    assert repository.persisted_details[0].components_present['statistics'] is True
    args, _kwargs = repository.errors[0]
    assert args[3] == 'statistics'
    assert args[4] == 'Required component has no normalized rows: statistics'


def _team_history_payload(
    *,
    fixture_id: int,
    league_id: int,
    league_name: str,
    home_team_id: int,
    away_team_id: int,
    timestamp: int,
) -> dict:
    item = fixture_payload(fixture_id=fixture_id)
    item['fixture']['timestamp'] = timestamp
    item['league'].update({
        'id': league_id,
        'name': league_name,
        'country': 'England',
        'season': 2025,
    })
    item['teams']['home']['id'] = home_team_id
    item['teams']['away']['id'] = away_team_id
    item['statistics'][0]['team']['id'] = home_team_id
    return item


def test_team_backfill_supports_uncatalogued_domestic_leagues_and_deduplicates():
    shared = _team_history_payload(
        fixture_id=8001,
        league_id=40,
        league_name='Championship',
        home_team_id=69,
        away_team_id=76,
        timestamp=1_700_000_001,
    )
    second = _team_history_payload(
        fixture_id=8002,
        league_id=41,
        league_name='League One',
        home_team_id=76,
        away_team_id=5,
        timestamp=1_700_000_002,
    )
    friendly = _team_history_payload(
        fixture_id=8003,
        league_id=667,
        league_name='Friendlies Clubs',
        home_team_id=69,
        away_team_id=6,
        timestamp=1_700_000_003,
    )
    wrong_team = _team_history_payload(
        fixture_id=8004,
        league_id=40,
        league_name='Championship',
        home_team_id=7,
        away_team_id=8,
        timestamp=1_700_000_004,
    )

    class TeamRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.competitions = {}
            self.statistics_complete = {8001}

        async def ensure_targeted_competition(self, league):
            league_id = int(league['id'])
            self.competitions.setdefault(league_id, {
                'id': 1_000 + league_id,
                'api_league_id': league_id,
                'internal_code': f'api_{league_id}',
                'name': league['name'],
                'country': league['country'],
                'competition_type': 'league',
                'enabled': False,
            })
            return self.competitions[league_id]

        async def fixture_ids_with_team_statistics(self, fixture_ids):
            return set(fixture_ids) & self.statistics_complete

    class TeamClient(FakeClient):
        def __init__(self):
            super().__init__(detail_items={8002: second})
            self.team_calls = []

        async def fixtures_for_team(self, team, **kwargs):
            self.team_calls.append((team, kwargs))
            return (
                [shared, friendly, wrong_team]
                if team == 69
                else [shared, second]
            )

    repository = TeamRepository()
    client = TeamClient()
    summary = asyncio.run(HistoricalSyncService(client, repository).backfill_team_history(
        team_ids=[69, 76, 69],
        max_fixtures_per_team=20,
        max_detail_fixtures=10,
    ))

    assert [call[0] for call in client.team_calls] == [69, 76]
    assert all(call[1] == {
        'last': 20,
        'timezone_name': 'America/Lima',
    } for call in client.team_calls)
    assert set(repository.competitions) == {40, 41}
    assert [item.api_fixture_id for item in repository.persisted_basic] == [8001, 8002]
    assert client.detail_calls == [[8002]]
    assert summary.teams_requested == 2
    assert summary.teams_processed == 2
    assert summary.fixtures_discovered == 5
    assert summary.fixtures_deduplicated == 1
    assert summary.fixtures_skipped == 2
    assert summary.details_skipped_existing == 1
    assert summary.details_complete == 1


def test_team_backfill_batches_details_by_default_and_prioritizes_recency():
    older = _team_history_payload(
        fixture_id=8101,
        league_id=94,
        league_name='Primeira Liga',
        home_team_id=104,
        away_team_id=10,
        timestamp=1_600_000_000,
    )
    newer = _team_history_payload(
        fixture_id=8102,
        league_id=94,
        league_name='Primeira Liga',
        home_team_id=11,
        away_team_id=104,
        timestamp=1_700_000_000,
    )

    class TeamRepository(FakeRepository):
        async def ensure_targeted_competition(self, league):
            return {
                **self.competition,
                'id': 94,
                'api_league_id': 94,
                'internal_code': 'api_94',
                'name': league['name'],
            }

        async def fixture_ids_with_team_statistics(self, _fixture_ids):
            return set()

    class TeamClient(FakeClient):
        def __init__(self):
            super().__init__(detail_items={8101: older, 8102: newer})

        async def fixtures_for_team(self, team, **_kwargs):
            assert team == 104
            return [older, newer]

    repository = TeamRepository()
    client = TeamClient()
    summary = asyncio.run(HistoricalSyncService(client, repository).backfill_team_history(
        team_ids=[104],
        max_detail_fixtures=2,
    ))

    assert client.detail_calls == [[8102, 8101]]
    assert summary.details_complete == 2


def test_team_backfill_saves_first_team_before_request_budget_stop():
    item = _team_history_payload(
        fixture_id=8201,
        league_id=203,
        league_name='Süper Lig',
        home_team_id=235,
        away_team_id=12,
        timestamp=1_700_000_100,
    )
    snapshot = RateLimitSnapshot(
        daily_limit=100,
        daily_remaining=15,
        requests_this_run=2,
        max_requests_per_run=2,
        daily_safety_reserve=15,
    )

    class TeamRepository(FakeRepository):
        async def ensure_targeted_competition(self, league):
            return {
                **self.competition,
                'id': 203,
                'api_league_id': 203,
                'internal_code': 'api_203',
                'name': league['name'],
            }

    class TeamClient(FakeClient):
        async def fixtures_for_team(self, team, **_kwargs):
            if team == 235:
                return [item]
            raise RateLimitExhaustedError('daily_safety_reserve', snapshot)

    repository = TeamRepository()
    client = TeamClient()
    client.rate_limit.snapshot = snapshot
    summary = asyncio.run(HistoricalSyncService(client, repository).backfill_team_history(
        team_ids=[235, 331],
    ))

    assert [value.api_fixture_id for value in repository.persisted_basic] == [8201]
    assert summary.stopped_safely is True
    assert summary.teams_processed == 1
    assert client.detail_calls == []


def test_team_metadata_backfill_is_separate_and_quota_bounded():
    class MetadataRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.metadata = []

        async def persist_team_metadata(self, payload):
            self.metadata.append(payload)

    class MetadataClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.metadata_calls = []

        async def team_by_id(self, team):
            self.metadata_calls.append(team)
            return {
                'team': {
                    'id': team,
                    'name': f'Team {team}',
                    'country': 'England',
                },
                'venue': {'id': 900 + team, 'name': 'Ground'},
            }

    repository = MetadataRepository()
    client = MetadataClient()
    summary = asyncio.run(HistoricalSyncService(client, repository).backfill_team_metadata(
        team_ids=[69, 76, 69]
    ))

    assert client.metadata_calls == [69, 76]
    assert [row['team']['id'] for row in repository.metadata] == [69, 76]
    assert summary.teams_requested == 2
    assert summary.team_metadata_updated == 2


def test_legacy_model_code_is_preserved_when_competition_is_resolved():
    repository = SupabaseRepository(client=object())
    repository._select = AsyncMock(return_value=[{'id': 39, 'code': 'E0'}])
    repository._upsert = AsyncMock(return_value=[])
    asyncio.run(repository.ensure_legacy_league({
        'api_league_id': 39,
        'internal_code': 'premier_league',
        'name': 'Premier League',
        'country': 'England',
        'enabled': True,
    }))
    written = repository._upsert.await_args.args[1]
    assert written['code'] == 'E0'

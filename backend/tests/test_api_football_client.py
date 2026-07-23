import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.config import Settings
from app.services.api_football_client import ApiFootballClient
from app.services.rate_limit_manager import RateLimitExhaustedError, RateLimitManager


def _settings(**overrides):
    values = {
        '_env_file': None,
        'api_football_key': 'valid_api_key_0123456789',
        'api_delay_seconds': 0,
        'api_retry_base_delay_seconds': 0,
        'api_retry_max_delay_seconds': 0,
    }
    values.update(overrides)
    return Settings(**values)


def _envelope(response, *, page=1, total=1, errors=None):
    return {
        'get': 'test',
        'parameters': {},
        'errors': errors or {},
        'results': len(response),
        'paging': {'current': page, 'total': total},
        'response': response,
    }


class _PersistedRateLimitSink:
    def __init__(self, persisted: dict):
        self.persisted = persisted
        self.records = []

    async def latest_api_rate_limit(self):
        return self.persisted

    async def log_api_request(self, record):
        self.records.append(record)


def test_pagination_and_successful_request_deduplication():
    calls: list[int] = []

    def handler(request: httpx.Request):
        page = int(request.url.params['page'])
        calls.append(page)
        return httpx.Response(200, json=_envelope([{'id': page}], page=page, total=2))

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url='https://example.test', transport=transport)
    client = ApiFootballClient(settings=_settings(), http_client=http_client)

    async def exercise():
        try:
            first = await client.get_all_pages('/leagues', {'search': 'Premier League'})
            second = await client.get_all_pages('/leagues', {'search': 'Premier League'})
            assert first == [{'id': 1}, {'id': 2}]
            assert second == first
        finally:
            await http_client.aclose()

    asyncio.run(exercise())
    assert calls == [1, 2]
    assert client.rate_limit.snapshot.requests_this_run == 2


def test_concurrent_duplicate_requests_share_one_http_call():
    calls = 0

    async def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx.Response(200, json=_envelope([{'id': 1}]))

    http_client = httpx.AsyncClient(
        base_url='https://example.test',
        transport=httpx.MockTransport(handler),
    )
    client = ApiFootballClient(settings=_settings(), http_client=http_client)

    async def exercise():
        try:
            first, second = await asyncio.gather(
                client.get('/leagues', {'search': 'Liga'}),
                client.get('/leagues', {'search': 'Liga'}),
            )
            assert first == second
        finally:
            await http_client.aclose()

    asyncio.run(exercise())
    assert calls == 1


def test_429_respects_retry_after_and_stops_after_three_attempts():
    calls = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={'Retry-After': '2'}, json=_envelope([]))

    async def fake_sleep(delay: float):
        sleeps.append(delay)

    http_client = httpx.AsyncClient(
        base_url='https://example.test',
        transport=httpx.MockTransport(handler),
    )
    client = ApiFootballClient(
        settings=_settings(),
        http_client=http_client,
        sleep=fake_sleep,
    )

    async def exercise():
        try:
            with pytest.raises(RateLimitExhaustedError) as captured:
                await client.get('/fixtures', {'league': 39, 'season': 2026})
            assert captured.value.reason == 'provider_429'
            assert captured.value.snapshot.can_continue is False
        finally:
            await http_client.aclose()

    asyncio.run(exercise())
    assert calls == 3
    assert sleeps == [2, 2]


def test_budget_exhaustion_does_not_touch_network():
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_envelope([]))

    manager = RateLimitManager(daily_safety_reserve=15, max_requests_per_run=80)
    manager.update_from_headers({'x-ratelimit-requests-remaining': '15'})
    http_client = httpx.AsyncClient(
        base_url='https://example.test',
        transport=httpx.MockTransport(handler),
    )
    client = ApiFootballClient(
        settings=_settings(),
        http_client=http_client,
        rate_limit=manager,
    )

    async def exercise():
        try:
            with pytest.raises(RateLimitExhaustedError):
                await client.get('/leagues', {'search': 'Serie A'})
        finally:
            await http_client.aclose()

    asyncio.run(exercise())
    assert calls == 0


def test_same_day_persisted_budget_blocks_before_http():
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_envelope([]))

    sink = _PersistedRateLimitSink(
        {
            'requested_at': datetime.now(timezone.utc).isoformat(),
            'daily_limit': 100,
            'daily_remaining': 14,
            'minute_limit': 10,
            'minute_remaining': 8,
        }
    )
    http_client = httpx.AsyncClient(
        base_url='https://example.test',
        transport=httpx.MockTransport(handler),
    )
    client = ApiFootballClient(
        settings=_settings(),
        http_client=http_client,
        request_log_sink=sink,
    )

    async def exercise():
        try:
            with pytest.raises(RateLimitExhaustedError) as captured:
                await client.get('/fixtures', {'date': '2026-07-22'})
            assert captured.value.reason == 'daily_safety_reserve'
            assert captured.value.snapshot.daily_remaining == 14
        finally:
            await http_client.aclose()

    asyncio.run(exercise())
    assert calls == 0
    assert sink.records == []


def test_previous_day_persisted_budget_is_ignored_and_http_proceeds():
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_envelope([{'id': 1}]))

    sink = _PersistedRateLimitSink(
        {
            'requested_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            'daily_limit': 100,
            'daily_remaining': 0,
            'minute_limit': 10,
            'minute_remaining': 0,
        }
    )
    http_client = httpx.AsyncClient(
        base_url='https://example.test',
        transport=httpx.MockTransport(handler),
    )
    client = ApiFootballClient(
        settings=_settings(),
        http_client=http_client,
        request_log_sink=sink,
    )

    async def exercise():
        try:
            payload = await client.get('/fixtures', {'date': '2026-07-22'})
            assert payload['response'] == [{'id': 1}]
        finally:
            await http_client.aclose()

    asyncio.run(exercise())
    assert calls == 1
    assert len(sink.records) == 1
    assert client.rate_limit.snapshot.requests_this_run == 1
    assert client.rate_limit.snapshot.daily_remaining is None


def test_stale_same_day_minute_limit_is_ignored_but_daily_budget_is_restored():
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_envelope([{'id': 1}]))

    sink = _PersistedRateLimitSink(
        {
            'requested_at': (
                datetime.now(timezone.utc) - timedelta(minutes=2)
            ).isoformat(),
            'daily_limit': 100,
            'daily_remaining': 63,
            'minute_limit': 10,
            'minute_remaining': 0,
        }
    )
    http_client = httpx.AsyncClient(
        base_url='https://example.test',
        transport=httpx.MockTransport(handler),
    )
    client = ApiFootballClient(
        settings=_settings(),
        http_client=http_client,
        request_log_sink=sink,
    )

    async def exercise():
        try:
            payload = await client.get('/fixtures', {'date': '2026-07-22'})
            assert payload['response'] == [{'id': 1}]
        finally:
            await http_client.aclose()

    asyncio.run(exercise())
    assert calls == 1
    assert client.rate_limit.snapshot.daily_remaining == 63
    assert client.rate_limit.snapshot.minute_remaining is None


def test_request_log_callback_is_decoupled_and_redacts_sensitive_parameters():
    records = []
    secret = 'never-store-me'

    def handler(_request: httpx.Request):
        return httpx.Response(
            200,
            headers={
                'x-ratelimit-requests-limit': '100',
                'x-ratelimit-requests-remaining': '78',
            },
            json=_envelope([{'id': 1}]),
        )

    http_client = httpx.AsyncClient(
        base_url='https://example.test',
        transport=httpx.MockTransport(handler),
    )
    client = ApiFootballClient(
        settings=_settings(),
        http_client=http_client,
        request_log_sink=records.append,
    )

    async def exercise():
        try:
            await client.get('/leagues', {'search': 'Liga', 'api_key': secret})
        finally:
            await http_client.aclose()

    asyncio.run(exercise())
    assert len(records) == 1
    assert records[0].parameters_json['api_key'] == '[REDACTED]'
    assert secret not in records[0].model_dump_json()
    assert records[0].daily_remaining == 78


def test_fixture_details_enforces_twenty_id_batch_limit():
    http_client = httpx.AsyncClient(base_url='https://example.test')
    client = ApiFootballClient(settings=_settings(), http_client=http_client)

    async def exercise():
        try:
            with pytest.raises(ValueError):
                await client.fixture_details(list(range(1, 22)))
        finally:
            await http_client.aclose()

    asyncio.run(exercise())


def test_optional_fixture_methods_use_expected_endpoints_and_bypass_cache():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request):
        calls.append((request.url.path, str(request.url.params.get('fixture'))))
        response = [{'fixture': {'id': 44}, 'endpoint': request.url.path}]
        return httpx.Response(200, json=_envelope(response))

    http_client = httpx.AsyncClient(
        base_url='https://example.test',
        transport=httpx.MockTransport(handler),
    )
    client = ApiFootballClient(settings=_settings(), http_client=http_client)

    async def exercise():
        try:
            assert len(await client.fixture_injuries(44)) == 1
            first_odds = await client.fixture_odds(44)
            second_odds = await client.fixture_odds(44)
            prediction = await client.fixture_external_prediction(44)
            assert len(await client.fixture_lineups(44)) == 1
            assert first_odds == second_odds
            assert set(prediction) == {'response'}
        finally:
            await http_client.aclose()

    asyncio.run(exercise())
    assert calls == [
        ('/injuries', '44'),
        ('/odds', '44'),
        ('/odds', '44'),
        ('/predictions', '44'),
        ('/fixtures/lineups', '44'),
    ]

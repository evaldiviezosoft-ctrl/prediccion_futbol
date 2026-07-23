import asyncio

import pytest

from app.services.rate_limit_manager import RateLimitExhaustedError, RateLimitManager


def test_headers_are_parsed_case_insensitively():
    manager = RateLimitManager(daily_safety_reserve=15, max_requests_per_run=80)
    snapshot = manager.update_from_headers(
        {
            'X-RateLimit-Requests-Limit': '100',
            'x-ratelimit-requests-remaining': '63',
            'X-RateLimit-Limit': '10',
            'x-ratelimit-remaining': '8',
        }
    )

    assert snapshot.daily_limit == 100
    assert snapshot.daily_remaining == 63
    assert snapshot.minute_limit == 10
    assert snapshot.minute_remaining == 8
    assert snapshot.can_continue is True


def test_daily_safety_reserve_stops_before_another_request():
    manager = RateLimitManager(daily_safety_reserve=15, max_requests_per_run=80)
    manager.update_from_headers({'x-ratelimit-requests-remaining': '15'})

    async def exercise():
        with pytest.raises(RateLimitExhaustedError) as captured:
            await manager.acquire_request_slot()
        assert captured.value.reason == 'daily_safety_reserve'
        assert captured.value.snapshot.requests_this_run == 0

    asyncio.run(exercise())


def test_minute_limit_stops_before_another_request():
    manager = RateLimitManager(daily_safety_reserve=15, max_requests_per_run=80)
    manager.update_from_headers(
        {
            'x-ratelimit-requests-remaining': '63',
            'x-ratelimit-remaining': '0',
        }
    )

    async def exercise():
        with pytest.raises(RateLimitExhaustedError) as captured:
            await manager.acquire_request_slot()
        assert captured.value.reason == 'minute_limit'
        assert captured.value.snapshot.requests_this_run == 0

    asyncio.run(exercise())


def test_restore_provider_budget_preserves_run_count_and_enforces_daily_reserve():
    manager = RateLimitManager(daily_safety_reserve=15, max_requests_per_run=80)

    asyncio.run(manager.acquire_request_slot())
    snapshot = manager.restore_provider_budget(
        daily_limit=100,
        daily_remaining=14,
        minute_limit=10,
        minute_remaining=7,
    )

    assert snapshot.daily_limit == 100
    assert snapshot.daily_remaining == 14
    assert snapshot.minute_limit == 10
    assert snapshot.minute_remaining == 7
    assert snapshot.requests_this_run == 1
    assert snapshot.can_continue is False
    assert snapshot.stop_reason == 'daily_safety_reserve'


def test_per_run_limit_counts_real_attempts_and_can_be_reset():
    manager = RateLimitManager(daily_safety_reserve=15, max_requests_per_run=1)

    async def exercise():
        await manager.acquire_request_slot()
        with pytest.raises(RateLimitExhaustedError) as captured:
            await manager.acquire_request_slot()
        assert captured.value.reason == 'max_requests_per_run'

    asyncio.run(exercise())
    manager.reset_run()
    assert manager.snapshot.requests_this_run == 0
    assert manager.snapshot.can_continue is True

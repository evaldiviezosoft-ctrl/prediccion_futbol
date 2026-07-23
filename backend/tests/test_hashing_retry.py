import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from app.utils.hashing import canonical_json, request_hash, response_hash, sanitize_for_logging
from app.utils.retry import RetryPolicy, parse_retry_after, retry_async, retry_delay


def test_request_hash_is_stable_for_parameter_order_and_response_hash_changes():
    first = request_hash('/fixtures', {'season': 2026, 'league': 39})
    second = request_hash('fixtures', {'league': 39, 'season': 2026})

    assert first == second
    assert len(first) == 64
    assert response_hash({'value': 1}) != response_hash({'value': 2})


def test_logging_sanitizer_never_serializes_credentials():
    secret = 'do-not-log-this-value'
    sanitized = sanitize_for_logging(
        {
            'league': 39,
            'api_key': secret,
            'nested': {'Authorization': secret},
        }
    )
    serialized = canonical_json(sanitized)

    assert secret not in serialized
    assert serialized.count('[REDACTED]') == 2


def test_retry_after_supports_seconds_and_http_dates():
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    retry_at = format_datetime(now + timedelta(seconds=17), usegmt=True)

    assert parse_retry_after('4') == 4
    assert parse_retry_after(retry_at, now=now) == 17
    assert retry_delay(3, policy=RetryPolicy(base_delay_seconds=2, max_backoff_seconds=5)) == 5


def test_retry_async_never_exceeds_three_attempts():
    calls = 0
    sleeps: list[float] = []

    async def operation():
        nonlocal calls
        calls += 1
        raise OSError('temporary')

    async def fake_sleep(delay: float):
        sleeps.append(delay)

    async def exercise():
        with pytest.raises(OSError):
            await retry_async(
                operation,
                should_retry=lambda exc: isinstance(exc, OSError),
                sleep=fake_sleep,
                policy=RetryPolicy(max_attempts=3, base_delay_seconds=1),
            )

    asyncio.run(exercise())
    assert calls == 3
    assert sleeps == [1, 2]


def test_retry_policy_rejects_more_than_three_attempts():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=4)

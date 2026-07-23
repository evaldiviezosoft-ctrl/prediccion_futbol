from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TypeVar


RETRYABLE_HTTP_STATUSES = frozenset({429, 499, 500, 502, 503, 504})
T = TypeVar('T')


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_backoff_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise ValueError('max_attempts must be between 1 and 3')
        if self.base_delay_seconds < 0:
            raise ValueError('base_delay_seconds cannot be negative')
        if self.max_backoff_seconds < 0:
            raise ValueError('max_backoff_seconds cannot be negative')


def is_retryable_status(status_code: int) -> bool:
    return status_code in RETRYABLE_HTTP_STATUSES


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - reference).total_seconds())


def retry_delay(
    failed_attempt: int,
    *,
    policy: RetryPolicy,
    retry_after: str | None = None,
) -> float:
    """Return the pause before the next attempt; attempt numbering starts at one."""

    provider_delay = parse_retry_after(retry_after)
    if provider_delay is not None:
        return provider_delay
    exponential = policy.base_delay_seconds * (2 ** max(0, failed_attempt - 1))
    return min(exponential, policy.max_backoff_seconds)


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    should_retry: Callable[[Exception], bool],
    sleep: Callable[[float], Awaitable[None]],
    policy: RetryPolicy | None = None,
) -> T:
    """Retry an async operation without ever exceeding three total attempts."""

    selected_policy = policy or RetryPolicy()
    for attempt in range(1, selected_policy.max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if attempt >= selected_policy.max_attempts or not should_retry(exc):
                raise
            await sleep(retry_delay(attempt, policy=selected_policy))
    raise RuntimeError('retry loop ended unexpectedly')

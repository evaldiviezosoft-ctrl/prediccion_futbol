from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from app.schemas.api_football import RateLimitSnapshot
from app.services.api_football import ApiFootballRateLimitError


class RateLimitExhaustedError(ApiFootballRateLimitError):
    """Raised before another request would violate a configured API budget."""

    def __init__(self, reason: str, snapshot: RateLimitSnapshot) -> None:
        self.reason = reason
        self.snapshot = snapshot.model_copy(
            update={'can_continue': False, 'stop_reason': reason}
        )
        messages = {
            'daily_safety_reserve': 'API-Football daily safety reserve has been reached.',
            'max_requests_per_run': 'API-Football request budget for this run has been reached.',
            'minute_limit': 'API-Football minute request limit has been reached.',
            'provider_429': 'API-Football rate limit has been reached.',
        }
        super().__init__(messages.get(reason, 'API-Football request budget is exhausted.'))


def _case_insensitive_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value).strip() for key, value in headers.items()}


def _non_negative_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


class RateLimitManager:
    """Tracks provider quotas and atomically reserves request slots per process run."""

    def __init__(
        self,
        *,
        daily_safety_reserve: int = 15,
        max_requests_per_run: int = 80,
    ) -> None:
        if daily_safety_reserve < 0:
            raise ValueError('daily_safety_reserve cannot be negative')
        if max_requests_per_run < 1:
            raise ValueError('max_requests_per_run must be positive')
        self.daily_safety_reserve = daily_safety_reserve
        self.max_requests_per_run = max_requests_per_run
        self.daily_limit: int | None = None
        self.daily_remaining: int | None = None
        self.minute_limit: int | None = None
        self.minute_remaining: int | None = None
        self.requests_this_run = 0
        self._lock = asyncio.Lock()

    @property
    def snapshot(self) -> RateLimitSnapshot:
        reason = self.stop_reason
        return RateLimitSnapshot(
            daily_limit=self.daily_limit,
            daily_remaining=self.daily_remaining,
            minute_limit=self.minute_limit,
            minute_remaining=self.minute_remaining,
            requests_this_run=self.requests_this_run,
            max_requests_per_run=self.max_requests_per_run,
            daily_safety_reserve=self.daily_safety_reserve,
            can_continue=reason is None,
            stop_reason=reason,
        )

    @property
    def stop_reason(self) -> str | None:
        if self.requests_this_run >= self.max_requests_per_run:
            return 'max_requests_per_run'
        if self.daily_remaining is not None and self.daily_remaining <= self.daily_safety_reserve:
            return 'daily_safety_reserve'
        if self.minute_remaining is not None and self.minute_remaining <= 0:
            return 'minute_limit'
        return None

    async def acquire_request_slot(self) -> RateLimitSnapshot:
        """Reserve one real HTTP request or stop before consuming the safety reserve."""

        async with self._lock:
            reason = self.stop_reason
            if reason is not None:
                raise RateLimitExhaustedError(reason, self.snapshot)
            self.requests_this_run += 1
            return self.snapshot

    def update_from_headers(self, headers: Mapping[str, Any]) -> RateLimitSnapshot:
        normalized = _case_insensitive_headers(headers)
        values = {
            'daily_limit': _non_negative_int(normalized.get('x-ratelimit-requests-limit')),
            'daily_remaining': _non_negative_int(
                normalized.get('x-ratelimit-requests-remaining')
            ),
            'minute_limit': _non_negative_int(normalized.get('x-ratelimit-limit')),
            'minute_remaining': _non_negative_int(normalized.get('x-ratelimit-remaining')),
        }
        for field, value in values.items():
            if value is not None:
                setattr(self, field, value)
        return self.snapshot

    def restore_provider_budget(
        self,
        *,
        daily_limit: int | None = None,
        daily_remaining: int | None = None,
        minute_limit: int | None = None,
        minute_remaining: int | None = None,
    ) -> RateLimitSnapshot:
        """Restore same-day provider counters without altering this run count."""

        values = {
            'daily_limit': daily_limit,
            'daily_remaining': daily_remaining,
            'minute_limit': minute_limit,
            'minute_remaining': minute_remaining,
        }
        for field, value in values.items():
            if value is not None:
                parsed = int(value)
                if parsed < 0:
                    raise ValueError(f'{field} cannot be negative')
                setattr(self, field, parsed)
        return self.snapshot

    def reset_run(self) -> None:
        """Reset only the local run counter; provider quota headers remain authoritative."""

        self.requests_this_run = 0


__all__ = ['RateLimitExhaustedError', 'RateLimitManager']

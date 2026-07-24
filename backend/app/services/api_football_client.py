from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect
import logging
from time import monotonic, perf_counter
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.schemas.api_football import ApiFootballEnvelope, ApiRequestLogRecord
from app.services.api_football import (
    ApiFootballAccessRestrictionError,
    ApiFootballConfigurationError,
    ApiFootballDateAccessError,
    ApiFootballError,
)
from app.services.rate_limit_manager import RateLimitExhaustedError, RateLimitManager
from app.utils.hashing import request_hash, sanitize_for_logging
from app.utils.retry import RetryPolicy, is_retryable_status, retry_delay


logger = logging.getLogger(__name__)
RequestLogCallback = Callable[[ApiRequestLogRecord], Awaitable[None] | None]
SleepCallback = Callable[[float], Awaitable[None]]


class ApiRequestLogSink(Protocol):
    async def log_api_request(self, record: ApiRequestLogRecord) -> None: ...


def _error_parts(errors: Any) -> tuple[set[str], str]:
    if isinstance(errors, Mapping):
        keys = {str(key).strip().lower() for key in errors}
        text = ' '.join(str(value) for value in errors.values()).lower()
        return keys, text
    return set(), str(errors).lower()


def _has_errors(errors: Any) -> bool:
    return errors not in (None, '', [], {})


class ApiFootballClient:
    """Async API-Football client with bounded retries, pagination and run deduplication."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
        rate_limit: RateLimitManager | None = None,
        request_log_sink: ApiRequestLogSink | RequestLogCallback | None = None,
        sleep: SleepCallback = asyncio.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        api_key = self.settings.require_api_football_key()
        self.rate_limit = rate_limit or RateLimitManager(
            daily_safety_reserve=self.settings.api_daily_safety_reserve,
            max_requests_per_run=self.settings.api_max_requests_per_run,
        )
        self._request_log_sink = request_log_sink
        self._sleep = sleep
        self._owns_http_client = http_client is None
        if http_client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.api_football_base_url,
                headers={'x-apisports-key': api_key},
                timeout=httpx.Timeout(self.settings.api_request_timeout_seconds),
            )
        else:
            self._client = http_client
            self._client.headers['x-apisports-key'] = api_key
        self._retry_policy = RetryPolicy(
            max_attempts=self.settings.api_retry_max_attempts,
            base_delay_seconds=self.settings.api_retry_base_delay_seconds,
            max_backoff_seconds=self.settings.api_retry_max_delay_seconds,
        )
        self._response_cache: dict[str, dict[str, Any]] = {}
        self._in_flight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._cache_lock = asyncio.Lock()
        self._request_gate = asyncio.Lock()
        self._budget_hydration_lock = asyncio.Lock()
        self._budget_hydrated = False
        self._last_request_started: float | None = None

    async def __aenter__(self) -> ApiFootballClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_http_client:
            await self._client.aclose()

    async def get(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Return one validated envelope and reuse successful duplicates within this run."""

        endpoint_text = endpoint.strip()
        if not endpoint_text or '://' in endpoint_text:
            raise ValueError('endpoint must be a non-empty API-Football path')
        normalized_endpoint = '/' + endpoint_text.lstrip('/')
        normalized_params = {
            str(key): value for key, value in (params or {}).items() if value is not None
        }
        fingerprint = request_hash(normalized_endpoint, normalized_params)

        if force_refresh:
            payload = await self._request_with_retries(
                normalized_endpoint,
                normalized_params,
                fingerprint,
            )
            async with self._cache_lock:
                self._response_cache[fingerprint] = deepcopy(payload)
            return payload

        async with self._cache_lock:
            cached = self._response_cache.get(fingerprint)
            if cached is not None:
                return deepcopy(cached)
            task = self._in_flight.get(fingerprint)
            owns_task = task is None
            if task is None:
                task = asyncio.create_task(
                    self._request_with_retries(
                        normalized_endpoint,
                        normalized_params,
                        fingerprint,
                    )
                )
                self._in_flight[fingerprint] = task

        try:
            payload = await task
            if owns_task:
                async with self._cache_lock:
                    self._response_cache[fingerprint] = deepcopy(payload)
            return deepcopy(payload)
        finally:
            if owns_task:
                async with self._cache_lock:
                    self._in_flight.pop(fingerprint, None)

    async def get_all_pages(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        max_pages: int = 1000,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        if max_pages < 1:
            raise ValueError('max_pages must be positive')
        base_params = dict(params or {})
        current_page = int(base_params.pop('page', 1))
        collected: list[dict[str, Any]] = []

        for _ in range(max_pages):
            page_params = {**base_params, 'page': current_page}
            payload = await self.get(
                endpoint,
                page_params,
                force_refresh=force_refresh,
            )
            response = payload.get('response')
            if response is None:
                page_items: list[dict[str, Any]] = []
            elif isinstance(response, list) and all(isinstance(item, dict) for item in response):
                page_items = response
            else:
                raise ApiFootballError('API-Football returned an invalid paginated response.')
            collected.extend(page_items)

            paging = payload.get('paging') or {}
            try:
                reported_current = int(paging.get('current', current_page))
                total_pages = int(paging.get('total', reported_current))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ApiFootballError('API-Football returned invalid pagination metadata.') from exc
            if reported_current >= total_pages:
                return collected
            next_page = reported_current + 1
            if next_page <= current_page:
                raise ApiFootballError('API-Football pagination did not advance.')
            current_page = next_page

        raise ApiFootballError('API-Football pagination exceeded the configured page guard.')

    async def leagues(
        self,
        *,
        search: str | None = None,
        country: str | None = None,
        season: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if search:
            params['search'] = search
        if country:
            params['country'] = country
        if season is not None:
            params['season'] = season
        # `/leagues` returns its complete catalog in one envelope and rejects
        # some otherwise-generic parameter combinations. Do not add `page=1`.
        payload = await self.get('/leagues', params)
        response = payload.get('response')
        if response is None:
            return []
        if not isinstance(response, list) or not all(
            isinstance(item, dict) for item in response
        ):
            raise ApiFootballError('API-Football returned an invalid leagues response.')
        return response

    async def fixtures(
        self,
        league: int,
        season: int,
        *,
        status: str | None = None,
        timezone_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        if league < 1:
            raise ValueError('league must be positive')
        if not 1900 <= season <= 2200:
            raise ValueError('season is outside the supported range')
        params: dict[str, Any] = {
            'league': league,
            'season': season,
            'timezone': timezone_name or self.settings.api_timezone,
        }
        if status:
            params['status'] = status
        if date_from:
            params['from'] = date_from
        if date_to:
            params['to'] = date_to
        # `/fixtures` returns all matching fixtures in one envelope. Supplying
        # the generic `page` parameter causes a provider application error.
        payload = await self.get('/fixtures', params)
        response = payload.get('response')
        if response is None:
            return []
        if not isinstance(response, list) or not all(
            isinstance(item, dict) for item in response
        ):
            raise ApiFootballError('API-Football returned an invalid fixtures response.')
        return response

    async def fixtures_for_team(
        self,
        team: int,
        *,
        last: int | None = None,
        season: int | None = None,
        status: str | None = None,
        timezone_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch a bounded recent history for one provider team.

        This intentionally uses the team-specific `/fixtures?team=...&last=...`
        query. It is the low-quota alternative to downloading every fixture in
        every domestic league just to seed one missing club profile.
        """

        if team < 1:
            raise ValueError('team must be positive')
        if (last is None) == (season is None):
            raise ValueError('exactly one of last or season is required')
        if last is not None and not 1 <= last <= 100:
            raise ValueError('last must be between 1 and 100')
        if season is not None and not 2000 <= season <= 2100:
            raise ValueError('season must be between 2000 and 2100')
        params: dict[str, Any] = {
            'team': team,
            'timezone': timezone_name or self.settings.api_timezone,
        }
        if last is not None:
            params['last'] = last
        if season is not None:
            params['season'] = season
        if status:
            params['status'] = status
        payload = await self.get('/fixtures', params)
        response = payload.get('response')
        if response is None:
            return []
        if not isinstance(response, list) or not all(
            isinstance(item, dict) for item in response
        ):
            raise ApiFootballError(
                'API-Football returned an invalid team fixture response.'
            )
        return response

    async def team_by_id(self, team: int) -> dict[str, Any] | None:
        """Return optional club and venue metadata for one exact team ID."""

        if team < 1:
            raise ValueError('team must be positive')
        payload = await self.get('/teams', {'id': team})
        response = payload.get('response')
        if response is None:
            return None
        if not isinstance(response, list) or not all(
            isinstance(item, dict) for item in response
        ):
            raise ApiFootballError('API-Football returned an invalid team response.')
        for item in response:
            provider_team = item.get('team')
            if not isinstance(provider_team, Mapping):
                continue
            try:
                if int(provider_team.get('id')) == team:
                    return item
            except (TypeError, ValueError):
                continue
        return None

    async def fixture_details(
        self,
        ids: Sequence[int],
        timezone_name: str | None = None,
    ) -> list[dict[str, Any]]:
        unique_ids = list(dict.fromkeys(ids))
        if not unique_ids:
            return []
        if len(unique_ids) > 20:
            raise ValueError('API-Football accepts at most 20 fixture ids per detail request')
        if any(fixture_id < 1 for fixture_id in unique_ids):
            raise ValueError('fixture ids must be positive')
        params = {
            ('id' if len(unique_ids) == 1 else 'ids'): (
                unique_ids[0]
                if len(unique_ids) == 1
                else '-'.join(str(fixture_id) for fixture_id in unique_ids)
            ),
            'timezone': timezone_name or self.settings.api_timezone,
        }
        payload = await self.get('/fixtures', params)
        response = payload.get('response')
        if response is None:
            return []
        if not isinstance(response, list) or not all(isinstance(item, dict) for item in response):
            raise ApiFootballError('API-Football returned an invalid fixture-details response.')
        return response

    async def fixtures_by_date(
        self,
        fixture_date: str,
        *,
        timezone_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch one provider calendar day without a season parameter.

        This is the safe fallback for plans that allow current dates but reject
        the current value of `season`.
        """

        try:
            datetime.fromisoformat(fixture_date)
        except ValueError as exc:
            raise ValueError('fixture_date must use ISO YYYY-MM-DD format') from exc
        payload = await self.get(
            '/fixtures',
            {
                'date': fixture_date,
                'timezone': timezone_name or self.settings.api_timezone,
            },
        )
        response = payload.get('response')
        if response is None:
            return []
        if not isinstance(response, list) or not all(isinstance(item, dict) for item in response):
            raise ApiFootballError('API-Football returned an invalid date fixture response.')
        return response

    async def fixture_injuries(self, fixture_id: int) -> list[dict[str, Any]]:
        """Fetch the current injury report for one fixture.

        This method deliberately bypasses the per-run response cache. The caller
        owns the four-hour refresh gate and, once that gate opens, needs a fresh
        provider response even in a long-lived process.
        """

        self._validate_fixture_id(fixture_id)
        return await self.get_all_pages(
            '/injuries',
            {'fixture': fixture_id},
            force_refresh=True,
        )

    async def fixture_odds(self, fixture_id: int) -> dict[str, Any]:
        """Fetch a complete, cache-bypassed odds snapshot for one fixture."""

        self._validate_fixture_id(fixture_id)
        items = await self.get_all_pages(
            '/odds',
            {'fixture': fixture_id},
            force_refresh=True,
        )
        return {'response': items}

    async def fixture_external_prediction(self, fixture_id: int) -> dict[str, Any]:
        """Fetch API-Football's own prediction without mixing it with our model."""

        self._validate_fixture_id(fixture_id)
        payload = await self.get(
            '/predictions',
            {'fixture': fixture_id},
            force_refresh=True,
        )
        response = payload.get('response')
        if response is None:
            response = []
        if not isinstance(response, list) or not all(
            isinstance(item, dict) for item in response
        ):
            raise ApiFootballError(
                'API-Football returned an invalid external-prediction response.'
            )
        # Store only provider data. Rate-limit metadata added by this client is
        # intentionally excluded so an unchanged prediction keeps one hash.
        return {'response': response}

    async def fixture_lineups(self, fixture_id: int) -> list[dict[str, Any]]:
        """Fetch the latest lineups for one fixture, bypassing the run cache."""

        self._validate_fixture_id(fixture_id)
        payload = await self.get(
            '/fixtures/lineups',
            {'fixture': fixture_id},
            force_refresh=True,
        )
        response = payload.get('response')
        if response is None:
            return []
        if not isinstance(response, list) or not all(
            isinstance(item, dict) for item in response
        ):
            raise ApiFootballError(
                'API-Football returned an invalid fixture-lineups response.'
            )
        return response

    @staticmethod
    def _validate_fixture_id(fixture_id: int) -> None:
        if fixture_id < 1:
            raise ValueError('fixture_id must be positive')

    async def _pace_request(self) -> None:
        delay = self.settings.api_delay_seconds
        if delay > 0 and self._last_request_started is not None:
            remaining = delay - (monotonic() - self._last_request_started)
            if remaining > 0:
                await self._sleep(remaining)
        self._last_request_started = monotonic()

    async def _hydrate_persisted_budget(self) -> None:
        if self._budget_hydrated:
            return
        async with self._budget_hydration_lock:
            if self._budget_hydrated:
                return
            self._budget_hydrated = True
            source = self._request_log_sink
            method = getattr(source, 'latest_api_rate_limit', None)
            if not callable(method):
                return
            try:
                value = method()
                if inspect.isawaitable(value):
                    value = await value
            except Exception:
                logger.exception('api_football_budget_restore_failed')
                return
            if not isinstance(value, Mapping):
                return
            requested_at = value.get('requested_at')
            try:
                if isinstance(requested_at, datetime):
                    logged_at = requested_at
                else:
                    logged_at = datetime.fromisoformat(
                        str(requested_at).replace('Z', '+00:00')
                    )
                if logged_at.tzinfo is None:
                    logged_at = logged_at.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                logged_at = logged_at.astimezone(timezone.utc)
                if logged_at.date() != now.date():
                    return
            except (TypeError, ValueError):
                return
            minute_values_are_fresh = timedelta(0) <= now - logged_at <= timedelta(
                minutes=1
            )
            self.rate_limit.restore_provider_budget(
                daily_limit=value.get('daily_limit'),
                daily_remaining=value.get('daily_remaining'),
                minute_limit=(
                    value.get('minute_limit') if minute_values_are_fresh else None
                ),
                minute_remaining=(
                    value.get('minute_remaining') if minute_values_are_fresh else None
                ),
            )

    async def _request_with_retries(
        self,
        endpoint: str,
        params: dict[str, Any],
        fingerprint: str,
    ) -> dict[str, Any]:
        await self._hydrate_persisted_budget()
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            requested_at = datetime.now(timezone.utc)
            started = perf_counter()
            response: httpx.Response | None = None
            try:
                async with self._request_gate:
                    await self.rate_limit.acquire_request_slot()
                    await self._pace_request()
                    response = await self._client.get(endpoint, params=params)
                self.rate_limit.update_from_headers(response.headers)
            except RateLimitExhaustedError:
                raise
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                await self._emit_request_log(
                    endpoint=endpoint,
                    params=params,
                    requested_at=requested_at,
                    status_code=None,
                    results_count=0,
                    duration_ms=self._duration_ms(started),
                    error_message='network_error',
                    fingerprint=fingerprint,
                )
                if attempt < self._retry_policy.max_attempts:
                    await self._sleep(retry_delay(attempt, policy=self._retry_policy))
                    continue
                raise ApiFootballError('API-Football could not be reached.') from exc

            status_code = response.status_code
            parsed_payload: dict[str, Any] | None = None
            parse_error = False
            try:
                raw_payload = response.json()
                if not isinstance(raw_payload, dict):
                    raise ValueError('payload is not an object')
                envelope = ApiFootballEnvelope.model_validate(raw_payload)
                parsed_payload = envelope.model_dump(mode='json')
            except (ValueError, ValidationError):
                parse_error = True

            results_count = int(parsed_payload.get('results', 0)) if parsed_payload else 0
            log_error = self._safe_error_code(status_code, parsed_payload, parse_error)
            await self._emit_request_log(
                endpoint=endpoint,
                params=params,
                requested_at=requested_at,
                status_code=status_code,
                results_count=results_count,
                duration_ms=self._duration_ms(started),
                error_message=log_error,
                fingerprint=fingerprint,
            )

            if is_retryable_status(status_code):
                if attempt < self._retry_policy.max_attempts:
                    await self._sleep(
                        retry_delay(
                            attempt,
                            policy=self._retry_policy,
                            retry_after=response.headers.get('Retry-After'),
                        )
                    )
                    continue
                if status_code == 429:
                    raise RateLimitExhaustedError('provider_429', self.rate_limit.snapshot)
                raise ApiFootballError(f'API-Football returned HTTP {status_code}.')
            if status_code in {401, 403}:
                raise ApiFootballConfigurationError('API-Football rejected the configured key.')
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ApiFootballError(f'API-Football returned HTTP {status_code}.') from exc
            if parse_error or parsed_payload is None:
                raise ApiFootballError('API-Football returned an invalid response.')

            self._raise_for_application_errors(endpoint, params, parsed_payload.get('errors'))
            snapshot = self.rate_limit.snapshot
            parsed_payload['_rate_limit'] = {
                'remaining_day': snapshot.daily_remaining,
                'limit_day': snapshot.daily_limit,
                'remaining_minute': snapshot.minute_remaining,
                'limit_minute': snapshot.minute_limit,
                'requests_this_run': snapshot.requests_this_run,
            }
            return parsed_payload

        raise ApiFootballError('API-Football retry loop ended unexpectedly.')

    def _raise_for_application_errors(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        errors: Any,
    ) -> None:
        if not _has_errors(errors):
            return
        keys, text = _error_parts(errors)
        if keys & {'auth', 'authentication', 'key', 'token'} or any(
            marker in text
            for marker in ('api key', 'authentication', 'invalid key', 'unauthorized')
        ):
            raise ApiFootballConfigurationError('API-Football reported an authentication error.')
        if keys & {'limit', 'quota', 'rate', 'rate_limit', 'requests'} or any(
            marker in text for marker in ('daily limit', 'rate limit', 'request limit', 'quota')
        ):
            raise RateLimitExhaustedError(
                'provider_429',
                self.rate_limit.snapshot,
            )
        if keys & {'access', 'plan'}:
            has_date_filter = any(key in params for key in ('date', 'from', 'to'))
            if endpoint == '/fixtures' and has_date_filter:
                raise ApiFootballDateAccessError(
                    'API-Football plan does not allow the requested fixture date.'
                )
            raise ApiFootballAccessRestrictionError(
                'API-Football plan does not allow the requested resource.'
            )
        raise ApiFootballError('API-Football reported an application error.')

    @staticmethod
    def _safe_error_code(
        status_code: int,
        payload: dict[str, Any] | None,
        parse_error: bool,
    ) -> str | None:
        if parse_error:
            return 'invalid_json'
        if status_code >= 400:
            return f'http_{status_code}'
        if payload is not None and _has_errors(payload.get('errors')):
            return 'provider_application_error'
        return None

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1000))

    async def _emit_request_log(
        self,
        *,
        endpoint: str,
        params: Mapping[str, Any],
        requested_at: datetime,
        status_code: int | None,
        results_count: int,
        duration_ms: int,
        error_message: str | None,
        fingerprint: str,
    ) -> None:
        if self._request_log_sink is None:
            return
        snapshot = self.rate_limit.snapshot
        record = ApiRequestLogRecord(
            endpoint=endpoint,
            parameters_json=sanitize_for_logging(dict(params)),
            requested_at=requested_at,
            response_status=status_code,
            results_count=results_count,
            daily_limit=snapshot.daily_limit,
            daily_remaining=snapshot.daily_remaining,
            minute_limit=snapshot.minute_limit,
            minute_remaining=snapshot.minute_remaining,
            duration_ms=duration_ms,
            error_message=error_message,
            request_hash=fingerprint,
        )
        try:
            if callable(self._request_log_sink):
                result = self._request_log_sink(record)
            else:
                method = getattr(self._request_log_sink, 'log_api_request')
                result = method(record)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                'api_football_request_log_sink_failed',
                extra={'endpoint': endpoint, 'request_hash': fingerprint},
            )


__all__ = [
    'ApiFootballClient',
    'ApiRequestLogSink',
    'RateLimitExhaustedError',
    'RateLimitManager',
]

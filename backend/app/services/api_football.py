from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import httpx
from app.core.config import get_settings
from app.core.errors import (
    ProviderAccessRestrictionError,
    ProviderConfigurationError,
    ProviderDateAccessError,
    ProviderError,
    ProviderRateLimitError,
)


class ApiFootballError(ProviderError):
    pass


class ApiFootballConfigurationError(ProviderConfigurationError):
    pass


class ApiFootballRateLimitError(ProviderRateLimitError):
    pass


class ApiFootballAccessRestrictionError(ProviderAccessRestrictionError):
    pass


class ApiFootballDateAccessError(ProviderDateAccessError):
    pass


def _error_parts(errors: Any) -> tuple[set[str], str]:
    """Normalize error keys/text without retaining or exposing the payload."""

    if isinstance(errors, Mapping):
        keys = {str(key).strip().lower() for key in errors}
        text = ' '.join(str(value) for value in errors.values()).lower()
        return keys, text
    return set(), str(errors).lower()


def _is_authentication_error(keys: set[str], text: str) -> bool:
    authentication_keys = {'auth', 'authentication', 'key', 'token'}
    authentication_markers = (
        'api key',
        'application key',
        'authentication',
        'invalid key',
        'invalid token',
        'missing key',
        'missing token',
        'unauthorized',
    )
    return bool(keys & authentication_keys) or any(marker in text for marker in authentication_markers)


def _is_rate_limit_error(keys: set[str], text: str) -> bool:
    rate_keys = {'limit', 'quota', 'rate', 'rate_limit', 'requests'}
    rate_markers = ('daily limit', 'rate limit', 'request limit', 'quota')
    return bool(keys & rate_keys) or any(marker in text for marker in rate_markers)


class ApiFootballClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._daily_limit: int | None = None
        self._daily_remaining: int | None = None
        self._client = httpx.AsyncClient(
            base_url=self.settings.api_football_base_url,
            headers={'x-apisports-key': self.settings.require_api_football_key()},
            timeout=25.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if (
            self._daily_limit is not None
            and self._daily_remaining is not None
            and self._daily_limit - self._daily_remaining >= self.settings.api_daily_soft_limit
        ):
            raise ApiFootballRateLimitError('The configured daily soft limit has been reached.')
        try:
            response = await self._client.get(endpoint, params=params)
        except httpx.TimeoutException as exc:
            raise ApiFootballError('API-Football timed out.') from exc
        except httpx.RequestError as exc:
            raise ApiFootballError('API-Football could not be reached.') from exc

        if response.status_code == 429:
            raise ApiFootballRateLimitError('API-Football rate limit reached.')
        if response.status_code in {401, 403}:
            raise ApiFootballConfigurationError('API-Football rejected the configured key.')
        if response.status_code >= 500:
            raise ApiFootballError(f'API-Football returned HTTP {response.status_code}.')
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise ApiFootballError('API-Football returned an invalid response.') from exc

        try:
            daily_limit = response.headers.get('x-ratelimit-requests-limit')
            daily_remaining = response.headers.get('x-ratelimit-requests-remaining')
            if daily_limit is not None and daily_remaining is not None:
                self._daily_limit = int(daily_limit)
                self._daily_remaining = int(daily_remaining)
        except ValueError:
            self._daily_limit = None
            self._daily_remaining = None
        if not isinstance(payload, dict):
            raise ApiFootballError('API-Football returned a non-object response.')

        errors = payload.get('errors')
        if errors:
            error_keys, error_text = _error_parts(errors)
            if _is_authentication_error(error_keys, error_text):
                raise ApiFootballConfigurationError('API-Football reported an authentication error.')
            if _is_rate_limit_error(error_keys, error_text):
                raise ApiFootballRateLimitError('API-Football reported a quota error.')
            if error_keys & {'access', 'plan'}:
                if endpoint == '/fixtures' and bool(params.get('date')):
                    raise ApiFootballDateAccessError(
                        'API-Football plan does not allow the requested fixture date.'
                    )
                raise ApiFootballAccessRestrictionError(
                    'API-Football plan does not allow the requested resource.'
                )
            raise ApiFootballError('API-Football reported an application error.')
        payload['_rate_limit'] = {
            'remaining_day': response.headers.get('x-ratelimit-requests-remaining'),
            'limit_day': response.headers.get('x-ratelimit-requests-limit'),
            'remaining_minute': response.headers.get('X-RateLimit-Remaining'),
        }
        return payload

    async def status(self) -> dict[str, Any]:
        return await self._get('/status', {})

    async def fixture(self, fixture_id: int) -> dict[str, Any]:
        return await self._get('/fixtures', {'id': fixture_id})

    async def fixtures_by_date(self, date: str, timezone: str = 'America/Lima') -> dict[str, Any]:
        return await self._get('/fixtures', {'date': date, 'timezone': timezone})

    async def odds(self, fixture_id: int) -> dict[str, Any]:
        return await self._get('/odds', {'fixture': fixture_id})

    async def injuries(self, fixture_id: int) -> dict[str, Any]:
        return await self._get('/injuries', {'fixture': fixture_id})

    async def lineups(self, fixture_id: int) -> dict[str, Any]:
        return await self._get('/fixtures/lineups', {'fixture': fixture_id})

    async def api_prediction(self, fixture_id: int) -> dict[str, Any]:
        return await self._get('/predictions', {'fixture': fixture_id})

    async def fixture_statistics(self, fixture_id: int) -> dict[str, Any]:
        return await self._get('/fixtures/statistics', {'fixture': fixture_id})

    async def fixture_players(self, fixture_id: int) -> dict[str, Any]:
        return await self._get('/fixtures/players', {'fixture': fixture_id})

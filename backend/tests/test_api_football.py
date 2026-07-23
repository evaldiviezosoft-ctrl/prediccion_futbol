import asyncio

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.services import api_football
from app.services.api_football import (
    ApiFootballAccessRestrictionError,
    ApiFootballClient,
    ApiFootballConfigurationError,
    ApiFootballDateAccessError,
    ApiFootballRateLimitError,
)


def test_client_rejects_missing_api_key_without_network_access(monkeypatch):
    monkeypatch.setattr(api_football, 'get_settings', lambda: Settings(_env_file=None))

    with pytest.raises(ConfigurationError):
        ApiFootballClient()


def test_http_429_is_mapped_to_provider_rate_limit(monkeypatch):
    settings = Settings(_env_file=None, api_football_key='valid_api_key_0123456789')
    monkeypatch.setattr(api_football, 'get_settings', lambda: settings)
    client = ApiFootballClient()

    async def exercise():
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url=settings.api_football_base_url,
            transport=httpx.MockTransport(lambda _request: httpx.Response(429, json={})),
        )
        try:
            with pytest.raises(ApiFootballRateLimitError):
                await client.fixture(123)
        finally:
            await client.close()

    asyncio.run(exercise())


def test_fixture_date_plan_error_has_specific_safe_classification(monkeypatch):
    settings = Settings(_env_file=None, api_football_key='valid_api_key_0123456789')
    monkeypatch.setattr(api_football, 'get_settings', lambda: settings)
    client = ApiFootballClient()
    provider_detail = 'Free plan cannot read 2099-08-24; internal account reference 98765.'

    async def exercise():
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url=settings.api_football_base_url,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={'errors': {'plan': provider_detail}})
            ),
        )
        try:
            with pytest.raises(ApiFootballDateAccessError) as captured:
                await client.fixtures_by_date('2099-08-24', 'UTC')
            assert provider_detail not in str(captured.value)
            assert captured.value.code == 'provider_date_access_restricted'
        finally:
            await client.close()

    asyncio.run(exercise())


def test_optional_endpoint_access_error_is_not_misclassified_as_date_error(monkeypatch):
    settings = Settings(_env_file=None, api_football_key='valid_api_key_0123456789')
    monkeypatch.setattr(api_football, 'get_settings', lambda: settings)
    client = ApiFootballClient()

    async def exercise():
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url=settings.api_football_base_url,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={'errors': {'access': 'This resource is not included in this plan.'}},
                )
            ),
        )
        try:
            with pytest.raises(ApiFootballAccessRestrictionError) as captured:
                await client.odds(123)
            assert not isinstance(captured.value, ApiFootballDateAccessError)
        finally:
            await client.close()

    asyncio.run(exercise())


def test_invalid_key_takes_priority_over_access_classification(monkeypatch):
    settings = Settings(_env_file=None, api_football_key='invalid_api_key_0123456789')
    monkeypatch.setattr(api_football, 'get_settings', lambda: settings)
    client = ApiFootballClient()

    async def exercise():
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url=settings.api_football_base_url,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={'errors': {'access': 'Invalid API key for this account.'}},
                )
            ),
        )
        try:
            with pytest.raises(ApiFootballConfigurationError):
                await client.fixtures_by_date('2099-08-22', 'UTC')
        finally:
            await client.close()

    asyncio.run(exercise())

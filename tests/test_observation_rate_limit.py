from unittest.mock import AsyncMock, MagicMock

import pytest

from chatgpt_web2api.api_server import APIServer
from chatgpt_web2api.backend_client import BackendClient
from chatgpt_web2api.cdp_driver import ObservationRateLimitError
from chatgpt_web2api.resilience import retry_on_rate_limit


def make_driver():
    driver = MagicMock()
    driver._access_token = "token"
    driver._token_fetched_at = 0
    driver.ensure_token = AsyncMock(return_value="token")
    driver._js_with_data_strict = AsyncMock(
        return_value='{"__status": 429}'
    )
    driver._breakers = None
    return driver


@pytest.mark.asyncio
async def test_projection_429_is_typed_observation_rate_limit():
    driver = make_driver()
    client = BackendClient(driver)

    with pytest.raises(ObservationRateLimitError):
        await client._fetch_recent_conversation_projection("conv")


@pytest.mark.asyncio
async def test_turn_observation_429_does_not_degrade_to_fetch_failed():
    driver = make_driver()
    client = BackendClient(driver)
    anchor = MagicMock()

    with pytest.raises(ObservationRateLimitError):
        await client._fetch_end_turn_for_turn(
            "conv",
            anchor,
            had_non_text_content=False,
        )


@pytest.mark.asyncio
async def test_send_retry_helper_never_retries_observation_rate_limit():
    driver = MagicMock()
    driver.dismiss_rate_limit = AsyncMock()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        raise ObservationRateLimitError(retry_after=60)

    with pytest.raises(ObservationRateLimitError):
        await retry_on_rate_limit(
            driver,
            operation,
            max_attempts=3,
        )

    assert calls == 1
    driver.dismiss_rate_limit.assert_not_awaited()


def test_rest_maps_observation_rate_limit_to_429():
    server = APIServer.__new__(APIServer)
    response = server._error_response(
        ObservationRateLimitError(retry_after=60)
    )

    assert response.status == 429
    assert response.headers["Retry-After"] == "60"

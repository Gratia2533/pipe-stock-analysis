from types import SimpleNamespace

import pytest

from finance_mcp.action_client import create_action_client
from finance_mcp.direct_action import DirectActionClient
from finance_mcp.open_connector import OpenConnectorClient


def _settings(**overrides: object) -> object:
    values: dict[str, object] = {
        "data_backend": "direct",
        "finmind_token": "",
        "finnhub_api_key": "",
        "open_connector_base_url": "http://connector.test",
        "open_connector_runtime_token": "",
        "upstream_max_response_bytes": 1024,
        "request_timeout_seconds": 1.0,
        "request_max_attempts": 1,
        "request_retry_base_seconds": 0.0,
        "request_max_concurrency": 1,
        "cache_ttl_seconds": 0.0,
        "cache_max_entries": 8,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_factory_defaults_to_direct_client_without_credentials() -> None:
    client = create_action_client(_settings())  # type: ignore[arg-type]

    assert isinstance(client, DirectActionClient)


@pytest.mark.asyncio
async def test_factory_preserves_openconnector_backend() -> None:
    client = create_action_client(
        _settings(data_backend="openconnector", open_connector_runtime_token="runtime-secret")  # type: ignore[arg-type]
    )

    assert isinstance(client, OpenConnectorClient)
    await client.aclose()


def test_openconnector_backend_requires_runtime_token() -> None:
    with pytest.raises(ValueError, match="OPEN_CONNECTOR_RUNTIME_TOKEN is required"):
        create_action_client(_settings(data_backend="openconnector"))  # type: ignore[arg-type]

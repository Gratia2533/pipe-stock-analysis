import pytest

from finance_mcp.config import Settings
from finance_mcp.server import health, mcp


def test_default_settings_use_direct_backend_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATA_BACKEND", raising=False)
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_CONNECTOR_RUNTIME_TOKEN", raising=False)

    settings = Settings.from_env()

    assert settings.data_backend == "direct"
    assert settings.finmind_token == ""
    assert settings.finnhub_api_key == ""
    assert settings.finance_oauth_enabled is False
    assert settings.finance_oauth_issuer_url == ""
    assert settings.finance_oauth_resource_url == ""
    assert settings.open_connector_base_url == "http://127.0.0.1:8001"
    assert settings.open_connector_runtime_token == ""
    assert settings.upstream_max_response_bytes == 4 * 1024 * 1024
    assert settings.mcp_port == 8010


def test_invalid_data_backend_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_BACKEND", "automatic")

    with pytest.raises(ValueError, match="DATA_BACKEND must be 'direct' or 'openconnector'"):
        Settings.from_env()


def test_open_connector_runtime_token_is_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_CONNECTOR_RUNTIME_TOKEN", "test-runtime-token")

    assert Settings.from_env().open_connector_runtime_token == "test-runtime-token"


@pytest.mark.parametrize("credential_name", ["FINMIND_TOKEN", "FINNHUB_API_KEY"])
def test_openconnector_backend_rejects_direct_credentials(
    monkeypatch: pytest.MonkeyPatch,
    credential_name: str,
) -> None:
    monkeypatch.setenv("DATA_BACKEND", "openconnector")
    monkeypatch.setenv(credential_name, "must-not-enter-openconnector-mode")

    with pytest.raises(ValueError, match="Direct provider credentials must be unset"):
        Settings.from_env()


def test_legacy_openconnector_response_limit_remains_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UPSTREAM_MAX_RESPONSE_BYTES", raising=False)
    monkeypatch.setenv("OPEN_CONNECTOR_MAX_RESPONSE_BYTES", "2048")

    assert Settings.from_env().upstream_max_response_bytes == 2048


def test_health_is_read_only() -> None:
    assert health() == {"status": "ok", "service": "finance-mcp", "mode": "read-only"}


@pytest.mark.asyncio
async def test_market_breadth_tools_are_registered() -> None:
    tools = {tool.name for tool in await mcp.list_tools()}
    assert {
        "get_taiwan_stock_market_indices",
        "get_taiwan_stock_etf_rankings",
        "get_taiwan_stock_new_listings",
        "get_taiwan_stock_market_calendar",
    } <= tools


@pytest.mark.asyncio
async def test_global_stock_tools_are_registered() -> None:
    tools = {tool.name for tool in await mcp.list_tools()}
    assert {
        "search_global_stock_symbols",
        "get_global_stock_quote",
        "get_global_stock_prices",
        "get_global_stock_profile",
        "get_global_stock_basic_financials",
        "get_global_stock_financial_reports",
        "get_global_stock_news",
    } <= tools

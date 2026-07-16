import pytest

from finance_mcp.config import Settings
from finance_mcp.server import health, mcp


def test_default_settings_disable_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_CONNECTOR_RUNTIME_TOKEN", raising=False)
    settings = Settings.from_env()

    assert settings.finance_oauth_enabled is False
    assert settings.finance_oauth_issuer_url == ""
    assert settings.finance_oauth_resource_url == ""
    assert settings.open_connector_base_url == "http://127.0.0.1:8001"
    assert settings.open_connector_runtime_token == ""
    assert settings.open_connector_max_response_bytes == 4 * 1024 * 1024
    assert settings.mcp_port == 8010


def test_open_connector_runtime_token_is_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_CONNECTOR_RUNTIME_TOKEN", "test-runtime-token")

    assert Settings.from_env().open_connector_runtime_token == "test-runtime-token"


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

import pytest

from finance_mcp.config import Settings
from finance_mcp.server import health, mcp


def test_default_settings_disable_oauth() -> None:
    settings = Settings.from_env()

    assert settings.finance_oauth_enabled is False
    assert settings.finance_oauth_issuer_url == ""
    assert settings.finance_oauth_resource_url == ""


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

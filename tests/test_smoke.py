from finance_mcp.config import Settings
from finance_mcp.server import health


def test_default_settings_disable_oauth() -> None:
    settings = Settings.from_env()

    assert settings.finance_oauth_enabled is False
    assert settings.finance_oauth_issuer_url == ""
    assert settings.finance_oauth_resource_url == ""


def test_health_is_read_only() -> None:
    assert health() == {"status": "ok", "service": "finance-mcp", "mode": "read-only"}

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, cast

McpTransport = Literal["stdio", "streamable-http"]


def _get_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _get_positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _get_non_negative_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _get_transport() -> McpTransport:
    value = os.getenv("MCP_TRANSPORT", "stdio")
    if value not in {"stdio", "streamable-http"}:
        raise ValueError("MCP_TRANSPORT must be 'stdio' or 'streamable-http'")
    return cast(McpTransport, value)


@dataclass(frozen=True, slots=True)
class Settings:
    finmind_base_url: str
    finmind_token: str | None
    twse_stock_day_url: str
    twse_daily_all_url: str
    twse_market_index_url: str
    twse_etf_ranking_url: str
    twse_new_listing_url: str
    twse_holiday_schedule_url: str
    tpex_daily_close_url: str
    twse_material_info_url: str
    tpex_material_info_url: str
    google_news_rss_url: str
    yahoo_tw_stock_base_url: str
    request_timeout_seconds: float
    request_max_attempts: int
    request_retry_base_seconds: float
    request_max_concurrency: int
    cache_ttl_seconds: float
    cache_max_entries: int
    mcp_transport: McpTransport
    mcp_host: str
    mcp_port: int
    mcp_streamable_http_path: str
    mcp_stateless_http: bool
    finance_oauth_enabled: bool
    finance_oauth_issuer_url: str
    finance_oauth_resource_url: str
    finance_oauth_username: str
    finance_oauth_password: str
    finance_oauth_database_path: str
    finance_oauth_scope: str
    finance_oauth_access_token_ttl_seconds: int
    finance_oauth_refresh_token_ttl_seconds: int

    @classmethod
    def from_env(cls) -> Settings:
        timeout = _get_non_negative_float("REQUEST_TIMEOUT_SECONDS", 15.0)
        if timeout == 0:
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be greater than zero")
        return cls(
            finmind_base_url=os.getenv(
                "FINMIND_BASE_URL",
                "https://api.finmindtrade.com/api/v4/data",
            ),
            finmind_token=os.getenv("FINMIND_TOKEN"),
            twse_stock_day_url=os.getenv(
                "TWSE_STOCK_DAY_URL",
                "https://www.twse.com.tw/exchangeReport/STOCK_DAY",
            ),
            twse_daily_all_url=os.getenv(
                "TWSE_DAILY_ALL_URL",
                "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            ),
            twse_market_index_url=os.getenv(
                "TWSE_MARKET_INDEX_URL",
                "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX",
            ),
            twse_etf_ranking_url=os.getenv(
                "TWSE_ETF_RANKING_URL",
                "https://openapi.twse.com.tw/v1/ETFReport/ETFRank",
            ),
            twse_new_listing_url=os.getenv(
                "TWSE_NEW_LISTING_URL",
                "https://openapi.twse.com.tw/v1/company/newlisting",
            ),
            twse_holiday_schedule_url=os.getenv(
                "TWSE_HOLIDAY_SCHEDULE_URL",
                "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
            ),
            tpex_daily_close_url=os.getenv(
                "TPEX_DAILY_CLOSE_URL",
                "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            ),
            twse_material_info_url=os.getenv(
                "TWSE_MATERIAL_INFO_URL",
                "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
            ),
            tpex_material_info_url=os.getenv(
                "TPEX_MATERIAL_INFO_URL",
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O",
            ),
            google_news_rss_url=os.getenv(
                "GOOGLE_NEWS_RSS_URL",
                "https://news.google.com/rss/search",
            ),
            yahoo_tw_stock_base_url=os.getenv(
                "YAHOO_TW_STOCK_BASE_URL",
                "https://tw.stock.yahoo.com",
            ),
            request_timeout_seconds=timeout,
            request_max_attempts=_get_positive_int("REQUEST_MAX_ATTEMPTS", 3),
            request_retry_base_seconds=_get_non_negative_float(
                "REQUEST_RETRY_BASE_SECONDS",
                0.5,
            ),
            request_max_concurrency=_get_positive_int("REQUEST_MAX_CONCURRENCY", 8),
            cache_ttl_seconds=_get_non_negative_float("CACHE_TTL_SECONDS", 300.0),
            cache_max_entries=_get_positive_int("CACHE_MAX_ENTRIES", 256),
            mcp_transport=_get_transport(),
            mcp_host=os.getenv("MCP_HOST", "127.0.0.1"),
            mcp_port=int(os.getenv("MCP_PORT", "8000")),
            mcp_streamable_http_path=os.getenv("MCP_STREAMABLE_HTTP_PATH", "/mcp"),
            mcp_stateless_http=_get_bool("MCP_STATELESS_HTTP", True),
            finance_oauth_enabled=_get_bool("FINANCE_OAUTH_ENABLED", False),
            finance_oauth_issuer_url=os.getenv("FINANCE_OAUTH_ISSUER_URL", ""),
            finance_oauth_resource_url=os.getenv("FINANCE_OAUTH_RESOURCE_URL", ""),
            finance_oauth_username=os.getenv("FINANCE_OAUTH_USERNAME", ""),
            finance_oauth_password=os.getenv("FINANCE_OAUTH_PASSWORD", ""),
            finance_oauth_database_path=os.getenv(
                "FINANCE_OAUTH_DATABASE_PATH",
                "/data/oauth/state.sqlite3",
            ),
            finance_oauth_scope=os.getenv("FINANCE_OAUTH_SCOPE", "finance:read"),
            finance_oauth_access_token_ttl_seconds=_get_positive_int(
                "FINANCE_OAUTH_ACCESS_TOKEN_TTL_SECONDS",
                3600,
            ),
            finance_oauth_refresh_token_ttl_seconds=_get_positive_int(
                "FINANCE_OAUTH_REFRESH_TOKEN_TTL_SECONDS",
                2592000,
            ),
        )


settings = Settings.from_env()

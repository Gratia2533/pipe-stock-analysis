from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Literal

import httpx
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from finance_mcp.analytics.chip import summarize_institutional_flows
from finance_mcp.analytics.financial_health import summarize_financial_health
from finance_mcp.analytics.fundamental import summarize_monthly_revenue, summarize_valuation
from finance_mcp.analytics.margin import summarize_margin_trading
from finance_mcp.analytics.technical import summarize_prices
from finance_mcp.config import settings
from finance_mcp.infra.logging import configure_logging, log_context
from finance_mcp.oauth import FinanceOAuthProvider
from finance_mcp.providers.announcements import MaterialAnnouncementClient
from finance_mcp.providers.finmind import FinMindClient
from finance_mcp.providers.finnhub import (
    CandleResolution,
    FinancialFrequency,
    FinancialStatement,
    FinnhubClient,
)
from finance_mcp.providers.news import NewsSource, TaiwanStockNewsClient
from finance_mcp.providers.tpex import TpexClient
from finance_mcp.providers.twse import TwseClient

PriceSource = Literal["auto", "finmind", "twse"]
OfficialMarket = Literal["auto", "twse", "tpex"]

configure_logging()
logger = logging.getLogger("finance_mcp.server")

def _create_mcp() -> tuple[FastMCP, FinanceOAuthProvider | None]:
    common_options = {
        "host": settings.mcp_host,
        "port": settings.mcp_port,
        "streamable_http_path": settings.mcp_streamable_http_path,
        "stateless_http": settings.mcp_stateless_http,
    }
    if not settings.finance_oauth_enabled:
        return FastMCP("finance-mcp", **common_options), None
    if not settings.finance_oauth_issuer_url or not settings.finance_oauth_resource_url:
        raise ValueError(
            "FINANCE_OAUTH_ISSUER_URL and FINANCE_OAUTH_RESOURCE_URL are required when "
            "FINANCE_OAUTH_ENABLED=true"
        )

    provider = FinanceOAuthProvider(
        issuer_url=settings.finance_oauth_issuer_url,
        resource_url=settings.finance_oauth_resource_url,
        username=settings.finance_oauth_username,
        password=settings.finance_oauth_password,
        database_path=settings.finance_oauth_database_path,
        scope=settings.finance_oauth_scope,
        access_token_ttl_seconds=settings.finance_oauth_access_token_ttl_seconds,
        refresh_token_ttl_seconds=settings.finance_oauth_refresh_token_ttl_seconds,
    )
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(settings.finance_oauth_issuer_url),
        resource_server_url=AnyHttpUrl(settings.finance_oauth_resource_url),
        required_scopes=[settings.finance_oauth_scope],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[settings.finance_oauth_scope],
            default_scopes=[settings.finance_oauth_scope],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    return (
        FastMCP(
            "finance-mcp",
            auth_server_provider=provider,
            auth=auth,
            **common_options,
        ),
        provider,
    )


mcp, oauth_provider = _create_mcp()
client = FinMindClient()
finnhub_client = FinnhubClient(
    base_url=settings.finnhub_base_url,
    api_key=settings.finnhub_api_key,
    timeout_seconds=settings.request_timeout_seconds,
    max_attempts=settings.request_max_attempts,
    retry_base_seconds=settings.request_retry_base_seconds,
    max_concurrency=settings.request_max_concurrency,
    cache_ttl_seconds=settings.cache_ttl_seconds,
    cache_max_entries=settings.cache_max_entries,
)
twse_client = TwseClient()
tpex_client = TpexClient()
announcement_client = MaterialAnnouncementClient()
news_client = TaiwanStockNewsClient()


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(_: Request) -> JSONResponse:
    """Return a lightweight HTTP health response for containers and tunnels."""
    return JSONResponse({"status": "ok", "service": "finance-mcp"})


if oauth_provider is not None:

    @mcp.custom_route("/login", methods=["GET"], include_in_schema=False)
    async def oauth_login(request: Request) -> Response:
        state = request.query_params.get("state")
        if not state:
            return JSONResponse({"error": "missing_state"}, status_code=400)
        return await oauth_provider.get_login_page(state)

    @mcp.custom_route("/login/callback", methods=["POST"], include_in_schema=False)
    async def oauth_login_callback(request: Request) -> Response:
        return await oauth_provider.handle_login_callback(request)


def _parse_date_range(start_date: str, end_date: str | None) -> tuple[date, date | None]:
    parsed_start = date.fromisoformat(start_date)
    parsed_end = date.fromisoformat(end_date) if end_date else None
    if parsed_end is not None and parsed_end < parsed_start:
        raise ValueError("end_date must not be earlier than start_date")
    return parsed_start, parsed_end


def _tag_price_rows(
    rows: list[dict[str, object]],
    source: str,
) -> list[dict[str, object]]:
    return [{**row, "source": source} for row in rows]


async def _fetch_price_rows(
    stock_id: str,
    start_date: date,
    end_date: date | None,
    source: PriceSource,
) -> tuple[list[dict[str, object]], str]:
    if source == "finmind":
        rows = await client.fetch_stock_prices(stock_id, start_date, end_date)
        return _tag_price_rows(rows, "FinMind/TaiwanStockPrice"), "FinMind/TaiwanStockPrice"
    if source == "twse":
        rows = await twse_client.fetch_stock_prices(stock_id, start_date, end_date)
        return _tag_price_rows(rows, "TWSE/STOCK_DAY"), "TWSE/STOCK_DAY"

    try:
        rows = await client.fetch_stock_prices(stock_id, start_date, end_date)
        if rows:
            return (
                _tag_price_rows(rows, "FinMind/TaiwanStockPrice"),
                "FinMind/TaiwanStockPrice",
            )
        fallback_reason = "empty_result"
    except (httpx.HTTPError, RuntimeError) as exc:
        fallback_reason = type(exc).__name__

    logger.warning(
        "price_provider_fallback",
        extra=log_context(
            stock_id=stock_id,
            primary_provider="finmind",
            fallback_provider="twse",
            reason=fallback_reason,
        ),
    )
    rows = await twse_client.fetch_stock_prices(stock_id, start_date, end_date)
    return _tag_price_rows(rows, "TWSE/STOCK_DAY"), "TWSE/STOCK_DAY"


async def _fetch_official_quote(
    stock_id: str,
    market: OfficialMarket,
) -> dict[str, object]:
    if market == "twse":
        quote = await twse_client.fetch_latest_quote(stock_id)
        if quote is None:
            raise ValueError(f"stock_id={stock_id} was not found on TWSE")
        return quote
    if market == "tpex":
        quote = await tpex_client.fetch_latest_quote(stock_id)
        if quote is None:
            raise ValueError(f"stock_id={stock_id} was not found on TPEx")
        return quote

    try:
        quote = await twse_client.fetch_latest_quote(stock_id)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning(
            "official_quote_provider_failed",
            extra=log_context(
                stock_id=stock_id,
                provider="twse",
                error_type=type(exc).__name__,
            ),
        )
    else:
        if quote is not None:
            return quote

    quote = await tpex_client.fetch_latest_quote(stock_id)
    if quote is not None:
        return quote
    raise ValueError(f"stock_id={stock_id} was not found on TWSE or TPEx")


@mcp.tool()
def health() -> dict[str, str]:
    """Return the Finance MCP service status."""
    return {"status": "ok", "service": "finance-mcp", "mode": "read-only"}


@mcp.tool()
async def search_global_stock_symbols(query: str, limit: int = 10) -> list[dict[str, object]]:
    """Search global stock symbols through Finnhub; availability depends on the API plan."""
    if not query.strip():
        raise ValueError("query must not be empty")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    return await finnhub_client.search_symbols(query.strip(), limit)


@mcp.tool()
async def get_global_stock_quote(symbol: str) -> dict[str, object]:
    """Get a current global stock quote from Finnhub."""
    return await finnhub_client.fetch_quote(symbol.strip().upper())


@mcp.tool()
async def get_global_stock_prices(
    symbol: str,
    start_date: str,
    end_date: str,
    resolution: CandleResolution = "D",
) -> dict[str, object]:
    """Get global stock candles from Finnhub; endpoint access depends on the API plan."""
    parsed_start, parsed_end = _parse_date_range(start_date, end_date)
    if parsed_end is None:
        raise ValueError("end_date is required")
    return await finnhub_client.fetch_candles(
        symbol.strip().upper(), parsed_start, parsed_end, resolution
    )


@mcp.tool()
async def get_global_stock_profile(symbol: str) -> dict[str, object]:
    """Get company profile data for a global stock symbol from Finnhub."""
    return await finnhub_client.fetch_profile(symbol.strip().upper())


@mcp.tool()
async def get_global_stock_basic_financials(
    symbol: str, metric: str = "all"
) -> dict[str, object]:
    """Get Finnhub basic financial metrics for a global stock symbol."""
    return await finnhub_client.fetch_basic_financials(symbol.strip().upper(), metric)


@mcp.tool()
async def get_global_stock_financial_reports(
    symbol: str,
    statement: FinancialStatement = "bs",
    frequency: FinancialFrequency = "annual",
) -> dict[str, object]:
    """Get Finnhub standardized financial statements for a global stock symbol."""
    return await finnhub_client.fetch_financial_reports(
        symbol.strip().upper(), statement, frequency
    )


@mcp.tool()
async def get_global_stock_news(
    symbol: str, start_date: str, end_date: str, limit: int = 10
) -> list[dict[str, object]]:
    """Get dated company news from Finnhub, limited to at most 20 rows."""
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    parsed_start, parsed_end = _parse_date_range(start_date, end_date)
    if parsed_end is None:
        raise ValueError("end_date is required")
    return await finnhub_client.fetch_company_news(
        symbol.strip().upper(), parsed_start, parsed_end, limit
    )


@mcp.tool()
async def get_taiwan_stock_prices(
    stock_id: str,
    start_date: str,
    end_date: str | None = None,
    source: PriceSource = "auto",
) -> list[dict[str, object]]:
    """Get Taiwan stock prices with optional FinMind-to-TWSE fallback."""
    parsed_start, parsed_end = _parse_date_range(start_date, end_date)
    rows, _ = await _fetch_price_rows(stock_id, parsed_start, parsed_end, source)
    return rows


@mcp.tool()
async def get_taiwan_stock_official_quote(
    stock_id: str,
    market: OfficialMarket = "auto",
) -> dict[str, object]:
    """Get the latest official TWSE or TPEx close quote for a Taiwan security."""
    return await _fetch_official_quote(stock_id, market)


@mcp.tool()
async def get_taiwan_stock_market_indices(
    query: str | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Get official TWSE market and sector indices, optionally filtered by name."""
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    rows = await twse_client.fetch_market_indices()
    if query and query.strip():
        normalized_query = query.strip().casefold()
        rows = [
            row
            for row in rows
            if normalized_query in str(row.get("name", "")).casefold()
        ]
    return rows[:limit]


@mcp.tool()
async def get_taiwan_stock_etf_rankings(
    query: str | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Get official TWSE ETF rankings by number of trading accounts."""
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    rows = await twse_client.fetch_etf_rankings()
    if query and query.strip():
        normalized_query = query.strip().casefold()
        rows = [
            row
            for row in rows
            if normalized_query in str(row.get("etf_id", "")).casefold()
            or normalized_query in str(row.get("name", "")).casefold()
        ]
    return rows[:limit]


@mcp.tool()
async def get_taiwan_stock_new_listings(
    query: str | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Get the official TWSE listing pipeline; rows may be pending rather than completed IPOs."""
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    rows = await twse_client.fetch_new_listings()
    if query and query.strip():
        normalized_query = query.strip().casefold()
        searchable_fields = ("stock_id", "company_name", "underwriter", "note")
        rows = [
            row
            for row in rows
            if any(
                normalized_query in str(row.get(field, "")).casefold()
                for field in searchable_fields
            )
        ]
    return rows[:limit]


@mcp.tool()
async def get_taiwan_stock_market_calendar(
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    """Get official TWSE holidays and special trading-day events for the published year."""
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    if parsed_start and parsed_end and parsed_end < parsed_start:
        raise ValueError("end_date must not be earlier than start_date")

    rows = await twse_client.fetch_holiday_schedule()
    filtered: list[dict[str, object]] = []
    for row in rows:
        row_date = date.fromisoformat(str(row["date"]))
        if parsed_start and row_date < parsed_start:
            continue
        if parsed_end and row_date > parsed_end:
            continue
        filtered.append(row)
    return filtered[:limit]


@mcp.tool()
async def get_taiwan_stock_material_announcements(
    stock_id: str,
    market: OfficialMarket = "auto",
    include_details: bool = False,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Get current MOPS announcements; announcement_date is the release date, not event date."""
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    rows = await announcement_client.fetch_announcements(stock_id, market)
    selected = rows[:limit]
    if include_details:
        return selected
    return [
        {
            key: value
            for key, value in row.items()
            if key != "details"
        }
        | {"details_available": bool(row.get("details"))}
        for row in selected
    ]


@mcp.tool()
async def get_taiwan_stock_news(
    stock_id: str,
    limit: int = 10,
    source: NewsSource = "auto",
) -> list[dict[str, object]]:
    """Get up to 10 Taiwan-stock news items from Google News RSS with Yahoo fallback."""
    if limit < 1 or limit > 10:
        raise ValueError("limit must be between 1 and 10")
    quote = await _fetch_official_quote(stock_id, "auto")
    company_name = str(quote["name"])
    market = str(quote["market"])
    if market not in {"TWSE", "TPEx"}:
        raise RuntimeError(f"unsupported market returned for stock_id={stock_id}: {market}")
    return await news_client.fetch_news(
        stock_id,
        company_name,
        market,
        limit=limit,
        source=source,
    )


@mcp.tool()
async def get_taiwan_stock_valuation(
    stock_id: str,
    start_date: str,
    end_date: str | None = None,
) -> list[dict[str, object]]:
    """Get historical PER, PBR and dividend-yield data for a Taiwan stock."""
    parsed_start, parsed_end = _parse_date_range(start_date, end_date)
    return await client.fetch_stock_valuation(stock_id, parsed_start, parsed_end)


@mcp.tool()
async def get_taiwan_stock_monthly_revenue(
    stock_id: str,
    start_date: str,
    end_date: str | None = None,
) -> list[dict[str, object]]:
    """Get historical monthly revenue for a Taiwan company."""
    parsed_start, parsed_end = _parse_date_range(start_date, end_date)
    return await client.fetch_monthly_revenue(stock_id, parsed_start, parsed_end)


@mcp.tool()
async def get_taiwan_stock_institutional_flows(
    stock_id: str,
    start_date: str,
    end_date: str | None = None,
) -> list[dict[str, object]]:
    """Get institutional investor buy and sell volumes for a Taiwan stock."""
    parsed_start, parsed_end = _parse_date_range(start_date, end_date)
    return await client.fetch_institutional_flows(stock_id, parsed_start, parsed_end)


@mcp.tool()
async def get_taiwan_stock_financial_reports(
    stock_id: str,
    start_date: str,
    end_date: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    """Get income statement, balance sheet and cash-flow rows for a Taiwan company."""
    parsed_start, parsed_end = _parse_date_range(start_date, end_date)
    income_rows, balance_rows, cash_flow_rows = await asyncio.gather(
        client.fetch_financial_statements(stock_id, parsed_start, parsed_end),
        client.fetch_balance_sheet(stock_id, parsed_start, parsed_end),
        client.fetch_cash_flow_statement(stock_id, parsed_start, parsed_end),
    )
    return {
        "income_statement": income_rows,
        "balance_sheet": balance_rows,
        "cash_flow_statement": cash_flow_rows,
    }


@mcp.tool()
async def get_taiwan_stock_margin_trading(
    stock_id: str,
    start_date: str,
    end_date: str | None = None,
) -> list[dict[str, object]]:
    """Get margin-purchase and short-sale balances for a Taiwan stock."""
    parsed_start, parsed_end = _parse_date_range(start_date, end_date)
    return await client.fetch_margin_trading(stock_id, parsed_start, parsed_end)


@mcp.tool()
async def analyze_taiwan_stock_technical(
    stock_id: str,
    lookback_days: int = 120,
    source: PriceSource = "auto",
) -> dict[str, float | int | str]:
    """Calculate deterministic trend, momentum, return and volatility metrics."""
    if lookback_days < 15 or lookback_days > 730:
        raise ValueError("lookback_days must be between 15 and 730")

    end = date.today()
    start = end - timedelta(days=lookback_days)
    rows, actual_source = await _fetch_price_rows(stock_id, start, end, source)
    if not rows:
        raise ValueError(f"no price data found for stock_id={stock_id}")

    result = summarize_prices(rows)
    result["stock_id"] = stock_id
    result["source"] = actual_source
    return result


@mcp.tool()
async def analyze_taiwan_stock_fundamental(
    stock_id: str,
    valuation_lookback_days: int = 365,
    revenue_lookback_months: int = 24,
) -> dict[str, object]:
    """Summarize valuation and monthly-revenue growth without investment recommendations."""
    if valuation_lookback_days < 30 or valuation_lookback_days > 3650:
        raise ValueError("valuation_lookback_days must be between 30 and 3650")
    if revenue_lookback_months < 2 or revenue_lookback_months > 120:
        raise ValueError("revenue_lookback_months must be between 2 and 120")

    end = date.today()
    valuation_start = end - timedelta(days=valuation_lookback_days)
    revenue_start = end - timedelta(days=revenue_lookback_months * 31)
    valuation_rows, revenue_rows = await asyncio.gather(
        client.fetch_stock_valuation(stock_id, valuation_start, end),
        client.fetch_monthly_revenue(stock_id, revenue_start, end),
    )
    if not valuation_rows:
        raise ValueError(f"no valuation data found for stock_id={stock_id}")
    if not revenue_rows:
        raise ValueError(f"no monthly revenue data found for stock_id={stock_id}")

    return {
        "stock_id": stock_id,
        "source": "FinMind",
        "valuation": summarize_valuation(valuation_rows),
        "monthly_revenue": summarize_monthly_revenue(revenue_rows),
    }


@mcp.tool()
async def analyze_taiwan_stock_financial_health(
    stock_id: str,
    lookback_years: int = 3,
) -> dict[str, object]:
    """Summarize reported profitability, balance-sheet strength and cash generation."""
    if lookback_years < 2 or lookback_years > 10:
        raise ValueError("lookback_years must be between 2 and 10")

    end = date.today()
    start = end - timedelta(days=lookback_years * 366)
    income_rows, balance_rows, cash_flow_rows = await asyncio.gather(
        client.fetch_financial_statements(stock_id, start, end),
        client.fetch_balance_sheet(stock_id, start, end),
        client.fetch_cash_flow_statement(stock_id, start, end),
    )
    result = summarize_financial_health(income_rows, balance_rows, cash_flow_rows)
    result["stock_id"] = stock_id
    result["source"] = "FinMind financial statements"
    return result


@mcp.tool()
async def analyze_taiwan_stock_margin_trading(
    stock_id: str,
    lookback_days: int = 30,
) -> dict[str, object]:
    """Summarize changes in margin-purchase and short-sale balances."""
    if lookback_days < 1 or lookback_days > 365:
        raise ValueError("lookback_days must be between 1 and 365")

    end = date.today()
    start = end - timedelta(days=lookback_days)
    rows = await client.fetch_margin_trading(stock_id, start, end)
    if not rows:
        raise ValueError(f"no margin trading data found for stock_id={stock_id}")

    result = summarize_margin_trading(rows)
    result["stock_id"] = stock_id
    result["source"] = "FinMind/TaiwanStockMarginPurchaseShortSale"
    return result


@mcp.tool()
async def analyze_taiwan_stock_institutional_flows(
    stock_id: str,
    lookback_days: int = 30,
) -> dict[str, object]:
    """Aggregate institutional investor buy, sell and net volumes by investor type."""
    if lookback_days < 1 or lookback_days > 365:
        raise ValueError("lookback_days must be between 1 and 365")

    end = date.today()
    start = end - timedelta(days=lookback_days)
    rows = await client.fetch_institutional_flows(stock_id, start, end)
    if not rows:
        raise ValueError(f"no institutional flow data found for stock_id={stock_id}")

    result = summarize_institutional_flows(rows)
    result["stock_id"] = stock_id
    result["source"] = "FinMind/TaiwanStockInstitutionalInvestorsBuySell"
    return result


def main() -> None:
    mcp.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    main()

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

import httpx

from finance_mcp.infra.http import JsonHttpClient

FinancialStatement = Literal["bs", "ic", "cf"]
FinancialFrequency = Literal["annual", "quarterly"]
CandleResolution = Literal["1", "5", "15", "30", "60", "D", "W", "M"]


class FinnhubClient:
    """Read-only client for the Finnhub REST API."""

    def __init__(
        self,
        *,
        base_url: str = "https://finnhub.io/api/v1",
        api_key: str | None = None,
        timeout_seconds: float = 15,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.5,
        max_concurrency: int = 8,
        cache_ttl_seconds: float = 300,
        cache_max_entries: int = 256,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = JsonHttpClient(
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            max_concurrency=max_concurrency,
            cache_ttl_seconds=cache_ttl_seconds,
            cache_max_entries=cache_max_entries,
            transport=transport,
        )

    def with_required_api_key(self, api_key: str | None = None) -> FinnhubClient:
        if not (api_key if api_key is not None else self._api_key):
            raise RuntimeError("FINNHUB_API_KEY is required for global-stock tools")
        return self

    async def _get(self, endpoint: str, params: dict[str, str], dataset: str):
        self.with_required_api_key()
        return await self._http.get_json(
            f"{self._base_url}/{endpoint}",
            params={**params, "token": str(self._api_key)},
            provider="Finnhub",
            dataset=dataset,
        )

    async def search_symbols(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        payload = await self._get("search", {"q": query}, "symbol_search")
        return list(payload.get("result", []))[:limit]

    async def fetch_quote(self, symbol: str) -> dict[str, object]:
        return await self._get("quote", {"symbol": symbol}, "quote")

    async def fetch_profile(self, symbol: str) -> dict[str, object]:
        return await self._get("stock/profile2", {"symbol": symbol}, "company_profile")

    async def fetch_basic_financials(self, symbol: str, metric: str = "all") -> dict[str, object]:
        return await self._get(
            "stock/metric", {"symbol": symbol, "metric": metric}, "basic_financials"
        )

    async def fetch_financial_reports(
        self,
        symbol: str,
        statement: FinancialStatement = "bs",
        frequency: FinancialFrequency = "annual",
    ) -> dict[str, object]:
        return await self._get(
            "stock/financials-reported",
            {"symbol": symbol, "statement": statement, "freq": frequency},
            "financial_reports",
        )

    async def fetch_candles(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        resolution: CandleResolution = "D",
    ) -> dict[str, object]:
        start_ts = int(datetime.combine(start_date, datetime.min.time(), UTC).timestamp())
        end_ts = int(datetime.combine(end_date, datetime.max.time(), UTC).timestamp())
        return await self._get(
            "stock/candle",
            {
                "symbol": symbol,
                "resolution": resolution,
                "from": str(start_ts),
                "to": str(end_ts),
            },
            "stock_candles",
        )

    async def fetch_company_news(
        self, symbol: str, start_date: date, end_date: date, limit: int = 10
    ) -> list[dict[str, object]]:
        payload = await self._get(
            "company-news",
            {"symbol": symbol, "from": start_date.isoformat(), "to": end_date.isoformat()},
            "company_news",
        )
        return list(payload)[:limit]

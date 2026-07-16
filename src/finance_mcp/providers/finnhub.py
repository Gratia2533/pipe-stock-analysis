from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

FinancialStatement = Literal["bs", "ic", "cf"]
FinancialFrequency = Literal["annual", "quarterly"]
CandleResolution = Literal["1", "5", "15", "30", "60", "D", "W", "M"]


class ActionClient(Protocol):
    async def call(self, action_id: str, action_input: dict[str, Any]) -> Any: ...


class FinnhubClient:
    """Read-only Finnhub client routed through curated OpenConnector actions."""

    def __init__(self, *, connector: ActionClient) -> None:
        self._connector = connector

    async def search_symbols(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        payload = await self._connector.call("finnhub.search_symbols", {"query": query})
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
            raise RuntimeError("Finnhub symbol search returned an invalid response")
        rows = payload["result"]
        if any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("Finnhub symbol search returned invalid rows")
        return rows[:limit]

    async def fetch_quote(self, symbol: str) -> dict[str, object]:
        return self._require_object(
            await self._connector.call("finnhub.get_quote", {"symbol": symbol}),
            "quote",
        )

    async def fetch_profile(self, symbol: str) -> dict[str, object]:
        return self._require_object(
            await self._connector.call("finnhub.get_company_profile", {"symbol": symbol}),
            "company profile",
        )

    async def fetch_basic_financials(self, symbol: str, metric: str = "all") -> dict[str, object]:
        return self._require_object(
            await self._connector.call(
                "finnhub.get_basic_financials",
                {"symbol": symbol, "metric": metric},
            ),
            "basic financials",
        )

    async def fetch_financial_reports(
        self,
        symbol: str,
        statement: FinancialStatement = "bs",
        frequency: FinancialFrequency = "annual",
    ) -> dict[str, object]:
        return self._require_object(
            await self._connector.call(
                "finnhub.get_financial_reports",
                {"symbol": symbol, "statement": statement, "frequency": frequency},
            ),
            "financial reports",
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
        return self._require_object(
            await self._connector.call(
                "finnhub.get_stock_candles",
                {
                    "symbol": symbol,
                    "resolution": resolution,
                    "from": start_ts,
                    "to": end_ts,
                },
            ),
            "stock candles",
        )

    async def fetch_company_news(
        self, symbol: str, start_date: date, end_date: date, limit: int = 10
    ) -> list[dict[str, object]]:
        payload = await self._connector.call(
            "finnhub.get_company_news",
            {
                "symbol": symbol,
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise RuntimeError("Finnhub company news returned an invalid response")
        return payload[:limit]

    @staticmethod
    def _require_object(payload: Any, operation: str) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise RuntimeError(f"Finnhub {operation} returned an invalid response")
        return payload

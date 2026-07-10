from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from finance_mcp.config import settings
from finance_mcp.infra.http import JsonHttpClient


class FinMindClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
    ) -> None:
        self._base_url = base_url or settings.finmind_base_url
        self._token = token if token is not None else settings.finmind_token
        self._http = JsonHttpClient(
            timeout_seconds=timeout or settings.request_timeout_seconds,
            max_attempts=max_attempts or settings.request_max_attempts,
            retry_base_seconds=(
                settings.request_retry_base_seconds
                if retry_base_seconds is None
                else retry_base_seconds
            ),
            max_concurrency=settings.request_max_concurrency,
            cache_ttl_seconds=(
                settings.cache_ttl_seconds if cache_ttl_seconds is None else cache_ttl_seconds
            ),
            cache_max_entries=settings.cache_max_entries,
            transport=transport,
        )

    async def fetch_dataset(
        self,
        dataset: str,
        *,
        stock_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {"dataset": dataset}
        if stock_id is not None:
            params["data_id"] = stock_id
        if start_date is not None:
            params["start_date"] = start_date.isoformat()
        if end_date is not None:
            params["end_date"] = end_date.isoformat()

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        payload = await self._http.get_json(
            self._base_url,
            params=params,
            headers=headers,
            cache_predicate=lambda value: isinstance(value, dict)
            and value.get("status") == 200,
            provider="finmind",
            dataset=dataset,
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"FinMind dataset {dataset} returned an invalid response")
        if payload.get("status") != 200:
            raise RuntimeError(payload.get("msg") or f"FinMind dataset {dataset} request failed")

        data = payload.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError(f"FinMind dataset {dataset} returned an invalid data payload")
        return data

    async def fetch_stock_prices(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockPrice",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

    async def fetch_stock_valuation(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockPER",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

    async def fetch_monthly_revenue(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockMonthRevenue",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

    async def fetch_institutional_flows(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockInstitutionalInvestorsBuySell",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

    async def fetch_financial_statements(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockFinancialStatements",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

    async def fetch_balance_sheet(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockBalanceSheet",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

    async def fetch_cash_flow_statement(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockCashFlowsStatement",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

    async def fetch_margin_trading(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockMarginPurchaseShortSale",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

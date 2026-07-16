from __future__ import annotations

from datetime import date
from typing import Any

from finance_mcp.action_client import ActionClient

_DATASET_ACTIONS = {
    "TaiwanStockPrice": "finmind.get_stock_prices",
    "TaiwanStockPER": "finmind.get_stock_valuation",
    "TaiwanStockMonthRevenue": "finmind.get_monthly_revenue",
    "TaiwanStockInstitutionalInvestorsBuySell": "finmind.get_institutional_flows",
    "TaiwanStockFinancialStatements": "finmind.get_financial_statements",
    "TaiwanStockBalanceSheet": "finmind.get_balance_sheet",
    "TaiwanStockCashFlowsStatement": "finmind.get_cash_flow_statement",
    "TaiwanStockMarginPurchaseShortSale": "finmind.get_margin_trading",
}


class FinMindClient:
    def __init__(self, *, connector: ActionClient) -> None:
        self._connector = connector

    async def fetch_dataset(
        self,
        dataset: str,
        *,
        stock_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        action_id = _DATASET_ACTIONS.get(dataset)
        if action_id is None:
            raise ValueError(f"unsupported FinMind dataset: {dataset}")
        if stock_id is None or start_date is None:
            raise ValueError("stock_id and start_date are required")

        action_input: dict[str, Any] = {
            "stockId": stock_id,
            "startDate": start_date.isoformat(),
        }
        if end_date is not None:
            action_input["endDate"] = end_date.isoformat()
        payload = await self._connector.call(action_id, action_input)
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise RuntimeError(f"FinMind dataset {dataset} returned an invalid data payload")
        return payload

    async def fetch_stock_prices(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockPrice", stock_id=stock_id, start_date=start_date, end_date=end_date
        )

    async def fetch_stock_valuation(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return await self.fetch_dataset(
            "TaiwanStockPER", stock_id=stock_id, start_date=start_date, end_date=end_date
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

from datetime import date
from typing import Any

import pytest

from finance_mcp.providers.finnhub import FinnhubClient


class StubConnector:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, action_id: str, action_input: dict[str, Any]) -> Any:
        self.calls.append((action_id, action_input))
        return self.result


@pytest.mark.asyncio
async def test_search_symbols_calls_curated_action_and_limits_results() -> None:
    connector = StubConnector({"count": 2, "result": [{"symbol": "AAPL"}, {"symbol": "APLE"}]})
    client = FinnhubClient(connector=connector)

    rows = await client.search_symbols("Apple", limit=1)

    assert rows == [{"symbol": "AAPL"}]
    assert connector.calls == [("finnhub.search_symbols", {"query": "Apple"})]


@pytest.mark.asyncio
async def test_company_news_maps_dates_to_curated_action() -> None:
    connector = StubConnector([{"headline": "one"}, {"headline": "two"}])
    client = FinnhubClient(connector=connector)

    rows = await client.fetch_company_news("AAPL", date(2026, 7, 1), date(2026, 7, 12), limit=1)

    assert rows == [{"headline": "one"}]
    assert connector.calls == [
        (
            "finnhub.get_company_news",
            {"symbol": "AAPL", "startDate": "2026-07-01", "endDate": "2026-07-12"},
        )
    ]

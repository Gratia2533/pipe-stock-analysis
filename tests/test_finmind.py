from datetime import date
from typing import Any

import pytest

from finance_mcp.providers.finmind import FinMindClient


class StubConnector:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, action_id: str, action_input: dict[str, Any]) -> Any:
        self.calls.append((action_id, action_input))
        return self.result


@pytest.mark.asyncio
async def test_fetch_stock_prices_calls_curated_open_connector_action() -> None:
    connector = StubConnector([{"stock_id": "2330"}])
    client = FinMindClient(connector=connector)

    result = await client.fetch_stock_prices(
        "2330",
        date(2026, 7, 1),
        date(2026, 7, 15),
    )

    assert result == [{"stock_id": "2330"}]
    assert connector.calls == [
        (
            "finmind.get_stock_prices",
            {"stockId": "2330", "startDate": "2026-07-01", "endDate": "2026-07-15"},
        )
    ]

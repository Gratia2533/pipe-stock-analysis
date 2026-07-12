from datetime import date

import httpx
import pytest

from finance_mcp.providers.finnhub import FinnhubClient


def make_client(handler) -> FinnhubClient:
    return FinnhubClient(
        base_url="https://finnhub.test/api/v1",
        api_key="secret",
        timeout_seconds=1,
        max_attempts=1,
        retry_base_seconds=0,
        max_concurrency=1,
        cache_ttl_seconds=0,
        cache_max_entries=8,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_search_symbols_sends_token_and_limits_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search"
        assert request.url.params["q"] == "Apple"
        assert request.url.params["token"] == "secret"
        return httpx.Response(
            200,
            json={"count": 2, "result": [{"symbol": "AAPL"}, {"symbol": "APLE"}]},
        )

    rows = await make_client(handler).search_symbols("Apple", limit=1)

    assert rows == [{"symbol": "AAPL"}]


@pytest.mark.asyncio
async def test_company_news_converts_dates_to_finnhub_parameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/company-news"
        assert request.url.params["symbol"] == "AAPL"
        assert request.url.params["from"] == "2026-07-01"
        assert request.url.params["to"] == "2026-07-12"
        return httpx.Response(200, json=[{"headline": "one"}, {"headline": "two"}])

    rows = await make_client(handler).fetch_company_news(
        "AAPL", date(2026, 7, 1), date(2026, 7, 12), limit=1
    )

    assert rows == [{"headline": "one"}]


def test_missing_api_key_is_rejected() -> None:
    client = FinnhubClient(api_key=None)

    with pytest.raises(RuntimeError, match="FINNHUB_API_KEY"):
        client.with_required_api_key()

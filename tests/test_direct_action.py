import httpx
import pytest

from finance_mcp.direct_action import DirectActionClient


@pytest.mark.asyncio
async def test_finmind_action_calls_fixed_dataset_endpoint_without_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "api.finmindtrade.com"
        assert request.url.path == "/api/v4/data"
        assert dict(request.url.params) == {
            "dataset": "TaiwanStockPrice",
            "data_id": "2330",
            "start_date": "2026-07-01",
            "end_date": "2026-07-15",
        }
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"status": 200, "data": [{"stock_id": "2330"}]})

    client = DirectActionClient(
        finmind_token="",
        finnhub_api_key="",
        timeout=1,
        max_attempts=1,
        retry_base_seconds=0,
        max_concurrency=1,
        cache_ttl_seconds=0,
        cache_max_entries=8,
        transport=httpx.MockTransport(handler),
    )

    result = await client.call(
        "finmind.get_stock_prices",
        {"stockId": "2330", "startDate": "2026-07-01", "endDate": "2026-07-15"},
    )

    assert result == [{"stock_id": "2330"}]
    await client.aclose()


@pytest.mark.parametrize(
    ("action_id", "dataset"),
    [
        ("finmind.get_stock_valuation", "TaiwanStockPER"),
        ("finmind.get_monthly_revenue", "TaiwanStockMonthRevenue"),
        ("finmind.get_institutional_flows", "TaiwanStockInstitutionalInvestorsBuySell"),
        ("finmind.get_financial_statements", "TaiwanStockFinancialStatements"),
        ("finmind.get_balance_sheet", "TaiwanStockBalanceSheet"),
        ("finmind.get_cash_flow_statement", "TaiwanStockCashFlowsStatement"),
        ("finmind.get_margin_trading", "TaiwanStockMarginPurchaseShortSale"),
    ],
)
@pytest.mark.asyncio
async def test_finmind_actions_map_to_fixed_datasets(action_id: str, dataset: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["dataset"] == dataset
        assert request.headers["authorization"] == "Bearer finmind-secret"
        return httpx.Response(200, json={"status": 200, "data": []})

    client = _client(finmind_token="finmind-secret", handler=handler)
    assert await client.call(action_id, {"stockId": "2330", "startDate": "2026-07-01"}) == []
    await client.aclose()


@pytest.mark.parametrize(
    ("action_id", "action_input", "path", "expected_query"),
    [
        ("finnhub.search_symbols", {"query": "Apple"}, "/api/v1/search", {"q": "Apple"}),
        ("finnhub.get_quote", {"symbol": "AAPL"}, "/api/v1/quote", {"symbol": "AAPL"}),
        (
            "finnhub.get_company_profile",
            {"symbol": "AAPL"},
            "/api/v1/stock/profile2",
            {"symbol": "AAPL"},
        ),
        (
            "finnhub.get_basic_financials",
            {"symbol": "AAPL", "metric": "all"},
            "/api/v1/stock/metric",
            {"symbol": "AAPL", "metric": "all"},
        ),
        (
            "finnhub.get_financial_reports",
            {"symbol": "AAPL", "statement": "bs", "frequency": "annual"},
            "/api/v1/stock/financials",
            {"symbol": "AAPL", "statement": "bs", "freq": "annual"},
        ),
        (
            "finnhub.get_stock_candles",
            {"symbol": "AAPL", "resolution": "D", "from": 100, "to": 200},
            "/api/v1/stock/candle",
            {"symbol": "AAPL", "resolution": "D", "from": "100", "to": "200"},
        ),
        (
            "finnhub.get_company_news",
            {"symbol": "AAPL", "startDate": "2026-07-01", "endDate": "2026-07-15"},
            "/api/v1/company-news",
            {"symbol": "AAPL", "from": "2026-07-01", "to": "2026-07-15"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_finnhub_actions_map_to_fixed_endpoints(
    action_id: str,
    action_input: dict[str, object],
    path: str,
    expected_query: dict[str, str],
) -> None:
    response_payload: object
    if action_id == "finnhub.search_symbols":
        response_payload = {"result": []}
    elif action_id == "finnhub.get_company_news":
        response_payload = []
    else:
        response_payload = {"ok": True}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        assert request.url.host == "finnhub.io"
        assert request.url.path == path
        assert request.url.params["token"] == "finnhub-secret"
        for key, value in expected_query.items():
            assert request.url.params[key] == value
        return httpx.Response(200, json=response_payload)

    client = _client(finnhub_api_key="finnhub-secret", handler=handler)
    assert await client.call(action_id, action_input) == response_payload
    await client.aclose()


@pytest.mark.asyncio
async def test_finnhub_action_requires_api_key_only_when_called() -> None:
    client = _client(handler=lambda _: pytest.fail("request should not be sent"))

    with pytest.raises(RuntimeError, match="FINNHUB_API_KEY is required"):
        await client.call("finnhub.get_quote", {"symbol": "AAPL"})
    await client.aclose()


@pytest.mark.asyncio
async def test_direct_action_rejects_unknown_action() -> None:
    client = _client(handler=lambda _: pytest.fail("request should not be sent"))

    with pytest.raises(ValueError, match="Direct action is not allowed"):
        await client.call("other.get_secret", {"notJson": object()})
    await client.aclose()


@pytest.mark.parametrize(
    ("action_id", "action_input", "message"),
    [
        (
            "finmind.get_stock_prices",
            {"stockId": "2330", "startDate": "2026/07/01"},
            "startDate must use YYYY-MM-DD format",
        ),
        (
            "finmind.get_stock_prices",
            {"stockId": "2330", "startDate": "2026-07-02", "endDate": "2026-07-01"},
            "endDate must not be earlier than startDate",
        ),
        (
            "finmind.get_stock_prices",
            {"stockId": "2330", "startDate": "2026-07-01", "extra": "value"},
            "unexpected fields: extra",
        ),
        (
            "finnhub.get_financial_reports",
            {"symbol": "AAPL", "statement": "bad", "frequency": "annual"},
            "statement must be one of",
        ),
        (
            "finnhub.get_financial_reports",
            {"symbol": "AAPL", "statement": "bs", "frequency": "monthly"},
            "frequency must be one of",
        ),
        (
            "finnhub.get_stock_candles",
            {"symbol": "AAPL", "resolution": "BAD", "from": 100, "to": 200},
            "resolution must be one of",
        ),
        (
            "finnhub.get_stock_candles",
            {"symbol": "AAPL", "resolution": "D", "from": -1, "to": 200},
            "from must not be negative",
        ),
        (
            "finnhub.get_stock_candles",
            {"symbol": "AAPL", "resolution": "D", "from": 200, "to": 100},
            "to must not be earlier than from",
        ),
        (
            "finnhub.get_company_news",
            {"symbol": "AAPL", "startDate": "2026-07-02", "endDate": "2026-07-01"},
            "endDate must not be earlier than startDate",
        ),
    ],
)
@pytest.mark.asyncio
async def test_direct_action_enforces_action_contract(
    action_id: str,
    action_input: dict[str, object],
    message: str,
) -> None:
    client = _client(
        finnhub_api_key="finnhub-secret",
        handler=lambda _: pytest.fail("request should not be sent"),
    )

    with pytest.raises(ValueError, match=message):
        await client.call(action_id, action_input)
    await client.aclose()


@pytest.mark.asyncio
async def test_direct_action_rejects_oversized_response() -> None:
    client = _client(
        finnhub_api_key="finnhub-secret",
        max_response_bytes=32,
        handler=lambda _: httpx.Response(200, content=b"x" * 33),
    )

    with pytest.raises(RuntimeError, match="response exceeded 32 bytes"):
        await client.call("finnhub.get_quote", {"symbol": "AAPL"})
    await client.aclose()


@pytest.mark.asyncio
async def test_direct_action_retries_retryable_status() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"status": 200, "data": []})

    client = _client(handler=handler, max_attempts=2)

    assert await client.call(
        "finmind.get_stock_prices", {"stockId": "2330", "startDate": "2026-07-01"}
    ) == []
    assert attempts == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_direct_action_caches_successful_response() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"status": 200, "data": [{"stock_id": "2330"}]})

    client = _client(handler=handler, cache_ttl_seconds=60)
    action_input = {"stockId": "2330", "startDate": "2026-07-01"}

    await client.call("finmind.get_stock_prices", action_input)
    await client.call("finmind.get_stock_prices", action_input)

    assert requests == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_finnhub_http_error_does_not_expose_api_key() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"error": "sensitive-finnhub-key"})

    client = _client(
        finnhub_api_key="sensitive-finnhub-key",
        cache_ttl_seconds=60,
        handler=handler,
    )

    for _ in range(2):
        with pytest.raises(RuntimeError) as captured:
            await client.call("finnhub.get_quote", {"symbol": "AAPL"})
        assert "sensitive-finnhub-key" not in str(captured.value)

    assert requests == 2
    await client.aclose()


def _client(
    *,
    handler: object,
    finmind_token: str = "",
    finnhub_api_key: str = "",
    max_response_bytes: int = 4 * 1024 * 1024,
    max_attempts: int = 1,
    cache_ttl_seconds: float = 0,
) -> DirectActionClient:
    return DirectActionClient(
        finmind_token=finmind_token,
        finnhub_api_key=finnhub_api_key,
        timeout=1,
        max_attempts=max_attempts,
        retry_base_seconds=0,
        max_concurrency=1,
        cache_ttl_seconds=cache_ttl_seconds,
        cache_max_entries=8,
        max_response_bytes=max_response_bytes,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )

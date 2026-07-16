import httpx
import pytest

from finance_mcp.open_connector import OpenConnectorClient


@pytest.mark.asyncio
async def test_call_sends_runtime_token_and_unwraps_success_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/actions/finmind.get_stock_prices"
        assert request.headers["authorization"] == "Bearer runtime-secret"
        assert request.read() == (b'{"input":{"stockId":"2330","startDate":"2026-07-01"}}')
        return httpx.Response(
            200,
            json={"success": True, "message": "OK", "data": [{"stock_id": "2330"}]},
        )

    client = OpenConnectorClient(
        base_url="http://connector.test",
        runtime_token="runtime-secret",
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
        {"stockId": "2330", "startDate": "2026-07-01"},
    )

    assert result == [{"stock_id": "2330"}]
    await client.aclose()


@pytest.mark.asyncio
async def test_call_rejects_oversized_responses() -> None:
    client = OpenConnectorClient(
        base_url="http://connector.test",
        runtime_token="runtime-secret",
        timeout=1,
        max_attempts=1,
        retry_base_seconds=0,
        max_concurrency=1,
        cache_ttl_seconds=0,
        cache_max_entries=8,
        max_response_bytes=32,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 33)),
    )

    with pytest.raises(RuntimeError, match="response exceeded 32 bytes"):
        await client.call("finnhub.get_quote", {"symbol": "AAPL"})
    await client.aclose()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"base_url": "ftp://connector.test"}, "base_url must use http or https"),
        ({"timeout": 0}, "timeout must be greater than zero"),
        ({"max_attempts": 0}, "max_attempts must be greater than zero"),
        ({"retry_base_seconds": -1}, "retry_base_seconds must not be negative"),
        ({"max_concurrency": 0}, "max_concurrency must be greater than zero"),
        ({"cache_ttl_seconds": -1}, "cache_ttl_seconds must not be negative"),
        ({"cache_max_entries": 0}, "cache_max_entries must be greater than zero"),
        ({"max_response_bytes": 0}, "max_response_bytes must be greater than zero"),
    ],
)
def test_constructor_rejects_invalid_limits(override: dict[str, object], message: str) -> None:
    kwargs: dict[str, object] = {
        "base_url": "http://connector.test",
        "runtime_token": "runtime-secret",
    }
    kwargs.update(override)

    with pytest.raises(ValueError, match=message):
        OpenConnectorClient(**kwargs)  # type: ignore[arg-type]

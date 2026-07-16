from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from finance_mcp.infra.cache import AsyncTTLCache

_ALLOWED_ACTIONS = frozenset(
    {
        "finmind.get_stock_prices",
        "finmind.get_stock_valuation",
        "finmind.get_monthly_revenue",
        "finmind.get_institutional_flows",
        "finmind.get_financial_statements",
        "finmind.get_balance_sheet",
        "finmind.get_cash_flow_statement",
        "finmind.get_margin_trading",
        "finnhub.search_symbols",
        "finnhub.get_quote",
        "finnhub.get_company_profile",
        "finnhub.get_basic_financials",
        "finnhub.get_financial_reports",
        "finnhub.get_stock_candles",
        "finnhub.get_company_news",
    }
)
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class OpenConnectorClient:
    """Narrow client for the curated finance actions exposed by OpenConnector."""

    def __init__(
        self,
        *,
        base_url: str,
        runtime_token: str,
        timeout: float = 15.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.5,
        max_concurrency: int = 8,
        cache_ttl_seconds: float = 300,
        cache_max_entries: int = 256,
        max_response_bytes: int = 4 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not runtime_token.strip():
            raise ValueError("OPEN_CONNECTOR_RUNTIME_TOKEN is required")
        parsed_base_url = urlsplit(base_url)
        if parsed_base_url.scheme not in {"http", "https"}:
            raise ValueError("base_url must use http or https")
        if not parsed_base_url.hostname or parsed_base_url.username or parsed_base_url.password:
            raise ValueError("base_url must contain a host and no userinfo")
        if parsed_base_url.query or parsed_base_url.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must not be negative")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must not be negative")
        if cache_max_entries <= 0:
            raise ValueError("cache_max_entries must be greater than zero")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than zero")
        self._base_url = base_url.rstrip("/")
        self._runtime_token = runtime_token
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._max_response_bytes = max_response_bytes
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._cache: AsyncTTLCache[Any] = AsyncTTLCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_entries,
        )

    async def call(self, action_id: str, action_input: dict[str, Any]) -> Any:
        if action_id not in _ALLOWED_ACTIONS:
            raise ValueError(f"OpenConnector action is not allowed: {action_id}")

        cache_key = f"{action_id}:{json.dumps(action_input, sort_keys=True, separators=(',', ':'))}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._semaphore:
            response_status: int | None = None
            response_is_success = False
            response_body = b""
            for attempt in range(1, self._max_attempts + 1):
                try:
                    async with self._client.stream(
                        "POST",
                        f"{self._base_url}/v1/actions/{action_id}",
                        headers={"Authorization": f"Bearer {self._runtime_token}"},
                        json={"input": action_input},
                    ) as response:
                        response_status = response.status_code
                        response_is_success = response.is_success
                        if (
                            response.status_code not in _RETRYABLE_STATUSES
                            or attempt >= self._max_attempts
                        ):
                            response_body = await _read_bounded_response(
                                response, self._max_response_bytes
                            )
                            break
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt >= self._max_attempts:
                        raise
                await asyncio.sleep(self._retry_base_seconds * (2 ** (attempt - 1)))

        if response_status is None:
            raise RuntimeError("OpenConnector request failed without a response")
        try:
            payload = json.loads(response_body)
        except ValueError as exc:
            raise RuntimeError(
                f"OpenConnector returned invalid JSON (HTTP {response_status})"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"OpenConnector returned an invalid response (HTTP {response_status})"
            )
        if response_is_success and payload.get("success") is True:
            data = payload.get("data")
            await self._cache.set(cache_key, data)
            return data

        code = payload.get("errorCode")
        normalized_code = code if isinstance(code, str) and code else f"http_{response_status}"
        message = payload.get("message")
        normalized_message = message if isinstance(message, str) and message else "request failed"
        safe_message = _sanitize_error_message(normalized_message, self._runtime_token)
        raise RuntimeError(f"{normalized_code}: {safe_message}")

    async def aclose(self) -> None:
        await self._client.aclose()


async def _read_bounded_response(response: httpx.Response, max_bytes: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise RuntimeError(f"OpenConnector response exceeded {max_bytes} bytes")
        except ValueError:
            pass

    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > max_bytes:
            raise RuntimeError(f"OpenConnector response exceeded {max_bytes} bytes")
        body.extend(chunk)
    return bytes(body)


def _sanitize_error_message(message: str, runtime_token: str) -> str:
    sanitized = message[:512].replace(runtime_token, "[REDACTED]")
    return re.sub(
        r"(?i)authorization\s*:\s*bearer\s+\S+",
        "Authorization: Bearer ***",
        sanitized,
    )

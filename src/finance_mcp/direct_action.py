from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import httpx

from finance_mcp.infra.cache import AsyncTTLCache

_FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
_FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_FINMIND_DATASETS = {
    "finmind.get_stock_prices": "TaiwanStockPrice",
    "finmind.get_stock_valuation": "TaiwanStockPER",
    "finmind.get_monthly_revenue": "TaiwanStockMonthRevenue",
    "finmind.get_institutional_flows": "TaiwanStockInstitutionalInvestorsBuySell",
    "finmind.get_financial_statements": "TaiwanStockFinancialStatements",
    "finmind.get_balance_sheet": "TaiwanStockBalanceSheet",
    "finmind.get_cash_flow_statement": "TaiwanStockCashFlowsStatement",
    "finmind.get_margin_trading": "TaiwanStockMarginPurchaseShortSale",
}
_FINNHUB_PATHS = {
    "finnhub.search_symbols": "/search",
    "finnhub.get_quote": "/quote",
    "finnhub.get_company_profile": "/stock/profile2",
    "finnhub.get_basic_financials": "/stock/metric",
    "finnhub.get_financial_reports": "/stock/financials",
    "finnhub.get_stock_candles": "/stock/candle",
    "finnhub.get_company_news": "/company-news",
}


class DirectActionClient:
    """Execute the curated finance action contract directly against fixed upstream APIs."""

    def __init__(
        self,
        *,
        finmind_token: str,
        finnhub_api_key: str,
        timeout: float = 15.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.5,
        max_concurrency: int = 8,
        cache_ttl_seconds: float = 300,
        cache_max_entries: int = 256,
        max_response_bytes: int = 4 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
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
        self._finmind_token = finmind_token.strip()
        self._finnhub_api_key = finnhub_api_key.strip()
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
        normalized_input = _normalize_action_input(action_id, action_input)
        cache_key = (
            f"{action_id}:{json.dumps(normalized_input, sort_keys=True, separators=(',', ':'))}"
        )
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        if action_id in _FINMIND_DATASETS:
            result = await self._call_finmind(action_id, normalized_input)
        else:
            result = await self._call_finnhub(action_id, normalized_input)

        await self._cache.set(cache_key, result)
        return result

    async def _call_finmind(self, action_id: str, action_input: dict[str, Any]) -> Any:
        params = {
            "dataset": _FINMIND_DATASETS[action_id],
            "data_id": _required_string(action_input, "stockId"),
            "start_date": _required_string(action_input, "startDate"),
        }
        end_date = _optional_string(action_input, "endDate")
        if end_date is not None:
            params["end_date"] = end_date
        headers = (
            {"Authorization": f"Bearer {self._finmind_token}"} if self._finmind_token else None
        )
        payload = await self._get_json(
            _FINMIND_URL,
            params=params,
            headers=headers,
            provider="FinMind",
        )
        if (
            not isinstance(payload, dict)
            or payload.get("status") != 200
            or not isinstance(payload.get("data"), list)
        ):
            raise RuntimeError("FinMind returned an invalid dataset response")
        return payload["data"]

    async def _call_finnhub(self, action_id: str, action_input: dict[str, Any]) -> Any:
        if not self._finnhub_api_key:
            raise RuntimeError("FINNHUB_API_KEY is required for Finnhub actions in direct mode")
        params = _finnhub_params(action_id, action_input)
        params["token"] = self._finnhub_api_key
        payload = await self._get_json(
            f"{_FINNHUB_BASE_URL}{_FINNHUB_PATHS[action_id]}",
            params=params,
            provider="Finnhub",
        )
        return _validate_finnhub_payload(action_id, payload)

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str],
        provider: str,
        headers: dict[str, str] | None = None,
    ) -> Any:
        async with self._semaphore:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    async with self._client.stream(
                        "GET", url, params=params, headers=headers
                    ) as response:
                        if (
                            response.status_code in _RETRYABLE_STATUSES
                            and attempt < self._max_attempts
                        ):
                            pass
                        else:
                            body = await _read_bounded_response(
                                response, self._max_response_bytes, provider
                            )
                            if not response.is_success:
                                raise RuntimeError(
                                    f"{provider} request failed (HTTP {response.status_code})"
                                )
                            try:
                                return json.loads(body)
                            except ValueError as exc:
                                raise RuntimeError(f"{provider} returned invalid JSON") from exc
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt >= self._max_attempts:
                        raise RuntimeError(f"{provider} request failed") from exc
                if self._retry_base_seconds > 0:
                    await asyncio.sleep(self._retry_base_seconds * (2 ** (attempt - 1)))
        raise RuntimeError(f"{provider} request failed")

    async def aclose(self) -> None:
        await self._client.aclose()


def _finnhub_params(action_id: str, action_input: dict[str, Any]) -> dict[str, str]:
    if action_id == "finnhub.search_symbols":
        return {"q": _required_string(action_input, "query")}
    if action_id in {"finnhub.get_quote", "finnhub.get_company_profile"}:
        return {"symbol": _required_string(action_input, "symbol")}
    if action_id == "finnhub.get_basic_financials":
        return {
            "symbol": _required_string(action_input, "symbol"),
            "metric": _optional_string(action_input, "metric") or "all",
        }
    if action_id == "finnhub.get_financial_reports":
        return {
            "symbol": _required_string(action_input, "symbol"),
            "statement": _required_string(action_input, "statement"),
            "freq": _required_string(action_input, "frequency"),
        }
    if action_id == "finnhub.get_stock_candles":
        return {
            "symbol": _required_string(action_input, "symbol"),
            "resolution": _required_string(action_input, "resolution"),
            "from": str(_required_integer(action_input, "from")),
            "to": str(_required_integer(action_input, "to")),
        }
    return {
        "symbol": _required_string(action_input, "symbol"),
        "from": _required_string(action_input, "startDate"),
        "to": _required_string(action_input, "endDate"),
    }


def _normalize_action_input(action_id: str, action_input: dict[str, Any]) -> dict[str, Any]:
    if action_id in _FINMIND_DATASETS:
        _reject_unexpected_fields(action_input, {"stockId", "startDate", "endDate"})
        start_date = _required_date(action_input, "startDate")
        normalized: dict[str, Any] = {
            "stockId": _required_string(action_input, "stockId"),
            "startDate": start_date,
        }
        end_date = _optional_date(action_input, "endDate")
        if end_date is not None:
            _validate_date_range(start_date, end_date)
            normalized["endDate"] = end_date
        return normalized

    if action_id not in _FINNHUB_PATHS:
        raise ValueError(f"Direct action is not allowed: {action_id}")

    if action_id == "finnhub.search_symbols":
        _reject_unexpected_fields(action_input, {"query"})
        return {"query": _required_string(action_input, "query")}

    if action_id in {"finnhub.get_quote", "finnhub.get_company_profile"}:
        _reject_unexpected_fields(action_input, {"symbol"})
        return {"symbol": _required_string(action_input, "symbol")}

    if action_id == "finnhub.get_basic_financials":
        _reject_unexpected_fields(action_input, {"symbol", "metric"})
        return {
            "symbol": _required_string(action_input, "symbol"),
            "metric": _optional_string(action_input, "metric") or "all",
        }

    if action_id == "finnhub.get_financial_reports":
        _reject_unexpected_fields(action_input, {"symbol", "statement", "frequency"})
        return {
            "symbol": _required_string(action_input, "symbol"),
            "statement": _required_enum(action_input, "statement", {"bs", "ic", "cf"}),
            "frequency": _required_enum(action_input, "frequency", {"annual", "quarterly"}),
        }

    if action_id == "finnhub.get_stock_candles":
        _reject_unexpected_fields(action_input, {"symbol", "resolution", "from", "to"})
        start = _required_non_negative_integer(action_input, "from")
        end = _required_non_negative_integer(action_input, "to")
        if end < start:
            raise ValueError("to must not be earlier than from")
        return {
            "symbol": _required_string(action_input, "symbol"),
            "resolution": _required_enum(
                action_input,
                "resolution",
                {"1", "5", "15", "30", "60", "D", "W", "M"},
            ),
            "from": start,
            "to": end,
        }

    _reject_unexpected_fields(action_input, {"symbol", "startDate", "endDate"})
    start_date = _required_date(action_input, "startDate")
    end_date = _required_date(action_input, "endDate")
    _validate_date_range(start_date, end_date)
    return {
        "symbol": _required_string(action_input, "symbol"),
        "startDate": start_date,
        "endDate": end_date,
    }


def _validate_finnhub_payload(action_id: str, payload: Any) -> Any:
    if isinstance(payload, dict) and "error" in payload:
        raise RuntimeError("Finnhub returned a provider error")
    if action_id == "finnhub.get_company_news":
        if not isinstance(payload, list):
            raise RuntimeError("Finnhub returned an invalid company-news response")
        return payload
    if not isinstance(payload, dict):
        raise RuntimeError("Finnhub returned an invalid response")
    if action_id == "finnhub.search_symbols" and not isinstance(payload.get("result"), list):
        raise RuntimeError("Finnhub returned an invalid symbol-search response")
    return payload


def _reject_unexpected_fields(action_input: dict[str, Any], allowed: set[str]) -> None:
    unexpected = sorted(set(action_input) - allowed)
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")


def _required_string(action_input: dict[str, Any], field: str) -> str:
    value = action_input.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_string(action_input: dict[str, Any], field: str) -> str | None:
    value = action_input.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _required_date(action_input: dict[str, Any], field: str) -> str:
    value = _required_string(action_input, field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must use YYYY-MM-DD format")
    return value


def _optional_date(action_input: dict[str, Any], field: str) -> str | None:
    if action_input.get(field) is None:
        return None
    return _required_date(action_input, field)


def _validate_date_range(start_date: str, end_date: str) -> None:
    if end_date < start_date:
        raise ValueError("endDate must not be earlier than startDate")


def _required_enum(action_input: dict[str, Any], field: str, allowed: set[str]) -> str:
    value = _required_string(action_input, field)
    if value not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return value


def _required_integer(action_input: dict[str, Any], field: str) -> int:
    value = action_input.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _required_non_negative_integer(action_input: dict[str, Any], field: str) -> int:
    value = _required_integer(action_input, field)
    if value < 0:
        raise ValueError(f"{field} must not be negative")
    return value


async def _read_bounded_response(
    response: httpx.Response,
    max_bytes: int,
    provider: str,
) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise RuntimeError(f"{provider} response exceeded {max_bytes} bytes")
        except ValueError:
            pass

    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > max_bytes:
            raise RuntimeError(f"{provider} response exceeded {max_bytes} bytes")
        body.extend(chunk)
    return bytes(body)

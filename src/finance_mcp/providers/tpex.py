from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from finance_mcp.config import settings
from finance_mcp.infra.http import JsonHttpClient


def _parse_compact_roc_date(value: str) -> str:
    if len(value) != 7:
        raise ValueError(f"invalid ROC date: {value}")
    return date(int(value[:3]) + 1911, int(value[3:5]), int(value[5:7])).isoformat()


def _parse_float(value: str) -> float | None:
    normalized = value.replace(",", "").replace("+", "").strip()
    if normalized in {"", "--", "---", "----"}:
        return None
    return float(normalized)


def _parse_int(value: str) -> int | None:
    parsed = _parse_float(value)
    return int(parsed) if parsed is not None else None


class TpexClient:
    """Official TPEx latest close quote client."""

    def __init__(
        self,
        *,
        daily_close_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
    ) -> None:
        self._daily_close_url = daily_close_url or settings.tpex_daily_close_url
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

    async def fetch_latest_quote(self, stock_id: str) -> dict[str, Any] | None:
        payload = await self._http.get_json(
            self._daily_close_url,
            params={},
            cache_predicate=lambda value: isinstance(value, list),
            provider="tpex",
            dataset="tpex_mainboard_daily_close_quotes",
        )
        if not isinstance(payload, list):
            raise RuntimeError("TPEx daily close quotes returned an invalid response")
        for row in payload:
            if isinstance(row, dict) and str(row.get("SecuritiesCompanyCode")) == stock_id:
                return self._normalize_quote(row)
        return None

    @staticmethod
    def _normalize_quote(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "date": _parse_compact_roc_date(str(row["Date"])),
            "stock_id": str(row["SecuritiesCompanyCode"]),
            "name": str(row["CompanyName"]),
            "market": "TPEx",
            "close": _parse_float(str(row["Close"])),
            "change": _parse_float(str(row["Change"])),
            "open": _parse_float(str(row["Open"])),
            "high": _parse_float(str(row["High"])),
            "low": _parse_float(str(row["Low"])),
            "average": _parse_float(str(row.get("Average", ""))),
            "volume": _parse_int(str(row["TradingShares"])),
            "trade_value": _parse_int(str(row["TransactionAmount"])),
            "transaction_count": _parse_int(str(row["TransactionNumber"])),
            "source": "TPEx/tpex_mainboard_daily_close_quotes",
        }

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from finance_mcp.config import settings
from finance_mcp.infra.http import JsonHttpClient


def _month_starts(start_date: date, end_date: date) -> list[date]:
    months: list[date] = []
    current = start_date.replace(day=1)
    final = end_date.replace(day=1)
    while current <= final:
        months.append(current)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def _parse_roc_date(value: str) -> str:
    year_text, month_text, day_text = value.split("/")
    return date(int(year_text) + 1911, int(month_text), int(day_text)).isoformat()


def _parse_compact_roc_date(value: str) -> str:
    if len(value) != 7:
        raise ValueError(f"invalid ROC date: {value}")
    return date(int(value[:3]) + 1911, int(value[3:5]), int(value[5:7])).isoformat()


def _parse_float(value: str) -> float | None:
    normalized = value.replace(",", "").replace("+", "").strip()
    if normalized in {"", "--", "---"}:
        return None
    return float(normalized)


def _parse_int(value: str) -> int | None:
    parsed = _parse_float(value)
    return int(parsed) if parsed is not None else None


class TwseClient:
    """Official TWSE listed-stock price client."""

    def __init__(
        self,
        *,
        stock_day_url: str | None = None,
        daily_all_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
    ) -> None:
        self._stock_day_url = stock_day_url or settings.twse_stock_day_url
        self._daily_all_url = daily_all_url or settings.twse_daily_all_url
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

    async def fetch_stock_prices(
        self,
        stock_id: str,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        resolved_end = end_date or date.today()
        if resolved_end < start_date:
            raise ValueError("end_date must not be earlier than start_date")
        months = _month_starts(start_date, resolved_end)
        if len(months) > 120:
            raise ValueError("TWSE price range must not exceed 120 months")

        rows: list[dict[str, Any]] = []
        for month in months:
            payload = await self._http.get_json(
                self._stock_day_url,
                params={
                    "response": "json",
                    "date": month.strftime("%Y%m01"),
                    "stockNo": stock_id,
                },
                cache_predicate=lambda value: isinstance(value, dict)
                and value.get("stat") == "OK",
                provider="twse",
                dataset=f"STOCK_DAY:{month:%Y-%m}",
            )
            if not isinstance(payload, dict):
                raise RuntimeError("TWSE STOCK_DAY returned an invalid response")
            if payload.get("stat") != "OK":
                continue
            data = payload.get("data", [])
            if not isinstance(data, list):
                raise RuntimeError("TWSE STOCK_DAY returned invalid data")
            for raw_row in data:
                if not isinstance(raw_row, list) or len(raw_row) < 9:
                    raise RuntimeError("TWSE STOCK_DAY returned an invalid row")
                normalized = self._normalize_history_row(stock_id, raw_row)
                row_date = date.fromisoformat(str(normalized["date"]))
                if normalized["close"] is not None and start_date <= row_date <= resolved_end:
                    rows.append(normalized)

        rows.sort(key=lambda row: str(row["date"]))
        return rows

    async def fetch_latest_quote(self, stock_id: str) -> dict[str, Any] | None:
        payload = await self._http.get_json(
            self._daily_all_url,
            params={},
            cache_predicate=lambda value: isinstance(value, list),
            provider="twse",
            dataset="STOCK_DAY_ALL",
        )
        if not isinstance(payload, list):
            raise RuntimeError("TWSE STOCK_DAY_ALL returned an invalid response")
        for row in payload:
            if isinstance(row, dict) and str(row.get("Code")) == stock_id:
                return self._normalize_latest_quote(row)
        return None

    @staticmethod
    def _normalize_history_row(stock_id: str, row: list[Any]) -> dict[str, Any]:
        values = [str(value) for value in row]
        return {
            "date": _parse_roc_date(values[0]),
            "stock_id": stock_id,
            "Trading_Volume": _parse_int(values[1]),
            "Trading_money": _parse_int(values[2]),
            "open": _parse_float(values[3]),
            "max": _parse_float(values[4]),
            "min": _parse_float(values[5]),
            "close": _parse_float(values[6]),
            "spread": _parse_float(values[7]),
            "Trading_turnover": _parse_int(values[8]),
            "source": "TWSE/STOCK_DAY",
        }

    @staticmethod
    def _normalize_latest_quote(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "date": _parse_compact_roc_date(str(row["Date"])),
            "stock_id": str(row["Code"]),
            "name": str(row["Name"]),
            "market": "TWSE",
            "close": _parse_float(str(row["ClosingPrice"])),
            "change": _parse_float(str(row["Change"])),
            "open": _parse_float(str(row["OpeningPrice"])),
            "high": _parse_float(str(row["HighestPrice"])),
            "low": _parse_float(str(row["LowestPrice"])),
            "volume": _parse_int(str(row["TradeVolume"])),
            "trade_value": _parse_int(str(row["TradeValue"])),
            "transaction_count": _parse_int(str(row["Transaction"])),
            "source": "TWSE/STOCK_DAY_ALL",
        }

from __future__ import annotations

import re
from datetime import date
from html import unescape
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


def _parse_optional_compact_roc_date(value: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) != 7 or not normalized.isdigit():
        return None
    try:
        return _parse_compact_roc_date(normalized)
    except ValueError:
        return None


def _clean_text(value: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(value)).strip()


def _parse_float(value: str) -> float | None:
    normalized = value.replace(",", "").replace("+", "").strip()
    if normalized in {"", "--", "---"}:
        return None
    return float(normalized)


def _parse_int(value: str) -> int | None:
    parsed = _parse_float(value)
    return int(parsed) if parsed is not None else None


def _parse_optional_float(value: str) -> float | None:
    try:
        return _parse_float(value)
    except ValueError:
        return None


def _parse_optional_int(value: str) -> int | None:
    parsed = _parse_optional_float(value)
    return int(parsed) if parsed is not None else None


class TwseClient:
    """Official TWSE listed-stock price client."""

    def __init__(
        self,
        *,
        stock_day_url: str | None = None,
        daily_all_url: str | None = None,
        market_index_url: str | None = None,
        etf_ranking_url: str | None = None,
        new_listing_url: str | None = None,
        holiday_schedule_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
    ) -> None:
        self._stock_day_url = stock_day_url or settings.twse_stock_day_url
        self._daily_all_url = daily_all_url or settings.twse_daily_all_url
        self._market_index_url = market_index_url or settings.twse_market_index_url
        self._etf_ranking_url = etf_ranking_url or settings.twse_etf_ranking_url
        self._new_listing_url = new_listing_url or settings.twse_new_listing_url
        self._holiday_schedule_url = holiday_schedule_url or settings.twse_holiday_schedule_url
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
                cache_predicate=lambda value: isinstance(value, dict) and value.get("stat") == "OK",
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

    async def fetch_market_indices(self) -> list[dict[str, Any]]:
        rows = await self._fetch_openapi_rows(
            self._market_index_url,
            dataset="MI_INDEX",
        )
        return [self._normalize_market_index(row) for row in rows]

    async def fetch_etf_rankings(self) -> list[dict[str, Any]]:
        rows = await self._fetch_openapi_rows(
            self._etf_ranking_url,
            dataset="ETFRank",
        )
        return [self._normalize_etf_ranking(row) for row in rows]

    async def fetch_new_listings(self) -> list[dict[str, Any]]:
        rows = await self._fetch_openapi_rows(
            self._new_listing_url,
            dataset="newlisting",
        )
        return [self._normalize_new_listing(row) for row in rows]

    async def fetch_holiday_schedule(self) -> list[dict[str, Any]]:
        rows = await self._fetch_openapi_rows(
            self._holiday_schedule_url,
            dataset="holidaySchedule",
        )
        normalized = [self._normalize_holiday_schedule(row) for row in rows]
        normalized.sort(key=lambda row: str(row["date"]))
        return normalized

    async def _fetch_openapi_rows(
        self,
        url: str,
        *,
        dataset: str,
    ) -> list[dict[str, Any]]:
        payload = await self._http.get_json(
            url,
            params={},
            cache_predicate=lambda value: isinstance(value, list),
            provider="twse",
            dataset=dataset,
        )
        if not isinstance(payload, list):
            raise RuntimeError(f"TWSE {dataset} returned an invalid response")
        if not all(isinstance(row, dict) for row in payload):
            raise RuntimeError(f"TWSE {dataset} returned an invalid row")
        return payload

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

    @staticmethod
    def _normalize_market_index(row: dict[str, Any]) -> dict[str, Any]:
        direction = str(row.get("漲跌", "")).strip()
        change_points = _parse_optional_float(str(row.get("漲跌點數", "")))
        if change_points is not None:
            if direction == "-":
                change_points = -abs(change_points)
            elif direction == "+":
                change_points = abs(change_points)
        return {
            "date": _parse_compact_roc_date(str(row["日期"])),
            "name": str(row["指數"]),
            "close": _parse_optional_float(str(row.get("收盤指數", ""))),
            "change_points": change_points,
            "change_percent": _parse_optional_float(str(row.get("漲跌百分比", ""))),
            "direction": direction or None,
            "special_note": str(row.get("特殊處理註記", "")).strip() or None,
            "source": "TWSE/MI_INDEX",
        }

    @staticmethod
    def _normalize_etf_ranking(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "rank": _parse_optional_int(str(row.get("No", ""))),
            "etf_id": str(row.get("ETFsSecurityCode", "")).strip(),
            "name": str(row.get("ETFsName", "")).strip(),
            "trading_account_count": _parse_optional_int(
                str(row.get("ETFsNumberofTradingAccounts", ""))
            ),
            "ranking_basis": "number_of_trading_accounts",
            "source": "TWSE/ETFRank",
        }

    @staticmethod
    def _normalize_new_listing(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "stock_id": str(row.get("Code", "")).strip(),
            "company_name": str(row.get("Company", "")).strip(),
            "application_date": _parse_optional_compact_roc_date(
                str(row.get("ApplicationDate", ""))
            ),
            "committee_date": _parse_optional_compact_roc_date(str(row.get("CommitteeDate", ""))),
            "approved_date": _parse_optional_compact_roc_date(str(row.get("ApprovedDate", ""))),
            "agreement_date": _parse_optional_compact_roc_date(str(row.get("AgreementDate", ""))),
            "listing_date": _parse_optional_compact_roc_date(str(row.get("ListingDate", ""))),
            "approved_listing_date": _parse_optional_compact_roc_date(
                str(row.get("ApprovedListingDate", ""))
            ),
            "chairman": str(row.get("Chairman", "")).strip() or None,
            "capital": _parse_optional_int(
                str(row.get("AmountofCapital ", row.get("AmountofCapital", "")))
            ),
            "underwriter": str(row.get("Underwriter", "")).strip() or None,
            "underwriting_price": _parse_optional_float(str(row.get("UnderwritingPrice", ""))),
            "note": str(row.get("Note", "")).strip() or None,
            "source": "TWSE/newlisting",
        }

    @staticmethod
    def _normalize_holiday_schedule(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "date": _parse_compact_roc_date(str(row["Date"])),
            "name": str(row.get("Name", "")).strip(),
            "weekday": str(row.get("Weekday", "")).strip() or None,
            "description": _clean_text(str(row.get("Description", ""))),
            "source": "TWSE/holidaySchedule",
        }

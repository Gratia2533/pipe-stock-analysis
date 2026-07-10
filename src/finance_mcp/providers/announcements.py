from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Literal

import httpx

from finance_mcp.config import settings
from finance_mcp.infra.http import JsonHttpClient
from finance_mcp.infra.logging import log_context

AnnouncementMarket = Literal["auto", "twse", "tpex"]
logger = logging.getLogger("finance_mcp.announcements")


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _parse_roc_date(value: object) -> str:
    normalized = str(value or "").strip()
    if "/" in normalized:
        year_text, month_text, day_text = normalized.split("/")
    elif len(normalized) == 7:
        year_text, month_text, day_text = normalized[:3], normalized[3:5], normalized[5:7]
    else:
        raise ValueError(f"invalid ROC date: {normalized}")
    return date(int(year_text) + 1911, int(month_text), int(day_text)).isoformat()


def _parse_time(value: object) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if not digits:
        return "00:00:00"
    padded = digits.zfill(6)[-6:]
    return f"{padded[:2]}:{padded[2:4]}:{padded[4:]}"


class MaterialAnnouncementClient:
    """Official TWSE/TPEx daily material-information client."""

    def __init__(
        self,
        *,
        twse_url: str | None = None,
        tpex_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
    ) -> None:
        self._twse_url = twse_url or settings.twse_material_info_url
        self._tpex_url = tpex_url or settings.tpex_material_info_url
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

    async def fetch_announcements(
        self,
        stock_id: str,
        market: AnnouncementMarket = "auto",
    ) -> list[dict[str, Any]]:
        if market == "twse":
            return await self._fetch_twse(stock_id)
        if market == "tpex":
            return await self._fetch_tpex(stock_id)

        results = await asyncio.gather(
            self._fetch_twse(stock_id),
            self._fetch_tpex(stock_id),
            return_exceptions=True,
        )
        rows: list[dict[str, Any]] = []
        errors: list[BaseException] = []
        for provider, result in zip(("twse", "tpex"), results, strict=True):
            if isinstance(result, BaseException):
                errors.append(result)
                logger.warning(
                    "announcement_provider_failed",
                    extra=log_context(
                        stock_id=stock_id,
                        provider=provider,
                        error_type=type(result).__name__,
                    ),
                )
            else:
                rows.extend(result)
        if len(errors) == 2:
            raise RuntimeError("both TWSE and TPEx announcement providers failed") from errors[0]
        rows.sort(
            key=lambda row: (
                str(row["announcement_date"]),
                str(row["announcement_time"]),
            ),
            reverse=True,
        )
        return rows

    async def _fetch_twse(self, stock_id: str) -> list[dict[str, Any]]:
        payload = await self._fetch_payload(
            self._twse_url,
            provider="twse",
            dataset="t187ap04_L",
        )
        return [
            self._normalize_twse(row)
            for row in payload
            if str(row.get("公司代號", "")).strip() == stock_id
        ]

    async def _fetch_tpex(self, stock_id: str) -> list[dict[str, Any]]:
        payload = await self._fetch_payload(
            self._tpex_url,
            provider="tpex",
            dataset="mopsfin_t187ap04_O",
        )
        return [
            self._normalize_tpex(row)
            for row in payload
            if str(row.get("SecuritiesCompanyCode", "")).strip() == stock_id
        ]

    async def _fetch_payload(
        self,
        url: str,
        *,
        provider: str,
        dataset: str,
    ) -> list[dict[str, Any]]:
        payload = await self._http.get_json(
            url,
            params={},
            cache_predicate=lambda value: isinstance(value, list),
            provider=provider,
            dataset=dataset,
        )
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise RuntimeError(f"{provider} {dataset} returned an invalid response")
        return payload

    @staticmethod
    def _normalize_twse(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "report_date": _parse_roc_date(row["出表日期"]),
            "announcement_date": _parse_roc_date(row["發言日期"]),
            "announcement_time": _parse_time(row["發言時間"]),
            "event_occurrence_date": _parse_roc_date(row["事實發生日"]),
            "stock_id": str(row["公司代號"]).strip(),
            "company_name": _clean_text(row["公司名稱"]),
            "market": "TWSE",
            "subject": _clean_text(row.get("主旨 ") or row.get("主旨")),
            "clause": _clean_text(row["符合條款"]),
            "details": _clean_text(row["說明"]),
            "source": "TWSE/MOPS/t187ap04_L",
        }

    @staticmethod
    def _normalize_tpex(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "report_date": _parse_roc_date(row["Date"]),
            "announcement_date": _parse_roc_date(row["發言日期"]),
            "announcement_time": _parse_time(row["發言時間"]),
            "event_occurrence_date": _parse_roc_date(row["事實發生日"]),
            "stock_id": str(row["SecuritiesCompanyCode"]).strip(),
            "company_name": _clean_text(row["CompanyName"]),
            "market": "TPEx",
            "subject": _clean_text(row["主旨"]),
            "clause": _clean_text(row["符合條款"]),
            "details": _clean_text(row["說明"]),
            "source": "TPEx/MOPS/mopsfin_t187ap04_O",
        }

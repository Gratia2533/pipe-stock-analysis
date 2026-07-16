from __future__ import annotations

import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from finance_mcp.config import settings
from finance_mcp.infra.cache import AsyncTTLCache
from finance_mcp.infra.logging import log_context

NewsSource = Literal["auto", "google", "yahoo"]
NewsMarket = Literal["TWSE", "TPEx"]
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_TAIPEI = ZoneInfo("Asia/Taipei")
logger = logging.getLogger("finance_mcp.news")


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _canonicalize_url(value: str) -> str:
    parsed = urlsplit(value)
    ignored = {"bcm", "bcmt", "guccounter", "guce_referrer", "oc"}
    query = [
        pair
        for pair in parsed.query.split("&")
        if pair and pair.split("=", 1)[0].lower() not in ignored
    ]
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, "&".join(query), ""))


def _normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def _is_duplicate(existing: list[dict[str, Any]], candidate: dict[str, Any]) -> bool:
    candidate_url = str(candidate.get("url", ""))
    candidate_title = _normalize_title(str(candidate.get("title", "")))
    for row in existing:
        if candidate_url and candidate_url == row.get("url"):
            return True
        existing_title = _normalize_title(str(row.get("title", "")))
        if candidate_title == existing_title:
            return True
        if candidate_title and existing_title:
            ratio = SequenceMatcher(None, candidate_title, existing_title).ratio()
            if ratio >= 0.93:
                return True
    return False


def _published_at(raw_value: str) -> str | None:
    if not raw_value:
        return None
    parsed = parsedate_to_datetime(raw_value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(_TAIPEI).isoformat()


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = _clean_text(data)
        if text:
            self.parts.append(text)


def _strip_html(value: str) -> str:
    parser = _PlainTextParser()
    parser.feed(value)
    return _clean_text(" ".join(parser.parts))


class _YahooNewsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []
        self._current: dict[str, list[str] | str] | None = None
        self._inside_h3 = False
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = attributes.get("class") or ""
        if tag == "li" and "js-stream-content" in classes:
            self._current = {"publisher": [], "title": [], "summary": [], "url": ""}
            return
        if self._current is None:
            return
        if tag == "div" and "Fz(13px)" in classes and "C(" in classes:
            self._capture = "publisher"
        elif tag == "h3":
            self._inside_h3 = True
        elif tag == "a" and self._inside_h3:
            self._capture = "title"
            self._current["url"] = attributes.get("href") or ""
        elif tag == "p":
            self._capture = "summary"

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if (
            (tag == "a" and self._capture == "title")
            or (tag == "p" and self._capture == "summary")
            or (tag == "div" and self._capture == "publisher")
        ):
            self._capture = None
        elif tag == "h3":
            self._inside_h3 = False
        elif tag == "li":
            title = _clean_text(" ".join(self._current["title"]))
            url = str(self._current["url"])
            if title and url:
                self.rows.append(
                    {
                        "publisher": _clean_text(" ".join(self._current["publisher"])),
                        "title": title,
                        "summary": _clean_text(" ".join(self._current["summary"])),
                        "url": url,
                    }
                )
            self._current = None
            self._capture = None
            self._inside_h3 = False

    def handle_data(self, data: str) -> None:
        if self._current is None or self._capture is None:
            return
        text = _clean_text(data)
        if text:
            target = self._current[self._capture]
            if isinstance(target, list):
                target.append(text)


class _TextHttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_attempts: int,
        retry_base_seconds: float,
        max_concurrency: int,
        cache_ttl_seconds: float,
        cache_max_entries: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._transport = transport
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._cache: AsyncTTLCache[str] = AsyncTTLCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_entries,
        )

    @staticmethod
    def _cache_key(url: str, params: Mapping[str, str]) -> str:
        return f"{url}?{urlencode(sorted(params.items()))}"

    async def get_text(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str] | None,
        provider: str,
        dataset: str,
        cache_predicate: Any = None,
    ) -> str:
        cache_key = self._cache_key(url, params)
        cached = await self._cache.get(cache_key)
        if cached is not None:
            logger.info("http_cache_hit", extra=log_context(provider=provider, dataset=dataset))
            return cached

        request_id = uuid4().hex
        async with (
            self._semaphore,
            httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=True,
            ) as client,
        ):
            for attempt in range(1, self._max_attempts + 1):
                started_at = time.monotonic()
                try:
                    response = await client.get(url, params=params, headers=headers)
                    if (
                        response.status_code in _RETRYABLE_STATUS_CODES
                        and attempt < self._max_attempts
                    ):
                        await self._wait_for_retry(
                            request_id,
                            provider,
                            dataset,
                            attempt,
                            f"status_{response.status_code}",
                        )
                        continue
                    response.raise_for_status()
                    text = response.text
                    if cache_predicate is None or cache_predicate(text):
                        await self._cache.set(cache_key, text)
                    logger.info(
                        "http_request_succeeded",
                        extra=log_context(
                            request_id=request_id,
                            provider=provider,
                            dataset=dataset,
                            attempt=attempt,
                            status_code=response.status_code,
                            duration_ms=round((time.monotonic() - started_at) * 1000, 2),
                        ),
                    )
                    return text
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt >= self._max_attempts:
                        raise
                    await self._wait_for_retry(
                        request_id,
                        provider,
                        dataset,
                        attempt,
                        type(exc).__name__,
                    )
                except httpx.HTTPStatusError:
                    raise
        raise RuntimeError("unreachable HTTP retry state")

    async def _wait_for_retry(
        self,
        request_id: str,
        provider: str,
        dataset: str,
        attempt: int,
        reason: str,
    ) -> None:
        delay = self._retry_base_seconds * (2 ** (attempt - 1))
        logger.warning(
            "http_request_retrying",
            extra=log_context(
                request_id=request_id,
                provider=provider,
                dataset=dataset,
                attempt=attempt,
                reason=reason,
                delay_seconds=delay,
            ),
        )
        if delay > 0:
            await asyncio.sleep(delay)


class TaiwanStockNewsClient:
    """Google News RSS primary provider with Yahoo Taiwan stock-page fallback."""

    def __init__(
        self,
        *,
        google_url: str | None = None,
        yahoo_base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
    ) -> None:
        self._google_url = google_url or settings.google_news_rss_url
        self._yahoo_base_url = (yahoo_base_url or settings.yahoo_tw_stock_base_url).rstrip("/")
        self._http = _TextHttpClient(
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

    async def fetch_news(
        self,
        stock_id: str,
        company_name: str,
        market: NewsMarket,
        *,
        limit: int = 10,
        source: NewsSource = "auto",
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10:
            raise ValueError("limit must be between 1 and 10")

        if source == "google":
            return (await self._fetch_google(stock_id, company_name, market))[:limit]
        if source == "yahoo":
            return (await self._fetch_yahoo(stock_id, company_name, market))[:limit]

        rows: list[dict[str, Any]] = []
        google_error: BaseException | None = None
        try:
            rows.extend(await self._fetch_google(stock_id, company_name, market))
        except (httpx.HTTPError, RuntimeError, ET.ParseError) as exc:
            google_error = exc
            logger.warning(
                "news_provider_failed",
                extra=log_context(
                    stock_id=stock_id,
                    provider="google_news_rss",
                    error_type=type(exc).__name__,
                ),
            )

        selected: list[dict[str, Any]] = []
        for row in rows:
            if not _is_duplicate(selected, row):
                selected.append(row)
            if len(selected) >= limit:
                return selected

        try:
            yahoo_rows = await self._fetch_yahoo(stock_id, company_name, market)
        except (httpx.HTTPError, RuntimeError) as exc:
            if not selected and google_error is not None:
                raise RuntimeError("both news providers failed") from google_error
            logger.warning(
                "news_provider_failed",
                extra=log_context(
                    stock_id=stock_id,
                    provider="yahoo_tw_stock",
                    error_type=type(exc).__name__,
                ),
            )
            return selected

        for row in yahoo_rows:
            if not _is_duplicate(selected, row):
                selected.append(row)
            if len(selected) >= limit:
                break
        return selected

    async def _fetch_google(
        self,
        stock_id: str,
        company_name: str,
        market: NewsMarket,
    ) -> list[dict[str, Any]]:
        text = await self._http.get_text(
            self._google_url,
            params={
                "q": f"{company_name} {stock_id}",
                "hl": "zh-TW",
                "gl": "TW",
                "ceid": "TW:zh-Hant",
            },
            headers={"User-Agent": "finance-mcp/0.1"},
            provider="google_news_rss",
            dataset="stock_news",
        )
        root = ET.fromstring(text)
        rows: list[dict[str, Any]] = []
        for item in root.findall("./channel/item"):
            publisher_element = item.find("source")
            publisher = _clean_text(publisher_element.text if publisher_element is not None else "")
            title = _clean_text(item.findtext("title"))
            suffix = f" - {publisher}"
            if publisher and title.endswith(suffix):
                title = title[: -len(suffix)].strip()
            raw_published_at = _clean_text(item.findtext("pubDate"))
            url = _canonicalize_url(_clean_text(item.findtext("link")))
            if not title or not url:
                continue
            rows.append(
                {
                    "stock_id": stock_id,
                    "company_name": company_name,
                    "market": market,
                    "title": title,
                    "published_at": _published_at(raw_published_at),
                    "published_at_original": raw_published_at or None,
                    "publisher": publisher or None,
                    "publisher_url": (
                        publisher_element.attrib.get("url")
                        if publisher_element is not None
                        else None
                    ),
                    "url": url,
                    "url_type": "aggregator",
                    "summary": _strip_html(_clean_text(item.findtext("description"))) or None,
                    "content_type": "general_media",
                    "source": "Google News RSS",
                }
            )
        return rows

    async def _fetch_yahoo(
        self,
        stock_id: str,
        company_name: str,
        market: NewsMarket,
    ) -> list[dict[str, Any]]:
        suffix = "TW" if market == "TWSE" else "TWO"
        text = await self._http.get_text(
            f"{self._yahoo_base_url}/quote/{stock_id}.{suffix}/news",
            params={},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/150.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-TW,zh;q=0.9",
            },
            provider="yahoo_tw_stock",
            dataset="stock_news",
            cache_predicate=lambda value: "Will be right back" not in value,
        )
        if "Will be right back" in text:
            raise RuntimeError("Yahoo Taiwan stock news is temporarily unavailable")

        parser = _YahooNewsParser()
        parser.feed(text)
        return [
            {
                "stock_id": stock_id,
                "company_name": company_name,
                "market": market,
                "title": row["title"],
                "published_at": None,
                "published_at_original": None,
                "publisher": row["publisher"] or None,
                "publisher_url": None,
                "url": _canonicalize_url(row["url"]),
                "url_type": "publisher_or_yahoo",
                "summary": row["summary"] or None,
                "content_type": "general_media",
                "source": "Yahoo Taiwan Stock",
            }
            for row in parser.rows
        ]

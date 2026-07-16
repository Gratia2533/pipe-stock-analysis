from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from finance_mcp.infra.cache import AsyncTTLCache
from finance_mcp.infra.logging import log_context

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
logger = logging.getLogger("finance_mcp.http")


class JsonHttpClient:
    """GET-only JSON client with bounded concurrency, retry, and TTL cache."""

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
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must not be negative")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._transport = transport
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._cache: AsyncTTLCache[Any] = AsyncTTLCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_entries,
        )

    @staticmethod
    def _cache_key(url: str, params: Mapping[str, str]) -> str:
        return f"{url}?{urlencode(sorted(params.items()))}"

    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
        use_cache: bool = True,
        cache_predicate: Callable[[Any], bool] | None = None,
        provider: str,
        dataset: str,
    ) -> Any:
        cache_key = self._cache_key(url, params)
        if use_cache:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                logger.info(
                    "http_cache_hit",
                    extra=log_context(provider=provider, dataset=dataset),
                )
                return cached

        request_id = uuid4().hex
        async with (
            self._semaphore,
            httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client,
        ):
            for attempt in range(1, self._max_attempts + 1):
                started_at = time.monotonic()
                try:
                    response = await client.get(url, params=params, headers=headers)
                    duration_ms = round((time.monotonic() - started_at) * 1000, 2)
                    if (
                        response.status_code in _RETRYABLE_STATUS_CODES
                        and attempt < self._max_attempts
                    ):
                        await self._log_and_wait_for_retry(
                            request_id=request_id,
                            provider=provider,
                            dataset=dataset,
                            attempt=attempt,
                            reason=f"status_{response.status_code}",
                        )
                        continue
                    response.raise_for_status()
                    try:
                        payload = response.json()
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"{provider} returned invalid JSON for {dataset}"
                        ) from exc
                    should_cache = cache_predicate(payload) if cache_predicate else True
                    if use_cache and should_cache:
                        await self._cache.set(cache_key, payload)
                    logger.info(
                        "http_request_succeeded",
                        extra=log_context(
                            request_id=request_id,
                            provider=provider,
                            dataset=dataset,
                            attempt=attempt,
                            status_code=response.status_code,
                            duration_ms=duration_ms,
                        ),
                    )
                    return payload
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt >= self._max_attempts:
                        logger.error(
                            "http_request_failed",
                            extra=log_context(
                                request_id=request_id,
                                provider=provider,
                                dataset=dataset,
                                attempt=attempt,
                                error_type=type(exc).__name__,
                            ),
                        )
                        raise
                    await self._log_and_wait_for_retry(
                        request_id=request_id,
                        provider=provider,
                        dataset=dataset,
                        attempt=attempt,
                        reason=type(exc).__name__,
                    )
                except httpx.HTTPStatusError as exc:
                    logger.error(
                        "http_request_failed",
                        extra=log_context(
                            request_id=request_id,
                            provider=provider,
                            dataset=dataset,
                            attempt=attempt,
                            status_code=exc.response.status_code,
                        ),
                    )
                    raise
        raise RuntimeError("unreachable HTTP retry state")

    async def _log_and_wait_for_retry(
        self,
        *,
        request_id: str,
        provider: str,
        dataset: str,
        attempt: int,
        reason: str,
    ) -> None:
        delay_seconds = self._retry_base_seconds * (2 ** (attempt - 1))
        logger.warning(
            "http_request_retrying",
            extra=log_context(
                request_id=request_id,
                provider=provider,
                dataset=dataset,
                attempt=attempt,
                reason=reason,
                delay_seconds=delay_seconds,
            ),
        )
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

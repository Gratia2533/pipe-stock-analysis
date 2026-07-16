from __future__ import annotations

from typing import Any, Protocol

from finance_mcp.config import Settings
from finance_mcp.direct_action import DirectActionClient
from finance_mcp.open_connector import OpenConnectorClient


class ActionClient(Protocol):
    async def call(self, action_id: str, action_input: dict[str, Any]) -> Any: ...


def create_action_client(settings: Settings) -> ActionClient:
    common_options = {
        "timeout": settings.request_timeout_seconds,
        "max_attempts": settings.request_max_attempts,
        "retry_base_seconds": settings.request_retry_base_seconds,
        "max_concurrency": settings.request_max_concurrency,
        "cache_ttl_seconds": settings.cache_ttl_seconds,
        "cache_max_entries": settings.cache_max_entries,
        "max_response_bytes": settings.upstream_max_response_bytes,
    }
    if settings.data_backend == "direct":
        return DirectActionClient(
            finmind_token=settings.finmind_token,
            finnhub_api_key=settings.finnhub_api_key,
            **common_options,
        )
    return OpenConnectorClient(
        base_url=settings.open_connector_base_url,
        runtime_token=settings.open_connector_runtime_token,
        **common_options,
    )

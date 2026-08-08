from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlsplit

import httpcore
import httpx


class PinnedNetworkBackend:
    """Connect only to addresses returned by a validated DNS lookup."""

    def __init__(
        self,
        backend: Any,
        *,
        hostname: str,
        port: int,
        addresses: tuple[str, ...],
    ) -> None:
        self._backend = backend
        self._hostname = hostname
        self._port = port
        self._addresses = addresses

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        if host != self._hostname or port != self._port:
            raise httpcore.ConnectError("Attempted to connect outside the validated OAuth origin")

        last_error: httpcore.ConnectError | httpcore.ConnectTimeout | None = None
        for address in self._addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise httpcore.ConnectError("No validated OAuth address is available")

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
        raise httpcore.ConnectError("Unix sockets are not allowed for OAuth metadata")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


def pinned_http_transport(url: str, addresses: tuple[str, ...]) -> httpx.AsyncHTTPTransport:
    """Pin TCP to validated addresses while preserving URL Host and TLS SNI."""
    parsed = urlsplit(url)
    if parsed.hostname is None:
        raise ValueError("Validated OAuth URL has no hostname")
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    port = parsed.port or 443

    transport = httpx.AsyncHTTPTransport(trust_env=False)
    pool = cast(Any, transport)._pool
    pool._network_backend = PinnedNetworkBackend(
        pool._network_backend,
        hostname=hostname,
        port=port,
        addresses=addresses,
    )
    return transport

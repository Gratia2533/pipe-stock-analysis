from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from finance_mcp.oauth import FinanceOAuthProvider


def make_provider(directory: str) -> FinanceOAuthProvider:
    return FinanceOAuthProvider(
        issuer_url="https://server.example",
        resource_url="https://server.example/mcp",
        username="user",
        password="password",
        database_path=str(Path(directory) / "state.sqlite3"),
    )


class Stream:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    async def __aenter__(self) -> httpx.Response:
        return self.response

    async def __aexit__(self, *args) -> None:
        await self.response.aclose()


@pytest.mark.asyncio
async def test_loads_valid_cimd_client() -> None:
    client_id = "https://client.example/oauth/client.json"
    response = httpx.Response(
        200,
        request=httpx.Request("GET", client_id),
        headers={"content-type": "application/json"},
        json={
            "client_id": client_id,
            "client_name": "Test client",
            "redirect_uris": ["https://client.example/callback"],
            "token_endpoint_auth_method": "none",
        },
    )
    with tempfile.TemporaryDirectory() as directory:
        provider = make_provider(directory)
        with (
            patch.object(provider, "_validate_public_cimd_url", new=AsyncMock(return_value=None)),
            patch("httpx.AsyncClient.stream", return_value=Stream(response)),
        ):
            client = await provider.get_client(client_id)

    assert client is not None
    assert client.client_id == client_id
    assert client.token_endpoint_auth_method == "none"


@pytest.mark.asyncio
async def test_deeply_nested_cimd_json_fails_closed() -> None:
    client_id = "https://client.example/oauth/client.json"
    body = b'{"nested":' + (b"[" * 1_500) + b"0" + (b"]" * 1_500) + b"}"
    response = httpx.Response(
        200,
        request=httpx.Request("GET", client_id),
        headers={"content-type": "application/json"},
        content=body,
    )
    with tempfile.TemporaryDirectory() as directory:
        provider = make_provider(directory)
        with (
            patch.object(
                provider,
                "_validate_public_cimd_url",
                new=AsyncMock(return_value=("93.184.216.34",)),
            ),
            patch("httpx.AsyncClient.stream", return_value=Stream(response)),
        ):
            client = await provider.get_client(client_id)

    assert client is None


@pytest.mark.asyncio
async def test_cimd_fetch_uses_validated_addresses_in_pinned_transport() -> None:
    client_id = "https://client.example/oauth/client.json"
    response = httpx.Response(
        200,
        request=httpx.Request("GET", client_id),
        headers={"content-type": "application/json"},
        json={
            "client_id": client_id,
            "client_name": "Pinned client",
            "redirect_uris": ["https://client.example/callback"],
            "token_endpoint_auth_method": "none",
        },
    )
    addresses = ("93.184.216.34",)
    with tempfile.TemporaryDirectory() as directory:
        provider = make_provider(directory)
        with (
            patch.object(
                provider,
                "_validate_public_cimd_url",
                new=AsyncMock(return_value=addresses),
            ),
            patch("finance_mcp.oauth.pinned_http_transport") as pinned_transport,
            patch("httpx.AsyncClient.stream", return_value=Stream(response)),
        ):
            client = await provider.get_client(client_id)

    assert client is not None
    pinned_transport.assert_called_once_with(client_id, addresses)


@pytest.mark.asyncio
async def test_loads_private_key_jwt_cimd_client() -> None:
    client_id = "https://chatgpt.com/oauth/client.json"
    response = httpx.Response(
        200,
        request=httpx.Request("GET", client_id),
        headers={"content-type": "application/json"},
        json={
            "client_id": client_id,
            "client_name": "ChatGPT",
            "redirect_uris": ["https://chatgpt.com/connector/oauth/callback"],
            "token_endpoint_auth_method": "private_key_jwt",
            "token_endpoint_auth_signing_alg": "RS256",
            "jwks_uri": "https://chatgpt.com/oauth/jwks.json",
        },
    )
    with tempfile.TemporaryDirectory() as directory:
        provider = make_provider(directory)
        with (
            patch.object(provider, "_validate_public_cimd_url", new=AsyncMock(return_value=None)),
            patch("httpx.AsyncClient.stream", return_value=Stream(response)),
        ):
            client = await provider.get_client(client_id)

    assert client is not None
    assert client.token_endpoint_auth_method == "private_key_jwt"
    assert str(client.jwks_uri) == "https://chatgpt.com/oauth/jwks.json"


@pytest.mark.asyncio
async def test_rejects_mismatched_cimd_client_id() -> None:
    client_id = "https://client.example/oauth/client.json"
    response = httpx.Response(
        200,
        request=httpx.Request("GET", client_id),
        headers={"content-type": "application/json"},
        json={
            "client_id": "https://attacker.example/client.json",
            "client_name": "Wrong client",
            "redirect_uris": ["https://attacker.example/callback"],
        },
    )
    with tempfile.TemporaryDirectory() as directory:
        provider = make_provider(directory)
        with (
            patch.object(provider, "_validate_public_cimd_url", new=AsyncMock(return_value=None)),
            patch("httpx.AsyncClient.stream", return_value=Stream(response)),
        ):
            client = await provider.get_client(client_id)

    assert client is None

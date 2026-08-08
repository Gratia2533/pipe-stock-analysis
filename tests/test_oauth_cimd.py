from __future__ import annotations

import base64
import hashlib
import tempfile
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.provider import AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.routing import Route

from finance_mcp.oauth import FinanceOAuthProvider
from finance_mcp.oauth_client_auth import PrivateKeyJWTClientAuthenticator


def make_provider(directory: str) -> FinanceOAuthProvider:
    return FinanceOAuthProvider(
        issuer_url="https://server.example",
        resource_url="https://server.example/mcp",
        username="user",
        password="password",
        database_path=str(Path(directory) / "state.sqlite3"),
    )


def token_app(provider: FinanceOAuthProvider) -> Starlette:
    authenticator = PrivateKeyJWTClientAuthenticator(provider)
    handler = TokenHandler(cast(Any, provider), cast(Any, authenticator))
    return Starlette(routes=[Route("/token", handler.handle, methods=["POST"])])


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


@pytest.mark.asyncio
async def test_stale_authorization_code_can_only_be_exchanged_once() -> None:
    client = OAuthClientInformationFull.model_validate(
        {
            "client_id": "https://client.example/oauth/client.json",
            "client_name": "Test client",
            "redirect_uris": ["https://client.example/callback"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
    )
    assert client.client_id is not None
    code = AuthorizationCode(
        code="one-time-code",
        client_id=client.client_id,
        redirect_uri=AnyHttpUrl("https://client.example/callback"),
        redirect_uri_provided_explicitly=True,
        expires_at=time.time() + 300,
        scopes=["finance:read"],
        code_challenge="pkce-challenge",
        resource="https://server.example/mcp",
    )

    with tempfile.TemporaryDirectory() as directory:
        provider = make_provider(directory)
        provider._put_model(
            "oauth_authorization_codes",
            "code",
            code.code,
            code,
        )
        first_loaded = await provider.load_authorization_code(client, code.code)
        second_loaded = await provider.load_authorization_code(client, code.code)
        assert first_loaded is not None
        assert second_loaded is not None

        await provider.exchange_authorization_code(client, first_loaded)
        with pytest.raises(ValueError, match="Invalid OAuth authorization code"):
            await provider.exchange_authorization_code(client, second_loaded)

        with provider._connect() as connection:
            code_count = connection.execute(
                "SELECT COUNT(*) FROM oauth_authorization_codes"
            ).fetchone()[0]
            token_pair_count = connection.execute(
                "SELECT COUNT(*) FROM oauth_token_pairs"
            ).fetchone()[0]

    assert code_count == 0
    assert token_pair_count == 1


@pytest.mark.asyncio
async def test_http_authorization_code_pkce_binding_resource_and_replay() -> None:
    client_id = "https://client.example/oauth/client.json"
    redirect_uri = "https://client.example/callback"
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    client = OAuthClientInformationFull.model_validate(
        {
            "client_id": client_id,
            "client_name": "Test CIMD client",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "private_key_jwt",
            "jwks_uri": "https://client.example/oauth/jwks.json",
        }
    )
    code = AuthorizationCode(
        code="http-one-time-code",
        client_id=client_id,
        redirect_uri=AnyHttpUrl(redirect_uri),
        redirect_uri_provided_explicitly=True,
        expires_at=time.time() + 300,
        scopes=["finance:read"],
        code_challenge=challenge,
        resource="https://server.example/mcp",
    )

    with tempfile.TemporaryDirectory() as directory:
        provider = make_provider(directory)
        provider._cimd_cache[client_id] = (time.monotonic() + 300, client)
        provider._put_model("oauth_authorization_codes", "code", code.code, code)
        transport = httpx.ASGITransport(app=token_app(provider))
        base_form = {
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code.code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        }

        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://server.example",
        ) as http_client:
            wrong_redirect = await http_client.post(
                "/token",
                data={**base_form, "redirect_uri": "https://attacker.example/callback"},
            )
            wrong_verifier = await http_client.post(
                "/token",
                data={**base_form, "code_verifier": "x" * 43},
            )
            success = await http_client.post(
                "/token",
                data={**base_form, "resource": "https://attacker.example/mcp"},
            )
            replay = await http_client.post("/token", data=base_form)

        assert wrong_redirect.status_code == 400
        assert wrong_verifier.status_code == 400
        assert success.status_code == 200
        assert replay.status_code == 400
        access_token = await provider.load_access_token(success.json()["access_token"])

    assert access_token is not None
    assert access_token.resource == "https://server.example/mcp"

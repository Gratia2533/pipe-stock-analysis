from __future__ import annotations

import base64
import ssl
import tempfile
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from urllib.parse import quote, urlencode

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError
from mcp.shared.auth import OAuthClientInformationFull
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route

from finance_mcp.oauth import FinanceOAuthProvider
from finance_mcp.oauth_client_auth import PrivateKeyJWTClientAuthenticator
from finance_mcp.oauth_network import PinnedNetworkBackend
from finance_mcp.server import _enable_cimd_metadata

CLIENT_ID = "https://chatgpt.com/oauth/client.json"
ISSUER = "https://finance.example"
JWKS_URI = "https://chatgpt.com/oauth/jwks.json"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


class Provider:
    issuer_url = ISSUER

    def __init__(
        self,
        *clients: OAuthClientInformationFull,
        cimd_client_ids: set[str] | None = None,
    ) -> None:
        self.clients = {client.client_id: client for client in clients}
        self.cimd_client_ids = cimd_client_ids or set()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    def is_cimd_client(self, client_id: str) -> bool:
        return client_id in self.cimd_client_ids


class Stream:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    async def __aenter__(self) -> httpx.Response:
        return self.response

    async def __aexit__(self, *args) -> None:
        await self.response.aclose()


class FakeNetworkStream:
    def __init__(self) -> None:
        body = b'{"keys": []}'
        self._response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"\r\n"
            + body
        )
        self.server_hostname: str | None = None
        self.ssl_context: ssl.SSLContext | None = None

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        response, self._response = self._response, b""
        return response

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> FakeNetworkStream:
        self.ssl_context = ssl_context
        self.server_hostname = server_hostname
        return self

    def get_extra_info(self, info: str) -> None:
        return None


async def _request(
    form: dict[str, str],
    authorization: str | None = None,
    *,
    path: str = "/token",
) -> Request:
    body = urlencode(form).encode()
    headers = [(b"content-type", b"application/x-www-form-urlencoded")]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": headers,
        },
        receive,
    )


def _auth_app(provider: Any) -> Starlette:
    authenticator = PrivateKeyJWTClientAuthenticator(provider)
    token_handler = TokenHandler(cast(Any, provider), cast(Any, authenticator))
    revocation_handler = RevocationHandler(cast(Any, provider), cast(Any, authenticator))
    return Starlette(
        routes=[
            Route("/token", token_handler.handle, methods=["POST"]),
            Route("/revoke", revocation_handler.handle, methods=["POST"]),
        ]
    )


def _client(
    auth_method: str = "private_key_jwt",
    *,
    client_id: str = CLIENT_ID,
    client_secret: str | None = None,
) -> OAuthClientInformationFull:
    metadata: dict[str, object] = {
        "client_id": client_id,
        "client_name": "ChatGPT",
        "redirect_uris": ["https://chatgpt.com/connector/oauth/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": auth_method,
    }
    if auth_method == "private_key_jwt":
        metadata["jwks_uri"] = JWKS_URI
    if client_secret is not None:
        metadata["client_secret"] = client_secret
    return OAuthClientInformationFull.model_validate(metadata)


def _key_pair() -> tuple[Any, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "chatgpt-test", "use": "sig", "alg": "RS256"})
    return private_key, public_jwk


def _jwt_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _assertion(
    private_key: Any,
    *,
    client_id: str = CLIENT_ID,
    **claim_overrides: Any,
) -> str:
    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": f"{ISSUER}/token",
        "exp": now + 60,
        "iat": now,
        "jti": "one-time-assertion",
    }
    claims.update(claim_overrides)
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "chatgpt-test"},
    )


async def _authenticate(
    authenticator: PrivateKeyJWTClientAuthenticator,
    assertion: str,
    *,
    client_id: str = CLIENT_ID,
) -> OAuthClientInformationFull:
    return await authenticator.authenticate_request(
        await _request(
            {
                "client_id": client_id,
                "client_assertion_type": ASSERTION_TYPE,
                "client_assertion": assertion,
            }
        )
    )


@pytest.mark.asyncio
async def test_authenticates_private_key_jwt_and_rejects_replay() -> None:
    private_key, public_jwk = _key_pair()
    assertion = _assertion(private_key)
    jwks_response = httpx.Response(
        200,
        request=httpx.Request("GET", JWKS_URI),
        headers={"content-type": "application/json"},
        json={"keys": [public_jwk]},
    )
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(
            PrivateKeyJWTClientAuthenticator,
            "_validate_public_url",
            new=AsyncMock(return_value=("93.184.216.34",)),
        ),
        patch("httpx.AsyncClient.stream", return_value=Stream(jwks_response)),
    ):
        authenticated = await _authenticate(authenticator, assertion)
        with pytest.raises(AuthenticationError, match="already been used"):
            await _authenticate(authenticator, assertion)

    assert authenticated.client_id == CLIENT_ID


@pytest.mark.asyncio
async def test_private_key_jwt_cimd_allows_authorization_code_pkce_fallback() -> None:
    authenticator = PrivateKeyJWTClientAuthenticator(
        Provider(_client(), cimd_client_ids={CLIENT_ID})
    )

    authenticated = await authenticator.authenticate_request(
        await _request(
            {
                "client_id": CLIENT_ID,
                "grant_type": "authorization_code",
                "code": "authorization-code",
                "code_verifier": "pkce-verifier",
                "redirect_uri": "https://chatgpt.com/connector/oauth/callback",
            }
        )
    )

    assert authenticated.client_id == CLIENT_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "form",
    [
        {"client_id": CLIENT_ID, "client_assertion_type": ASSERTION_TYPE},
        {"client_id": CLIENT_ID, "client_assertion": "not-a-jwt"},
        {"client_id": CLIENT_ID, "client_assertion_type": ""},
        {"client_id": CLIENT_ID, "client_assertion": ""},
        {
            "client_id": CLIENT_ID,
            "client_assertion_type": "",
            "client_assertion": "",
        },
    ],
)
async def test_private_key_jwt_cimd_rejects_present_invalid_assertion_fields(
    form: dict[str, str],
) -> None:
    authenticator = PrivateKeyJWTClientAuthenticator(
        Provider(_client(), cimd_client_ids={CLIENT_ID})
    )

    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_request(await _request(form))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("form", "path"),
    [
        (
            {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": "refresh-token",
            },
            "/token",
        ),
        (
            {
                "client_id": CLIENT_ID,
                "grant_type": "authorization_code",
                "code": "authorization-code",
                "code_verifier": "pkce-verifier",
            },
            "/revoke",
        ),
        (
            {
                "client_id": CLIENT_ID,
                "grant_type": "authorization_code",
                "code": "authorization-code",
            },
            "/token",
        ),
    ],
)
async def test_private_key_jwt_cimd_rejects_fallback_outside_token_code_pkce(
    form: dict[str, str],
    path: str,
) -> None:
    authenticator = PrivateKeyJWTClientAuthenticator(
        Provider(_client(), cimd_client_ids={CLIENT_ID})
    )

    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_request(await _request(form, path=path))


@pytest.mark.asyncio
async def test_registered_private_key_jwt_client_cannot_use_public_fallback() -> None:
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_request(
            await _request(
                {
                    "client_id": CLIENT_ID,
                    "grant_type": "authorization_code",
                    "code": "authorization-code",
                    "code_verifier": "pkce-verifier",
                }
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "form"),
    [
        (
            "/token",
            {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": "refresh-token",
            },
        ),
        (
            "/revoke",
            {"client_id": CLIENT_ID, "token": "access-token"},
        ),
        (
            "/token",
            {
                "client_id": CLIENT_ID,
                "grant_type": "authorization_code",
                "code": "authorization-code",
                "code_verifier": "pkce-verifier",
                "client_assertion_type": "",
            },
        ),
    ],
)
async def test_http_handlers_reject_assertionless_non_code_and_empty_assertion(
    path: str,
    form: dict[str, str],
) -> None:
    provider = Provider(_client(), cimd_client_ids={CLIENT_ID})
    transport = httpx.ASGITransport(app=_auth_app(provider))

    async with httpx.AsyncClient(transport=transport, base_url=ISSUER) as client:
        response = await client.post(path, data=form)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_replay_is_rejected_during_expiration_leeway() -> None:
    private_key, public_jwk = _key_pair()
    expires_at = int(time.time()) - 2
    assertion = _assertion(
        private_key,
        iat=expires_at - 60,
        exp=expires_at,
        jti="leeway-replay",
    )
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(authenticator, "_load_jwks", new=AsyncMock(return_value=[public_jwk])),
        patch("finance_mcp.oauth_client_auth.time.time", return_value=expires_at + 1),
    ):
        await _authenticate(authenticator, assertion)
        with pytest.raises(AuthenticationError, match="already been used"):
            await _authenticate(authenticator, assertion)


@pytest.mark.asyncio
async def test_rejects_assertion_expiring_before_it_is_issued() -> None:
    private_key, public_jwk = _key_pair()
    now = int(time.time())
    assertion = _assertion(private_key, iat=now, exp=now - 1, jti="negative-lifetime")
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(authenticator, "_load_jwks", new=AsyncMock(return_value=[public_jwk])),
        pytest.raises(AuthenticationError, match="Invalid client assertion"),
    ):
        await _authenticate(authenticator, assertion)


@pytest.mark.asyncio
async def test_rejects_assertion_with_distant_expiration() -> None:
    private_key, public_jwk = _key_pair()
    now = int(time.time())
    assertion = _assertion(private_key, iat=now, exp=now + 3600, jti="distant-exp")
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(authenticator, "_load_jwks", new=AsyncMock(return_value=[public_jwk])),
        pytest.raises(AuthenticationError, match="Invalid client assertion"),
    ):
        await _authenticate(authenticator, assertion)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("exp", 10**1000),
        ("exp", float("inf")),
        ("exp", float("nan")),
        ("exp", True),
        ("exp", 1e308),
        ("iat", -(10**1000)),
        ("iat", float("-inf")),
        ("iat", float("nan")),
        ("iat", False),
        ("iat", -1e308),
    ],
    ids=[
        "huge-exp",
        "infinite-exp",
        "nan-exp",
        "boolean-exp",
        "extreme-exp",
        "huge-iat",
        "infinite-iat",
        "nan-iat",
        "boolean-iat",
        "extreme-iat",
    ],
)
async def test_malformed_numeric_dates_are_authentication_errors(
    claim: str,
    value: object,
) -> None:
    private_key, public_jwk = _key_pair()
    assertion = (
        _assertion(private_key, exp=value, jti="invalid-exp")
        if claim == "exp"
        else _assertion(private_key, iat=value, jti="invalid-iat")
    )
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(authenticator, "_load_jwks", new=AsyncMock(return_value=[public_jwk])),
        pytest.raises(AuthenticationError, match="Invalid client assertion"),
    ):
        await _authenticate(authenticator, assertion)


@pytest.mark.asyncio
async def test_full_replay_cache_fails_closed_without_evicting_valid_entries(monkeypatch) -> None:
    private_key, public_jwk = _key_pair()
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))
    monkeypatch.setattr("finance_mcp.oauth_client_auth._MAX_REPLAY_CACHE_ENTRIES", 1)

    with patch.object(
        authenticator,
        "_load_jwks",
        new=AsyncMock(return_value=[public_jwk]),
    ):
        await _authenticate(authenticator, _assertion(private_key, jti="cached"))
        with pytest.raises(AuthenticationError, match="replay cache is full"):
            await _authenticate(authenticator, _assertion(private_key, jti="overflow"))
        with pytest.raises(AuthenticationError, match="already been used"):
            await _authenticate(authenticator, _assertion(private_key, jti="cached"))


@pytest.mark.asyncio
async def test_replay_identity_is_scoped_to_client_id() -> None:
    other_client_id = "https://chatgpt.com/oauth/other-client.json"
    private_key, public_jwk = _key_pair()
    authenticator = PrivateKeyJWTClientAuthenticator(
        Provider(_client(), _client(client_id=other_client_id))
    )
    first_assertion = _assertion(private_key, jti="shared-jti")
    other_assertion = _assertion(
        private_key,
        client_id=other_client_id,
        jti="shared-jti",
    )

    with patch.object(
        authenticator,
        "_load_jwks",
        new=AsyncMock(return_value=[public_jwk]),
    ):
        await _authenticate(authenticator, first_assertion)
        await _authenticate(authenticator, other_assertion, client_id=other_client_id)
        with pytest.raises(AuthenticationError, match="already been used"):
            await _authenticate(authenticator, first_assertion)


@pytest.mark.asyncio
async def test_deeply_nested_jwks_json_is_authentication_error() -> None:
    private_key, _public_jwk = _key_pair()
    body = b'{"keys":' + (b"[" * 1_500) + b"0" + (b"]" * 1_500) + b"}"
    response = httpx.Response(
        200,
        request=httpx.Request("GET", JWKS_URI),
        headers={"content-type": "application/json"},
        content=body,
    )
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(
            authenticator,
            "_validate_public_url",
            new=AsyncMock(return_value=("93.184.216.34",)),
        ),
        patch("httpx.AsyncClient.stream", return_value=Stream(response)),
        pytest.raises(AuthenticationError, match="Invalid client assertion"),
    ):
        await _authenticate(authenticator, _assertion(private_key))


@pytest.mark.asyncio
async def test_dns_resolution_failure_is_authentication_error() -> None:
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(
            authenticator,
            "_validate_public_url",
            new=AsyncMock(side_effect=OSError("resolver unavailable")),
        ),
        pytest.raises(AuthenticationError, match="Unable to resolve client JWKS"),
    ):
        await authenticator._load_jwks(JWKS_URI)


@pytest.mark.asyncio
async def test_pinned_backend_connects_to_validated_address_without_reresolving() -> None:
    underlying = AsyncMock()
    expected_stream = object()
    underlying.connect_tcp.return_value = expected_stream
    backend = PinnedNetworkBackend(
        underlying,
        hostname="chatgpt.com",
        port=443,
        addresses=("93.184.216.34",),
    )

    stream = await backend.connect_tcp(
        "chatgpt.com",
        443,
        timeout=3.0,
        local_address=None,
        socket_options=None,
    )

    assert stream is expected_stream
    underlying.connect_tcp.assert_awaited_once_with(
        "93.184.216.34",
        443,
        timeout=3.0,
        local_address=None,
        socket_options=None,
    )


@pytest.mark.asyncio
async def test_jwks_fetch_pins_tcp_but_preserves_tls_hostname_verification() -> None:
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))
    stream = FakeNetworkStream()
    connected_hosts: list[str] = []

    async def connect_tcp(_backend: Any, host: str, port: int, **kwargs: Any) -> Any:
        connected_hosts.append(host)
        return stream

    with (
        patch.object(
            authenticator,
            "_validate_public_url",
            new=AsyncMock(return_value=("93.184.216.34",)),
        ),
        patch("httpcore._backends.auto.AutoBackend.connect_tcp", new=connect_tcp),
    ):
        keys = await authenticator._load_jwks(JWKS_URI)

    assert keys == []
    assert connected_hosts == ["93.184.216.34"]
    assert stream.server_hostname == "chatgpt.com"
    assert stream.ssl_context is not None
    assert stream.ssl_context.check_hostname is True
    assert stream.ssl_context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.asyncio
@pytest.mark.parametrize("nested_segment", ["header", "payload"])
async def test_deeply_nested_jwt_json_is_authentication_error(nested_segment: str) -> None:
    _private_key, public_jwk = _key_pair()
    nested = b'{"nested":' + (b"[" * 1_500) + b"0" + (b"]" * 1_500) + b"}"
    header = b'{"alg":"RS256","kid":"chatgpt-test"}'
    payload = b"{}"
    if nested_segment == "header":
        header = (
            b'{"alg":"RS256","kid":"chatgpt-test","nested":'
            + (b"[" * 1_500)
            + b"0"
            + (b"]" * 1_500)
            + b"}"
        )
    else:
        payload = nested
    assertion = f"{_jwt_segment(header)}.{_jwt_segment(payload)}."
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(authenticator, "_load_jwks", new=AsyncMock(return_value=[public_jwk])),
        pytest.raises(AuthenticationError, match="Invalid client assertion"),
    ):
        await _authenticate(authenticator, assertion)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("assertion_factory", "error_match"),
    [
        (lambda _key: "not-a-jwt", "Invalid client assertion"),
        (
            lambda _key: _assertion(
                rsa.generate_private_key(public_exponent=65537, key_size=2048)
            ),
            "Invalid client assertion",
        ),
        (
            lambda key: _assertion(key, aud="https://attacker.example/token"),
            "Invalid client assertion",
        ),
        (lambda key: _assertion(key, sub="https://attacker.example/client"), "subject mismatch"),
    ],
    ids=["malformed", "bad-signature", "bad-audience", "bad-subject"],
)
async def test_rejects_malformed_signature_and_claim_cases(
    assertion_factory: Any,
    error_match: str,
) -> None:
    private_key, public_jwk = _key_pair()
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(authenticator, "_load_jwks", new=AsyncMock(return_value=[public_jwk])),
        pytest.raises(AuthenticationError, match=error_match),
    ):
        await _authenticate(authenticator, assertion_factory(private_key))


@pytest.mark.asyncio
async def test_rejects_assertion_with_missing_required_claim() -> None:
    private_key, public_jwk = _key_pair()
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": CLIENT_ID,
            "sub": CLIENT_ID,
            "aud": f"{ISSUER}/token",
            "iat": now,
            "jti": "missing-exp",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "chatgpt-test"},
    )
    authenticator = PrivateKeyJWTClientAuthenticator(Provider(_client()))

    with (
        patch.object(authenticator, "_load_jwks", new=AsyncMock(return_value=[public_jwk])),
        pytest.raises(AuthenticationError, match="Invalid client assertion"),
    ):
        await _authenticate(authenticator, assertion)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_method", ["none", "client_secret_post", "client_secret_basic"])
async def test_runtime_supports_every_advertised_non_jwt_auth_method(auth_method: str) -> None:
    secret = None if auth_method == "none" else "registered-secret"
    authenticator = PrivateKeyJWTClientAuthenticator(
        Provider(_client(auth_method, client_secret=secret))
    )
    form = {"client_id": CLIENT_ID}
    authorization = None
    if auth_method == "client_secret_post":
        form["client_secret"] = cast(str, secret)
    elif auth_method == "client_secret_basic":
        credentials = base64.b64encode(
            f"{quote(CLIENT_ID, safe='')}:{quote(cast(str, secret), safe='')}".encode()
        ).decode()
        authorization = f"Basic {credentials}"

    authenticated = await authenticator.authenticate_request(await _request(form, authorization))

    assert authenticated.client_id == CLIENT_ID


@pytest.mark.asyncio
async def test_private_key_jwt_revocation_without_client_secret_invalidates_token() -> None:
    from mcp.server.auth.handlers import revoke as revocation_handlers

    private_key, public_jwk = _key_pair()
    assertion = _assertion(private_key, jti="revocation-assertion")
    original_request_model = revocation_handlers.RevocationRequest
    _enable_cimd_metadata()
    try:
        with tempfile.TemporaryDirectory() as directory:
            provider = FinanceOAuthProvider(
                issuer_url=ISSUER,
                resource_url=f"{ISSUER}/mcp",
                username="user",
                password="test",
                database_path=str(Path(directory) / "state.sqlite3"),
            )
            client = _client()
            provider._cimd_cache[CLIENT_ID] = (time.monotonic() + 300, client)
            issued = provider._issue_token_pair(
                client_id=CLIENT_ID,
                scopes=["finance:read"],
                resource=f"{ISSUER}/mcp",
                subject="user",
            )
            transport = httpx.ASGITransport(app=_auth_app(provider))

            with patch.object(
                PrivateKeyJWTClientAuthenticator,
                "_load_jwks",
                new=AsyncMock(return_value=[public_jwk]),
            ):
                async with httpx.AsyncClient(transport=transport, base_url=ISSUER) as client_http:
                    response = await client_http.post(
                        "/revoke",
                        data={
                            "client_id": CLIENT_ID,
                            "token": issued.access_token,
                            "token_type_hint": "access_token",
                            "client_assertion_type": ASSERTION_TYPE,
                            "client_assertion": assertion,
                        },
                    )

            remaining = await provider.load_access_token(issued.access_token)
    finally:
        revocation_handlers.RevocationRequest = original_request_model

    assert response.status_code == 200
    assert remaining is None


def test_cimd_metadata_matches_token_and_revocation_runtime(monkeypatch) -> None:
    from mcp.server.auth import routes

    class Metadata:
        client_id_metadata_document_supported = False
        token_endpoint_auth_methods_supported = ["client_secret_post", "client_secret_basic"]
        token_endpoint_auth_signing_alg_values_supported = None
        revocation_endpoint = "https://finance.example/revoke"
        revocation_endpoint_auth_methods_supported = ["client_secret_post", "client_secret_basic"]
        revocation_endpoint_auth_signing_alg_values_supported = None

    monkeypatch.setattr(routes, "build_metadata", lambda *args, **kwargs: Metadata())
    monkeypatch.setattr(routes, "ClientAuthenticator", object)

    _enable_cimd_metadata()
    metadata = cast(Any, routes.build_metadata)()
    expected_methods = ["none", "client_secret_post", "client_secret_basic", "private_key_jwt"]

    assert metadata.client_id_metadata_document_supported is True
    assert metadata.token_endpoint_auth_methods_supported == expected_methods
    assert metadata.revocation_endpoint_auth_methods_supported == expected_methods
    assert metadata.token_endpoint_auth_signing_alg_values_supported == ["RS256"]
    assert metadata.revocation_endpoint_auth_signing_alg_values_supported == ["RS256"]
    assert routes.ClientAuthenticator is PrivateKeyJWTClientAuthenticator

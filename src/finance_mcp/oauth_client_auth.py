from __future__ import annotations

import json
import math
import threading
import time
from typing import Any

import httpx
import jwt
from mcp.server.auth.middleware.client_auth import (
    AuthenticationError,
    ClientAuthenticator,
)
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request

from finance_mcp.oauth import FinanceOAuthProvider
from finance_mcp.oauth_network import pinned_http_transport

_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_MAX_JWKS_BYTES = 64 * 1024
_JWT_LEEWAY_SECONDS = 5
_MAX_ASSERTION_LIFETIME_SECONDS = 5 * 60
_MAX_REPLAY_CACHE_ENTRIES = 10_000
SUPPORTED_CLIENT_AUTH_METHODS: tuple[str, ...] = (
    "none",
    "client_secret_post",
    "client_secret_basic",
    "private_key_jwt",
)


class PrivateKeyJWTClientAuthenticator:
    """Authenticate standard MCP clients plus CIMD RS256 private_key_jwt clients.

    The JTI cache is intentionally process-local: this deployment runs one OAuth
    server process. Running multiple workers would require a shared replay cache.
    """

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self._default_authenticator = ClientAuthenticator(provider)
        self._used_jtis: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    async def authenticate_request(self, request: Request) -> OAuthClientInformationFull:
        form_data = await request.form()
        client_id = form_data.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise AuthenticationError("Missing client_id")

        client = await self.provider.get_client(client_id)
        if client is None:
            raise AuthenticationError("Invalid client_id")
        if client.token_endpoint_auth_method != "private_key_jwt":
            return await self._default_authenticator.authenticate_request(request)

        assertion_type = form_data.get("client_assertion_type")
        assertion = form_data.get("client_assertion")
        if assertion_type != _ASSERTION_TYPE:
            raise AuthenticationError("Missing or invalid client_assertion_type")
        if not isinstance(assertion, str) or not assertion:
            raise AuthenticationError("Missing client_assertion")

        await self._verify_assertion(client, assertion, client_id)
        return client

    @staticmethod
    async def _validate_public_url(url: str) -> tuple[str, ...]:
        return await FinanceOAuthProvider._validate_public_cimd_url(url)

    async def _load_jwks(self, jwks_uri: str) -> list[dict[str, Any]]:
        try:
            addresses = await self._validate_public_url(jwks_uri)
        except OSError as exc:
            raise AuthenticationError("Unable to resolve client JWKS") from exc
        except ValueError as exc:
            raise AuthenticationError("Invalid client JWKS URL") from exc

        transport = pinned_http_transport(jwks_uri, addresses)
        timeout = httpx.Timeout(5.0, connect=3.0)
        async with (
            httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client,
            # The URL retains the original hostname, so HTTP Host, TLS SNI, and
            # certificate hostname verification are unchanged. Only TCP uses the
            # previously validated address through the pinned network backend.
            client.stream(
                "GET",
                jwks_uri,
                headers={"Accept": "application/json", "User-Agent": "finance-mcp-cimd/1.0"},
            ) as response,
        ):
            if response.status_code != 200:
                raise AuthenticationError("Unable to fetch client JWKS")
            if "json" not in response.headers.get("content-type", "").lower():
                raise AuthenticationError("Client JWKS is not JSON")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_JWKS_BYTES:
                    raise AuthenticationError("Client JWKS is too large")

        payload = json.loads(body)
        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list):
            raise AuthenticationError("Client JWKS is invalid")
        return [key for key in keys if isinstance(key, dict)]

    async def _verify_assertion(
        self,
        client: OAuthClientInformationFull,
        assertion: str,
        client_id: str,
    ) -> None:
        jwks_uri = getattr(client, "jwks_uri", None)
        if not jwks_uri:
            raise AuthenticationError("Client jwks_uri is required")
        try:
            keys = await self._load_jwks(str(jwks_uri))
            header = jwt.get_unverified_header(assertion)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise AuthenticationError("Unsupported client assertion header")
            jwk = next((key for key in keys if key.get("kid") == header["kid"]), None)
            if jwk is None:
                raise AuthenticationError("Client assertion key not found")
            signing_key = jwt.PyJWK.from_dict(jwk).key
            claims = jwt.decode(
                assertion,
                signing_key,
                algorithms=["RS256"],
                audience=f"{self.provider.issuer_url}/token",
                issuer=client_id,
                options={
                    "require": ["iss", "sub", "aud", "exp", "iat", "jti"],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
            if claims.get("sub") != client_id:
                raise AuthenticationError("Client assertion subject mismatch")
            expires_at = self._validate_numeric_dates(claims)
            jti = claims.get("jti")
            if not isinstance(jti, str) or not jti:
                raise AuthenticationError("Client assertion jti is invalid")
            self._record_jti(client_id, jti, expires_at + _JWT_LEEWAY_SECONDS)
        except AuthenticationError:
            raise
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            OverflowError,
            RecursionError,
            ValueError,
            KeyError,
            TypeError,
            jwt.PyJWTError,
        ) as exc:
            raise AuthenticationError("Invalid client assertion") from exc

    @staticmethod
    def _validate_numeric_dates(claims: dict[str, Any]) -> float:
        numeric_dates: dict[str, float] = {}
        for claim_name in ("exp", "iat"):
            value = claims[claim_name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{claim_name} must be a NumericDate")
            numeric_value = float(value)
            if not math.isfinite(numeric_value):
                raise ValueError(f"{claim_name} must be finite")
            numeric_dates[claim_name] = numeric_value

        now = time.time()
        expires_at = numeric_dates["exp"]
        issued_at = numeric_dates["iat"]
        if issued_at > now + _JWT_LEEWAY_SECONDS:
            raise ValueError("Client assertion was issued in the future")
        if expires_at <= now - _JWT_LEEWAY_SECONDS:
            raise ValueError("Client assertion has expired")
        if expires_at <= issued_at:
            raise ValueError("Client assertion must expire after issuance")
        if expires_at - issued_at > _MAX_ASSERTION_LIFETIME_SECONDS:
            raise ValueError("Client assertion lifetime is too long")
        if expires_at > now + _MAX_ASSERTION_LIFETIME_SECONDS + _JWT_LEEWAY_SECONDS:
            raise ValueError("Client assertion expiration is too distant")
        return expires_at

    def _record_jti(self, client_id: str, jti: str, expires_at: float) -> None:
        now = time.time()
        replay_key = (client_id, jti)
        with self._lock:
            self._used_jtis = {
                value: expiry for value, expiry in self._used_jtis.items() if expiry > now
            }
            if replay_key in self._used_jtis:
                raise AuthenticationError("Client assertion has already been used")
            if len(self._used_jtis) >= _MAX_REPLAY_CACHE_ENTRIES:
                raise AuthenticationError("Client assertion replay cache is full")
            self._used_jtis[replay_key] = expires_at

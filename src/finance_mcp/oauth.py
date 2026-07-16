from __future__ import annotations

import asyncio
import hmac
import html
import ipaddress
import json
import os
import secrets
import socket
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, ValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response


class FinanceOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Single-user OAuth 2.1 provider with SQLite-backed client and token state."""

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_url: str,
        username: str,
        password: str,
        database_path: str,
        scope: str = "finance:read",
        access_token_ttl_seconds: int = 3600,
        refresh_token_ttl_seconds: int = 30 * 24 * 3600,
    ) -> None:
        if not username:
            raise ValueError("OAuth username must be configured")
        if access_token_ttl_seconds <= 0 or refresh_token_ttl_seconds <= 0:
            raise ValueError("OAuth token TTLs must be greater than zero")

        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = resource_url
        self.username = username
        self.scope = scope
        self.access_token_ttl_seconds = access_token_ttl_seconds
        self.refresh_token_ttl_seconds = refresh_token_ttl_seconds
        self._database_path = Path(database_path)
        self._lock = threading.RLock()
        self._cimd_cache: dict[str, tuple[float, OAuthClientInformationFull]] = {}
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self.password = password or self._load_or_create_password()
        self._initialize_database()

    def _load_or_create_password(self) -> str:
        password_path = self._database_path.parent / "admin_password"
        try:
            return password_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            password = secrets.token_urlsafe(24)
            file_descriptor = os.open(
                password_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(password)
            return password

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_database(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    client_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
                    code TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_access_tokens (
                    token TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
                    token TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_token_pairs (
                    access_token TEXT PRIMARY KEY,
                    refresh_token TEXT UNIQUE NOT NULL,
                    resource TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )

    def _put_model(self, table: str, key_column: str, key: str, model: Any) -> None:
        payload = model.model_dump_json()
        with self._lock, self._connect() as connection:
            connection.execute(
                f"INSERT OR REPLACE INTO {table} ({key_column}, payload) VALUES (?, ?)",
                (key, payload),
            )

    def _get_model(self, table: str, key_column: str, key: str, model_type: Any) -> Any | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"SELECT payload FROM {table} WHERE {key_column} = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return model_type.model_validate_json(row["payload"])

    def _delete(self, table: str, key_column: str, key: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(f"DELETE FROM {table} WHERE {key_column} = ?", (key,))

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        registered_client = self._get_model(
            "oauth_clients",
            "client_id",
            client_id,
            OAuthClientInformationFull,
        )
        if registered_client is not None:
            return registered_client
        if not client_id.startswith("https://"):
            return None

        cached = self._cimd_cache.get(client_id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]

        client = await self._load_cimd_client(client_id)
        if client is not None:
            self._cimd_cache[client_id] = (time.monotonic() + 300, client)
        return client

    @staticmethod
    async def _validate_public_cimd_url(client_id: str) -> None:
        parsed = urlsplit(client_id)
        decoded_path = unquote(parsed.path)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.path
            or parsed.path == "/"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or any(segment in {".", ".."} for segment in decoded_path.split("/"))
        ):
            raise ValueError("Invalid CIMD client_id URL")

        loop = asyncio.get_running_loop()
        addresses = await loop.getaddrinfo(
            parsed.hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
        if not addresses:
            raise ValueError("CIMD hostname did not resolve")
        for address in addresses:
            if not ipaddress.ip_address(address[4][0]).is_global:
                raise ValueError("CIMD hostname resolves to a non-public address")

    async def _load_cimd_client(self, client_id: str) -> OAuthClientInformationFull | None:
        try:
            await self._validate_public_cimd_url(client_id)
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(5.0, connect=3.0),
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    "GET",
                    client_id,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "finance-mcp-cimd/1.0",
                    },
                ) as response,
            ):
                if response.status_code != 200:
                    return None
                if "json" not in response.headers.get("content-type", "").lower():
                    return None
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 64 * 1024:
                        return None

            metadata = json.loads(body)
            if not isinstance(metadata, dict) or metadata.get("client_id") != client_id:
                return None
            if not isinstance(metadata.get("client_name"), str):
                return None
            redirect_uris = metadata.get("redirect_uris")
            if not isinstance(redirect_uris, list) or not redirect_uris:
                return None
            if metadata.get("token_endpoint_auth_method", "none") != "none":
                return None
            metadata["token_endpoint_auth_method"] = "none"
            metadata["scope"] = self.scope
            return OAuthClientInformationFull.model_validate(metadata)
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            OSError,
            ValidationError,
            ValueError,
        ):
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("OAuth client_id is required")
        self._put_model(
            "oauth_clients",
            "client_id",
            client_info.client_id,
            client_info,
        )

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        if not client.client_id:
            raise ValueError("OAuth client_id is required")

        requested_scopes = params.scopes or [self.scope]
        if any(scope != self.scope for scope in requested_scopes):
            raise HTTPException(400, "Unsupported OAuth scope")

        state = params.state or secrets.token_urlsafe(24)
        payload = {
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "client_id": client.client_id,
            "resource": params.resource or self.resource_url,
            "scopes": requested_scopes,
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO oauth_states (state, payload, expires_at) VALUES (?, ?, ?)",
                (state, json.dumps(payload), time.time() + 600),
            )

        return f"{self.issuer_url}/login?{urlencode({'state': state})}"

    def _load_state(self, state: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload, expires_at FROM oauth_states WHERE state = ?",
                (state,),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) < time.time():
                connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
                return None
        return json.loads(row["payload"])

    async def get_login_page(
        self,
        state: str,
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        if self._load_state(state) is None:
            raise HTTPException(400, "Invalid or expired OAuth state")

        safe_state = html.escape(state, quote=True)
        safe_action = html.escape(f"{self.issuer_url}/login/callback", quote=True)
        error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
        content = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Finance MCP Authorization</title>
<style>
body {{
  font-family: system-ui, -apple-system, sans-serif;
  background:#f5f6f8;
  margin:0;
  color:#20242c;
}}
main {{
  max-width:440px;
  margin:8vh auto;
  background:#fff;
  border:1px solid #d8dde5;
  border-radius:16px;
  padding:28px;
  box-shadow:0 18px 48px rgba(20,24,32,.10);
}}
h1 {{ margin:0 0 8px; font-size:24px; }}
p {{ color:#626b78; line-height:1.55; }}
label {{ display:block; margin:16px 0 6px; font-weight:700; }}
input {{
  width:100%;
  box-sizing:border-box;
  padding:11px 12px;
  border:1px solid #b8c0cc;
  border-radius:9px;
  font-size:16px;
}}
button {{
  width:100%;
  margin-top:20px;
  padding:12px;
  border:0;
  border-radius:9px;
  background:#134f5c;
  color:white;
  font-size:16px;
  font-weight:800;
  cursor:pointer;
}}
.error {{
  margin:14px 0;
  padding:10px 12px;
  border-radius:8px;
  background:#fff0f0;
  color:#9f1d1d;
}}
.scope {{
  margin-top:18px;
  padding:12px;
  border-radius:9px;
  background:#f0f3f6;
  font-size:14px;
}}
</style>
</head>
<body><main>
<h1>授權 Finance MCP</h1>
<p>登入後，ChatGPT 只能呼叫唯讀股票資料與分析工具，不具備下單或帳戶操作能力。</p>
{error_html}
<form action="{safe_action}" method="post">
<input type="hidden" name="state" value="{safe_state}">
<label for="username">帳號</label>
<input id="username" name="username" autocomplete="username" required>
<label for="password">密碼</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<div class="scope">授權範圍：<strong>{html.escape(self.scope)}</strong></div>
<button type="submit">登入並授權</button>
</form>
</main></body></html>"""
        return HTMLResponse(content=content, status_code=status_code)

    async def handle_login_callback(self, request: Request) -> Response:
        form = await request.form()
        state = form.get("state")
        username = form.get("username")
        password = form.get("password")
        if not all(isinstance(value, str) for value in (state, username, password)):
            raise HTTPException(400, "Invalid login form")

        assert isinstance(state, str)
        assert isinstance(username, str)
        assert isinstance(password, str)
        state_data = self._load_state(state)
        if state_data is None:
            raise HTTPException(400, "Invalid or expired OAuth state")

        valid_username = hmac.compare_digest(username, self.username)
        valid_password = hmac.compare_digest(password, self.password)
        if not (valid_username and valid_password):
            return await self.get_login_page(
                state,
                error="帳號或密碼錯誤",
                status_code=401,
            )

        authorization_code_value = f"mcp_{secrets.token_urlsafe(32)}"
        authorization_code = AuthorizationCode(
            code=authorization_code_value,
            client_id=state_data["client_id"],
            redirect_uri=AnyHttpUrl(state_data["redirect_uri"]),
            redirect_uri_provided_explicitly=bool(state_data["redirect_uri_provided_explicitly"]),
            expires_at=time.time() + 300,
            scopes=list(state_data["scopes"]),
            code_challenge=state_data["code_challenge"],
            resource=state_data["resource"],
            subject=self.username,
        )
        self._put_model(
            "oauth_authorization_codes",
            "code",
            authorization_code_value,
            authorization_code,
        )
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))

        redirect_url = construct_redirect_uri(
            state_data["redirect_uri"],
            code=authorization_code_value,
            state=state,
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code = self._get_model(
            "oauth_authorization_codes",
            "code",
            authorization_code,
            AuthorizationCode,
        )
        if code is None or code.client_id != client.client_id:
            return None
        return code

    def _issue_token_pair(
        self,
        *,
        client_id: str,
        scopes: list[str],
        resource: str,
        subject: str | None,
    ) -> OAuthToken:
        now = int(time.time())
        access_value = f"mcp_at_{secrets.token_urlsafe(40)}"
        refresh_value = f"mcp_rt_{secrets.token_urlsafe(40)}"
        access_token = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + self.access_token_ttl_seconds,
            resource=resource,
            subject=subject,
            claims={"iss": self.issuer_url, "aud": resource, "sub": subject},
        )
        refresh_token = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + self.refresh_token_ttl_seconds,
            subject=subject,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO oauth_access_tokens (token, payload) VALUES (?, ?)",
                (access_value, access_token.model_dump_json()),
            )
            connection.execute(
                "INSERT INTO oauth_refresh_tokens (token, payload) VALUES (?, ?)",
                (refresh_value, refresh_token.model_dump_json()),
            )
            connection.execute(
                "INSERT INTO oauth_token_pairs "
                "(access_token, refresh_token, resource) VALUES (?, ?, ?)",
                (access_value, refresh_value, resource),
            )
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=self.access_token_ttl_seconds,
            scope=" ".join(scopes),
            refresh_token=refresh_value,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        if not client.client_id or authorization_code.client_id != client.client_id:
            raise ValueError("Invalid OAuth authorization code")
        resource = authorization_code.resource or self.resource_url
        token = self._issue_token_pair(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            resource=resource,
            subject=authorization_code.subject,
        )
        self._delete(
            "oauth_authorization_codes",
            "code",
            authorization_code.code,
        )
        return token

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self._get_model(
            "oauth_access_tokens",
            "token",
            token,
            AccessToken,
        )
        if access_token is None:
            return None
        if access_token.expires_at and access_token.expires_at < time.time():
            await self.revoke_token(access_token)
            return None
        if access_token.resource != self.resource_url:
            return None
        return access_token

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        token = self._get_model(
            "oauth_refresh_tokens",
            "token",
            refresh_token,
            RefreshToken,
        )
        if token is None or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not client.client_id or refresh_token.client_id != client.client_id:
            raise ValueError("Invalid OAuth refresh token")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT access_token, resource FROM oauth_token_pairs WHERE refresh_token = ?",
                (refresh_token.token,),
            ).fetchone()
            if row is None:
                raise ValueError("Invalid OAuth refresh token")
            old_access = row["access_token"]
            resource = row["resource"]
            connection.execute(
                "DELETE FROM oauth_token_pairs WHERE refresh_token = ?",
                (refresh_token.token,),
            )
            connection.execute(
                "DELETE FROM oauth_access_tokens WHERE token = ?",
                (old_access,),
            )
            connection.execute(
                "DELETE FROM oauth_refresh_tokens WHERE token = ?",
                (refresh_token.token,),
            )
        return self._issue_token_pair(
            client_id=client.client_id,
            scopes=scopes,
            resource=resource,
            subject=refresh_token.subject,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        token_value = token.token
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT access_token, refresh_token
                FROM oauth_token_pairs
                WHERE access_token = ? OR refresh_token = ?
                """,
                (token_value, token_value),
            ).fetchone()
            if row is None:
                connection.execute(
                    "DELETE FROM oauth_access_tokens WHERE token = ?",
                    (token_value,),
                )
                connection.execute(
                    "DELETE FROM oauth_refresh_tokens WHERE token = ?",
                    (token_value,),
                )
                return
            connection.execute(
                "DELETE FROM oauth_token_pairs WHERE access_token = ?",
                (row["access_token"],),
            )
            connection.execute(
                "DELETE FROM oauth_access_tokens WHERE token = ?",
                (row["access_token"],),
            )
            connection.execute(
                "DELETE FROM oauth_refresh_tokens WHERE token = ?",
                (row["refresh_token"],),
            )

from __future__ import annotations

import pytest

from finance_mcp.server import _oauth_transport_security


def test_oauth_transport_security_allows_public_and_local_hosts() -> None:
    security = _oauth_transport_security("https://finance.puin.dpdns.org")

    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == [
        "finance.puin.dpdns.org",
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    ]
    assert security.allowed_origins[0] == "https://finance.puin.dpdns.org"


def test_oauth_transport_security_preserves_non_default_port() -> None:
    security = _oauth_transport_security("https://finance.example:8443")

    assert security.allowed_hosts[0] == "finance.example:8443"
    assert security.allowed_origins[0] == "https://finance.example:8443"


def test_oauth_transport_security_requires_absolute_issuer_url() -> None:
    with pytest.raises(ValueError, match="must be an absolute URL"):
        _oauth_transport_security("finance.puin.dpdns.org")

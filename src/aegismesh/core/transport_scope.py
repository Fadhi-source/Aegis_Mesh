"""
core/transport_scope.py
=======================
Network boundary enforcer — AegisMesh v1.x is strictly localhost-only.
Every endpoint URL is validated here before a connection is attempted.
"""
from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


class TransportScopeError(RuntimeError):
    """Raised when an agent endpoint falls outside the localhost boundary."""


def validate_localhost_endpoint(endpoint: str, context: str = "agent") -> None:
    """
    Validates that `endpoint` is localhost-bound.

    Called at:
      1. AgentCard POST /register  (Registry)
      2. A2ABusClient.__init__()   (Supervisor dispatch)

    Raises TransportScopeError on any non-localhost host.
    """
    try:
        parsed = urlparse(endpoint)
    except Exception as exc:
        raise TransportScopeError(
            f"[SCOPE] {context}: malformed endpoint URL '{endpoint}': {exc}"
        ) from exc

    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise TransportScopeError(
            f"[SCOPE VIOLATION] {context} endpoint '{endpoint}' targets '{host}'. "
            "AegisMesh v1.x permits only localhost (127.0.0.1 / ::1). "
            "Cross-machine support requires AegisMesh v2.x with mTLS."
        )
    if parsed.scheme not in ("http",):
        raise TransportScopeError(
            f"[SCOPE] {context}: scheme '{parsed.scheme}' is not permitted. "
            "Use http:// on loopback."
        )

"""
core/a2a_client.py
==================
A2A Bus Client — JSON-RPC 2.0 over HTTP/1.1 Keep-Alive.

Key design decisions:
  - One AsyncClient per target agent (connection pool recycled across calls)
  - http1=True, http2=False: single-stream per connection, no HOL blocking risk
  - Bounded pool: max_keepalive_connections=5 prevents FD exhaustion on Windows
  - Idempotency: `id` field is monotonic per-client, callee must honour replays
  - Endpoint validated against localhost scope boundary before first use
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from aegismesh.core.transport_scope import validate_localhost_endpoint

logger = logging.getLogger("aegis.a2a_client")

ALLOWED_METHODS = {"execute_task", "health_check", "get_capabilities"}


class A2ARPCError(Exception):
    """Structured JSON-RPC error raised when the callee returns an error object."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(f"[RPC {code}] {message}")


class A2ABusClient:
    """
    Persistent HTTP/1.1 client for a single target agent endpoint.

    Lifecycle:
        client = A2ABusClient("http://127.0.0.1:8101")
        result = await client.call("execute_task", {...}, trace_id="inv_abc")
        await client.aclose()   # Always close — use as async context manager in tests
    """

    def __init__(self, agent_url: str, timeout: float = 10.0) -> None:
        # Raises TransportScopeError immediately if URL is non-localhost
        validate_localhost_endpoint(agent_url, context=f"A2ABusClient({agent_url})")
        self._url = agent_url.rstrip("/")
        self._req_counter = 0

        self._session = httpx.AsyncClient(
            base_url=self._url,
            http1=True,
            http2=False,
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=30.0,
            ),
            timeout=httpx.Timeout(
                connect=2.0,
                read=timeout,
                write=5.0,
                pool=1.0,
            ),
            headers={"Content-Type": "application/json; protocol=aegismesh/1.4"},
        )

    def _next_id(self) -> str:
        self._req_counter += 1
        return f"req_{self._req_counter:06d}"

    async def call(
        self,
        method: str,
        params: dict[str, Any],
        trace_id: str,
    ) -> dict:
        """
        Executes a JSON-RPC 2.0 request.

        Idempotency guarantee:
          The `id` field is stable per client instance — callee MUST return
          identical results for a replayed `id`. The Supervisor retries on
          transport errors by creating a fresh client (new counter = new IDs).
        """
        if method not in ALLOWED_METHODS:
            raise ValueError(f"Unknown RPC method '{method}'. Allowed: {ALLOWED_METHODS}")

        rpc_id = self._next_id()
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": {**params, "trace_id": trace_id},
            "id": rpc_id,
        }

        logger.debug(
            "→ RPC %s %s [%s] trace=%s", self._url, method, rpc_id, trace_id
        )

        try:
            resp = await self._session.post("/rpc", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise A2ARPCError(-32000, f"HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except httpx.TimeoutException as exc:
            raise A2ARPCError(-32001, f"Request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise A2ARPCError(-32002, f"Connection refused to {self._url}: {exc}") from exc

        body: dict = resp.json()

        # JSON-RPC error object
        if "error" in body:
            err = body["error"]
            raise A2ARPCError(err.get("code", -32603), err.get("message", "Unknown RPC error"))

        # Validate response envelope
        if body.get("jsonrpc") != "2.0" or "result" not in body:
            raise A2ARPCError(-32603, f"Malformed JSON-RPC response: {body}")

        logger.debug("← RPC %s [%s] OK", method, rpc_id)
        return body["result"]

    async def aclose(self) -> None:
        await self._session.aclose()

    async def __aenter__(self) -> "A2ABusClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

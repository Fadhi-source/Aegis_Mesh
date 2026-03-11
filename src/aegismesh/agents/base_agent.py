"""
agents/base_agent.py
====================
BaseAgent — the foundation every specialist agent inherits from.

Provides:
  - FastAPI JSON-RPC server with /rpc, /health, and /capabilities endpoints
  - Source-level static analysis via SecuritySandbox at construction time
  - HeartbeatManager: auto re-registration on Registry restart detection
  - Pydantic JSON-RPC request validation with size guard
  - Version negotiation middleware (Content-Type: protocol=aegismesh/1.4)

Subclass pattern:
    class LogAgent(BaseAgent):
        def get_card(self) -> AgentCard:
            return AgentCard(id="log-agent-001", ...)

        async def execute_task(self, params: dict, trace_id: str) -> dict:
            ...
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from aegismesh.agents.security_sandbox import SecuritySandbox
from aegismesh.core.config import REGISTRY_URL
from aegismesh.registry.models import AgentCard, JsonRpcRequest

logger = logging.getLogger("aegis.base_agent")

SUPPORTED_MAJOR = 1
SUPPORTED_MINOR_MAX = 4

_VERSION_RE = re.compile(r"protocol=aegismesh/(\d+)\.(\d+)")


class BaseAgent(ABC):
    """
    Abstract base class for all AegisMesh specialist agents.
    Instantiate with `create_app()` to get a uvicorn-ready ASGI app.
    """

    def __init__(self) -> None:
        # Static analysis — validates this class's own source at construction
        SecuritySandbox.validate_self(self.__class__)
        self._heartbeat_task: asyncio.Task | None = None

    @abstractmethod
    def get_card(self) -> AgentCard:
        """Return this agent's AgentCard (used for self-registration)."""
        ...

    @abstractmethod
    async def execute_task(self, params: dict, trace_id: str) -> dict:
        """
        Main task handler. Must be non-destructive (read-only diagnostics only).
        Return a dict that will be placed in the JSON-RPC `result` field.
        """
        ...

    def create_app(self) -> FastAPI:
        """Returns the FastAPI ASGI application for this agent."""

        @asynccontextmanager
        async def lifespan(application: FastAPI):
            await self._register()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            yield
            if self._heartbeat_task:
                self._heartbeat_task.cancel()

        agent_app = FastAPI(
            title=self.get_card().name,
            version=self.get_card().version,
            lifespan=lifespan,
        )

        # ── Version negotiation middleware ────────────────────────────────────
        @agent_app.middleware("http")
        async def version_middleware(request: Request, call_next):
            ct = request.headers.get("content-type", "")
            match = _VERSION_RE.search(ct)
            if match:
                major = int(match.group(1))
                if major != SUPPORTED_MAJOR:
                    return JSONResponse(
                        status_code=415,
                        content={
                            "error": "PROTOCOL_INCOMPATIBLE",
                            "message": f"Agent supports aegismesh/{SUPPORTED_MAJOR}.x only.",
                            "supported": [f"{SUPPORTED_MAJOR}.{SUPPORTED_MINOR_MAX}"],
                        },
                    )
            return await call_next(request)

        # ── JSON-RPC endpoint ─────────────────────────────────────────────────
        @agent_app.post("/rpc")
        async def rpc_handler(raw: dict):
            # Validate envelope
            try:
                req = JsonRpcRequest(**raw)
            except Exception as exc:
                return JSONResponse(
                    status_code=200,
                    content={
                        "jsonrpc": "2.0",
                        "error": {"code": -32600, "message": str(exc)},
                        "id": raw.get("id", "unknown"),
                    },
                )

            trace_id: str = req.params.get("trace_id", "unknown")

            if req.method == "execute_task":
                try:
                    result = await self.execute_task(req.params, trace_id)
                    return {
                        "jsonrpc": "2.0",
                        "result": result,
                        "id": req.id,
                    }
                except Exception as exc:
                    logger.exception("execute_task error for trace %s", trace_id)
                    return JSONResponse(
                        status_code=200,
                        content={
                            "jsonrpc": "2.0",
                            "error": {"code": -32603, "message": str(exc)},
                            "id": req.id,
                        },
                    )

            if req.method == "health_check":
                return {
                    "jsonrpc": "2.0",
                    "result": {"status": "ok", "ts": time.time()},
                    "id": req.id,
                }

            if req.method == "get_capabilities":
                card = self.get_card()
                return {
                    "jsonrpc": "2.0",
                    "result": card.model_dump(),
                    "id": req.id,
                }

        # ── REST helpers ──────────────────────────────────────────────────────
        @agent_app.get("/health")
        async def health():
            return {"status": "ok", "agent": self.get_card().id, "ts": time.time()}

        @agent_app.get("/capabilities")
        async def capabilities():
            return self.get_card().model_dump()

        return agent_app

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _register(self) -> None:
        card = self.get_card()
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.post(
                    f"{REGISTRY_URL}/register",
                    json=card.model_dump(),
                )
                if resp.status_code in (200, 201):
                    logger.info("Agent '%s' registered with Registry.", card.id)
                else:
                    logger.warning(
                        "Agent '%s' registration returned %d: %s",
                        card.id,
                        resp.status_code,
                        resp.text,
                    )
            except Exception as exc:
                logger.error("Agent '%s' registration failed: %s", card.id, exc)

    async def _heartbeat_loop(self) -> None:
        card = self.get_card()
        interval = card.health.heartbeat_interval_s
        while True:
            await asyncio.sleep(interval)
            success = await self._send_heartbeat(card.id)
            if not success:
                logger.warning(
                    "Heartbeat failed for '%s' — attempting re-registration.", card.id
                )
                await self._register()

    async def _send_heartbeat(self, agent_id: str) -> bool:
        async with httpx.AsyncClient(timeout=3.0) as client:
            try:
                resp = await client.post(
                    f"{REGISTRY_URL}/heartbeat",
                    json={"agent_id": agent_id},
                )
                return resp.status_code == 200
            except Exception:
                return False

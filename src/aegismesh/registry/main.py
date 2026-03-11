"""
registry/main.py
================
AegisRegistry — FastAPI Control Plane.

Endpoints:
  POST /register                → Agent self-registration
  POST /heartbeat               → Agent keep-alive
  GET  /.well-known/agents.json → RFC 8615 discovery
  GET  /agents/{id}/health      → Per-agent health metrics
  GET  /health                  → Registry liveness probe
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from aegismesh.core.config import REGISTRY_DB
from aegismesh.core.transport_scope import validate_localhost_endpoint, TransportScopeError
from aegismesh.registry.db import RegistryDatabase
from aegismesh.registry.models import AgentCard
from aegismesh.registry.recovery import RegistryRecovery

logger = logging.getLogger("aegis.registry")

# ── Database singleton ────────────────────────────────────────────────────────
_db: RegistryDatabase | None = None


def get_db() -> RegistryDatabase:
    global _db
    if _db is None:
        _db = RegistryDatabase(REGISTRY_DB)
    return _db


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Runs startup recovery before accepting requests."""
    db = get_db()
    recovery = RegistryRecovery(db)
    result = await recovery.run_startup_recovery()
    logger.info("Registry startup recovery: %s", result)
    yield
    # Graceful shutdown — nothing to flush for SQLite WAL


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AegisRegistry",
    version="1.4.0",
    description="Control Plane for AegisMesh — RFC 8615 compliant agent directory.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/register", status_code=201)
async def register_agent(card: AgentCard):
    """
    Agent self-registration.
    Validates that endpoint is localhost-bound (TransportScope contract).
    """
    try:
        validate_localhost_endpoint(card.endpoint, context=f"AgentCard[{card.id}]")
    except TransportScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    db = get_db()
    db.upsert_agent(card.model_dump())
    logger.info("Agent registered: %s @ %s", card.id, card.endpoint)
    return {"status": "registered", "agent_id": card.id, "timestamp": time.time()}


class HeartbeatPayload(BaseModel):
    agent_id: str


@app.post("/heartbeat")
async def heartbeat(payload: HeartbeatPayload):
    db = get_db()
    db.update_last_seen(payload.agent_id)
    return {"status": "ok", "ts": time.time()}


# Primary discovery endpoint — always accessible
@app.get("/agents")
async def list_agents(skill: Optional[str] = Query(default=None)):
    """
    Agent discovery endpoint.
    Returns CLOSED-circuit agents ranked by (success_rate DESC, latency ASC).
    """
    db = get_db()
    agents = db.list_healthy_agents(skill_filter=skill)
    return {
        "agents": agents,
        "count": len(agents),
        "timestamp": time.time(),
        "registry_version": "1.4.0",
    }

# RFC 8615 alias — registered via add_api_route to bypass Starlette
# path-segment sanitization that strips leading dots in fastapi>=0.111
app.add_api_route(
    "/.well-known/agents.json",
    list_agents,
    methods=["GET"],
    include_in_schema=True,
)


@app.get("/agents/{agent_id}/health")
async def get_agent_health(agent_id: str):
    db = get_db()
    health = db.get_health(agent_id)
    if health is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return health


@app.get("/health")
async def registry_health():
    """Liveness probe — used by agents' HeartbeatManager to detect Registry restarts."""
    return {"status": "ok", "ts": time.time(), "version": "1.4.0"}

"""
gateway/main.py
===============
AegisMesh Gateway — the single public HTTP entry point for investigations.

POST /investigate   → Accepts a natural language query, returns diagnostic report
GET  /health        → Liveness probe
GET  /docs          → FastAPI auto-generated interactive docs (Swagger UI)

The Gateway:
  1. Validates the incoming query
  2. Creates an InvestigationState with a unique trace_id
  3. Runs the compiled LangGraph supervisor graph with a hard timeout
  4. Returns the final report and metadata in a structured JSON response
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from aegismesh.core.config import INVESTIGATION_TIMEOUT_S
from aegismesh.core.governor import get_governor
from aegismesh.core.telemetry import new_trace_id
from aegismesh.supervisor.graph import get_supervisor_graph
from aegismesh.supervisor.state import InvestigationState

logger = logging.getLogger("aegis.gateway")


# ── Input / Output Models ─────────────────────────────────────────────────────

class InvestigateRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2048, description="Natural language diagnostic query")


class InvestigateResponse(BaseModel):
    trace_id: str
    report: str
    termination_reason: str
    low_confidence: bool
    confidence_score: float
    facts_collected: int
    agents_dispatched: int
    errors: list[str]
    duration_seconds: float


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup: initialise RAM Governor and pre-compile the LangGraph."""
    governor = get_governor()
    await governor.start()
    logger.info("Gateway: RAM Governor started.")

    # Pre-warm the LangGraph (avoids cold-start on first user request)
    logger.info("Gateway: Pre-compiling Supervisor graph...")
    get_supervisor_graph()
    logger.info("Gateway: Supervisor graph ready.")

    yield

    await governor.stop()
    logger.info("Gateway: shutdown complete.")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AegisMesh Gateway",
    version="1.4.0",
    description=(
        "Sovereign Agent-to-Agent Diagnostic Mesh — submit a PC problem description "
        "and receive a structured causal diagnostic report."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Gateway is localhost-only; wildcard is safe
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/investigate", response_model=InvestigateResponse)
async def investigate(request: InvestigateRequest):
    """
    Submit a natural language diagnostic query.

    Example queries:
      - "My PC has been lagging for the past hour and feels very slow"
      - "Application X crashes when I open large files"
      - "My internet connection drops randomly throughout the day"
    """
    trace_id = new_trace_id()
    start_time = time.time()

    logger.info("[%s] Investigation started: '%s'", trace_id, request.query[:80])

    # Initialise state
    initial_state = InvestigationState(
        user_query=request.query,
        trace_id=trace_id,
    )

    # Run the graph with a hard timeout enforced at the Gateway layer
    graph = get_supervisor_graph()
    try:
        final_state: InvestigationState = await asyncio.wait_for(
            graph.ainvoke(initial_state),
            timeout=INVESTIGATION_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error("[%s] Investigation timed out after %.0fs", trace_id, INVESTIGATION_TIMEOUT_S)
        raise HTTPException(
            status_code=504,
            detail={
                "trace_id": trace_id,
                "error": "INVESTIGATION_TIMEOUT",
                "message": f"The investigation exceeded the {INVESTIGATION_TIMEOUT_S}s time limit.",
            }
        )
    except Exception as exc:
        logger.exception("[%s] Supervisor graph error", trace_id)
        raise HTTPException(status_code=500, detail={"trace_id": trace_id, "error": str(exc)})

    duration = round(time.time() - start_time, 2)
    logger.info(
        "[%s] Done in %.2fs | reason=%s | confidence=%.2f",
        trace_id, duration,
        final_state.get("termination_reason"),
        final_state.get("overall_confidence", 0.0),
    )

    return InvestigateResponse(
        trace_id=trace_id,
        report=final_state.get("final_report", ""),
        termination_reason=final_state.get("termination_reason") or "UNKNOWN",
        low_confidence=final_state.get("low_confidence", False),
        confidence_score=final_state.get("overall_confidence", 0.0),
        facts_collected=len(final_state.get("raw_facts", [])),
        agents_dispatched=len(final_state.get("selected_agents", [])),
        errors=final_state.get("dispatch_errors", []),
        duration_seconds=duration,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "ts": time.time(), "component": "gateway", "version": "1.4.0"}

# Mount frontend at the very end to act as a catch-all for assets and index.html
import os
frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
else:
    logger.warning("Frontend directory not found at %s. UI disabled.", frontend_dir)

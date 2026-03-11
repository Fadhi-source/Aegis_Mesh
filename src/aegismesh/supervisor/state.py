"""
supervisor/state.py
===================
The canonical LangGraph state container for a single investigation.

All 5 nodes in the reasoning loop read from and write to this object.
`Annotated[list, operator.add]` fields are append-only — safe for
concurrent writes from multiple agents without race conditions.
"""
from __future__ import annotations

import operator
from typing import Annotated, Optional
from pydantic import BaseModel, Field


class RawFact(BaseModel):
    """A single diagnostic observation returned by one agent."""
    fact_type: str          # METRIC | EVENT | ANOMALY
    description: str = ""
    name: str = ""
    value: Optional[float] = None
    unit: Optional[str] = None
    source_agent: str = ""
    confidence: float = 0.8


class AgentCandidate(BaseModel):
    """A scored agent from the Registry, ready for dispatch."""
    agent_id: str
    name: str
    endpoint: str
    skills: list[str]
    cws_score: float        # Composite Weighted Score (0.0–1.0)
    matched_skill: str


class InvestigationState(BaseModel):
    """
    Full mutable state for one investigation lifecycle.

    LangGraph streams state through each node in sequence.
    Append-only list fields prevent concurrent write conflicts.
    """

    # ── User Input ─────────────────────────────────────────────────────────────
    user_query: str = ""
    trace_id: str = ""

    # ── Intent Parsing ─────────────────────────────────────────────────────────
    parsed_intent: str = ""              # Normalised intent string
    required_skills: list[str] = Field(default_factory=list)

    # ── Skill Discovery ────────────────────────────────────────────────────────
    selected_agents: list[AgentCandidate] = Field(default_factory=list)

    # ── Task Dispatch ──────────────────────────────────────────────────────────
    # append-only — safe for concurrent agent writes via asyncio.gather
    raw_facts: Annotated[list[RawFact], operator.add] = Field(default_factory=list)
    dispatch_errors: Annotated[list[str], operator.add] = Field(default_factory=list)

    # ── Evidence Synthesis ─────────────────────────────────────────────────────
    graph_stats: dict = Field(default_factory=dict)
    causal_chain: list[dict] = Field(default_factory=list)
    overall_confidence: float = 0.0

    # ── Causal Validation (Final Answer) ──────────────────────────────────────
    final_report: str = ""
    low_confidence: bool = False

    # ── Termination ────────────────────────────────────────────────────────────
    termination_reason: str = ""      # SUCCESS | TIMEOUT | LOW_CONFIDENCE | etc.

    class Config:
        arbitrary_types_allowed = True

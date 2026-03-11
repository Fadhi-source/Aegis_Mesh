"""
supervisor/graph.py
===================
LangGraph StateGraph — the AegisMesh Supervisor Engine.

Cyclic Reasoning Loop (as specified in the blueprint):
  START
    │
    ▼
  [1] intent_parser      ← LLM: extract intent + required_skills
    │
    ▼
  [2] skill_discovery    ← Registry: CWS-score and select agents per skill
    │
    ▼
  [3] task_dispatch      ← Governor: concurrent A2A calls to selected agents
    │
    ▼
  [4] evidence_synthesis ← EvidenceGraph: build DAG, run temporal correlation
    │
    ▼
  [5] causal_validation  ← LLM: produce final diagnostic report
    │
    ▼
   END

State is streamed through nodes as an InvestigationState object.
"""
from __future__ import annotations

import logging

from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from aegismesh.core.config import LLM_PROVIDER, LLM_MODEL, OLLAMA_BASE_URL, OPENAI_API_KEY
from aegismesh.supervisor.state import InvestigationState
from aegismesh.supervisor.nodes.intent_parser import intent_parser_node
from aegismesh.supervisor.nodes.skill_discovery import skill_discovery_node
from aegismesh.supervisor.nodes.task_dispatch import task_dispatch_node
from aegismesh.supervisor.nodes.evidence_synthesis import evidence_synthesis_node
from aegismesh.supervisor.nodes.causal_validation import causal_validation_node

logger = logging.getLogger("aegis.supervisor.graph")


def build_llm():
    """
    Builds the LLM client based on AEGIS_LLM_PROVIDER env var.
    Supports ollama (local) and openai (cloud).
    """
    if LLM_PROVIDER == "ollama":
        logger.info("Supervisor: using Ollama/%s at %s", LLM_MODEL, OLLAMA_BASE_URL)
        return ChatOllama(
            model=LLM_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0.1,         # Low temp: more deterministic diagnostics
            num_predict=512,          # Cap output length — prevents multi-minute responses
        )
    else:
        logger.info("Supervisor: using OpenAI/%s", LLM_MODEL)
        return ChatOpenAI(
            model=LLM_MODEL,
            api_key=OPENAI_API_KEY,
            temperature=0.1,
        )


def build_supervisor_graph():
    """
    Constructs and compiles the LangGraph StateGraph.
    Returns a compiled runnable graph.
    """
    llm = build_llm()

    # Wrap each node to inject the LLM where needed
    async def _intent_parser(state: InvestigationState):
        return await intent_parser_node(state, llm)

    async def _skill_discovery(state: InvestigationState):
        return await skill_discovery_node(state)

    async def _task_dispatch(state: InvestigationState):
        return await task_dispatch_node(state)

    async def _evidence_synthesis(state: InvestigationState):
        return await evidence_synthesis_node(state)

    async def _causal_validation(state: InvestigationState):
        return await causal_validation_node(state, llm)

    # ── Build Graph ───────────────────────────────────────────────────────────
    builder = StateGraph(InvestigationState)

    builder.add_node("intent_parser", _intent_parser)
    builder.add_node("skill_discovery", _skill_discovery)
    builder.add_node("task_dispatch", _task_dispatch)
    builder.add_node("evidence_synthesis", _evidence_synthesis)
    builder.add_node("causal_validation", _causal_validation)

    # ── Wire the sequence ─────────────────────────────────────────────────────
    builder.set_entry_point("intent_parser")
    builder.add_edge("intent_parser",     "skill_discovery")
    builder.add_edge("skill_discovery",   "task_dispatch")
    builder.add_edge("task_dispatch",     "evidence_synthesis")
    builder.add_edge("evidence_synthesis","causal_validation")
    builder.add_edge("causal_validation", END)

    graph = builder.compile()
    logger.info("Supervisor graph compiled successfully.")
    return graph


# ── Module-level singleton (lazy-initialised in gateway) ──────────────────────
_graph = None


def get_supervisor_graph():
    global _graph
    if _graph is None:
        _graph = build_supervisor_graph()
    return _graph

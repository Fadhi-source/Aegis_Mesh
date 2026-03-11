"""
supervisor/nodes/evidence_synthesis.py
========================================
Node 4 of 5 in the LangGraph reasoning loop.

Responsibility:
  - Converts all RawFact objects into EvidenceNode objects
  - Adds them into an EvidenceGraph
  - Runs temporal causal correlation pass
  - Persists the graph for post-mortem XAI access
  - Extracts the highest-confidence causal chain for the validator
"""
from __future__ import annotations

import logging
import time
import uuid

from aegismesh.evidence.graph import EvidenceGraph, EvidenceNode
from aegismesh.supervisor.state import InvestigationState

logger = logging.getLogger("aegis.supervisor.evidence_synthesis")


async def evidence_synthesis_node(state: InvestigationState) -> dict:
    """
    LangGraph node: builds the EvidenceGraph from raw agent facts.
    """
    logger.info(
        "[%s] Evidence synthesis: %d raw facts incoming",
        state.trace_id, len(state.raw_facts),
    )

    graph = EvidenceGraph(trace_id=state.trace_id)
    now = time.time()

    # Stream facts into the graph as they appear in raw_facts
    for i, fact in enumerate(state.raw_facts):
        # Temporal offset: treat each fact as arriving ~100ms after the previous
        # This creates a minimal time spread so the correlation algorithm can work
        fact_ts = now - (len(state.raw_facts) - i) * 0.1

        node = EvidenceNode(
            node_id=f"fact_{i:04d}_{uuid.uuid4().hex[:6]}",
            fact_type=fact.fact_type,
            description=fact.description or fact.name or "No description",
            value=fact.value,
            unit=fact.unit,
            timestamp=fact_ts,
            source_agent=fact.source_agent,
            confidence=fact.confidence,
            metadata={"query_intent": state.parsed_intent},
        )
        added = graph.add_evidence(node)
        if not added:
            logger.debug("[%s] Node dropped (capacity limit): %s", state.trace_id, node.node_id)

    # Run O(N²) causal pass
    edges_created = graph.run_temporal_correlation()
    logger.info(
        "[%s] Temporal correlation: %d edges created from %d nodes",
        state.trace_id, edges_created, graph.stats["nodes"],
    )

    # Persist for XAI / post-mortem
    try:
        graph.persist()
    except Exception as exc:
        logger.warning("[%s] Graph persistence failed (non-fatal): %s", state.trace_id, exc)

    # Extract causal chain for the LLM validator
    chain = graph.get_highest_confidence_chain()

    return {
        "graph_stats": graph.stats,
        "causal_chain": chain,
        "overall_confidence": graph.overall_confidence,
    }

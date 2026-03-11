"""
supervisor/nodes/causal_validation.py
=======================================
Node 5 of 5 in the LangGraph reasoning loop — the "Brain's Mouth."

Responsibility:
  - Takes the causal_chain + graph_stats + all raw_facts
  - Prompts Llama 3 to produce a structured diagnostic report
  - Classifies the termination reason
  - Handles partial-confidence scenarios (advisory-only report)
"""
from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from aegismesh.supervisor.state import InvestigationState
from aegismesh.supervisor.termination import classify_termination, TerminationReason

logger = logging.getLogger("aegis.supervisor.causal_validation")

SYSTEM_PROMPT = """You are AegisMesh — a sovereign PC diagnostic system.
You have gathered evidence from specialist diagnostic agents running on the local machine.
Your task is to produce a final diagnostic report.

Format your report as a structured text with these sections:
1. ROOT CAUSE: What is most likely causing the user's problem
2. EVIDENCE: The key facts that support this conclusion
3. RECOMMENDATION: Specific actionable steps the user can take
4. CONFIDENCE: Your confidence level (high/medium/low)

Be concise, technical, and actionable. Do not mention system internals."""


def _build_user_prompt(state: InvestigationState) -> str:
    """Assembles the full evidence context into the LLM prompt."""
    lines = [
        f"User's problem: {state.user_query}",
        f"",
        f"Diagnostic Evidence Collected:",
    ]

    # Add raw facts
    for i, fact in enumerate(state.raw_facts):
        if fact.fact_type == "ANOMALY":
            lines.append(f"  ⚠ ANOMALY ({fact.source_agent}): {fact.description}")
        elif fact.fact_type == "METRIC":
            val_str = f" = {fact.value} {fact.unit or ''}".strip() if fact.value is not None else ""
            lines.append(f"  📊 METRIC ({fact.source_agent}): {fact.name}{val_str}")
        else:
            lines.append(f"  ℹ EVENT ({fact.source_agent}): {fact.description}")

    # Add causal chain if found
    if state.causal_chain:
        lines.append(f"")
        lines.append(f"Strongest Causal Chain Detected:")
        for node in state.causal_chain:
            lines.append(f"  → [{node.get('fact_type')}] {node.get('description', node.get('name',''))}")

    # Add graph stats
    stats = state.graph_stats
    lines.append(f"")
    lines.append(f"Evidence Graph: {stats.get('nodes', 0)} nodes, {stats.get('edges', 0)} causal edges")
    lines.append(f"Confidence Score: {state.overall_confidence:.2%}")

    return "\n".join(lines)


async def causal_validation_node(state: InvestigationState, llm) -> dict:
    """
    LangGraph node: uses Llama 3 to produce the final diagnostic report.
    """
    logger.info(
        "[%s] Causal validation: confidence=%.2f, facts=%d",
        state.trace_id, state.overall_confidence, len(state.raw_facts),
    )

    # Classify termination before calling LLM
    termination = classify_termination(
        confidence=state.overall_confidence,
        selected_agents=state.selected_agents,
        dispatch_errors=state.dispatch_errors,
    )

    # No facts at all — short-circuit
    if not state.raw_facts:
        return {
            "final_report": (
                "No diagnostic evidence was collected. This usually means:\n"
                "• All specialist agents are unavailable (check ports 8101–8103)\n"
                "• The query didn't match any known skills\n\n"
                "Try running: `.venv\\Scripts\\python mesh_bootstrapper.py`"
            ),
            "termination_reason": termination.value,
            "low_confidence": True,
        }

    # ── ADAPTIVE TIER 1: DETERMINISTIC REPORT ─────────────────────────────────
    # If confidence is extremely high (anomalies detected structurally), we do not need the LLM
    # to hallucinate or summarize. We can build a perfect report instantly.
    if state.overall_confidence >= 0.90:
        logger.info("[%s] Adaptive Routing: Confidence %.2f >= 0.90 lock. Generating deterministic report, bypassing LLM.", state.trace_id, state.overall_confidence)
        report = _deterministic_high_confidence_report(state)
        return {
            "final_report": report,
            "termination_reason": termination.value,
            "low_confidence": False,
        }

    # ── ADAPTIVE TIER 2: LLM SYNTHESIS ────────────────────────────────────────
    logger.info("[%s] Adaptive Routing: Confidence %.2f < 0.90. Escalating to LLM for complex synthesis.", state.trace_id, state.overall_confidence)
    
    # Build LLM prompt
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=_build_user_prompt(state)),
    ]

    report = ""
    try:
        response = await llm.ainvoke(messages)
        report = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        logger.error("[%s] LLM validation failed: %s", state.trace_id, exc)
        # Fallback: format the raw facts directly without LLM
        report = _fallback_report(state)

    # Annotate low-confidence reports
    is_low_confidence = termination in (
        TerminationReason.LOW_CONFIDENCE,
        TerminationReason.PARTIAL_CONFIDENCE,
        TerminationReason.FAILURE_DEADLOCK,
    )

    if is_low_confidence:
        report = f"⚠ ADVISORY ONLY (confidence: {state.overall_confidence:.0%})\n\n{report}"

    logger.info("[%s] Investigation complete: %s", state.trace_id, termination.value)

    return {
        "final_report": report,
        "termination_reason": termination.value,
        "low_confidence": is_low_confidence,
    }


def _deterministic_high_confidence_report(state: InvestigationState) -> str:
    """Builds a structured report rapidly when the root cause is mathematically obvious."""
    lines = []
    
    anomalies = [f for f in state.raw_facts if f.fact_type == "ANOMALY"]
    if anomalies:
        lines.append("1. ROOT CAUSE:")
        for a in anomalies:
            lines.append(f"   • {a.description}")
        lines.append("")
        
    lines.append("2. EVIDENCE:")
    metrics = [f for f in state.raw_facts if f.fact_type == "METRIC"]
    for m in metrics:
        val = f"{m.value} {m.unit}" if m.value is not None else "N/A"
        lines.append(f"   • {m.name}: {val}")
    
    if state.causal_chain:
        lines.append("")
        lines.append("   • Causal Chain Formed:")
        for node in state.causal_chain:
            lines.append(f"       → [{node.get('fact_type')}] {node.get('description', node.get('name', ''))}")
    lines.append("")

    lines.append("3. RECOMMENDATION:")
    lines.append("   • Address the anomalies listed in the root cause section immediately.")
    lines.append("   • If system resources are exhausted, consider closing background applications or upgrading hardware.")
    lines.append("")
    
    lines.append("4. CONFIDENCE:")
    lines.append(f"   HIGH (Deterministic Match: {state.overall_confidence:.0%})")
    
    return "\n".join(lines)


def _fallback_report(state: InvestigationState) -> str:
    """Plain-text report when the LLM is unavailable."""
    lines = [f"DIAGNOSTIC REPORT (LLM offline — raw evidence):", ""]
    anomalies = [f for f in state.raw_facts if f.fact_type == "ANOMALY"]
    metrics = [f for f in state.raw_facts if f.fact_type == "METRIC"]

    if anomalies:
        lines.append("ANOMALIES DETECTED:")
        for a in anomalies:
            lines.append(f"  • {a.description}")

    if metrics:
        lines.append("\nKEY METRICS:")
        for m in metrics:
            val = f"{m.value} {m.unit}" if m.value is not None else "N/A"
            lines.append(f"  • {m.name}: {val}")

    lines.append(f"\nOverall system confidence: {state.overall_confidence:.0%}")
    return "\n".join(lines)

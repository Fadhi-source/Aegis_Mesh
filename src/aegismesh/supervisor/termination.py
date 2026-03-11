"""
supervisor/termination.py
=========================
Termination condition evaluator for the investigation lifecycle.
Called at the end of a causal_validation node to classify the outcome.
"""
from __future__ import annotations
from enum import Enum
from aegismesh.core.config import (
    HIGH_CONFIDENCE_THRESHOLD,
    PARTIAL_CONFIDENCE_THRESHOLD,
    MAX_INVESTIGATION_RETRIES,
)


class TerminationReason(str, Enum):
    SUCCESS             = "SUCCESS"               # Confidence >= 0.70, chain found
    PARTIAL_CONFIDENCE  = "PARTIAL_CONFIDENCE"    # 0.40 <= confidence < 0.70
    LOW_CONFIDENCE      = "LOW_CONFIDENCE"        # Confidence < 0.40
    NO_AGENTS_FOUND     = "NO_AGENTS_FOUND"       # Registry returned 0 candidates
    ALL_CIRCUITS_OPEN   = "ALL_CIRCUITS_OPEN"     # Every selected agent is OPEN
    INVESTIGATION_TIMEOUT = "INVESTIGATION_TIMEOUT"  # asyncio.wait_for fired
    FAILURE_DEADLOCK    = "FAILURE_DEADLOCK"      # 100% dispatch errors


def classify_termination(
    confidence: float,
    selected_agents: list,
    dispatch_errors: list,
    timed_out: bool = False,
) -> TerminationReason:
    """
    Deterministic decision tree as specified in G-09.
    Returns the highest-priority matching TerminationReason.
    """
    if timed_out:
        return TerminationReason.INVESTIGATION_TIMEOUT

    if not selected_agents:
        return TerminationReason.NO_AGENTS_FOUND

    # All dispatched agents failed
    if len(dispatch_errors) >= len(selected_agents) and selected_agents:
        return TerminationReason.FAILURE_DEADLOCK

    if confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return TerminationReason.SUCCESS

    if confidence >= PARTIAL_CONFIDENCE_THRESHOLD:
        return TerminationReason.PARTIAL_CONFIDENCE

    return TerminationReason.LOW_CONFIDENCE

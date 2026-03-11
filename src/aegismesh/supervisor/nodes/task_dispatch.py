"""
supervisor/nodes/task_dispatch.py
===================================
Node 3 of 5 in the LangGraph reasoning loop.

Responsibility:
  - Dispatches A2A RPC calls to all selected agents concurrently
  - Each call is gated through the RAM Governor semaphore
  - Records health metrics back to the Registry on each response
  - Converts raw agent output into typed RawFact objects
  - Appends to state.raw_facts (append-only list — race safe)
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from aegismesh.core.a2a_client import A2ABusClient, A2ARPCError
from aegismesh.core.governor import get_governor
from aegismesh.core.config import REGISTRY_URL
from aegismesh.supervisor.state import InvestigationState, RawFact

logger = logging.getLogger("aegis.supervisor.task_dispatch")


async def _record_health(agent_id: str, duration_ms: float, success: bool) -> None:
    """Fire-and-forget health update back to Registry."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{REGISTRY_URL}/heartbeat",
                json={"agent_id": agent_id},
            )
    except Exception:
        pass  # Health recording is best-effort


async def _dispatch_one(
    agent,
    intent: str,
    trace_id: str,
) -> tuple[list[RawFact], str | None]:
    """
    Dispatches to a single agent under the Governor semaphore.
    Returns (facts, error_message_or_None).
    """
    governor = get_governor()
    facts: list[RawFact] = []
    error: str | None = None

    async with governor.acquire(agent_id=agent.agent_id):
        start_ms = time.time() * 1000
        try:
            async with A2ABusClient(agent.endpoint, timeout=15.0) as client:
                result = await client.call(
                    method="execute_task",
                    params={"intent": intent},
                    trace_id=trace_id,
                )

            duration_ms = time.time() * 1000 - start_ms
            await _record_health(agent.agent_id, duration_ms, success=True)

            # Parse the result into RawFact objects
            for fact_dict in result.get("facts", []):
                facts.append(RawFact(
                    fact_type=fact_dict.get("type", "EVENT"),
                    description=fact_dict.get("description", ""),
                    name=fact_dict.get("name", ""),
                    value=fact_dict.get("value"),
                    unit=fact_dict.get("unit"),
                    source_agent=agent.agent_id,
                    confidence=fact_dict.get("confidence", 0.80),
                ))

            logger.info(
                "[%s] Agent '%s' returned %d facts in %.0fms",
                trace_id, agent.agent_id, len(facts), duration_ms,
            )

        except A2ARPCError as exc:
            duration_ms = time.time() * 1000 - start_ms
            await _record_health(agent.agent_id, duration_ms, success=False)
            error = f"A2ARPC error from '{agent.agent_id}': {exc}"
            logger.warning("[%s] %s", trace_id, error)

        except Exception as exc:
            duration_ms = time.time() * 1000 - start_ms
            await _record_health(agent.agent_id, duration_ms, success=False)
            error = f"Unexpected error from '{agent.agent_id}': {exc}"
            logger.error("[%s] %s", trace_id, error)

    return facts, error


async def task_dispatch_node(state: InvestigationState) -> dict:
    """
    LangGraph node: concurrently dispatches tasks to all selected agents.
    Governor semaphore limits concurrency based on live RAM usage.
    """
    if not state.selected_agents:
        logger.warning("[%s] No agents selected — skipping dispatch.", state.trace_id)
        return {"raw_facts": [], "dispatch_errors": ["NO_AGENTS_SELECTED"]}

    logger.info(
        "[%s] Dispatching to %d agents concurrently (intent: '%s')",
        state.trace_id, len(state.selected_agents), state.parsed_intent[:60],
    )

    # Kick off all agent calls concurrently
    tasks = [
        _dispatch_one(agent, state.parsed_intent, state.trace_id)
        for agent in state.selected_agents
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_facts: list[RawFact] = []
    all_errors: list[str] = []

    for result in results:
        if isinstance(result, Exception):
            all_errors.append(f"Gather exception: {result}")
            logger.error("[%s] Gather exception: %s", state.trace_id, result)
        else:
            facts, error = result
            all_facts.extend(facts)
            if error:
                all_errors.append(error)

    logger.info(
        "[%s] Dispatch complete: %d facts, %d errors",
        state.trace_id, len(all_facts), len(all_errors),
    )

    return {"raw_facts": all_facts, "dispatch_errors": all_errors}

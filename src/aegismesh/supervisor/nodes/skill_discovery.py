"""
supervisor/nodes/skill_discovery.py
====================================
Node 2 of 5 in the LangGraph reasoning loop.

Responsibility:
  - Queries the Registry for agents with required_skills
  - Scores each candidate using the Composite Weighted Score (CWS)
  - Selects the top agent per skill (not per agent — deduplicated)
  - Deterministic tie-breaking: last_success_at → registered_at → lexicographic id

CWS formula (from addendum G-01):
  CWS = α * success_rate + β * (1 - norm_latency) + γ * skill_confidence
  α=0.50, β=0.30, γ=0.20
"""
from __future__ import annotations

import logging
import httpx

from aegismesh.core.config import REGISTRY_URL, CACHE_TTL_SECONDS
from aegismesh.supervisor.state import InvestigationState, AgentCandidate

logger = logging.getLogger("aegis.supervisor.skill_discovery")

# CWS weights (G-01)
ALPHA = 0.50   # success_rate weight
BETA  = 0.30   # latency weight (inverted)
GAMMA = 0.20   # per-skill confidence weight

# Normalisation cap for latency (ms) — anything above this is treated as worst-case
MAX_LATENCY_MS = 5000.0


def _compute_cws(
    success_rate: float,
    mean_response_ms: float,
    skill_confidence: float,
) -> float:
    norm_latency = min(mean_response_ms / MAX_LATENCY_MS, 1.0)
    score = (
        ALPHA * success_rate
        + BETA  * (1.0 - norm_latency)
        + GAMMA * skill_confidence
    )
    return round(min(score, 1.0), 6)


async def skill_discovery_node(state: InvestigationState) -> dict:
    """
    LangGraph node: queries Registry and scores candidates per required skill.
    Returns the best (highest CWS) agent per skill, deduplicated.
    """
    logger.info("[%s] Skill discovery: %s", state.trace_id, state.required_skills)

    selected: list[AgentCandidate] = []
    seen_agent_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=5.0) as client:
        for skill in state.required_skills:
            try:
                resp = await client.get(
                    f"{REGISTRY_URL}/agents",
                    params={"skill": skill},
                )
                if resp.status_code != 200:
                    logger.warning("[%s] Registry returned %d for skill '%s'.", state.trace_id, resp.status_code, skill)
                    continue

                candidates = resp.json().get("agents", [])
                if not candidates:
                    logger.info("[%s] No available agents for skill '%s'.", state.trace_id, skill)
                    continue

                # Score all candidates
                scored = []
                for agent in candidates:
                    health = agent  # Registry JOIN already includes health columns
                    skill_conf = agent.get("skill_confidence", {}).get(skill, 0.5)
                    cws = _compute_cws(
                        success_rate=float(health.get("success_rate", 1.0)),
                        mean_response_ms=float(health.get("mean_response_ms", 0.0)),
                        skill_confidence=skill_conf,
                    )
                    scored.append((cws, agent))

                # Sort descending by CWS; tie-break: last_success_at desc, registered_at asc, id lex
                scored.sort(
                    key=lambda x: (
                        -x[0],
                        -(x[1].get("last_success_at") or 0.0),
                        x[1].get("registered_at", 0.0),
                        x[1].get("id", ""),
                    )
                )

                best_cws, best_agent = scored[0]
                agent_id = best_agent["id"]

                # Deduplicate — same agent can cover multiple skills, add only once
                if agent_id not in seen_agent_ids:
                    seen_agent_ids.add(agent_id)
                    selected.append(AgentCandidate(
                        agent_id=agent_id,
                        name=best_agent.get("name", agent_id),
                        endpoint=best_agent.get("endpoint", ""),
                        skills=best_agent.get("skills", []),
                        cws_score=best_cws,
                        matched_skill=skill,
                    ))
                    logger.info(
                        "[%s] Selected '%s' for skill '%s' (CWS=%.4f)",
                        state.trace_id, agent_id, skill, best_cws,
                    )

            except Exception as exc:
                logger.error("[%s] Skill discovery error for '%s': %s", state.trace_id, skill, exc)

    logger.info("[%s] Dispatch list: %d agents", state.trace_id, len(selected))
    return {"selected_agents": selected}

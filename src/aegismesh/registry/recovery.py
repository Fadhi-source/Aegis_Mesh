"""
registry/recovery.py
====================
Startup recovery: evict stale agents, probe survivors, handle re-registration.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from aegismesh.core.config import AGENT_TTL_SECONDS

logger = logging.getLogger("aegis.registry.recovery")


class RegistryRecovery:
    def __init__(self, db) -> None:
        self._db = db

    async def run_startup_recovery(self) -> dict:
        evicted = self._evict_stale_agents()
        verified, unreachable = await self._probe_surviving_agents()
        return {
            "evicted_stale": evicted,
            "verified": verified,
            "unreachable_evicted": unreachable,
        }

    def _evict_stale_agents(self) -> list[str]:
        cutoff = time.time() - AGENT_TTL_SECONDS
        stale = self._db.get_agents_last_seen_before(cutoff)
        for agent in stale:
            self._db.delete_agent(agent["id"])
            logger.info("Recovery: evicted stale agent '%s'", agent["id"])
        return [a["id"] for a in stale]

    async def _probe_surviving_agents(self) -> tuple[list[str], list[str]]:
        agents = self._db.list_all_agents()
        if not agents:
            return [], []

        verified: list[str] = []
        unreachable: list[str] = []

        async with httpx.AsyncClient(timeout=5.0) as client:
            tasks = [self._probe_one(client, a) for a in agents]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for agent, result in zip(agents, results):
            if result is True:
                self._db.update_last_seen(agent["id"])
                verified.append(agent["id"])
            else:
                self._db.delete_agent(agent["id"])
                unreachable.append(agent["id"])
                logger.warning(
                    "Recovery: agent '%s' unreachable — evicted. Error: %s",
                    agent["id"],
                    result,
                )
        return verified, unreachable

    async def _probe_one(self, client: httpx.AsyncClient, agent: dict) -> bool:
        try:
            resp = await client.get(f"{agent['endpoint']}/health")
            return resp.status_code == 200
        except Exception as exc:
            return exc  # type: ignore[return-value]  — caught as non-True in caller

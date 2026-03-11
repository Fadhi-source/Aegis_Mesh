"""
core/black_box.py
=================
Append-only JSONL structured logger for every A2A inter-agent message.

Design constraints:
  - aiofiles: non-blocking writes — never competes with the asyncio event loop
  - One file per investigation (named by trace_id)
  - JSONL format: one JSON object per line, structurally immune to log poisoning
    (json.dumps escapes all control chars / newlines automatically)
  - Directory is created lazily on first write
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import aiofiles

from aegismesh.core.config import LOG_DIR

logger = logging.getLogger("aegis.blackbox")


class BlackBoxRecorder:
    """
    Write structured log entries asynchronously to
    logs/aegis_blackbox/<trace_id>.jsonl
    """

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self._path: Path = LOG_DIR / f"{trace_id}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def record(
        self,
        event_type: str,
        source: str,
        target: str,
        payload: dict,
    ) -> None:
        """
        Appends a JSONL entry. The json.dumps serializer escapes all
        control characters — preventing log injection via crafted payloads.

        event_type examples:
          RPC_CALL | RPC_RESPONSE | CIRCUIT_OPEN | GOVERNOR_THROTTLE |
          AGENT_EVICTED | INVESTIGATION_START | INVESTIGATION_END
        """
        entry = {
            "ts": round(time.time(), 6),
            "trace_id": self.trace_id,
            "event_type": event_type,
            "source": source,
            "target": target,
            "payload": payload,
        }
        line = json.dumps(entry, ensure_ascii=True, default=str) + "\n"
        try:
            async with aiofiles.open(self._path, mode="a", encoding="utf-8") as fh:
                await fh.write(line)
        except OSError as exc:
            # Non-fatal: log to stderr but do not crash the investigation
            logger.error("BlackBox write failed for %s: %s", self.trace_id, exc)

    def record_sync(self, event_type: str, source: str, target: str, payload: dict) -> None:
        """Synchronous fallback for non-async callers (e.g., startup hooks)."""
        asyncio.get_event_loop().run_until_complete(
            self.record(event_type, source, target, payload)
        )

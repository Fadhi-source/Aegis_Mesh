"""
registry/db.py
==============
SQLite data layer for AegisRegistry.

Notes on SQLite on Windows:
  - WAL mode: reader-writer concurrency without blocking
  - check_same_thread=False: required because FastAPI's async handlers
    run on the thread pool; we use a Lock to serialize writes ourselves
  - EWMA α=0.2 gives ~40% weight to the last 5 samples — sufficient for
    a local mesh where conditions change slowly
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from aegismesh.core.config import (
    REGISTRY_DB,
    AGENT_TTL_SECONDS,
    CB_FAILURE_THRESHOLD,
    CB_OPEN_TIMEOUT_S,
)

logger = logging.getLogger("aegis.registry.db")

EWMA_ALPHA: float = 0.2  # Exponentially Weighted Moving Average smoothing factor


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    endpoint        TEXT NOT NULL UNIQUE,
    skills          TEXT NOT NULL,
    skill_confidence TEXT NOT NULL DEFAULT '{}',
    version         TEXT NOT NULL DEFAULT '1.0.0',
    fallback_agent_id TEXT,
    registered_at   REAL NOT NULL,
    last_seen       REAL,
    circuit_state   TEXT NOT NULL DEFAULT 'CLOSED'
);

CREATE TABLE IF NOT EXISTS agent_health (
    agent_id            TEXT PRIMARY KEY,
    mean_response_ms    REAL NOT NULL DEFAULT 0.0,
    success_rate        REAL NOT NULL DEFAULT 1.0,
    failure_count       INTEGER NOT NULL DEFAULT 0,
    circuit_opened_at   REAL,
    last_failure_at     REAL,
    last_success_at     REAL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agents_circuit  ON agents(circuit_state);
CREATE INDEX IF NOT EXISTS idx_agents_lastseen ON agents(last_seen);
"""


class RegistryDatabase:
    """Thread-safe SQLite wrapper for AegisRegistry."""

    def __init__(self, db_path: str = REGISTRY_DB) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions manually
        )
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()
        logger.info("RegistryDatabase initialised at %s", db_path)

    def _apply_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # ── Agent registration ────────────────────────────────────────────────────

    def upsert_agent(self, card_dict: dict) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agents
                    (id, name, endpoint, skills, skill_confidence, version,
                     fallback_agent_id, registered_at, last_seen, circuit_state)
                VALUES (?,?,?,?,?,?,?,?,?,'CLOSED')
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    endpoint=excluded.endpoint,
                    skills=excluded.skills,
                    skill_confidence=excluded.skill_confidence,
                    version=excluded.version,
                    fallback_agent_id=excluded.fallback_agent_id,
                    last_seen=excluded.last_seen
                """,
                (
                    card_dict["id"],
                    card_dict["name"],
                    card_dict["endpoint"],
                    json.dumps(card_dict["skills"]),
                    json.dumps(card_dict.get("skill_confidence", {})),
                    card_dict.get("version", "1.0.0"),
                    card_dict.get("fallback_agent_id"),
                    now,
                    now,
                ),
            )
            # Ensure health row exists
            self._conn.execute(
                """
                INSERT OR IGNORE INTO agent_health (agent_id)
                VALUES (?)
                """,
                (card_dict["id"],),
            )

    def delete_agent(self, agent_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))

    def update_last_seen(self, agent_id: str, ts: float | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agents SET last_seen=? WHERE id=?",
                (ts or time.time(), agent_id),
            )

    # ── Health recording ──────────────────────────────────────────────────────

    def record_response(self, agent_id: str, duration_ms: float, success: bool) -> None:
        """Updates EWMA metrics and failure count in an atomic transaction."""
        with self._lock:
            row = self._conn.execute(
                "SELECT mean_response_ms, success_rate, failure_count "
                "FROM agent_health WHERE agent_id=?",
                (agent_id,),
            ).fetchone()
            if row is None:
                return

            old_mean = row["mean_response_ms"]
            old_rate = row["success_rate"]
            fail_cnt = row["failure_count"]

            new_mean = EWMA_ALPHA * duration_ms + (1 - EWMA_ALPHA) * old_mean
            new_rate = EWMA_ALPHA * (1.0 if success else 0.0) + (1 - EWMA_ALPHA) * old_rate
            new_fail = 0 if success else fail_cnt + 1
            now = time.time()

            self._conn.execute(
                """
                UPDATE agent_health
                SET mean_response_ms=?,
                    success_rate=?,
                    failure_count=?,
                    last_success_at=?,
                    last_failure_at=?
                WHERE agent_id=?
                """,
                (
                    new_mean,
                    new_rate,
                    new_fail,
                    now if success else None,
                    None if success else now,
                    agent_id,
                ),
            )

            # Auto-open circuit after CB_FAILURE_THRESHOLD consecutive failures
            if not success and new_fail >= CB_FAILURE_THRESHOLD:
                self._conn.execute(
                    "UPDATE agents SET circuit_state='OPEN' WHERE id=?",
                    (agent_id,),
                )
                self._conn.execute(
                    "UPDATE agent_health SET circuit_opened_at=? WHERE agent_id=?",
                    (now, agent_id),
                )
                logger.warning("Circuit OPEN for agent '%s' after %d failures.", agent_id, new_fail)

    # ── Circuit breaker ───────────────────────────────────────────────────────

    def get_circuit_state(self, agent_id: str) -> str:
        row = self._conn.execute(
            "SELECT circuit_state FROM agents WHERE id=?", (agent_id,)
        ).fetchone()
        return row["circuit_state"] if row else "OPEN"

    def set_circuit_state(self, agent_id: str, state: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agents SET circuit_state=? WHERE id=?", (state, agent_id)
            )

    def maybe_transition_open_to_half_open(self, agent_id: str) -> bool:
        """
        If OPEN and cooldown has passed, transitions to HALF_OPEN.
        Returns True if caller should send a probe request.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT a.circuit_state, h.circuit_opened_at "
                "FROM agents a JOIN agent_health h ON a.id=h.agent_id "
                "WHERE a.id=?",
                (agent_id,),
            ).fetchone()
            if not row or row["circuit_state"] != "OPEN":
                return False
            opened_at = row["circuit_opened_at"] or 0.0
            if time.time() - opened_at > CB_OPEN_TIMEOUT_S:
                self._conn.execute(
                    "UPDATE agents SET circuit_state='HALF_OPEN' WHERE id=?",
                    (agent_id,),
                )
                return True
            return False

    def reset_failures(self, agent_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agent_health SET failure_count=0, circuit_opened_at=NULL "
                "WHERE agent_id=?",
                (agent_id,),
            )
            self._conn.execute(
                "UPDATE agents SET circuit_state='CLOSED' WHERE id=?", (agent_id,)
            )

    # ── Discovery / routing ───────────────────────────────────────────────────

    def list_healthy_agents(self, skill_filter: Optional[str] = None) -> list[dict]:
        """
        Returns CLOSED-circuit agents sorted for Latency-Aware Routing:
          ORDER BY success_rate DESC, mean_response_ms ASC
        """
        rows = self._conn.execute(
            """
            SELECT a.id, a.name, a.endpoint, a.skills, a.skill_confidence,
                   a.version, a.fallback_agent_id, a.circuit_state,
                   h.mean_response_ms, h.success_rate, h.failure_count,
                   h.last_success_at
            FROM agents a
            LEFT JOIN agent_health h ON a.id = h.agent_id
            WHERE a.circuit_state = 'CLOSED'
            ORDER BY h.success_rate DESC, h.mean_response_ms ASC
            """
        ).fetchall()

        agents = []
        for row in rows:
            d = dict(row)
            d["skills"] = json.loads(d["skills"])
            d["skill_confidence"] = json.loads(d["skill_confidence"])
            agents.append(d)

        if skill_filter:
            skill_filter = skill_filter.lower().strip()
            agents = [a for a in agents if skill_filter in a["skills"]]

        return agents

    def list_all_agents(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM agents").fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["skills"] = json.loads(d["skills"])
            result.append(d)
        return result

    def get_agents_last_seen_before(self, cutoff_ts: float) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM agents WHERE last_seen < ? OR last_seen IS NULL",
            (cutoff_ts,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_health(self, agent_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM agent_health WHERE agent_id=?", (agent_id,)
        ).fetchone()
        return dict(row) if row else None

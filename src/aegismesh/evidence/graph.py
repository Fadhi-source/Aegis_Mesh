"""
evidence/graph.py
=================
EvidenceGraph — NetworkX DAG with temporal causal correlation and SQLite persistence.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import networkx as nx

from aegismesh.core.config import (
    EVIDENCE_DB,
    MAX_NODES_PER_INVESTIGATION,
    MAX_NODES_PER_AGENT,
    MAX_EDGES_PER_INVESTIGATION,
    MAX_OUT_DEGREE_PER_NODE,
    BASE_CAUSAL_WINDOW_MS,
)

logger = logging.getLogger("aegis.evidence")


@dataclass
class EvidenceNode:
    node_id: str
    fact_type: str           # METRIC | EVENT | ANOMALY
    description: str
    value: Optional[float]
    unit: Optional[str]
    timestamp: float         # Unix epoch
    source_agent: str
    confidence: float        # 0.0–1.0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Sanitize — prevent log poisoning via crafted strings
        self.description  = str(self.description)[:512]
        self.source_agent = str(self.source_agent)[:64]
        self.confidence   = max(0.0, min(1.0, float(self.confidence)))


@dataclass
class CausalEdge:
    source: str
    target: str
    relation: str       # CAUSED_BY | PRECEDED_BY | CORRELATED_WITH
    weight: float       # 0.0–1.0
    delta_t_ms: float


class EvidenceGraph:
    """NetworkX DAG for investigation causal reasoning."""

    def __init__(self, trace_id: str, db_path: str = EVIDENCE_DB) -> None:
        self.trace_id = trace_id
        self._g: nx.DiGraph = nx.DiGraph()
        self._db_path = db_path

    # ── Node management ────────────────────────────────────────────────────────

    def add_evidence(self, node: EvidenceNode) -> bool:
        """
        Adds a node with capacity guards.
        Returns True if added, False if rejected by a limit.
        """
        # Per-agent cap
        agent_count = sum(
            1 for n in self._g.nodes
            if self._g.nodes[n].get("source_agent") == node.source_agent
        )
        if agent_count >= MAX_NODES_PER_AGENT:
            logger.debug(
                "EvidenceGraph: per-agent cap hit for '%s'. Node '%s' dropped.",
                node.source_agent,
                node.node_id,
            )
            return False

        # Global cap with confidence-based eviction of leaf nodes
        if len(self._g.nodes) >= MAX_NODES_PER_INVESTIGATION:
            candidates = [
                (self._g.nodes[n].get("confidence", 0.0), n)
                for n in self._g.nodes
                if self._g.out_degree(n) == 0
            ]
            if candidates:
                _, evict_id = min(candidates)
                self._g.remove_node(evict_id)
                logger.debug("EvidenceGraph: evicted low-confidence node '%s'.", evict_id)
            else:
                return False  # All nodes have out-edges — can't safely evict

        self._g.add_node(node.node_id, **asdict(node))
        return True

    # ── Causal correlation ─────────────────────────────────────────────────────

    def adaptive_causal_window(self) -> float:
        """Shrinks the causal window as graph density increases. See addendum G-03."""
        n = len(self._g.nodes)
        if n <= 10:
            return BASE_CAUSAL_WINDOW_MS
        return BASE_CAUSAL_WINDOW_MS / (1 + math.log2(n / 10))

    def run_temporal_correlation(self) -> int:
        """
        O(N²) causal inference pass over time-sorted nodes.
        Returns the number of edges created.
        """
        window_ms = self.adaptive_causal_window()
        nodes = sorted(
            [self._g.nodes[n] for n in self._g.nodes],
            key=lambda n: n["timestamp"],
        )
        edges_added = 0

        for i, node_a in enumerate(nodes):
            if self._g.number_of_edges() >= MAX_EDGES_PER_INVESTIGATION:
                logger.warning("EvidenceGraph: MAX_EDGES reached, stopping correlation.")
                break

            for node_b in nodes[i + 1:]:
                delta_ms = (node_b["timestamp"] - node_a["timestamp"]) * 1000.0
                if delta_ms > window_ms:
                    break  # Sorted — no further pairs qualify

                # ANOMALY antecedent + EVENT consequent → CAUSED_BY
                if node_a["fact_type"] == "ANOMALY" and node_b["fact_type"] == "EVENT":
                    # Check out-degree cap
                    if self._g.out_degree(node_a["node_id"]) >= MAX_OUT_DEGREE_PER_NODE:
                        continue

                    weight = round(1.0 - (delta_ms / window_ms), 4)
                    edge = CausalEdge(
                        source=node_a["node_id"],
                        target=node_b["node_id"],
                        relation="CAUSED_BY",
                        weight=weight,
                        delta_t_ms=round(delta_ms, 2),
                    )
                    self._g.add_edge(node_a["node_id"], node_b["node_id"], **asdict(edge))
                    edges_added += 1

                # METRIC near EVENT → CORRELATED_WITH (weaker signal)
                elif node_a["fact_type"] == "METRIC" and node_b["fact_type"] == "EVENT":
                    if self._g.out_degree(node_a["node_id"]) < MAX_OUT_DEGREE_PER_NODE:
                        weight = round((1.0 - (delta_ms / window_ms)) * 0.5, 4)
                        self._g.add_edge(
                            node_a["node_id"],
                            node_b["node_id"],
                            source=node_a["node_id"],
                            target=node_b["node_id"],
                            relation="CORRELATED_WITH",
                            weight=weight,
                            delta_t_ms=round(delta_ms, 2),
                        )
                        edges_added += 1

        return edges_added

    # ── Chain traversal ────────────────────────────────────────────────────────

    def get_highest_confidence_chain(self) -> list[dict]:
        """Returns the nodes constituting the highest-weight causal path in the DAG."""
        if not self._g.nodes:
            return []
        if not nx.is_directed_acyclic_graph(self._g):
            logger.error(
                "EvidenceGraph has cycles (trace %s) — temporal data may be corrupt. "
                "Returning nodes sorted by confidence.",
                self.trace_id,
            )
            return sorted(
                [dict(self._g.nodes[n]) for n in self._g.nodes],
                key=lambda n: n.get("confidence", 0),
                reverse=True,
            )
        path = nx.dag_longest_path(self._g, weight="weight")
        return [dict(self._g.nodes[n]) for n in path]

    @property
    def overall_confidence(self) -> float:
        """Weighted mean confidence across all nodes."""
        if not self._g.nodes:
            return 0.0
        scores = [
            self._g.nodes[n].get("confidence", 0.5)
            * (1.0 + sum(d.get("weight", 0) for _, _, d in self._g.out_edges(n, data=True)))
            for n in self._g.nodes
        ]
        return round(min(sum(scores) / len(scores), 1.0), 4)

    @property
    def stats(self) -> dict:
        return {
            "nodes": self._g.number_of_nodes(),
            "edges": self._g.number_of_edges(),
            "confidence": self.overall_confidence,
            "is_dag": nx.is_directed_acyclic_graph(self._g) if self._g.nodes else True,
        }

    # ── Persistence ────────────────────────────────────────────────────────────

    def persist(self) -> None:
        """Serializes the graph to SQLite for post-mortem XAI access."""
        import pathlib
        pathlib.Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS graphs "
            "(trace_id TEXT PRIMARY KEY, data TEXT, ts REAL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO graphs VALUES (?,?,?)",
            (
                self.trace_id,
                json.dumps(nx.node_link_data(self._g), default=str),
                time.time(),
            ),
        )
        conn.commit()
        conn.close()
        logger.debug("EvidenceGraph persisted for trace %s.", self.trace_id)

    @classmethod
    def load(cls, trace_id: str, db_path: str = EVIDENCE_DB) -> "EvidenceGraph":
        """Reconstructs a graph from SQLite for post-mortem analysis."""
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT data FROM graphs WHERE trace_id=?", (trace_id,)
        ).fetchone()
        conn.close()
        if row is None:
            raise KeyError(f"No EvidenceGraph found for trace '{trace_id}'")
        instance = cls(trace_id, db_path)
        instance._g = nx.node_link_graph(json.loads(row[0]))
        return instance

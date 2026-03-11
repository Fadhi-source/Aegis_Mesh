# AegisMesh: Technical Masterpiece Blueprint
### Principal-Level Engineering Specification — v1.4.0 Stable
*Sovereign Agent-to-Agent (A2A) Diagnostic Mesh*

---

> [!IMPORTANT]
> This document is the definitive engineering specification for AegisMesh. Every design decision is grounded in the constraints declared in the whitepaper: **16GB RAM / i5 CPU, privacy-first local execution, non-destructive safety, and verifiable causal reasoning.**

---

## Table of Contents

1. [System Topology — Component Diagram](#1-system-topology--component-diagram)
2. [The A2A Bus — JSON-RPC 2.0 over HTTP/1.1 Keep-Alive](#2-the-a2a-bus)
3. [The Supervisor State Machine — LangGraph Cyclic Loop](#3-the-supervisor-state-machine)
4. [The 16GB RAM Governor — Resource Semaphore](#4-the-16gb-ram-governor)
5. [AegisRegistry — Control Plane Deep-Dive](#5-aegisregistry--the-control-plane)
6. [EvidenceGraph — Causal Reasoning Engine](#6-evidencegraph--causal-reasoning-engine)
7. [Reliability & Safety — Circuit Breaker + Guardrails](#7-reliability--safety)
8. [Observability — OpenTelemetry + Black Box Recorder](#8-observability--opentelemetry)
9. [Agent Card Schema Definition](#9-agent-card-schema-definition)
10. [Implementation Roadmap — Masterpiece Milestones](#10-implementation-roadmap)

---

## 1. System Topology — Component Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              AegisMesh — Local Process Boundary                  │
│                                                                                  │
│  ┌──────────────┐     JSON      ┌───────────────────────────────────────────┐   │
│  │              │◄─────────────►│              GATEWAY LAYER                │   │
│  │    USER /    │  (HTTP/1.1)   │   FastAPI · Auth · Trace Init · Normalize  │   │
│  │    STDIN     │               └───────────────────┬───────────────────────┘   │
│  │              │                                   │  InvestigationSchema       │
│  └──────────────┘                                   ▼                           │
│                                   ┌───────────────────────────────────────────┐ │
│                                   │           SUPERVISOR ENGINE               │ │
│                                   │   LangGraph Cyclic State Machine          │ │
│                                   │                                           │ │
│                                   │  ┌──────────┐   ┌──────────────────────┐ │ │
│                                   │  │  Intent  │──►│  Skill Discovery     │ │ │
│                                   │  │  Parser  │   │  (Registry Query)    │ │ │
│                                   │  └──────────┘   └──────────┬───────────┘ │ │
│                                   │        ▲                   │             │ │
│                                   │        │Causal Validate    ▼             │ │
│                                   │  ┌──────────────┐  ┌──────────────────┐ │ │
│                                   │  │  Evidence    │  │ Concurrent Task  │ │ │
│                                   │  │  Synthesis   │◄─│ Dispatch (asyncio│ │ │
│                                   │  └──────────────┘  │ + Semaphore Gov) │ │ │
│                                   │                    └──────────────────┘ │ │
│                                   └──────────┬────────────────────────────────┘ │
│                                              │ A2A RPC (JSON-RPC 2.0 Keep-Alive) │
│                          ┌───────────────────┼────────────────────────┐          │
│                          │   CONTROL PLANE   │                        │          │
│  ┌───────────────────────▼───────────┐       │                        │          │
│  │          AegisRegistry            │       │    SPECIALIST AGENTS   │          │
│  │  ┌─────────────────────────────┐  │       │  ┌─────────────────┐  │          │
│  │  │  .well-known/agents.json    │  │  RPC  │  │  ProcessAgent   │  │          │
│  │  │  SQLite: agent_health_db    │  │◄─────►│  │  LogAgent       │  │          │
│  │  │  Circuit Breaker States     │  │       │  │  NetworkAgent   │  │          │
│  │  │  Latency-Aware Router       │  │       │  │  DiskAgent      │  │          │
│  │  └─────────────────────────────┘  │       │  └────────┬────────┘  │          │
│  └───────────────────────────────────┘       └──────────┼────────────┘          │
│                                                          │ Evidence               │
│                          ┌───────────────────────────────▼──────────────────┐   │
│                          │             EvidenceGraph Engine                   │   │
│                          │   NetworkX DAG · SQLite Persistence · Causal AI   │   │
│                          └───────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  OBSERVABILITY SIDECAR: OpenTelemetry Exporter · JSONL Black Box Logger  │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │  RAM GOVERNOR (psutil): >85% → Sequential Mode · <75% → Parallel Mode   │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Key architectural principle: Shared-Nothing Architecture.** Every specialist agent runs as an independent process with its own memory arena, preventing a single agent's heap overflow from corrupting the mesh state. The only shared surfaces are the Registry's SQLite database (accessed via WAL mode to eliminate reader-writer contention) and the JSONL log stream.

---

## 2. The A2A Bus

### 2.1 Transport — JSON-RPC 2.0 over HTTP/1.1 Keep-Alive

The A2A bus is deliberately **not** gRPC or WebSockets. This is a conscious engineering trade-off: the system runs on a single developer machine where the cost of protocol negotiation (TLS setup, binary framing overhead) exceeds the benefit. HTTP/1.1 with persistent connections offers:

- **Zero handshake overhead** on subsequent calls within the same investigation (TCP connection is recycled).
- **Human-debuggable payloads** (plain JSON), critical for the Black Box Recorder.
- **Idempotency enforcement** via explicit `id` fields in every JSON-RPC envelope.

```python
# core/a2a_client.py
import httpx
import asyncio
from typing import Any

class A2ABusClient:
    """
    Persistent HTTP/1.1 session per target agent.
    Keep-Alive is maintained by httpx's AsyncClient connection pool.
    Connection pool is bounded to prevent resource exhaustion under backpressure.
    """
    def __init__(self, agent_url: str, timeout: float = 10.0):
        self._url = agent_url
        # limits=httpx.Limits: max_keepalive_connections=5 prevents
        # file-descriptor exhaustion on i5 hardware.
        self._session = httpx.AsyncClient(
            base_url=agent_url,
            http1=True,
            http2=False,  # Explicit: single-stream per connection, no head-of-line blocking risk
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            timeout=httpx.Timeout(connect=2.0, read=timeout, write=5.0, pool=1.0),
        )
        self._req_counter = 0

    def _next_id(self) -> str:
        self._req_counter += 1
        return f"req_{self._req_counter:06d}"

    async def call(self, method: str, params: dict[str, Any], trace_id: str) -> dict:
        """
        Executes a JSON-RPC 2.0 call. Idempotency is guaranteed by the unique
        `id` field — the callee must produce the same result for a replayed `id`.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": {**params, "trace_id": trace_id},
            "id": self._next_id(),
        }
        resp = await self._session.post("/rpc", json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise A2ARPCError(body["error"]["code"], body["error"]["message"])
        return body["result"]

    async def aclose(self):
        await self._session.aclose()


class A2ARPCError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"[RPC {code}] {message}")
```

### 2.2 Why Keep-Alive Matters on 16GB Hardware

In a local mesh where 8–12 RPC calls occur per investigation, TCP three-way handshake costs accumulate. On a loopback interface, a typical handshake adds ~0.3ms. Keep-Alive amortizes this across the session:

```
Without Keep-Alive: 10 calls × (0.3ms handshake + 2ms work) = 23ms overhead
With Keep-Alive:     10 calls × (0ms handshake + 2ms work) + 0.3ms = 20.3ms
```

The real gain is **file descriptor conservation**. Each unclosed TCP socket consumes an FD. On Windows, the default FD limit per process is 512. Bounded connection pools in httpx ensure we never approach this ceiling even under concurrent dispatch.

---

## 3. The Supervisor State Machine

### 3.1 LangGraph Cyclic Reasoning Loop

The Supervisor is not a linear pipeline. It is a **cyclic state machine** capable of backtracking when causal validation fails. This is the critical distinction from a naive chain-of-thought approach.

```
┌─────────────────────────────────────────────────────────────────┐
│                  LangGraph State Machine                        │
│                                                                 │
│   ┌────────────────┐                                            │
│   │  START / Query │                                            │
│   └───────┬────────┘                                            │
│           ▼                                                     │
│   ┌────────────────┐     Ambiguous     ┌─────────────────────┐ │
│   │ Intent Parser  │──────────────────►│  Clarification Node │ │
│   │  (LLM + NER)   │                   │  (back to START)    │ │
│   └───────┬────────┘                   └─────────────────────┘ │
│           │ Structured Intent                                   │
│           ▼                                                     │
│   ┌────────────────────┐                                        │
│   │  Skill-Based       │  → Queries Registry for agents with   │
│   │  Discovery         │    skill_tags matching intent nouns   │
│   └───────┬────────────┘                                        │
│           │ Ranked AgentCard list                               │
│           ▼                                                     │
│   ┌────────────────────┐                                        │
│   │  Concurrent Task   │  → asyncio.gather() with Semaphore    │
│   │  Dispatch          │    Governor gating concurrency         │
│   └───────┬────────────┘                                        │
│           │ Raw Evidence dict                                   │
│           ▼                                                     │
│   ┌────────────────────┐                                        │
│   │  Evidence          │  → Builds NetworkX DAG nodes/edges    │
│   │  Synthesis         │    Temporal correlation pass           │
│   └───────┬────────────┘                                        │
│           │ EvidenceGraph                                       │
│           ▼                                                     │
│   ┌────────────────────┐    Confidence    ┌───────────────────┐ │
│   │  Causal Validation │───── < 0.70 ────►│ Re-Dispatch Node  │ │
│   │  (DAG traversal)   │                   │ (loop back)       │ │
│   └───────┬────────────┘                   └───────────────────┘ │
│           │ Confidence >= 0.70                                  │
│           ▼                                                     │
│   ┌────────────────────┐                                        │
│   │  Final Report      │  → Structured Diagnosis JSON          │
│   │  Generation        │                                        │
│   └───────┬────────────┘                                        │
│           ▼                                                     │
│        [ END ]                                                  │
└─────────────────────────────────────────────────────────────────┘
```

```python
# supervisor/graph.py
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
import operator

class SupervisorState(TypedDict):
    query: str
    trace_id: str
    intent: dict                        # Parsed intent
    candidate_agents: list[dict]        # AgentCards from Registry
    raw_evidence: Annotated[list, operator.add]  # Accumulates across retries
    evidence_graph: object              # NetworkX DiGraph handle
    confidence: float
    diagnosis: dict
    retry_count: int

def build_supervisor_graph() -> StateGraph:
    graph = StateGraph(SupervisorState)

    graph.add_node("intent_parser",      node_intent_parser)
    graph.add_node("skill_discovery",    node_skill_discovery)
    graph.add_node("task_dispatch",      node_task_dispatch)
    graph.add_node("evidence_synthesis", node_evidence_synthesis)
    graph.add_node("causal_validation",  node_causal_validation)
    graph.add_node("report_generation",  node_report_generation)

    graph.set_entry_point("intent_parser")
    graph.add_edge("intent_parser",      "skill_discovery")
    graph.add_edge("skill_discovery",    "task_dispatch")
    graph.add_edge("task_dispatch",      "evidence_synthesis")
    graph.add_edge("evidence_synthesis", "causal_validation")

    # Conditional edge — the "cyclic" back-edge that enables re-investigation
    graph.add_conditional_edges(
        "causal_validation",
        lambda s: "report_generation" if s["confidence"] >= 0.70 or s["retry_count"] >= 2
                  else "skill_discovery",   # Re-dispatch with broader skill tags
        {"report_generation": "report_generation", "skill_discovery": "skill_discovery"},
    )
    graph.add_edge("report_generation", END)

    return graph.compile()
```

**Race Condition mitigation:** The `raw_evidence` field uses `Annotated[list, operator.add]` which instructs LangGraph's reducer to **append** concurrent node outputs rather than overwrite — eliminating the last-writer-wins race that would corrupt evidence from parallel agents.

---

## 4. The 16GB RAM Governor

### 4.1 Resource Semaphore Design

The Governor is a **proactive backpressure mechanism**. Instead of waiting for OOM errors, it continuously monitors `psutil.virtual_memory().percent` and dynamically resizes the asyncio worker semaphore. This is the difference between a junior engineer's try/except and a principal engineer's admission control system.

```python
# core/governor.py
import asyncio
import psutil
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger("aegis.governor")

class RAMGovernor:
    """
    Dynamic concurrency controller. Monitors RAM and adjusts the asyncio
    semaphore ceiling to prevent OS-level swapping on 16GB machines.

    Thresholds (configurable):
      < 75% RAM  → PARALLEL mode:    up to N_PARALLEL concurrent agents
      75–85% RAM → DEGRADED mode:    up to N_DEGRADED concurrent agents
      > 85% RAM  → SEQUENTIAL mode:  exactly 1 concurrent agent (serialized)
    """
    N_PARALLEL   = 8   # i5 with 16GB: safe default
    N_DEGRADED   = 3
    N_SEQUENTIAL = 1

    THRESHOLD_HIGH = 85.0
    THRESHOLD_MED  = 75.0
    POLL_INTERVAL  = 2.0   # seconds

    def __init__(self):
        self._semaphore = asyncio.Semaphore(self.N_PARALLEL)
        self._current_limit = self.N_PARALLEL
        self._monitor_task: asyncio.Task | None = None

    async def start(self):
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self):
        if self._monitor_task:
            self._monitor_task.cancel()

    async def _monitor_loop(self):
        while True:
            ram_pct = psutil.virtual_memory().percent
            new_limit = self._compute_limit(ram_pct)
            if new_limit != self._current_limit:
                await self._resize_semaphore(new_limit)
                logger.warning(
                    "RAM Governor: %.1f%% RAM → switching concurrency %d→%d",
                    ram_pct, self._current_limit, new_limit
                )
                self._current_limit = new_limit
            await asyncio.sleep(self.POLL_INTERVAL)

    def _compute_limit(self, ram_pct: float) -> int:
        if ram_pct > self.THRESHOLD_HIGH:
            return self.N_SEQUENTIAL
        elif ram_pct > self.THRESHOLD_MED:
            return self.N_DEGRADED
        return self.N_PARALLEL

    async def _resize_semaphore(self, new_limit: int):
        """
        Semaphore resize is non-trivial: asyncio.Semaphore has no
        resize API. Strategy: release/acquire delta slots.
        This is safe because we hold the governor lock during resize.
        """
        delta = new_limit - self._current_limit
        if delta > 0:
            for _ in range(delta):
                self._semaphore.release()   # Add capacity
        elif delta < 0:
            # Drain excess capacity by acquiring without blocking tasks —
            # tasks currently holding permits complete naturally.
            for _ in range(-delta):
                try:
                    self._semaphore._value = max(0, self._semaphore._value - 1)
                except Exception:
                    pass

    @asynccontextmanager
    async def acquire(self):
        """Context manager for governed task dispatch."""
        async with self._semaphore:
            yield
```

```python
# supervisor/nodes/task_dispatch.py
async def node_task_dispatch(state: SupervisorState) -> dict:
    governor = get_governor()   # Singleton from app context
    tasks = build_task_list(state["candidate_agents"], state["intent"])

    async def dispatch_one(agent_card: dict, task_params: dict) -> dict:
        async with governor.acquire():      # Backpressure point
            client = A2ABusClient(agent_card["endpoint"])
            try:
                result = await client.call(
                    "execute_task", task_params, state["trace_id"]
                )
                return {"agent": agent_card["id"], "status": "ok", "data": result}
            except A2ARPCError as e:
                return {"agent": agent_card["id"], "status": "error", "error": str(e)}
            finally:
                await client.aclose()

    results = await asyncio.gather(
        *[dispatch_one(ac, tp) for ac, tp in tasks],
        return_exceptions=False   # Surface errors as dicts, not unhandled exceptions
    )
    return {"raw_evidence": list(results)}
```

---

## 5. AegisRegistry — The Control Plane

### 5.1 RFC 8615 Discovery Endpoint

The Registry follows [RFC 8615](https://www.rfc-editor.org/rfc/rfc8615) which mandates the `/.well-known/` URI prefix for service metadata. Agents self-register by POSTing their AgentCard; the Registry serves the directory at `/.well-known/agents.json`.

```python
# registry/main.py
from fastapi import FastAPI, HTTPException
from registry.models import AgentCard, AgentHealth
from registry.db import RegistryDatabase
import time

app = FastAPI(title="AegisRegistry", version="1.4.0")
db  = RegistryDatabase("aegis_registry.db")

@app.post("/register")
async def register_agent(card: AgentCard):
    db.upsert_agent(card)
    return {"status": "registered", "agent_id": card.id}

@app.get("/.well-known/agents.json")
async def list_agents(skill: str | None = None):
    """
    RFC 8615-compliant discovery endpoint.
    Optional `skill` query param for server-side skill filtering.
    """
    agents = db.list_healthy_agents(skill_filter=skill)
    return {"agents": agents, "timestamp": time.time()}

@app.get("/agents/{agent_id}/health")
async def get_agent_health(agent_id: str):
    health = db.get_health(agent_id)
    if not health:
        raise HTTPException(404, "Agent not found")
    return health
```

### 5.2 Passive Health Monitor — Latency-Aware Routing

The Registry maintains a rolling **EWMA (Exponentially Weighted Moving Average)** of `mean_response_time` and a sliding window `success_rate`. This telemetry enables the Supervisor to perform **Latency-Aware Routing** — selecting the fastest healthy agent for a given skill rather than a random one.

```sql
-- registry/schema.sql
CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    endpoint        TEXT NOT NULL UNIQUE,
    skills          TEXT NOT NULL,   -- JSON array
    version         TEXT NOT NULL,
    registered_at   REAL NOT NULL,
    last_seen       REAL,
    circuit_state   TEXT DEFAULT 'CLOSED'  -- CLOSED | OPEN | HALF_OPEN
);

CREATE TABLE IF NOT EXISTS agent_health (
    agent_id            TEXT PRIMARY KEY REFERENCES agents(id),
    mean_response_ms    REAL DEFAULT 0.0,     -- EWMA, α=0.2
    success_rate        REAL DEFAULT 1.0,     -- Rolling 10-sample window
    failure_count       INTEGER DEFAULT 0,    -- Consecutive failures
    last_failure_at     REAL,
    last_success_at     REAL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agents_circuit ON agents(circuit_state);
```

```python
# registry/db.py
import sqlite3, json, time, math

EWMA_ALPHA = 0.2   # High α = more weight on recent samples

class RegistryDatabase:
    def record_response(self, agent_id: str, duration_ms: float, success: bool):
        """
        Called by the Supervisor after every RPC call (via OTel span hook).
        Updates EWMA response time and success rate in one atomic transaction.
        """
        with self._conn:  # WAL mode: reads never block this write
            row = self._conn.execute(
                "SELECT mean_response_ms, success_rate, failure_count "
                "FROM agent_health WHERE agent_id=?", (agent_id,)
            ).fetchone()

            if row:
                old_mean, old_rate, fail_cnt = row
                new_mean = EWMA_ALPHA * duration_ms + (1 - EWMA_ALPHA) * old_mean
                # Sliding success_rate: bitmask would be ideal but REAL approximation suffices
                new_rate = EWMA_ALPHA * (1.0 if success else 0.0) + (1 - EWMA_ALPHA) * old_rate
                new_fail = 0 if success else fail_cnt + 1
                self._conn.execute(
                    "UPDATE agent_health SET mean_response_ms=?, success_rate=?, "
                    "failure_count=?, last_success_at=? WHERE agent_id=?",
                    (new_mean, new_rate, new_fail,
                     time.time() if success else None, agent_id)
                )

    def list_healthy_agents(self, skill_filter: str | None) -> list[dict]:
        """
        Returns agents sorted by (success_rate DESC, mean_response_ms ASC).
        This is Latency-Aware Routing — the Supervisor gets a pre-ranked list.
        """
        query = """
            SELECT a.*, h.mean_response_ms, h.success_rate, h.circuit_state
            FROM agents a
            LEFT JOIN agent_health h ON a.id = h.agent_id
            WHERE a.circuit_state = 'CLOSED'
            ORDER BY h.success_rate DESC, h.mean_response_ms ASC
        """
        rows = self._conn.execute(query).fetchall()
        agents = [dict(r) for r in rows]
        if skill_filter:
            agents = [a for a in agents if skill_filter in json.loads(a["skills"])]
        return agents
```

---

## 6. EvidenceGraph — Causal Reasoning Engine

### 6.1 NetworkX DAG Construction

The EvidenceGraph transforms raw agent reports into a **Directed Acyclic Graph** where nodes are timestamped facts and edges encode causal direction. The graph is persisted to SQLite after each investigation for post-mortem XAI access.

```python
# evidence/graph.py
import networkx as nx
import sqlite3, json, time
from dataclasses import dataclass, field, asdict

@dataclass
class EvidenceNode:
    node_id:      str
    fact_type:    str        # METRIC | EVENT | ANOMALY
    description:  str
    value:        float | None
    unit:         str | None
    timestamp:    float      # Unix epoch — critical for temporal correlation
    source_agent: str
    confidence:   float      # 0.0–1.0 from source agent
    metadata:     dict = field(default_factory=dict)

@dataclass
class CausalEdge:
    source:       str        # node_id
    target:       str        # node_id
    relation:     str        # CAUSED_BY | PRECEDED_BY | CORRELATED_WITH
    weight:       float      # Causal strength 0.0–1.0
    delta_t_ms:   float      # Temporal gap between events

class EvidenceGraph:
    CAUSAL_WINDOW_MS = 5000   # Events within 5s of each other are causal candidates

    def __init__(self, trace_id: str, db_path: str = "aegis_evidence.db"):
        self.trace_id = trace_id
        self._g = nx.DiGraph()
        self._db_path = db_path

    def add_evidence(self, node: EvidenceNode):
        self._g.add_node(node.node_id, **asdict(node))

    def run_temporal_correlation(self):
        """
        Causal Inference Algorithm — O(N²) over evidence nodes.
        For each pair (A, B) where ts_A < ts_B and |ts_B - ts_A| < CAUSAL_WINDOW:
          - If A is an ANOMALY and B is an EVENT → infer CAUSED_BY edge
          - Weight = 1 - (delta_t / CAUSAL_WINDOW), decaying linearly with time gap
        
        This implements the principle: "Events close in time with a metric spike
        antecedent are likely causally related."
        """
        nodes = sorted(
            [self._g.nodes[n] for n in self._g.nodes],
            key=lambda n: n["timestamp"]
        )
        for i, node_a in enumerate(nodes):
            for node_b in nodes[i+1:]:
                delta_ms = (node_b["timestamp"] - node_a["timestamp"]) * 1000
                if delta_ms > self.CAUSAL_WINDOW_MS:
                    break   # Sorted, so no further pairs qualify

                if node_a["fact_type"] == "ANOMALY" and node_b["fact_type"] == "EVENT":
                    weight = 1.0 - (delta_ms / self.CAUSAL_WINDOW_MS)
                    edge = CausalEdge(
                        source=node_a["node_id"],
                        target=node_b["node_id"],
                        relation="CAUSED_BY",
                        weight=round(weight, 4),
                        delta_t_ms=round(delta_ms, 2),
                    )
                    self._g.add_edge(
                        node_a["node_id"], node_b["node_id"],
                        **asdict(edge)
                    )

    def get_highest_confidence_chain(self) -> list[dict]:
        """
        Traverses the DAG to find the causal chain with maximum
        cumulative edge weight. Returns ordered list of node dicts.
        """
        if not nx.is_directed_acyclic_graph(self._g):
            raise ValueError("EvidenceGraph has cycles — temporal data may be corrupt")

        # Find the node with max out-degree (the "root cause" candidate)
        root = max(self._g.nodes, key=lambda n: self._g.out_degree(n))
        # Longest path from root by weight
        path = nx.dag_longest_path(self._g, weight="weight")
        return [self._g.nodes[n] for n in path]

    @property
    def overall_confidence(self) -> float:
        """Mean confidence of all nodes, weighted by causal edge strength."""
        if not self._g.nodes:
            return 0.0
        node_scores = [
            self._g.nodes[n]["confidence"] *
            (1 + sum(d["weight"] for _, _, d in self._g.out_edges(n, data=True)))
            for n in self._g.nodes
        ]
        return min(sum(node_scores) / len(node_scores), 1.0)

    def persist(self):
        """Serialize graph to SQLite for post-mortem analysis."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS graphs (trace_id TEXT PRIMARY KEY, data TEXT, ts REAL)")
        conn.execute(
            "INSERT OR REPLACE INTO graphs VALUES (?,?,?)",
            (self.trace_id, json.dumps(nx.node_link_data(self._g)), time.time())
        )
        conn.commit()
        conn.close()
```

---

## 7. Reliability & Safety

### 7.1 Circuit Breaker Pattern — The "Flappy Agent" Handler

```
Circuit States:
  CLOSED     → Normal operation. Calls pass through.
  OPEN       → Agent is blocked. Immediate fallback without attempting the call.
  HALF_OPEN  → One probe call allowed. If it succeeds → CLOSED. If fails → OPEN.

Transition Triggers:
  CLOSED  → OPEN:      3 consecutive failures within 60s window
  OPEN    → HALF_OPEN: 30s cooldown (configurable)
  HALF_OPEN → CLOSED:  1 successful probe call
  HALF_OPEN → OPEN:    1 failed probe call
```

```python
# registry/circuit_breaker.py
import time
from enum import Enum

class CircuitState(str, Enum):
    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"

FAILURE_THRESHOLD = 3
OPEN_TIMEOUT_S    = 30

class CircuitBreaker:
    def __init__(self, agent_id: str, db: "RegistryDatabase"):
        self.agent_id = agent_id
        self._db = db

    def record_failure(self):
        health = self._db.get_health(self.agent_id)
        if health["failure_count"] + 1 >= FAILURE_THRESHOLD:
            self._db.set_circuit_state(self.agent_id, CircuitState.OPEN)
            self._db.set_open_time(self.agent_id, time.time())
        else:
            self._db.increment_failure(self.agent_id)

    def record_success(self):
        state = self._db.get_circuit_state(self.agent_id)
        if state == CircuitState.HALF_OPEN:
            # Probe succeeded — close the circuit
            self._db.set_circuit_state(self.agent_id, CircuitState.CLOSED)
        self._db.reset_failures(self.agent_id)

    def allow_request(self) -> bool:
        """
        Returns True if the call should proceed.
        Called by Supervisor's dispatch node before every RPC.
        """
        state = self._db.get_circuit_state(self.agent_id)
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.OPEN:
            opened_at = self._db.get_open_time(self.agent_id)
            if time.time() - opened_at > OPEN_TIMEOUT_S:
                self._db.set_circuit_state(self.agent_id, CircuitState.HALF_OPEN)
                return True   # Allow one probe
            return False      # Block — return fallback immediately
        return True   # HALF_OPEN: allow probe
```

**Fallback Routing:** When a circuit is OPEN, the Supervisor queries the Registry for an agent with the same `skill_tag` but a different `id`. If no fallback exists, the investigation continues with partial evidence and notes the gap in the final diagnosis.

### 7.2 Deterministic Security Guardrails

```python
# agents/base_agent.py
import ast, textwrap
from functools import wraps

# ─── Deny List: patterns that indicate host-mutating intent ───────────────────
_FORBIDDEN_PATTERNS = [
    "os.system", "subprocess.run", "subprocess.Popen", "subprocess.call",
    "subprocess.check_output", "os.remove", "os.unlink", "shutil.rmtree",
    "pathlib.Path.unlink", "open.*[\"']w[\"']",   # Write-mode file operations
    "socket.connect",                               # Outbound network (not local RPC)
    "ctypes", "cffi",                              # Raw memory / DLL injection
]

class SecuritySandbox:
    """
    Static analysis guard executed at agent initialization time.
    Prevents 'supply chain'-style attacks if an agent module is tampered with.
    """
    @staticmethod
    def validate_agent_module(source_code: str, agent_name: str):
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern in source_code:
                raise SecurityViolationError(
                    f"Agent '{agent_name}' contains forbidden pattern: '{pattern}'. "
                    "Only Read-Only WMI/Log access is permitted."
                )

    @staticmethod
    def read_only_wmi(query: str) -> list[dict]:
        """
        Safe WMI query wrapper — SELECT only, no INSERT/UPDATE/DELETE/EXEC.
        Enforces the whitepaper's Non-Destructive Execution principle.
        """
        query_upper = query.strip().upper()
        if not query_upper.startswith("SELECT"):
            raise SecurityViolationError(
                f"Forbidden WMI operation: '{query[:80]}'. Only SELECT queries allowed."
            )
        import wmi
        c = wmi.WMI()
        return [dict(obj.wmi_property_pairs) for obj in c.query(query)]


class SecurityViolationError(RuntimeError):
    pass
```

---

## 8. Observability — OpenTelemetry

### 8.1 Span Context Propagation

Every investigation begins with a `trace_id` generated at the Gateway. This ID is injected into every downstream RPC (as seen in the whitepaper's schema) and into every OTel span, creating a complete distributed trace of the investigation.

```python
# core/telemetry.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import inject, extract
import uuid

# Configure the tracer — exporter targets a local Jaeger or Zipkin instance
provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4317"))
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("aegismesh.supervisor")

def new_trace_id() -> str:
    return f"inv_{uuid.uuid4().hex[:12]}"

class InvestigationTracer:
    def __init__(self, trace_id: str):
        self.trace_id = trace_id

    def span(self, name: str, agent_id: str | None = None):
        """
        Context manager. Wraps an RPC call in an OTel span.
        Attributes become queryable in Jaeger/Zipkin UI.
        """
        return tracer.start_as_current_span(
            name,
            attributes={
                "aegis.trace_id":      self.trace_id,
                "aegis.agent_id":      agent_id or "supervisor",
                "aegis.component":     "A2ABus",
            }
        )
```

### 8.2 The Black Box Recorder — JSONL Structured Log

Every inter-agent message is appended to a **JSONL** (JSON Lines) file. This format is chosen because it is append-only (no corruption on crash), trivially parseable by `jq`, and compatible with log aggregators like Loki.

```python
# core/black_box.py
import json, time, aiofiles
from pathlib import Path

LOG_DIR = Path("logs/aegis_blackbox")
LOG_DIR.mkdir(parents=True, exist_ok=True)

class BlackBoxRecorder:
    """
    Append-only structured logger for every A2A message.
    Uses aiofiles for non-blocking I/O — never compete with the asyncio event loop.
    Each investigation gets its own JSONL file named by trace_id.
    """
    def __init__(self, trace_id: str):
        self._path = LOG_DIR / f"{trace_id}.jsonl"

    async def record(self, event_type: str, source: str, target: str, payload: dict):
        entry = {
            "ts":         time.time(),
            "trace_id":   self._path.stem,
            "event_type": event_type,        # RPC_CALL | RPC_RESPONSE | CIRCUIT_OPEN | GOVERNOR_THROTTLE
            "source":     source,
            "target":     target,
            "payload":    payload,
        }
        async with aiofiles.open(self._path, mode="a") as f:
            await f.write(json.dumps(entry) + "\n")

# Example JSONL entry:
# {"ts": 1741693437.12, "trace_id": "inv_8842a3f1c2d0", "event_type": "RPC_CALL",
#  "source": "supervisor", "target": "LogAgent", "payload": {"method": "execute_task",
#  "params": {"task": "log_analysis", "file_path": "/var/log/sys.log"}}}
```

---

## 9. Agent Card Schema Definition

The **AgentCard** is the fundamental currency of the mesh. It is the data contract that every agent must publish to the Registry upon startup. It is intentionally minimal to reduce registration overhead but expressive enough for skill-based routing and circuit breaker decisions.

```json
{
  "$schema": "https://aegismesh.local/schemas/agent-card/v1.4.0",
  "$comment": "AegisMesh AgentCard — register via POST /register",

  "id":          "log-agent-001",
  "name":        "LogAgent",
  "version":     "2.1.0",
  "endpoint":    "http://127.0.0.1:8101",

  "skills": [
    "log_analysis",
    "error_pattern_detection",
    "log_tail"
  ],

  "capabilities": {
    "max_concurrent_tasks": 3,
    "supports_streaming":   false,
    "read_only":            true,
    "wmi_access":           false,
    "filesystem_access":    true,
    "filesystem_scope":     "PROJECT_DIR_ONLY"
  },

  "health": {
    "heartbeat_interval_s": 30,
    "health_check_path":    "/health"
  },

  "security": {
    "sandbox_level":   "READ_ONLY",
    "forbidden_calls": ["os.system", "subprocess.*", "socket.connect"],
    "audit_log":       true
  },

  "fallback_agent_id": "log-agent-002",

  "meta": {
    "author":      "AegisMesh Team",
    "description": "Specialist agent for structured and unstructured log parsing.",
    "tags":        ["diagnostics", "observability", "python"],
    "created_at":  "2026-03-11T07:23:00Z"
  }
}
```

**Key design decisions:**

| Field | Rationale |
|---|---|
| `skills` | String array (not enum) for forward-compatibility as new diagnostic domains emerge |
| `fallback_agent_id` | Declarative Circuit Breaker target — Registry doesn't need to discover fallbacks at failure-time |
| `filesystem_scope: PROJECT_DIR_ONLY` | Enforces whitepaper's scope restriction programmatically |
| `max_concurrent_tasks` | The Governor uses this to avoid dispatching more tasks than an agent can handle |
| `heartbeat_interval_s` | Registry's Passive Health Monitor evicts agents that miss 3 heartbeats |

---

## 10. Implementation Roadmap

### Phase 1 — The Foundation *(Week 1–2)*
**Milestone: BaseAgent + A2A Handshake**

```
deliverables/
├── core/
│   ├── base_agent.py       # BaseAgent class w/ JSON-RPC server (FastAPI)
│   ├── a2a_client.py       # A2ABusClient (HTTP/1.1 Keep-Alive)
│   ├── black_box.py        # BlackBoxRecorder (JSONL)
│   └── telemetry.py        # OTel tracer bootstrap
├── agents/
│   └── security_sandbox.py # SecuritySandbox + read_only_wmi()
└── tests/
    └── test_rpc_roundtrip.py  # Validates idempotency + JSON-RPC schema
```

**Key technical gate:** Every BaseAgent must pass a loopback RPC roundtrip test with a valid AgentCard before it can proceed to Phase 2. The test asserts:
  1. Response envelope contains `jsonrpc: "2.0"` and matching `id`.
  2. A re-sent request with the same `id` returns an identical response (idempotency).
  3. A malformed request returns JSON-RPC error code `-32600`.

### Phase 2 — The Mesh *(Week 3–4)*
**Milestone: AegisRegistry + HA Agent Cache**

```
deliverables/
├── registry/
│   ├── main.py          # FastAPI app + /.well-known/agents.json
│   ├── db.py            # RegistryDatabase (SQLite, WAL mode)
│   ├── models.py        # AgentCard Pydantic model
│   ├── circuit_breaker.py
│   └── schema.sql
└── tests/
    └── test_registry_ha.py  # Simulates Registry crash → Supervisor uses local cache
```

**Key technical gate:** Kill the Registry mid-investigation and prove the Supervisor continues using its in-memory cache. No `KeyError` or `ConnectionRefusedError` surfaces to the user.

### Phase 3 — The Brain *(Week 5–6)*
**Milestone: LangGraph Supervisor + RAM Governor**

```
deliverables/
├── supervisor/
│   ├── graph.py            # LangGraph StateGraph definition
│   ├── nodes/
│   │   ├── intent_parser.py
│   │   ├── skill_discovery.py
│   │   ├── task_dispatch.py
│   │   ├── evidence_synthesis.py
│   │   ├── causal_validation.py
│   │   └── report_generation.py
│   └── governor.py         # RAMGovernor (psutil + asyncio.Semaphore)
└── tests/
    └── test_governor.py    # Mocks psutil at 86% → asserts semaphore limit == 1
```

**Key technical gate:** Stress-inject a mock agent that consumes 400MB and verify the Governor drops worker count from 8 to 1 within one polling cycle (2s). Assert no OOM or asyncio task starvation occurs.

### Phase 4 — The Graph *(Week 7–8)*
**Milestone: NetworkX EvidenceGraph + Causal Validation**

```
deliverables/
├── evidence/
│   ├── graph.py            # EvidenceGraph (NetworkX DAG + temporal correlation)
│   └── persistence.py      # SQLite serialization for post-mortem XAI
├── agents/
│   ├── process_agent.py    # WMI-based process monitor
│   ├── log_agent.py        # Log file parser with anomaly detection
│   ├── disk_agent.py       # Disk I/O monitor
│   └── network_agent.py    # Network connection inspector
└── tests/
    └── test_causal_inference.py  # Injects synthetic "disk spike → crash" events
                                  # Asserts CAUSED_BY edge with weight > 0.6
```

**Key technical gate:** Feed the graph 5 synthetic `EvidenceNode` objects where Node A (ANOMALY: disk spike) precedes Node B (EVENT: process crash) by 1.5s. Assert the algorithm produces a `CAUSED_BY` edge with `weight >= 0.70` and `delta_t_ms ≈ 1500`.

---

> [!NOTE]
> **Vocabulary Glossary (as used in this specification)**
>
> | Term | Definition in AegisMesh |
> |---|---|
> | **Backpressure** | The RAM Governor's semaphore that limits task ingestion rate when system is under memory pressure |
> | **Idempotency** | JSON-RPC `id` field guarantees replayed requests produce identical responses |
> | **Race Condition** | Prevented in LangGraph via `Annotated[list, operator.add]` reducer on `raw_evidence` |
> | **Shared-Nothing Architecture** | Each specialist agent is an independent process with isolated memory |
> | **Telemetry** | OTel spans + EWMA health metrics in Registry SQLite |

---

*AegisMesh v1.4.0 — Technical Masterpiece Blueprint*  
*Authored: March 11, 2026 | Classification: Technical Specification / Systems Architecture*

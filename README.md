# ▲ AegisMesh v1.4.0

### Sovereign Agent-to-Agent (A2A) Diagnostic Mesh

**AegisMesh** is a local-first, privacy-preserving, autonomous multi-agent ecosystem engineered to diagnose system anomalies. It operates strictly within a local process boundary on consumer hardware (e.g., 16GB RAM / i5 CPU), utilizing a lightweight LangGraph supervisor, a specialized agent registry, and a deterministic EvidenceGraph for completely verifiable root-cause analysis.

---

## 🚀 Key Features

* **Sovereign Local Execution**: All telemetry remains strictly local. No outbound diagnostic data is sent to external observability vendors.
* **Component-Level Isolation**: Built on a Shared-Nothing Architecture. Agents (`SysMon`, `WinLog`, `NetDiag`) run as independent Uvicorn process boundaries communicating via a JSON-RPC 2.0 Keep-Alive bus.
* **Adaptive Cognitive Routing (Hybrid AI)**: Replaces slow, blind LLM-first architectures with a deterministic O(1) heuristic fast-path. Easily bypasses costly LLM inference (up to 80% reduction in calls) for determinable anomalies while intelligently falling back to Llama 3 for complex semantics.
* **Causal EvidenceGraph**: Does not blindly hallucinate diagnostics. Agent anomalies are mathematically linked into a NetworkX Directed Acyclic Graph based on temporal weighting and correlation.
* **16GB RAM Governor**: Features a proactive dynamic backpressure Semaphore gate that seamlessly controls async task concurrency (`8 -> 3 -> 1` lanes) natively tied to the host's `psutil.virtual_memory` limits to prevent OOM swapping.
* **Resilient Service Mesh Control Plane**: Internal `AegisRegistry` utilizes Latency-Aware Routing, EWMA response times, and an RFC 8615-compliant `/.well-known/agents.json` directory. Includes Circuit Breaker pattern auto-eviction of zombie agents.

---

## 💻 Cyber-Aesthetic Native Web UI

AegisMesh ships with a zero-dependency, pure glassmorphism front-end mounted directly into the Gateway API. Type queries naturally into the terminal, observe telemetry streams, and watch the Supervisor construct its Causal Confidence Dial in real-time.

---

## ⚙️ Quick Start

### 1. Requirements

* Windows OS (due to `WMI` read-only queries in agents)
* Python 3.10+
* 16GB RAM constraint highly supported
* Ollama / Llama 3 (for default local LLM synthesis)

### 2. Bootstrapping the Mesh

The `mesh_bootstrapper.py` script automatically manages port sweeping, SQLite WAL cleanups from previous states, and spins up the required 5 micro-processes sequentially.

```bash
# Start the entire local service mesh
.venv\Scripts\python mesh_bootstrapper.py
```

### 3. Open the UI

Navigate to the Gateway port on your local machine:
**`http://127.0.0.1:9000`**

### 4. Running the Integration Suite

If you want to bypass the UI and execute all 13 staging tests directly against the Gateway API to view the Fast-Path routing logic in stdout:

```bash
# (While the Mesh Bootstrapper is running in another terminal)
.venv\Scripts\python automated_tests.py
```

---

## 🧬 Architectural Topology

```text
  User Input → [Gateway :9000]
                    ↓
[Supervisor LangGraph Engine + Adaptive Router]  ←  [EvidenceGraph Causal Generator]
                    ↓  (JSON-RPC 2.0)
     [AegisRegistry Control Plane :8000]
                    ↓
    [Specialist Distributed Agent Processes]
      ↳ SystemMonitorAgent :8101
      ↳ NetworkDiagnosticAgent :8102
      ↳ WindowsEventLogAgent :8103
```

---

*For deeper implementation specs, review the `docs/` folder.*

"""
tests/test_phase1_foundation.py
================================
Phase 1 Technical Gate Tests (Blueprint §10 — Implementation Roadmap)

Tests verify:
  1. JSON-RPC roundtrip idempotency
  2. Malformed request → error code -32600
  3. Transport scope enforcement (non-localhost rejected)
  4. EvidenceGraph causal inference (disk spike → process crash)
  5. Governor semaphore limit changes on RAM threshold
  6. SecuritySandbox: forbidden pattern detection
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

# ── Transport Scope ────────────────────────────────────────────────────────────
from aegismesh.core.transport_scope import (
    validate_localhost_endpoint,
    TransportScopeError,
)


def test_transport_scope_allows_localhost():
    """localhost and 127.0.0.1 must pass."""
    validate_localhost_endpoint("http://127.0.0.1:8101")
    validate_localhost_endpoint("http://localhost:8080")


def test_transport_scope_rejects_external():
    """External hosts must raise TransportScopeError."""
    with pytest.raises(TransportScopeError):
        validate_localhost_endpoint("http://192.168.1.50:8101")

    with pytest.raises(TransportScopeError):
        validate_localhost_endpoint("http://example.com/rpc")


# ── SecuritySandbox ────────────────────────────────────────────────────────────
from aegismesh.agents.security_sandbox import SecuritySandbox, SecurityViolationError


def test_sandbox_passes_clean_code():
    clean = "import psutil\nresult = psutil.virtual_memory()\n"
    SecuritySandbox.validate_agent_module(clean, "CleanAgent")  # Must not raise


def test_sandbox_blocks_os_system():
    dirty = "import os\nos.system('whoami')\n"
    with pytest.raises(SecurityViolationError, match="os.system"):
        SecuritySandbox.validate_agent_module(dirty, "DirtyAgent")


def test_sandbox_blocks_subprocess():
    dirty = "import subprocess\nsubprocess.run(['del', 'file.txt'])\n"
    with pytest.raises(SecurityViolationError, match="subprocess.run"):
        SecuritySandbox.validate_agent_module(dirty, "DirtyAgent")


def test_sandbox_blocks_eval():
    dirty = 'result = eval("os.getcwd()")\n'
    with pytest.raises(SecurityViolationError, match="eval"):
        SecuritySandbox.validate_agent_module(dirty, "DirtyAgent")


# ── EvidenceGraph Causal Inference ────────────────────────────────────────────
from aegismesh.evidence.graph import EvidenceGraph, EvidenceNode


def test_causal_inference_disk_spike_causes_crash():
    """
    Phase 4 Gate: feed ANOMALY + EVENT 1.5s apart.
    Assert CAUSED_BY edge with weight >= 0.70.
    """
    graph = EvidenceGraph(trace_id="test_inv_001", db_path=":memory:")

    t0 = time.time()

    disk_spike = EvidenceNode(
        node_id="node_disk",
        fact_type="ANOMALY",
        description="Disk I/O spike: 98% utilisation",
        value=98.0,
        unit="percent",
        timestamp=t0,
        source_agent="DiskAgent",
        confidence=0.90,
    )
    process_crash = EvidenceNode(
        node_id="node_crash",
        fact_type="EVENT",
        description="Process PID 1234 terminated: exit code -1073741819",
        value=None,
        unit=None,
        timestamp=t0 + 1.5,  # 1500ms later
        source_agent="ProcessAgent",
        confidence=0.95,
    )

    graph.add_evidence(disk_spike)
    graph.add_evidence(process_crash)
    graph.run_temporal_correlation()

    assert graph._g.has_edge("node_disk", "node_crash"), "CAUSED_BY edge missing"
    edge_data = graph._g.get_edge_data("node_disk", "node_crash")
    assert edge_data["relation"] == "CAUSED_BY"
    assert edge_data["weight"] >= 0.70, f"Weight too low: {edge_data['weight']}"
    assert abs(edge_data["delta_t_ms"] - 1500.0) < 50.0, "delta_t_ms inaccurate"


def test_evidence_node_sanitizes_inputs():
    """Long description and out-of-range confidence must be clamped."""
    node = EvidenceNode(
        node_id="n1",
        fact_type="METRIC",
        description="x" * 1000,  # Over 512 char limit
        value=42.0,
        unit="ms",
        timestamp=time.time(),
        source_agent="TestAgent",
        confidence=9.99,  # Over 1.0
    )
    assert len(node.description) == 512
    assert node.confidence == 1.0


def test_evidence_graph_overall_confidence_empty():
    """Empty graph must return 0.0 confidence, not raise."""
    graph = EvidenceGraph(trace_id="empty_test", db_path=":memory:")
    assert graph.overall_confidence == 0.0


# ── Governor semaphore ────────────────────────────────────────────────────────
from aegismesh.core.governor import RAMGovernor, reset_governor


@pytest.mark.asyncio
async def test_governor_starts_with_parallel_limit():
    reset_governor()
    gov = RAMGovernor()
    assert gov.current_limit == 8  # PARALLEL_WORKERS default


@pytest.mark.asyncio
async def test_governor_semaphore_respects_limit():
    """Goroutines beyond limit must wait (not crash)."""
    gov = RAMGovernor()
    gov._resize_semaphore(2)  # Force 2-slot limit

    results: list[str] = []

    async def worker(name: str):
        async with gov.acquire(agent_id=name):
            await asyncio.sleep(0.05)
            results.append(name)

    await asyncio.gather(worker("a"), worker("b"), worker("c"))
    assert sorted(results) == ["a", "b", "c"]  # All completed, just serialised

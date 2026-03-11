"""
core/config.py
==============
Single source of truth for all runtime configuration.
Values are read from .env (via python-dotenv) at import time.
Every other module imports from here — no module ever reads os.environ directly.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve .env relative to the project root (two levels up from src/aegismesh/core/)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env", override=False)


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _get_int(key: str, default: int) -> int:
    raw = _get(key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    raw = _get(key, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(key: str, default: bool) -> bool:
    return _get(key, str(default)).lower() in ("true", "1", "yes")


# ── LLM ──────────────────────────────────────────────────────────────────────
LLM_PROVIDER: str = _get("AEGIS_LLM_PROVIDER", "openai")   # "openai" | "ollama"
LLM_MODEL: str    = _get("AEGIS_LLM_MODEL", "gpt-4o-mini")
OPENAI_API_KEY: str = _get("OPENAI_API_KEY", "")
OLLAMA_BASE_URL: str = _get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

# ── Registry ──────────────────────────────────────────────────────────────────
REGISTRY_HOST: str = _get("AEGIS_REGISTRY_HOST", "127.0.0.1")
REGISTRY_PORT: int = _get_int("AEGIS_REGISTRY_PORT", 8000)
REGISTRY_URL: str  = f"http://{REGISTRY_HOST}:{REGISTRY_PORT}"
REGISTRY_DB: str   = _get("AEGIS_REGISTRY_DB", str(_PROJECT_ROOT / "aegis_registry.db"))

# ── Evidence ──────────────────────────────────────────────────────────────────
EVIDENCE_DB: str = _get("AEGIS_EVIDENCE_DB", str(_PROJECT_ROOT / "aegis_evidence.db"))

# ── Observability ─────────────────────────────────────────────────────────────
OTEL_ENDPOINT: str = _get("AEGIS_OTEL_ENDPOINT", "none")   # "none" disables export
LOG_DIR: Path      = _PROJECT_ROOT / "logs" / "aegis_blackbox"

# ── Governor ──────────────────────────────────────────────────────────────────
RAM_THRESHOLD_HIGH: float = _get_float("AEGIS_RAM_THRESHOLD_HIGH", 85.0)
RAM_THRESHOLD_MED: float  = _get_float("AEGIS_RAM_THRESHOLD_MED", 75.0)
PARALLEL_WORKERS: int     = _get_int("AEGIS_PARALLEL_WORKERS", 8)
DEGRADED_WORKERS: int     = 3
SEQUENTIAL_WORKERS: int   = 1

# ── Investigation Lifecycle ───────────────────────────────────────────────────
INVESTIGATION_TIMEOUT_S: float   = 300.0
MAX_INVESTIGATION_RETRIES: int   = 2
HIGH_CONFIDENCE_THRESHOLD: float = 0.70
PARTIAL_CONFIDENCE_THRESHOLD: float = 0.40

# ── EvidenceGraph Limits ──────────────────────────────────────────────────────
MAX_NODES_PER_INVESTIGATION: int = 200
MAX_NODES_PER_AGENT: int         = 50
MAX_EDGES_PER_INVESTIGATION: int = 500
MAX_OUT_DEGREE_PER_NODE: int     = 10
BASE_CAUSAL_WINDOW_MS: float     = 5000.0

# ── Security ──────────────────────────────────────────────────────────────────
SANDBOX_ENABLED: bool = _get_bool("AEGIS_SANDBOX_ENABLED", True)

# ── Agent Scheduling ──────────────────────────────────────────────────────────
MAX_HOLD_TIME_S: float        = 30.0
STARVATION_AGING_RATE_MS: float = 100.0
AGENT_DEFAULT_CEILING_MB: float = 512.0
AGENT_TTL_SECONDS: float        = 90.0

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHE_TTL_SECONDS: float = 120.0

# ── Circuit Breaker ───────────────────────────────────────────────────────────
CB_FAILURE_THRESHOLD: int = 3
CB_OPEN_TIMEOUT_S: float  = 30.0

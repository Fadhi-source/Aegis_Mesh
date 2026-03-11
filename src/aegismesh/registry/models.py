"""
registry/models.py
==================
Pydantic v2 models for AgentCard and AgentHealth.
These are the canonical data contracts — used by both the Registry and agents.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class AgentCapabilities(BaseModel):
    max_concurrent_tasks: int = 3
    supports_streaming: bool = False
    read_only: bool = True
    wmi_access: bool = False
    filesystem_access: bool = True
    filesystem_scope: str = "PROJECT_DIR_ONLY"


class AgentHealth(BaseModel):
    heartbeat_interval_s: int = 30
    health_check_path: str = "/health"


class AgentSecurity(BaseModel):
    sandbox_level: str = "READ_ONLY"
    audit_log: bool = True


class AgentMeta(BaseModel):
    author: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""


class AgentCard(BaseModel):
    """
    The fundamental currency of the AegisMesh mesh.
    Every agent must POST this to /register at startup.
    """

    id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=128)
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    endpoint: str = Field(..., description="Agent's HTTP endpoint — must be localhost")

    skills: list[str] = Field(..., min_length=1)
    skill_confidence: dict[str, float] = Field(
        default_factory=dict,
        description="Per-skill confidence 0.0–1.0 for CWS scoring",
    )

    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    health: AgentHealth = Field(default_factory=AgentHealth)
    security: AgentSecurity = Field(default_factory=AgentSecurity)

    fallback_agent_id: Optional[str] = None
    meta: AgentMeta = Field(default_factory=AgentMeta)

    @field_validator("skill_confidence")
    @classmethod
    def clamp_confidence(cls, v: dict[str, float]) -> dict[str, float]:
        return {k: max(0.0, min(1.0, val)) for k, val in v.items()}

    @field_validator("skills")
    @classmethod
    def skills_lowercase(cls, v: list[str]) -> list[str]:
        return [s.lower().strip() for s in v]


class JsonRpcRequest(BaseModel):
    """Validated incoming JSON-RPC 2.0 envelope for agent endpoints."""

    jsonrpc: str = Field(..., pattern=r"^2\.0$")
    method: str
    params: dict = Field(default_factory=dict)
    id: str

    @field_validator("method")
    @classmethod
    def allowed_method(cls, v: str) -> str:
        allowed = {"execute_task", "health_check", "get_capabilities"}
        if v not in allowed:
            raise ValueError(f"Unknown method '{v}'. Allowed: {allowed}")
        return v

    @field_validator("params")
    @classmethod
    def params_size_guard(cls, v: dict) -> dict:
        import json
        if len(json.dumps(v)) > 1_048_576:  # 1 MB
            raise ValueError("params payload exceeds 1MB limit")
        return v

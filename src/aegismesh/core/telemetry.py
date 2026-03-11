"""
core/telemetry.py
=================
OpenTelemetry bootstrap and InvestigationTracer helper.

If AEGIS_OTEL_ENDPOINT is "none" (the default), OTel is configured with
a NoOp exporter so the rest of the codebase never needs an if-guard.
All spans are recorded — they are simply not exported anywhere.
"""
from __future__ import annotations

import uuid
import logging
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from aegismesh.core.config import OTEL_ENDPOINT

logger = logging.getLogger("aegis.telemetry")

# ── Provider setup ────────────────────────────────────────────────────────────
_provider = TracerProvider()

if OTEL_ENDPOINT and OTEL_ENDPOINT.lower() != "none":
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        _exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
        _provider.add_span_processor(BatchSpanProcessor(_exporter))
        logger.info("OTel: exporting spans to %s", OTEL_ENDPOINT)
    except Exception as exc:
        logger.warning("OTel exporter failed to init (%s) — falling back to console.", exc)
        _provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
else:
    # Offline / default: log to console at DEBUG level (practically silent)
    _provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    logger.debug("OTel: AEGIS_OTEL_ENDPOINT=none — console exporter active (debug only).")

trace.set_tracer_provider(_provider)
_tracer = trace.get_tracer("aegismesh", schema_url="https://opentelemetry.io/schemas/1.11.0")


# ── Public API ────────────────────────────────────────────────────────────────

def new_trace_id() -> str:
    """Generates a short, human-readable investigation trace ID."""
    return f"inv_{uuid.uuid4().hex[:12]}"


class InvestigationTracer:
    """
    Thin wrapper around the OTel tracer.
    Attaches aegis.trace_id and aegis.agent_id to every span automatically.

    Usage:
        tracer = InvestigationTracer(trace_id="inv_abc123")
        with tracer.span("task_dispatch", agent_id="LogAgent"):
            await rpc_call(...)
    """

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id

    @contextmanager
    def span(self, name: str, agent_id: str | None = None, **extra_attrs):
        attrs = {
            "aegis.trace_id": self.trace_id,
            "aegis.agent_id": agent_id or "supervisor",
            "aegis.component": "A2ABus",
            **extra_attrs,
        }
        with _tracer.start_as_current_span(name, attributes=attrs) as s:
            yield s

"""
supervisor/nodes/intent_parser.py
==================================
Node 1 of 5 in the LangGraph reasoning loop.

Responsibility:
  - Takes the raw user_query string
  - Uses Llama 3 (via Ollama) to extract: parsed_intent + required_skills
  - Normalises skill names to match the Registry's known skill vocabulary
  - Outputs: state.parsed_intent, state.required_skills

Design decision:
  - We use a strict JSON output prompt so Llama 3 produces parseable output
  - Fallback: if Llama 3 produces non-JSON, we do keyword matching ourselves
    (no investigation should fail because of an LLM formatting hiccup)
"""
from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from aegismesh.supervisor.state import InvestigationState

logger = logging.getLogger("aegis.supervisor.intent_parser")

# All known skills across all registered agents (must match AgentCard definitions)
KNOWN_SKILLS = [
    "check_ram",
    "check_cpu_spike",
    "check_disk_space",
    "get_top_processes",
    "check_socket_exhaustion",
    "check_listening_ports",
    "check_network_adapters",
    "check_application_crashes",
    "check_service_failures",
    "query_event_log",
]

SYSTEM_PROMPT = """You are an expert Windows PC diagnostic assistant.
A user will describe a problem. Your task is to:
1) Write a concise one-sentence description of the core technical issue.
2) Select the most relevant diagnostic skills to investigate it.

You MUST respond with ONLY valid JSON in this exact format, no other text:
{
  "intent": "<one-sentence technical description>",
  "required_skills": ["<skill1>", "<skill2>"]
}

Available skills: """ + ", ".join(KNOWN_SKILLS)


def _fast_keyword_match(query: str) -> list[str]:
    """
    Rule-based deterministic fast-path.
    Maps common words to skills deterministically.
    """
    q = query.lower()
    skills = []

    if any(w in q for w in ["slow", "ram", "memory", "lag", "sluggish"]):
        skills += ["check_ram", "get_top_processes"]
    if any(w in q for w in ["cpu", "processor", "spike", "100%", "hot", "fan"]):
        skills += ["check_cpu_spike", "get_top_processes"]
    if any(w in q for w in ["disk", "storage", "space", "full"]):
        skills += ["check_disk_space"]
    if any(w in q for w in ["crash", "error", "blue screen", "bsod", "hang", "close"]):
        skills += ["check_application_crashes", "check_service_failures"]
    if any(w in q for w in ["network", "internet", "socket", "port", "connection", "drop"]):
        skills += ["check_socket_exhaustion", "check_listening_ports", "check_network_adapters"]

    return list(dict.fromkeys(skills))  # dedupe preserving order


async def intent_parser_node(state: InvestigationState, llm) -> dict:
    """
    LangGraph node: parses user query into structured intent + required skills.
    Implements Adaptive Routing to avoid heavy local LLM inference when possible.
    """
    logger.info("[%s] Intent parser: '%s'", state.trace_id, state.user_query[:80])

    # ── ADAPTIVE TIER 1: DETERMINISTIC FAST-PATH ──────────────────────────────
    fast_skills = _fast_keyword_match(state.user_query)
    if fast_skills:
        logger.info("[%s] Adaptive Routing: Deterministic FAST-PATH triggered. Bypassing LLM intent parsing.", state.trace_id)
        return {
            "parsed_intent": f"Diagnostic investigation: {state.user_query}",
            "required_skills": fast_skills
        }

    # ── ADAPTIVE TIER 2: LLM INFERENCE ─────────────────────────────────────────
    logger.info("[%s] Adaptive Routing: Ambiguous query, escalating to LLM intent parsing.", state.trace_id)
    
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"User problem: {state.user_query}"),
    ]

    llm_output = ""
    try:
        response = await llm.ainvoke(messages)
        llm_output = response.content if hasattr(response, "content") else str(response)

        json_match = re.search(r"\{.*?\}", llm_output, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON object found in LLM output")

        parsed = json.loads(json_match.group())
        intent = str(parsed.get("intent", state.user_query))
        raw_skills = [s.strip().lower() for s in parsed.get("required_skills", [])]

        validated = [s for s in raw_skills if s in KNOWN_SKILLS]
        if not validated:
            logger.warning("[%s] LLM returned unknown skills %s — defaulting to comprehensive.", state.trace_id, raw_skills)
            validated = ["check_ram", "check_cpu_spike", "check_disk_space"]

        logger.info("[%s] Parsed intent: '%s' | Skills: %s", state.trace_id, intent, validated)
        return {"parsed_intent": intent, "required_skills": validated}

    except Exception as exc:
        logger.warning("[%s] LLM intent parsing failed (%s) — using fallback.", state.trace_id, exc)
        return {"parsed_intent": state.user_query, "required_skills": ["check_ram", "check_cpu_spike", "check_disk_space"]}

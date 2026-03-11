"""
agents/netdiag_agent.py
=======================
Network Diagnostics Agent.
Inherits from BaseAgent.
"""
from __future__ import annotations

import logging
import psutil

from aegismesh.agents.base_agent import BaseAgent
from aegismesh.registry.models import AgentCard, AgentCapabilities, AgentMeta

logger = logging.getLogger("aegis.agents.netdiag")


class NetworkDiagnosticAgent(BaseAgent):
    """
    Agent responsible for diagnosing network connectivity, socket exhaustion,
    and listening ports without doing deep packet inspection.
    """

    def get_card(self) -> AgentCard:
        return AgentCard(
            id="netdiag_001",
            name="NetworkDiagnosticAgent",
            version="1.0.0",
            endpoint="http://127.0.0.1:8102",
            skills=[
                "check_socket_exhaustion",
                "check_listening_ports",
                "check_network_adapters"
            ],
            skill_confidence={
                "check_socket_exhaustion": 0.90,
                "check_listening_ports": 0.95,
                "check_network_adapters": 0.80
            },
            capabilities=AgentCapabilities(
                max_concurrent_tasks=3,
                wmi_access=False
            ),
            meta=AgentMeta(
                description="Network socket and interface diagnostic agent.",
                tags=["network", "tcp", "sockets"]
            )
        )

    async def execute_task(self, params: dict, trace_id: str) -> dict:
        intent = params.get("intent", "").lower()
        logger.info("[Trace: %s] NetDiag analyzing intent: %s", trace_id, intent)

        result: dict = {"facts": []}

        try:
            conns = psutil.net_connections(kind='tcp')
        except psutil.AccessDenied:
            return {"facts": [{"type": "EVENT", "description": "Access denied to read TCP sockets (requires Administrator elevation)."}]}

        # 1. Socket Exhaustion / Connection Count
        if "socket" in intent or "exhaust" in intent or "connection" in intent:
            time_wait = sum(1 for c in conns if c.status == 'TIME_WAIT')
            established = sum(1 for c in conns if c.status == 'ESTABLISHED')
            total = len(conns)

            result["facts"].append({
                "type": "METRIC",
                "name": "tcp_established_count",
                "value": established,
                "unit": "sockets"
            })
            result["facts"].append({
                "type": "METRIC",
                "name": "tcp_time_wait_count",
                "value": time_wait,
                "unit": "sockets"
            })

            if time_wait > 4000:
                result["facts"].append({
                    "type": "ANOMALY",
                    "description": f"Ephermal port exhaustion risk: {time_wait} sockets stuck in TIME_WAIT state."
                })

        # 2. Port conflict checking
        if "port" in intent or "bind" in intent:
            target_port = params.get("target_port")
            if target_port:
                conflicts = [c for c in conns if c.status == 'LISTEN' and c.laddr.port == int(target_port)]
                if conflicts:
                    pid = conflicts[0].pid
                    proc_name = "Unknown"
                    if pid:
                        try:
                            proc_name = psutil.Process(pid).name()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    result["facts"].append({
                        "type": "ANOMALY",
                        "description": f"Port {target_port} bind conflict. Currently owned by {proc_name} (PID: {pid})."
                    })
                else:
                    result["facts"].append({
                        "type": "EVENT",
                        "description": f"Port {target_port} is not in use by any listening socket."
                    })
            else:
                listening = sum(1 for c in conns if c.status == 'LISTEN')
                result["facts"].append({
                    "type": "METRIC",
                    "name": "tcp_listening_sockets",
                    "value": listening,
                    "unit": "sockets"
                })

        if not result["facts"]:
            result["facts"].append({
                "type": "EVENT",
                "description": "Standard network health checks returned nominal values."
            })

        return result

app = NetworkDiagnosticAgent().create_app()

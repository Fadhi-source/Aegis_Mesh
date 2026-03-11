"""
agents/sysmon_agent.py
======================
System Monitor Agent — Specialises in CPU, Memory, and Disk diagnostics.
Inherits from BaseAgent.
"""
from __future__ import annotations

import logging
import psutil

from aegismesh.agents.base_agent import BaseAgent
from aegismesh.registry.models import AgentCard, AgentCapabilities, AgentMeta

logger = logging.getLogger("aegis.agents.sysmon")


class SystemMonitorAgent(BaseAgent):
    """
    Agent responsible for diagnosing hardware resource constraints
    (RAM, CPU, Disk usage).
    """

    def get_card(self) -> AgentCard:
        return AgentCard(
            id="sysmon_001",
            name="SystemMonitorAgent",
            version="1.0.0",
            endpoint="http://127.0.0.1:8101",
            skills=[
                "check_ram",
                "check_cpu_spike",
                "check_disk_space",
                "get_top_processes"
            ],
            skill_confidence={
                "check_ram": 0.95,
                "check_cpu_spike": 0.90,
                "check_disk_space": 0.99,
                "get_top_processes": 0.85
            },
            capabilities=AgentCapabilities(
                max_concurrent_tasks=5,
                filesystem_access=False,
                wmi_access=False
            ),
            meta=AgentMeta(
                description="Hardware resource diagnostic agent utilizing psutil.",
                tags=["hardware", "ram", "cpu", "disk"]
            )
        )

    async def execute_task(self, params: dict, trace_id: str) -> dict:
        """
        Translates intent into specific psutil readings.
        """
        intent = params.get("intent", "").lower()
        logger.info("[Trace: %s] SysMon analyzing intent: %s", trace_id, intent)

        result: dict = {"facts": []}

        # 1. RAM Checks
        if "ram" in intent or "memory" in intent:
            mem = psutil.virtual_memory()
            result["facts"].append({
                "type": "METRIC",
                "name": "ram_usage_percent",
                "value": mem.percent,
                "unit": "%"
            })
            result["facts"].append({
                "type": "METRIC",
                "name": "ram_available_mb",
                "value": round(mem.available / (1024 * 1024), 2),
                "unit": "MB"
            })
            if mem.percent > 90.0:
                result["facts"].append({
                    "type": "ANOMALY",
                    "description": f"Critical RAM starvation detected: {mem.percent}% used."
                })

        # 2. CPU Checks
        if "cpu" in intent or "spike" in intent or "load" in intent:
            cpu = psutil.cpu_percent(interval=0.5)
            result["facts"].append({
                "type": "METRIC",
                "name": "cpu_usage_percent",
                "value": cpu,
                "unit": "%"
            })
            if cpu > 95.0:
                result["facts"].append({
                    "type": "ANOMALY",
                    "description": f"Severe CPU throttling detected: {cpu}% utilised."
                })

        # 3. Disk Space
        if "disk" in intent or "space" in intent:
            disk = psutil.disk_usage("/")
            result["facts"].append({
                "type": "METRIC",
                "name": "disk_usage_percent",
                "value": disk.percent,
                "unit": "%"
            })
            if disk.percent > 98.0:
                result["facts"].append({
                    "type": "ANOMALY",
                    "description": f"Disk exhaustion imminent: {disk.percent}% full on root volume."
                })

        # 4. Top Consumers (Process check)
        if "process" in intent or "consumer" in intent:
            procs = sorted(
                psutil.process_iter(['pid', 'name', 'cpu_percent']),
                key=lambda p: p.info['cpu_percent'] or 0.0,
                reverse=True
            )[:3]
            top_killers = [f"{p.info['name']}[PID:{p.info['pid']}]@{p.info['cpu_percent']}%" for p in procs]
            result["facts"].append({
                "type": "EVENT",
                "description": f"Top CPU consumers: {', '.join(top_killers)}"
            })

        if not result["facts"]:
            result["facts"].append({
                "type": "EVENT",
                "description": "No actionable hardware metrics found for the requested intent."
            })

        return result

# Expose the ASGI app for uvicorn
app = SystemMonitorAgent().create_app()

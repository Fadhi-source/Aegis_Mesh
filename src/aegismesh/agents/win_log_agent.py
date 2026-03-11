"""
agents/win_log_agent.py
=======================
Windows Event Log Agent.
Uses the read-only sandbox WMI wrapper to query the System and Application logs safely.
"""
from __future__ import annotations

import logging

from aegismesh.agents.base_agent import BaseAgent
from aegismesh.agents.security_sandbox import SecuritySandbox, SecurityViolationError
from aegismesh.registry.models import AgentCard, AgentCapabilities, AgentMeta

logger = logging.getLogger("aegis.agents.winlog")


class WindowsEventLogAgent(BaseAgent):
    """
    Agent responsible for hunting application crashes, blue screens,
    and service failures within the native Windows Event Log via WMI.
    Enforces the Non-Destructive Execution contract implicitly via SecuritySandbox.
    """

    def get_card(self) -> AgentCard:
        return AgentCard(
            id="winlog_001",
            name="WindowsEventLogAgent",
            version="1.0.0",
            endpoint="http://127.0.0.1:8103",
            skills=[
                "check_application_crashes",
                "check_service_failures",
                "query_event_log"
            ],
            skill_confidence={
                "check_application_crashes": 0.90,
                "check_service_failures": 0.95,
                "query_event_log": 0.85
            },
            capabilities=AgentCapabilities(
                max_concurrent_tasks=2,
                wmi_access=True
            ),
            meta=AgentMeta(
                description="Hunts critical Windows Event Log errors via Sandbox WMI.",
                tags=["wmi", "windows", "eventlog", "crashes"]
            )
        )

    async def execute_task(self, params: dict, trace_id: str) -> dict:
        intent = params.get("intent", "").lower()
        logger.info("[Trace: %s] WinLog analyzing intent: %s", trace_id, intent)

        result: dict = {"facts": []}

        # WQL query to find Errors (Type=1) in the Application or System log
        # generated in the very recent past. We limit the result set to 5 to avoid memory explosion.
        
        # NOTE: Standard WMI class for this is Win32_NTLogEvent.
        # Since this can be slow, we only trigger it if specifically asked about crashes.
        if "crash" in intent or "error" in intent or "failure" in intent or "log" in intent:
            query = (
                "SELECT Logfile, SourceName, EventCode, Message, TimeGenerated "
                "FROM Win32_NTLogEvent "
                "WHERE Type = 'error' "
                "AND (Logfile = 'Application' OR Logfile = 'System') "
            )
            
            try:
                # Use the Read-Only WMI sandbox
                # This guarantees we aren't executing destructive methods.
                events = SecuritySandbox.read_only_wmi(query)
                
                # Truncate to the most recent 3 events
                events = events[:3] if events else []

                if events:
                    for ev in events:
                        msg = str(ev.get("Message", "Unknown error"))[:200].replace("\n", " ").strip()
                        result["facts"].append({
                            "type": "EVENT",
                            "description": f"Windows Log Error ({ev.get('Logfile')} - {ev.get('SourceName')}): {msg}"
                        })
                else:
                    result["facts"].append({
                        "type": "EVENT",
                        "description": "No recent critical Application or System errors found in the Windows Event Log."
                    })
                    
            except SecurityViolationError as e:
                # This should physically never happen because we hardcode "SELECT",
                # but if the sandbox rejects it, report the anomaly.
                result["facts"].append({
                    "type": "ANOMALY",
                    "description": f"WMI Sandbox Violation: {e}"
                })
            except Exception as e:
                 result["facts"].append({
                    "type": "EVENT",
                    "description": f"WMI query failed or is unavailable on this OS: {e}"
                })
        else:
             result["facts"].append({
                "type": "EVENT",
                "description": "Intent did not match any Windows Log triggers."
            })

        return result

app = WindowsEventLogAgent().create_app()

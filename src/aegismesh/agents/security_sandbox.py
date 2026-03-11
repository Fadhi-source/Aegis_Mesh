"""
agents/security_sandbox.py
===========================
Static analysis guard executed at agent MODULE LOAD time.
Prevents supply-chain attacks by scanning agent source code for forbidden patterns
before any code executes.

Also provides read_only_wmi() — the safe WMI query wrapper enforcing the
Non-Destructive Execution principle from the AegisMesh whitepaper.
"""
from __future__ import annotations

import inspect
import logging

from aegismesh.core.config import SANDBOX_ENABLED

logger = logging.getLogger("aegis.sandbox")

# ── Deny list ─────────────────────────────────────────────────────────────────
# Patterns that indicate host-mutating or privilege-escalating intent.
# String containment check (not regex) — fast and unambiguous.
_FORBIDDEN_PATTERNS: list[str] = [
    "os.system",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_output",
    "subprocess.check_call",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "shutil.rmtree",
    "shutil.move",
    "pathlib.Path.unlink",
    "pathlib.Path.rmdir",
    "socket.connect",       # Outbound network — not loopback RPC
    "ctypes",               # Raw memory / DLL injection
    "cffi",                 # Alternative FFI path
    "eval(",                # Dynamic code execution
    "exec(",                # Dynamic code execution
    "importlib.import_module",  # Dynamic import (supply-chain vector)
    "__import__(",          # Low-level dynamic import
    "open(",                # Covered more specifically below
]

# Write-mode open() is forbidden; read-mode is allowed.
_FORBIDDEN_OPEN_MODES: tuple[str, ...] = ('"w"', "'w'", '"a"', "'a'", '"x"', "'x'", '"wb"', "'wb'")


class SecurityViolationError(RuntimeError):
    """Raised when a forbidden pattern is detected in agent source code."""


class SecuritySandbox:
    """
    Static analysis guard. Call `validate_agent_module(source, name)` at agent
    startup — before any agent code runs — to enforce the security contract.
    """

    @staticmethod
    def validate_agent_module(source_code: str, agent_name: str) -> None:
        """
        Scans agent source for forbidden patterns.
        Raises SecurityViolationError on the first match.
        Skips validation if SANDBOX_ENABLED=false (dev override).
        """
        if not SANDBOX_ENABLED:
            logger.warning(
                "SecuritySandbox: DISABLED (AEGIS_SANDBOX_ENABLED=false). "
                "Do not use in production."
            )
            return

        for pattern in _FORBIDDEN_PATTERNS:
            if pattern in source_code:
                raise SecurityViolationError(
                    f"[SANDBOX] Agent '{agent_name}' contains forbidden pattern: "
                    f"'{pattern}'. Only Read-Only WMI/Log access is permitted."
                )

        # Separate check: open() with write modes
        for mode in _FORBIDDEN_OPEN_MODES:
            if f"open(" in source_code and mode in source_code:
                raise SecurityViolationError(
                    f"[SANDBOX] Agent '{agent_name}' contains write-mode open({mode}). "
                    "Agents must not write to the filesystem."
                )

        logger.debug("SecuritySandbox: '%s' passed static analysis.", agent_name)

    @staticmethod
    def validate_self(agent_class) -> None:
        """
        Convenience: validate the source of the calling agent's own class.
        Call this in __init_subclass__ or in the BaseAgent constructor.
        """
        try:
            src = inspect.getsource(agent_class)
        except (OSError, TypeError):
            logger.warning(
                "SecuritySandbox: could not retrieve source for '%s'. "
                "Skipping static analysis.",
                agent_class.__name__,
            )
            return
        SecuritySandbox.validate_agent_module(src, agent_class.__name__)

    @staticmethod
    def read_only_wmi(query: str) -> list[dict]:
        """
        Safe WMI query wrapper.
        Enforces SELECT-only constraint — refuses INSERT/UPDATE/DELETE/EXEC.
        Windows-only; returns empty list on non-Windows platforms (graceful).
        """
        query_upper = query.strip().upper()
        if not query_upper.startswith("SELECT"):
            raise SecurityViolationError(
                f"[SANDBOX] Forbidden WMI operation '{query[:80]}'. "
                "Only SELECT queries are permitted."
            )
        try:
            import wmi  # type: ignore[import]
            c = wmi.WMI()
            return [dict(zip(obj.properties.keys(), obj.properties.values())) for obj in c.query(query)]
        except ImportError:
            logger.warning("WMI module not available (non-Windows or not installed).")
            return []
        except Exception as exc:
            logger.error("WMI query failed: %s", exc)
            return []

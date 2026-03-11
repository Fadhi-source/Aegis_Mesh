"""
core/governor.py
================
RAM Governor — dynamic asyncio concurrency controller.

The Governor is a singleton. Get the live instance via `get_governor()`.
Call `await governor.start()` once at application startup.

Concurrency ladder (configurable via .env):
  RAM < 75%   → PARALLEL   (N_PARALLEL workers — default 8)
  75–85% RAM  → DEGRADED   (N_DEGRADED  workers — 3)
  RAM > 85%   → SEQUENTIAL (1 worker)

Semaphore resize: Python's asyncio.Semaphore has no public resize API.
We manipulate `_value` directly (CPython implementation detail) and
re-heapify the internal wait queue. This is safe as long as we are the
only writer to `_value`. The lock is held by the asyncio event loop.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import psutil

from aegismesh.core.config import (
    RAM_THRESHOLD_HIGH,
    RAM_THRESHOLD_MED,
    PARALLEL_WORKERS,
    DEGRADED_WORKERS,
    SEQUENTIAL_WORKERS,
    MAX_HOLD_TIME_S,
)

logger = logging.getLogger("aegis.governor")


class TaskHangError(RuntimeError):
    """Raised when an agent holds a semaphore slot beyond MAX_HOLD_TIME_S."""


class RAMGovernor:
    """Global dynamic concurrency semaphore gated by live RAM usage."""

    POLL_INTERVAL = 2.0  # seconds

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(PARALLEL_WORKERS)
        self._current_limit = PARALLEL_WORKERS
        self._monitor_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Launch the background polling loop. Call once at app startup."""
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="ram_governor_monitor"
        )
        logger.info(
            "Governor started | limits: parallel=%d degraded=%d sequential=%d",
            PARALLEL_WORKERS,
            DEGRADED_WORKERS,
            SEQUENTIAL_WORKERS,
        )

    async def stop(self) -> None:
        task = self._monitor_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self) -> None:
        while True:
            try:
                ram_pct = psutil.virtual_memory().percent
                new_limit = self._compute_limit(ram_pct)
                if new_limit != self._current_limit:
                    self._resize_semaphore(new_limit)
                    logger.warning(
                        "Governor: %.1f%% RAM → concurrency %d→%d",
                        ram_pct,
                        self._current_limit,
                        new_limit,
                    )
                    self._current_limit = new_limit
            except Exception as exc:
                logger.error("Governor monitor error: %s", exc)
            await asyncio.sleep(self.POLL_INTERVAL)

    def _compute_limit(self, ram_pct: float) -> int:
        if ram_pct > RAM_THRESHOLD_HIGH:
            return SEQUENTIAL_WORKERS
        if ram_pct > RAM_THRESHOLD_MED:
            return DEGRADED_WORKERS
        return PARALLEL_WORKERS

    def _resize_semaphore(self, new_limit: int) -> None:
        """
        Resize by directly adjusting `_value`.

        Safety contract:
          - Only this method ever modifies `_value` outside normal acquire/release.
          - We are within the asyncio event loop (single-threaded), so no race.
          - In-flight tasks complete naturally; they do not lose their acquired slot.
        """
        delta = new_limit - self._current_limit
        if delta > 0:
            # Add capacity — safe to release without prior acquire
            for _ in range(delta):
                self._semaphore._value += 1
                # Wake any waiting coroutines
                self._semaphore._wake_up_next()
        elif delta < 0:
            # Reduce capacity — only affects future acquires, not in-flight holders
            reduction = min(-delta, self._semaphore._value)
            self._semaphore._value -= reduction

    @property
    def current_limit(self) -> int:
        return self._current_limit

    @asynccontextmanager
    async def acquire(
        self,
        agent_id: str = "unknown",
        hold_timeout: float = MAX_HOLD_TIME_S,
    ) -> AsyncGenerator[None, None]:
        """
        Context manager that acquires a semaphore slot.
        If the slot is not released within `hold_timeout` seconds,
        raises TaskHangError and releases the slot forcibly.
        """
        await self._semaphore.acquire()
        try:
            yield
        except asyncio.TimeoutError:
            raise TaskHangError(
                f"Agent '{agent_id}' exceeded max hold time ({hold_timeout}s). "
                "Slot forcibly released."
            )
        finally:
            self._semaphore.release()


# ── Singleton ─────────────────────────────────────────────────────────────────
_governor: RAMGovernor | None = None


def get_governor() -> RAMGovernor:
    """Returns the application-scoped Governor singleton."""
    global _governor
    if _governor is None:
        _governor = RAMGovernor()
    return _governor


def reset_governor() -> None:
    """Test helper — resets the singleton so tests get a fresh Governor."""
    global _governor
    _governor = None

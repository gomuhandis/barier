"""Background loop: every N seconds alternate a `close` command between cameras.

Reset behavior:
    The countdown restarts from 0 whenever the barrier is opened — by the
    admin panel, by the Telegram bot, or by the camera itself recognizing a
    plate.  Call `scheduler.notify_opened(reason=...)` to reset.

    Example: interval=5s. A close is 1s away from firing, a user presses
    "open" in the web panel. `notify_opened()` is invoked, the pending close
    is skipped, and the next close is scheduled 5s from now (not 1s).
"""
from __future__ import annotations

import asyncio
import logging

from src.config import get_settings
from src.isapi.registry import registry

logger = logging.getLogger(__name__)


class AlternatingCloseScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._reset = asyncio.Event()
        self._last_reset_reason: str | None = None

    # ---------------------- public API ----------------------
    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._reset.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("Alternating-close scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()

    def notify_opened(self, reason: str | None = None) -> None:
        """Someone/something just opened the barrier — restart countdown from 0.

        Safe to call from any sync context (doesn't block, uses asyncio.Event).
        """
        self._last_reset_reason = reason
        self._reset.set()
        logger.info("[alt-close] countdown reset (reason=%s)", reason or "unknown")

    # ---------------------- internals ----------------------
    async def _run(self) -> None:
        interval = get_settings().alt_close_interval_seconds
        idx = 0
        while not self._stop.is_set():
            clients = registry.all()
            if not clients:
                # no cameras configured yet; wait a bit and re-check
                if await self._sleep_or_wake(interval):
                    break
                continue

            # --- sleep up to `interval` seconds, wake early on stop/reset ---
            woke_by = await self._wait_phase(interval)
            if woke_by == "stop":
                break
            if woke_by == "reset":
                # skip this round entirely — the barrier was just opened,
                # so the next close should be a full `interval` from now.
                self._reset.clear()
                logger.debug(
                    "[alt-close] round skipped after reset (reason=%s)",
                    self._last_reset_reason,
                )
                self._last_reset_reason = None
                continue

            # --- interval elapsed cleanly: fire the next close ---
            # We deliberately do NOT persist these to barrier_action_logs:
            # the loop fires every `interval` seconds and would flood the
            # table, pollute the Loglar page, and skew dashboard counters.
            # Anything we want to know lives in the python log.
            client = clients[idx % len(clients)]
            idx += 1
            try:
                resp = await client.barrier_control("close")
                logger.debug(
                    "[alt-close] %s close → %s %s",
                    client.cam.name,
                    resp.status_code,
                    resp.message,
                )
            except Exception as exc:
                logger.warning("[alt-close] error on %s: %s", client.cam.name, exc)

    async def _wait_phase(self, timeout: float) -> str:
        """Returns 'stop', 'reset', or 'timeout'."""
        stop_wait = asyncio.create_task(self._stop.wait())
        reset_wait = asyncio.create_task(self._reset.wait())
        try:
            done, _ = await asyncio.wait(
                {stop_wait, reset_wait},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_wait.cancel()
            reset_wait.cancel()
        if self._stop.is_set():
            return "stop"
        if self._reset.is_set():
            return "reset"
        return "timeout"

    async def _sleep_or_wake(self, timeout: float) -> bool:
        """Sleep up to `timeout` seconds; returns True if stop was requested."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True


scheduler = AlternatingCloseScheduler()

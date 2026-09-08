"""Explicit cancellation for one operation or a caller-owned group of operations."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock


class OperationCancelled(KeyboardInterrupt):
    """The caller stopped an operation; this is not a tool failure observation."""


class Cancellation:
    def __init__(self) -> None:
        self._cancelled = Event()
        self._lock = Lock()
        self._callbacks: dict[object, Callable[[], None]] = {}

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise OperationCancelled("operation cancelled")

    def register(self, stop: Callable[[], None]) -> Callable[[], None]:
        """Register cleanup atomically with cancellation, returning its release."""
        key = object()
        with self._lock:
            cancelled = self.cancelled
            if not cancelled:
                self._callbacks[key] = stop
        if cancelled:
            stop()

        def release() -> None:
            with self._lock:
                self._callbacks.pop(key, None)

        return release

    def cancel(self) -> None:
        with self._lock:
            if self.cancelled:
                return
            self._cancelled.set()
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        error: BaseException | None = None
        for stop in callbacks:
            try:
                stop()
            except BaseException as failure:
                # Attempt every registered cleanup even if one owner fails.
                if error is None:
                    error = failure
        if error is not None:
            raise error

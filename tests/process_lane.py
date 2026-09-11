"""Test-lane guards for this repository's pytest collection. Not a product API."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

import pytest

from pf.adapters.process import SubprocessRunner
from pf.cancellation import Cancellation
from pf.schemas.evaluation import ProcessObservation, ProcessSpec

PROCESS_LANE_MARKERS = frozenset({"process", "e2e", "qualification"})
_CURRENT_ITEM: pytest.Item | None = None
_COLLECTED_ITEMS: list[pytest.Item] = []
_GUARD_INSTALLED = False


class ProcessLaneViolation(RuntimeError):
    """An unmarked test entered a real uv/ty/pytest/pf process."""


@contextmanager
def using_item(item: pytest.Item) -> Iterator[None]:
    global _CURRENT_ITEM
    previous = _CURRENT_ITEM
    _CURRENT_ITEM = item
    try:
        yield
    finally:
        _CURRENT_ITEM = previous


def require_external_process_lane(origin: str) -> None:
    item = _CURRENT_ITEM
    if item is None:
        raise ProcessLaneViolation(
            f"{origin} ran outside a pytest item; mark the caller process, e2e, or qualification"
        )
    if any(item.get_closest_marker(name) is not None for name in PROCESS_LANE_MARKERS):
        return
    raise ProcessLaneViolation(
        f"{item.nodeid} entered {origin} without process, e2e, or qualification"
    )


def record_collected_item(item: pytest.Item) -> None:
    _COLLECTED_ITEMS.append(item)


def collected_items() -> tuple[pytest.Item, ...]:
    return tuple(_COLLECTED_ITEMS)


def clear_collected_items() -> None:
    _COLLECTED_ITEMS.clear()


def assert_e2e_implies_process(items: Iterable[object]) -> None:
    missing: list[str] = []
    for item in items:
        get_marker = getattr(item, "get_closest_marker")
        if get_marker("e2e") is not None and get_marker("process") is None:
            missing.append(str(getattr(item, "nodeid")))
    if missing:
        raise ProcessLaneViolation(
            "e2e tests must also be marked process: " + ", ".join(missing)
        )


def install_subprocess_runner_guard() -> None:
    global _GUARD_INSTALLED
    if _GUARD_INSTALLED:
        return
    original = SubprocessRunner.run

    def guarded(
        self: SubprocessRunner,
        spec: ProcessSpec,
        *,
        cancellation: Cancellation | None = None,
    ) -> ProcessObservation:
        require_external_process_lane("SubprocessRunner.run")
        return original(self, spec, cancellation=cancellation)

    SubprocessRunner.run = guarded
    _GUARD_INSTALLED = True

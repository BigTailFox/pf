"""Standalone pytest plugin copied into a target test environment by PF."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sys
import time
from typing import Any, cast

import pytest

_DIRECTORY_VARIABLE = "PF_PYTEST_WITNESS_DIR"
_NONCE_VARIABLE = "PF_PYTEST_WITNESS_NONCE"
_PROTOCOL = "pf-pytest-failure-witness-v1"
_PROGRESS_DIRECTORY_VARIABLE = "PF_PYTEST_PROGRESS_DIR"
_PROGRESS_PROTOCOL = "pf-pytest-progress-v1"
_facts: set[tuple[str, str]] = set()
_execution_mode = "unknown"
_progress_completed = 0
_progress_last_commit = 0.0
_progress_remaining: set[str] | None = None
_progress_total = 0


@pytest.hookimpl(tryfirst=True)
def pytest_collectreport(report: object) -> None:
    if getattr(report, "failed", False):
        _facts.add(("COLLECTION_FAILED", "collect"))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report: object) -> None:
    phase = getattr(report, "when", None)
    if getattr(report, "failed", False) and phase in {"setup", "call", "teardown"}:
        _facts.add(("TEST_FAILED", phase))


@pytest.hookimpl(tryfirst=True)
def pytest_internalerror() -> None:
    _facts.add(("INTERNAL_ERROR", "pytest"))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: object) -> None:
    global _execution_mode
    config = getattr(session, "config", None)
    pluginmanager = getattr(config, "pluginmanager", None)
    hasplugin = getattr(pluginmanager, "hasplugin", None)
    if not callable(hasplugin) or not (
        hasplugin("xdist") or hasplugin("xdist.plugin")
    ):
        _execution_mode = "serial"
        return
    try:
        from xdist import is_xdist_controller, is_xdist_worker
    except Exception:
        _execution_mode = "unknown"
        return
    try:
        controller = bool(is_xdist_controller(cast(Any, session)))
        worker = bool(is_xdist_worker(cast(Any, session)))
    except Exception:
        _execution_mode = "unknown"
        return
    if controller and worker:
        _execution_mode = "unknown"
    elif controller or worker:
        _execution_mode = "xdist"
    else:
        _execution_mode = "serial"


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: object) -> None:
    try:
        _initialize_progress(session)
    except Exception:
        return


def _initialize_progress(session: object) -> None:
    global _progress_completed, _progress_remaining, _progress_total
    if (
        _execution_mode != "serial"
        or _PROGRESS_DIRECTORY_VARIABLE not in os.environ
        or ("COLLECTION_FAILED", "collect") in _facts
    ):
        return
    config = getattr(session, "config", None)
    option = getattr(config, "option", None)
    if bool(getattr(option, "collectonly", False)):
        return
    items = getattr(session, "items", None)
    if not isinstance(items, list):
        return
    nodeids = [getattr(item, "nodeid", None) for item in items]
    if any(type(nodeid) is not str or not nodeid for nodeid in nodeids):
        return
    remaining = set(cast(list[str], nodeids))
    if len(remaining) != len(nodeids):
        return
    _progress_completed = 0
    _progress_remaining = remaining
    _progress_total = len(remaining)
    _commit_progress(force=True)


@pytest.hookimpl(trylast=True)
def pytest_runtest_logfinish(nodeid: str, location: object) -> None:
    del location
    try:
        _advance_progress(nodeid)
    except Exception:
        return


def _advance_progress(nodeid: str) -> None:
    global _progress_completed
    if _progress_remaining is not None and nodeid in _progress_remaining:
        _progress_remaining.remove(nodeid)
        _progress_completed += 1
        _commit_progress(force=not _progress_remaining)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_cmdline_main(config: object):
    outcome = yield
    if getattr(outcome, "excinfo", None) is not None:
        _facts.add(("INTERNAL_ERROR", "pytest"))
    _commit_progress(force=True)
    _commit_summary()


def _commit_summary() -> None:
    try:
        directory = Path(os.environ[_DIRECTORY_VARIABLE])
        nonce = os.environ[_NONCE_VARIABLE]
        summary = {
            "execution_mode": _execution_mode,
            "facts": [
                {"kind": kind, "phase": phase}
                for kind, phase in sorted(_facts)
            ],
            "finalized": True,
            "protocol": _PROTOCOL,
            "pytest_version": pytest.__version__,
            "python_implementation": sys.implementation.name,
            "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
            "run_nonce": nonce,
        }
        payload = (
            json.dumps(
                summary,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")
        token = secrets.token_hex(16)
        temporary = directory / f".{token}.tmp"
        final = directory / f"summary-{token}.json"
        temporary.write_bytes(payload)
        os.replace(temporary, final)
    except Exception:
        return


def _commit_progress(*, force: bool) -> None:
    global _progress_last_commit
    try:
        directory_value = os.environ.get(_PROGRESS_DIRECTORY_VARIABLE)
        if directory_value is None or _progress_remaining is None:
            return
        now = time.monotonic()
        if not force and now - _progress_last_commit < 0.1:
            return
        document = {
            "completed": _progress_completed,
            "protocol": _PROGRESS_PROTOCOL,
            "run_nonce": os.environ[_NONCE_VARIABLE],
            "total": _progress_total,
            "unit": "tests",
        }
        payload = (
            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")
        directory = Path(directory_value)
        temporary = directory / f".{secrets.token_hex(16)}.tmp"
        final = directory / "progress.json"
        temporary.write_bytes(payload)
        os.replace(temporary, final)
        _progress_last_commit = now
    except Exception:
        return

"""Standalone pytest plugin copied into a target test environment by PF."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any, cast

import pytest

_DIRECTORY_VARIABLE = "PF_PYTEST_WITNESS_DIR"
_NONCE_VARIABLE = "PF_PYTEST_WITNESS_NONCE"
_PROTOCOL = "pf-pytest-failure-witness-v1"
_PROGRESS_DIRECTORY_VARIABLE = "PF_PYTEST_PROGRESS_DIR"
_PROGRESS_NONCE_VARIABLE = "PF_PYTEST_PROGRESS_NONCE"
_PROGRESS_PROTOCOL = "pf-pytest-progress-v1"
_FAILURE_DETAILS_DIRECTORY_VARIABLE = "PF_PYTEST_FAILURE_DETAILS_DIR"
_FAILURE_DETAILS_PROTOCOL = "pf-pytest-failure-details-v1"
_MAX_FAILURE_DETAILS = 10_000
_MAX_NODEID_LENGTH = 4_096
_facts: set[tuple[str, str]] = set()
_execution_mode = "unknown"
_failure_details: dict[str, str] = {}
_failure_details_valid = True
_progress_completed = 0
_progress_remaining: set[str] | None = None
_progress_total = 0


@pytest.hookimpl(tryfirst=True)
def pytest_collectreport(report: object) -> None:
    if getattr(report, "failed", False):
        _facts.add(("COLLECTION_FAILED", "collect"))
        _record_failure_detail(report, "collect")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report: object) -> None:
    phase = getattr(report, "when", None)
    if getattr(report, "failed", False) and phase in {"setup", "call", "teardown"}:
        _facts.add(("TEST_FAILED", phase))
        _record_failure_detail(report, phase)


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
        or not os.environ.get(_PROGRESS_NONCE_VARIABLE)
        or os.environ[_PROGRESS_NONCE_VARIABLE] != os.environ.get(_NONCE_VARIABLE)
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
    _commit_progress()


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
        _commit_progress()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_cmdline_main(config: object):
    outcome = yield
    if getattr(outcome, "excinfo", None) is not None:
        _facts.add(("INTERNAL_ERROR", "pytest"))
    _commit_progress()
    _commit_failure_details()
    _commit_summary()


def _record_failure_detail(report: object, phase: str) -> None:
    global _failure_details_valid
    try:
        if not _failure_details_valid:
            return
        nodeid = getattr(report, "nodeid", None)
        if (
            type(nodeid) is not str
            or not nodeid
            or len(nodeid) > _MAX_NODEID_LENGTH
            or any(
                ord(character) < 32
                or 127 <= ord(character) <= 159
                or 0xD800 <= ord(character) <= 0xDFFF
                for character in nodeid
            )
        ):
            _failure_details_valid = False
            _failure_details.clear()
            return
        if nodeid not in _failure_details:
            if len(_failure_details) >= _MAX_FAILURE_DETAILS:
                _failure_details_valid = False
                _failure_details.clear()
                return
            _failure_details[nodeid] = phase
    except Exception:
        _failure_details_valid = False
        _failure_details.clear()


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


def _commit_failure_details() -> None:
    try:
        directory_value = os.environ.get(_FAILURE_DETAILS_DIRECTORY_VARIABLE)
        if (
            directory_value is None
            or not _failure_details_valid
            or not _failure_details
        ):
            return
        first_nodeid = next(iter(_failure_details))
        document = {
            "first": {
                "nodeid": first_nodeid,
                "phase": _failure_details[first_nodeid],
            },
            "protocol": _FAILURE_DETAILS_PROTOCOL,
            "run_nonce": os.environ[_NONCE_VARIABLE],
            "total": len(_failure_details),
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
        token = secrets.token_hex(16)
        temporary = directory / f".{token}.tmp"
        final = directory / f"details-{token}.json"
        temporary.write_bytes(payload)
        os.replace(temporary, final)
    except Exception:
        return


def _commit_progress() -> None:
    try:
        directory_value = os.environ.get(_PROGRESS_DIRECTORY_VARIABLE)
        progress_nonce = os.environ.get(_PROGRESS_NONCE_VARIABLE)
        if (
            directory_value is None
            or _progress_remaining is None
            or not progress_nonce
            or progress_nonce != os.environ.get(_NONCE_VARIABLE)
        ):
            return
        document = {
            "completed": _progress_completed,
            "protocol": _PROGRESS_PROTOCOL,
            "run_nonce": progress_nonce,
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
    except Exception:
        return

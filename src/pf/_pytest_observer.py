"""Standalone pytest plugin copied into a target test environment by PF."""

from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any, cast

import pytest

_DIRECTORY_VARIABLE = "PF_PYTEST_OBSERVER_DIR"
_NONCE_VARIABLE = "PF_PYTEST_OBSERVER_NONCE"
_PROTOCOL = "pf-pytest-observer-v1"
_PROGRESS_DIRECTORY_VARIABLE = "PF_PYTEST_PROGRESS_DIR"
_PROGRESS_NONCE_VARIABLE = "PF_PYTEST_PROGRESS_NONCE"
_PROGRESS_PROTOCOL = "pf-pytest-progress-v1"
_FAILURE_DETAILS_DIRECTORY_VARIABLE = "PF_PYTEST_OBSERVER_DETAILS_DIR"
_FAILURE_DETAILS_PROTOCOL = "pf-pytest-observer-details-v1"
_CASES_DIRECTORY_VARIABLE = "PF_PYTEST_OBSERVER_CASES_DIR"
_CASES_PROJECTION_VARIABLE = "PF_PYTEST_OBSERVER_CASES_PROJECTION"
_CASES_PROTOCOL = "pf-pytest-observer-cases-v1"
_MAX_FAILURE_DETAILS = 10_000
_MAX_NODEID_LENGTH = 4_096
_MAX_CASES_BYTES = 8 * 1024 * 1024
_facts: set[tuple[str, str]] = set()
_execution_mode = "unknown"
_session_role = "unknown"
_failure_details: dict[str, str] = {}
_failure_details_valid = True
_failed_cases: dict[str, None] = {}
_failed_cases_valid = True
_collected_nodeids: list[str] | None = None
_collected_valid = True
_collection_completed = False
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
        _record_failed_case(report)


@pytest.hookimpl(tryfirst=True)
def pytest_internalerror() -> None:
    _facts.add(("INTERNAL_ERROR", "pytest"))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: object) -> None:
    global _execution_mode, _session_role
    config = getattr(session, "config", None)
    pluginmanager = getattr(config, "pluginmanager", None)
    hasplugin = getattr(pluginmanager, "hasplugin", None)
    if not callable(hasplugin) or not (
        hasplugin("xdist") or hasplugin("xdist.plugin")
    ):
        _execution_mode = "serial"
        _session_role = "serial"
        return
    try:
        from xdist import is_xdist_controller, is_xdist_worker
    except Exception:
        _execution_mode = "unknown"
        _session_role = "unknown"
        return
    try:
        controller = bool(is_xdist_controller(cast(Any, session)))
        worker = bool(is_xdist_worker(cast(Any, session)))
    except Exception:
        _execution_mode = "unknown"
        _session_role = "unknown"
        return
    if controller and worker:
        _execution_mode = "unknown"
        _session_role = "unknown"
    elif controller:
        _execution_mode = "xdist"
        _session_role = "controller"
    elif worker:
        _execution_mode = "xdist"
        _session_role = "worker"
    else:
        _execution_mode = "serial"
        _session_role = "serial"


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: object) -> None:
    try:
        _record_collection(session)
    except Exception:
        _invalidate_collection()
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
    _commit_cases()
    _commit_summary()


def _nodeid_is_safe(nodeid: object) -> bool:
    return (
        type(nodeid) is str
        and bool(nodeid)
        and len(nodeid) <= _MAX_NODEID_LENGTH
        and not any(
            ord(character) < 32
            or 127 <= ord(character) <= 159
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in nodeid
        )
    )


def _invalidate_collection() -> None:
    global _collected_valid, _collected_nodeids, _collection_completed
    _collected_valid = False
    _collected_nodeids = None
    _collection_completed = False


def _record_collection(session: object) -> None:
    global _collected_nodeids, _collected_valid, _collection_completed
    if not _collected_valid:
        return
    items = getattr(session, "items", None)
    if not isinstance(items, list):
        _invalidate_collection()
        return
    nodeids = [getattr(item, "nodeid", None) for item in items]
    if any(not _nodeid_is_safe(nodeid) for nodeid in nodeids):
        _invalidate_collection()
        return
    _collected_nodeids = cast(list[str], nodeids)
    _collection_completed = True


def _record_failed_case(report: object) -> None:
    global _failed_cases_valid
    try:
        if not _failed_cases_valid:
            return
        nodeid = getattr(report, "nodeid", None)
        if not _nodeid_is_safe(nodeid):
            _failed_cases_valid = False
            _failed_cases.clear()
            return
        _failed_cases[cast(str, nodeid)] = None
    except Exception:
        _failed_cases_valid = False
        _failed_cases.clear()


def _record_failure_detail(report: object, phase: str) -> None:
    global _failure_details_valid
    try:
        if not _failure_details_valid:
            return
        nodeid = getattr(report, "nodeid", None)
        if not _nodeid_is_safe(nodeid):
            _failure_details_valid = False
            _failure_details.clear()
            return
        assert type(nodeid) is str
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


def _commit_cases() -> None:
    try:
        directory_value = os.environ.get(_CASES_DIRECTORY_VARIABLE)
        projection = os.environ.get(_CASES_PROJECTION_VARIABLE)
        if (
            directory_value is None
            or projection not in {"failed", "collected"}
            or _session_role not in {"serial", "controller", "worker"}
        ):
            return
        if projection == "collected":
            if not _collected_valid or _collected_nodeids is None:
                return
            nodeids = list(_collected_nodeids)
        else:
            if not _failed_cases_valid:
                return
            nodeids = sorted(_failed_cases)
        document = {
            "collection_completed": _collection_completed,
            "collection_failed": ("COLLECTION_FAILED", "collect") in _facts,
            "nodeids": nodeids,
            "projection": projection,
            "protocol": _CASES_PROTOCOL,
            "role": _session_role,
            "run_nonce": os.environ[_NONCE_VARIABLE],
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
        if len(payload) > _MAX_CASES_BYTES:
            return
        directory = Path(directory_value)
        token = secrets.token_hex(16)
        temporary = directory / f".{token}.tmp"
        final = directory / f"cases-{token}.json"
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


if re.fullmatch(r"_pf_pytest_observer_[0-9a-f]{32}", __name__) is not None:
    atexit.register(_commit_summary)

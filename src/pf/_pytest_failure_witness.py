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
_facts: set[tuple[str, str]] = set()
_execution_mode = "unknown"


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


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_cmdline_main(config: object):
    outcome = yield
    if getattr(outcome, "excinfo", None) is not None:
        _facts.add(("INTERNAL_ERROR", "pytest"))
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

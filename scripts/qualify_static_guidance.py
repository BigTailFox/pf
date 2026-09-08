"""Qualify D038 static guidance through public Check/Search workflows.

Run from the repository root after a risk check, outside the agent sandbox:

    UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/qualify_static_guidance.py \\
        --mode controlled --output /tmp/d038-controlled.json

    UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/qualify_static_guidance.py \\
        --mode check --root /abs/isolated/mkdocs --output /tmp/d038-check.json

    UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/qualify_static_guidance.py \\
        --mode search --root /abs/isolated/mkdocs --output /tmp/d038-search.json

The script uses CheckRequest/SearchRequest and CliContext public seams. It does
not invent product CLI path flags. Isolated experiment roots must already exist.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from pf.cli import CliContext
from pf.report import ReportStore
from pf.runlog import RunLogStore
from pf.schemas.config import CheckRequest, SearchRequest
from pf.schemas.evaluation import CheckCompatibilityFailure, CheckPass, PassEvaluation
from pf.schemas.report import CellSuccess
from pf.terminal import TerminalPresenter


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "pf-static-guidance-qualification-v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id(logs: RunLogStore) -> str:
    return logs.run_id


def _context(root: Path) -> tuple[CliContext, RunLogStore]:
    logs = RunLogStore(root=root)
    presenter = TerminalPresenter(logs=logs, root=root)
    return CliContext(presenter=presenter, run_logs=logs, root=root), logs


def _cell_key(cell) -> dict[str, object]:
    return {
        "package": cell.package,
        "python_minor": cell.python_minor,
        "extra_surface": list(cell.extra_surface),
        "target": cell.target,
    }


def _evaluation_record(evaluation) -> dict[str, object]:
    payload: dict[str, object] = {"status": evaluation.status}
    if isinstance(evaluation, PassEvaluation):
        payload["verifier_terminal"] = evaluation.verifier.terminal.model_dump(mode="json")
        payload["vector"] = [
            {"name": pin.name, "version": pin.version}
            for pin in evaluation.proposal.managed_vector
        ]
    return payload


def _project_name(root: Path) -> str:
    text = (root / "pyproject.toml").read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("name"):
            return stripped.split("=", 1)[1].strip().strip("\"'")
    return root.name


def _check_document(result, *, root: Path, logs: RunLogStore) -> dict[str, object]:
    journal = logs.read_latest_journal(_project_name(root))
    static_scopes = []
    if journal is not None:
        static_scopes = [
            {
                "scope_ref": member.scope.scope_ref,
                "cell": _cell_key(member.scope.cell),
                "facts": len(member.scope.facts),
                "comparisons": len(member.scope.comparisons),
                "searches": len(member.scope.searches),
                "producer_kinds": sorted({
                    fact.observation.fact.kind for fact in member.scope.facts
                }),
                "highest_uncollected": (
                    None
                    if member.scope.highest_uncollected is None
                    else member.scope.highest_uncollected.unavailable.model_dump(mode="json")
                ),
            }
            for member in journal.static_scopes
        ]
    outcomes = []
    for outcome in getattr(result, "outcomes", ()):
        record = {
            "status": outcome.status,
            "role": outcome.role,
            "cell": _cell_key(outcome.attempt.identity.cell),
            "entered_verifier": (
                outcome.evaluation is not None
                and outcome.evaluation.status
                in {"PASS", "VERIFIER_REJECTED", "INDETERMINATE"}
            ),
        }
        if outcome.evaluation is not None:
            record["evaluation"] = _evaluation_record(outcome.evaluation)
        if outcome.failure is not None:
            record["failure"] = {
                "failure_id": outcome.failure.failure_id,
                "cause": outcome.failure.cause,
                "stage": outcome.failure.stage,
                "disposition": outcome.failure.disposition,
                "authority_kind": outcome.failure.authority.kind,
            }
        outcomes.append(record)
    return {
        "mode": "check",
        "status": result.status,
        "root": str(root),
        "run_id": _run_id(logs),
        "outcomes": outcomes,
        "static_journal": static_scopes,
        "kind": type(result).__name__,
    }


def _search_document(result, *, root: Path, logs: RunLogStore) -> dict[str, object]:
    report = result.report
    store = ReportStore()
    replayed = store.read(root / result.report_path)
    assert replayed.report_generation_id == report.report_generation_id
    cells = []
    for cell_result in report.cell_results:
        record: dict[str, object] = {
            "status": cell_result.status,
            "cell": _cell_key(cell_result.cell),
        }
        if isinstance(cell_result, CellSuccess):
            record["final_vector"] = [
                {"name": pin.name, "version": pin.version}
                for pin in cell_result.final_vector
            ]
            record["final_pass"] = cell_result.final_evaluation.status == "PASS"
            record["final_verifier_terminal"] = (
                cell_result.final_evaluation.verifier.terminal.model_dump(mode="json")
            )
            record["boundaries"] = [
                {
                    "dependency": boundary.dependency,
                    "floor": boundary.floor,
                    "predecessor": boundary.predecessor,
                    "predecessor_failure_id": boundary.predecessor_failure_id,
                    "predecessor_authority_kind": next(
                        (
                            failure.authority.kind
                            for failure in cell_result.failure_records
                            if failure.failure_id == boundary.predecessor_failure_id
                        ),
                        None,
                    ),
                }
                for boundary in cell_result.search.boundaries
            ]
        cells.append(record)
    return {
        "mode": "search",
        "root": str(root),
        "run_id": _run_id(logs),
        "report_path": result.report_path,
        "report_generation_id": report.report_generation_id,
        "report_roundtrip": True,
        "guidance_policy_identity": report.guidance_policy_identity,
        "search_derivation_identity": report.search_derivation_identity,
        "cells": cells,
        "static_scopes": [
            {
                "scope_ref": scope.scope_ref,
                "facts": len(scope.facts),
                "comparisons": len(scope.comparisons),
                "searches": len(scope.searches),
                "omissions": len(scope.omissions),
                "skips": len(scope.skips),
                "selections": len(scope.selections),
            }
            for scope in report.static_scopes
        ],
    }


def qualify_controlled(output: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="pf-static-guidance-controlled-") as temporary:
        root = Path(temporary) / "demo"
        (root / "src/demo").mkdir(parents=True)
        (root / "src/demo/__init__.py").write_text("VALUE = 1\n")
        (root / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "1"
[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
[tool.pf]
pythons = ["3.10"]
test-command = ["python", "-c", "import demo; assert demo.VALUE == 1; print('verified demo')"]
"""
        )
        context, logs = _context(root)
        try:
            result = context.check_workflow.run(CheckRequest(root=str(root)))
            document = _check_document(result, root=root, logs=logs)
        finally:
            context.close()
        document.update({
            "schema": SCHEMA,
            "profile": "controlled-prepare-ty-verifier-v1",
            "recorded_at": _utc_now(),
            "python": sys.version.split()[0],
            "mode": "controlled",
        })
        assert isinstance(result, (CheckPass, CheckCompatibilityFailure))
        assert document["static_journal"], "controlled check must persist static journal audit"
        outcomes = document["outcomes"]
        assert isinstance(outcomes, list)
        assert all(
            isinstance(item, dict) and item["entered_verifier"] for item in outcomes
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(document, indent=2) + "\n")
        return document


def qualify_project(root: Path, *, mode: str, output: Path) -> dict[str, object]:
    root = root.resolve()
    if not root.is_dir():
        raise SystemExit(f"project root does not exist: {root}")
    context, logs = _context(root)
    try:
        if mode == "check":
            result = context.check_workflow.run(CheckRequest(root=str(root)))
            document = _check_document(result, root=root, logs=logs)
        else:
            result = context.search_workflow.run(SearchRequest(root=str(root)))
            document = _search_document(result, root=root, logs=logs)
    finally:
        context.close()
    document.update({
        "schema": SCHEMA,
        "profile": f"public-{mode}-workflow-v1",
        "recorded_at": _utc_now(),
        "python": sys.version.split()[0],
        "cwd": str(Path.cwd()),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n")
    return document


def isolate_mkdocs(source: Path, destination: Path) -> dict[str, object]:
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise SystemExit(f"isolation destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(".pf", "package-floor.json", ".git")
    shutil.copytree(source, destination, ignore=ignore, symlinks=False)
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {
        "source": str(source),
        "destination": str(destination),
        "git_head": head,
        "dirty": bool(dirty.strip()),
        "excluded": [".pf", "package-floor.json", ".git"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("controlled", "check", "search", "isolate"), required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    os.chdir(ROOT)
    if arguments.mode == "controlled":
        qualify_controlled(arguments.output)
        return
    if arguments.mode == "isolate":
        if arguments.root is None or arguments.destination is None:
            raise SystemExit("isolate requires --root and --destination")
        payload = isolate_mkdocs(arguments.root, arguments.destination)
        payload.update({"schema": SCHEMA, "mode": "isolate", "recorded_at": _utc_now()})
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(payload, indent=2) + "\n")
        return
    if arguments.root is None:
        raise SystemExit(f"{arguments.mode} requires --root")
    qualify_project(arguments.root, mode=arguments.mode, output=arguments.output)


if __name__ == "__main__":
    main()

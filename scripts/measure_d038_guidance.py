"""Compare D038 guidance vs mechanical search on the public evaluator seam.

Mechanical mode wraps only open_static_slice so the product coordinator yields
no hint. It is not a configuration switch. ExecutionPolicy, source, Cell,
candidate snapshot and small_threshold stay fixed.

    UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/measure_d038_guidance.py \\
        --output docs/experiments/data/E009/measurement.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import sys
import tempfile
from typing import Any

from pf.schemas.evaluation import (
    NormalExit,
    ProcessResult,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    VerifierDiagnostics,
    VerifierPass,
    VerifierRejected,
    VerifierRun,
)
from pf.schemas.report import CellSuccess
from pf.static import TyCheckCache


ROOT = Path(__file__).resolve().parents[1]


def _load_evaluation_fixtures() -> dict[str, Any]:
    tests = str(ROOT / "tests")
    sys.path.insert(0, tests)
    try:
        return runpy.run_path(str(ROOT / "tests/evaluation_fixtures.py"))
    finally:
        sys.path.remove(tests)


FIXTURES = _load_evaluation_fixtures()


def _count_events(events: list[tuple[str, int]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind, _version in events:
        counts[kind] = counts.get(kind, 0) + 1
    return counts


class _MechanicalEvaluator:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.static_opens = 0

    def evaluate(self, vector):
        return self._inner.evaluate(vector)

    def evaluate_in_slice(self, request):
        return self._inner.evaluate_in_slice(request)

    def lookup_direct_in_slice(self, request):
        return self._inner.lookup_direct_in_slice(request)

    def consume_direct_in_slice(self, request, evidence):
        return self._inner.consume_direct_in_slice(request, evidence)

    def record_direct_bound(self, vector, **kwargs):
        return self._inner.record_direct_bound(vector, **kwargs)

    def open_static_slice(self, vector, *, dependency, versions):
        self.static_opens += 1
        return None

    def finish_coordinate(self):
        return self._inner.finish_coordinate()


def _run_variant(
    *,
    tmp_path: Path,
    floor: int,
    mechanical: bool,
    unavailable: int | None,
) -> dict[str, Any]:
    events: list[tuple[str, int]] = []
    diagnostic = TyDiagnostic(
        identity="snapshot|src/demo/__init__.py|1|1|example",
        origin="snapshot",
        path="src/demo/__init__.py",
        line=1,
        column=1,
        code="example",
        severity="error",
        message="static suspicion",
    )
    successful_process = FIXTURES["successful_process"]

    def ty(vector, _call):
        version = int(vector[0].version)
        events.append(("ty", version))
        if unavailable is not None and version == unavailable:
            return ToolFailure(
                cause="TOOL_FAILURE",
                stage="ty",
                process=ProcessResult(exit_code=2, duration_seconds=0.01),
            )
        regression = version < 3
        return TyCheck(
            process=successful_process(exit_code=1 if regression else 0),
            diagnostics=(diagnostic,) if regression else (),
        )

    def verifier(vector, _call):
        version = int(vector[0].version)
        events.append(("verifier", version))
        return VerifierRun(
            authoritative=(
                VerifierPass(terminal=NormalExit(exit_code=0))
                if version >= floor
                else VerifierRejected(terminal=NormalExit(exit_code=1))
            ),
            diagnostics=VerifierDiagnostics(
                process=successful_process(exit_code=0 if version >= floor else 1)
            ),
        )

    project = FIXTURES["evaluation_project"](tmp_path)
    assembly = FIXTURES["evaluation_assembly"](
        ty_handler=ty,
        verifier_handler=verifier,
    )
    wrapper: _MechanicalEvaluator | None = None
    if mechanical:
        original = assembly.coordinate_search.minimize

        def minimize(*, start, candidates, evaluator, **more):
            nonlocal wrapper
            wrapper = _MechanicalEvaluator(evaluator)
            return original(
                start=start, candidates=candidates, evaluator=wrapper, **more
            )

        assembly.coordinate_search.minimize = minimize  # type: ignore[method-assign]
    try:
        result = assembly.coordinator.search(
            run_cache=TyCheckCache(),
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )
    finally:
        project.snapshot.close()
    vector = []
    static_searches = 0
    if isinstance(result, CellSuccess):
        vector = [
            {"name": pin.name, "version": pin.version} for pin in result.final_vector
        ]
    if wrapper is not None:
        static_searches = wrapper.static_opens
    elif isinstance(result, CellSuccess):
        # Guided path: count persisted static stages from the live run cache later.
        static_searches = sum(1 for kind, _version in events if kind == "ty") - 1
    return {
        "status": getattr(result, "status", type(result).__name__),
        "floor_vector": vector,
        "events": [{"kind": kind, "version": version} for kind, version in events],
        "event_counts": _count_events(events),
        "mechanical": mechanical,
        "injected_unavailable": unavailable,
        "static_opens_or_ty_after_highest": static_searches,
        "environments_closed": all(
            not root.exists() for root in assembly.uv.environment_roots
        ),
    }


def measure() -> dict[str, object]:
    cases = []
    with tempfile.TemporaryDirectory(prefix="pf-d038-measure-") as temporary:
        root = Path(temporary)
        for floor in (1, 2, 3):
            for unavailable in (None, 1):
                guided = _run_variant(
                    tmp_path=root / f"g-{floor}-{unavailable}",
                    floor=floor,
                    mechanical=False,
                    unavailable=unavailable,
                )
                mechanical = _run_variant(
                    tmp_path=root / f"m-{floor}-{unavailable}",
                    floor=floor,
                    mechanical=True,
                    unavailable=unavailable,
                )
                cases.append({
                    "floor": floor,
                    "unavailable": unavailable,
                    "guided": guided,
                    "mechanical": mechanical,
                    "same_floor": guided["floor_vector"] == mechanical["floor_vector"],
                    "same_status": guided["status"] == mechanical["status"],
                })
    return {
        "schema": "pf-d038-guidance-measurement-v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "seam": "SearchCoordinator + CoordinateSearch public evaluator",
        "note": (
            "Mechanical mode wraps open_static_slice only. "
            "It is not a product configuration switch. Counts come from "
            "scripted adapters, not registry wall time."
        ),
        "cases": cases,
        "all_floors_match": all(
            case["same_floor"] and case["same_status"] for case in cases
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    payload = measure()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(payload, indent=2) + "\n")
    if not payload["all_floors_match"]:
        raise SystemExit("guided and mechanical floors diverged")


if __name__ == "__main__":
    main()

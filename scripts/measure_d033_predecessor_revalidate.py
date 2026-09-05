"""Measure D033 predecessor revalidation through PF's real evaluator graph.

Run from the repository root:

    .venv/bin/python scripts/measure_d033_predecessor_revalidate.py \
        --output docs/experiments/data/D033/measurement.json

Variant A uses the current flat search with predecessor history disabled. Variant B
uses the shipped CoordinateSearch. Both variants retain the same baseline seed,
CandidateBuilder, EnvironmentFactory, StaticEvaluator, RuntimeEvaluator and
invocation-local _ProposalRunner cache. Only deterministic lower adapters are used;
there is no registry, uv subprocess, ty subprocess, or configured verifier wall time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import runpy
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import pf.coordinate_search as coordinate_search
from pf.coordinate_search import (
    CoordinateSearch,
    RuntimeBackedVectorEvaluator,
    VectorEvaluator,
)
from pf.schemas.evaluation import (
    NormalExit,
    SearchProbeRequest,
    VerifierPass,
    VerifierRejected,
    VerifierRun,
)
from pf.schemas.project import CandidateSnapshot, VersionPin
from pf.schemas.report import (
    CellSuccess,
    CoordinateBoundary,
    CoordinateOutcome,
    ProbeEvidence,
    StaticOnlyEvidence,
    StaticRegion,
)
from pf.search import SearchCoordinator


ROOT = Path(__file__).resolve().parents[1]
Variant = Literal["A", "B"]
VARIANTS: tuple[Variant, Variant] = ("A", "B")
StageCounts = tuple[int, int, int]


def _load_evaluation_fixtures() -> dict[str, object]:
    tests = str(ROOT / "tests")
    sys.path.insert(0, tests)
    try:
        return runpy.run_path(str(ROOT / "tests/evaluation_fixtures.py"))
    finally:
        sys.path.remove(tests)


FIXTURES = _load_evaluation_fixtures()


@dataclass(frozen=True)
class Scenario:
    name: str
    dependencies: tuple[str, ...]
    accepts: Callable[[dict[str, int]], bool]


SCENARIOS = (
    Scenario(
        name="changed-context-stable-boundary",
        dependencies=("alpha", "beta"),
        accepts=lambda versions: versions["alpha"] >= 7
        and versions["beta"] >= 4,
    ),
    Scenario(
        name="changed-context-predecessor-turns-pass",
        dependencies=("alpha", "beta"),
        accepts=lambda versions: versions["alpha"]
        >= (7 if versions["beta"] >= 5 else 6)
        and versions["beta"] >= 4,
    ),
    Scenario(
        name="same-context-cache-only",
        dependencies=("alpha",),
        accepts=lambda versions: versions["alpha"] >= 7,
    ),
)


@dataclass
class RequestMetrics:
    logical_requests: int = 0
    cache_hits: int = 0
    prepare_misses: int = 0
    static_misses: int = 0
    runtime_misses: int = 0
    static_guidance: int = 0
    coordinate_wall_seconds: float = 0.0
    operation_counts: Counter[str] = field(default_factory=Counter)
    vectors: set[tuple[tuple[str, str], ...]] = field(default_factory=set)
    trace: list[dict[str, object]] = field(default_factory=list)

    def document(self) -> dict[str, object]:
        return {
            "logical_requests": self.logical_requests,
            "cache_hits": self.cache_hits,
            "unique_vectors": len(self.vectors),
            "prepare_misses": self.prepare_misses,
            "static_misses": self.static_misses,
            "runtime_misses": self.runtime_misses,
            "static_guidance": self.static_guidance,
            "coordinate_wall_seconds": self.coordinate_wall_seconds,
            "operation_counts": dict(sorted(self.operation_counts.items())),
            "trace": self.trace,
        }


class MeasuredEvaluator(
    VectorEvaluator,
    RuntimeBackedVectorEvaluator,
    Protocol,
):
    pass


class CountingEvaluator:
    def __init__(
        self,
        inner: MeasuredEvaluator,
        *,
        counts: Callable[[], StageCounts],
        metrics: RequestMetrics,
    ) -> None:
        self._inner = inner
        self._counts = counts
        self._metrics = metrics

    @property
    def regions(self) -> tuple[StaticRegion, ...]:
        return self._inner.regions

    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        return cast(
            ProbeEvidence,
            self._record("evaluate", vector, lambda: self._inner.evaluate(vector)),
        )

    def evaluate_in_slice(
        self,
        request: SearchProbeRequest,
    ) -> ProbeEvidence | StaticOnlyEvidence:
        return self._record(
            "evaluate_in_slice",
            request.vector,
            lambda: self._inner.evaluate_in_slice(request),
        )

    def promote(self, request: SearchProbeRequest) -> ProbeEvidence:
        return cast(
            ProbeEvidence,
            self._record(
                "promote",
                request.vector,
                lambda: self._inner.promote(request),
            ),
        )

    def _record(
        self,
        operation: str,
        vector: tuple[VersionPin, ...],
        call: Callable[[], ProbeEvidence | StaticOnlyEvidence],
    ) -> ProbeEvidence | StaticOnlyEvidence:
        before = self._counts()
        started = time.perf_counter()
        result = call()
        elapsed = time.perf_counter() - started
        after = self._counts()
        delta = tuple(end - start for start, end in zip(before, after, strict=True))
        key = tuple((pin.name, pin.version) for pin in vector)
        self._metrics.logical_requests += 1
        self._metrics.operation_counts[operation] += 1
        self._metrics.vectors.add(key)
        self._metrics.prepare_misses += delta[0]
        self._metrics.static_misses += delta[1]
        self._metrics.runtime_misses += delta[2]
        if not any(delta):
            self._metrics.cache_hits += 1
        if isinstance(result, StaticOnlyEvidence):
            self._metrics.static_guidance += 1
            outcome = f"STATIC_ONLY:{result.guidance}"
        else:
            outcome = result.status
        self._metrics.trace.append(
            {
                "operation": operation,
                "vector": dict(key),
                "outcome": outcome,
                "cache_hit": not any(delta),
                "prepare_misses": delta[0],
                "static_misses": delta[1],
                "runtime_misses": delta[2],
                "seconds": elapsed,
            }
        )
        return result


class _FlatCoordinateRun(coordinate_search._CoordinateRun):  # type: ignore[attr-defined]
    """Current flat algorithm with only D033 predecessor history disabled."""

    def _find_floor(
        self,
        *,
        current: dict[str, str],
        snapshot: CandidateSnapshot,
        hint: str | None,
        history: CoordinateBoundary | None,
    ) -> tuple[str, CoordinateBoundary]:
        del history
        return super()._find_floor(
            current=current,
            snapshot=snapshot,
            hint=hint,
            history=None,
        )


class _FlatCoordinateSearch(CoordinateSearch):
    def minimize(
        self,
        *,
        start: tuple[VersionPin, ...],
        candidates: tuple[CandidateSnapshot, ...],
        evaluator: VectorEvaluator,
        hints: tuple[VersionPin, ...] = (),
        progress: coordinate_search.CoordinateProgressConsumer | None = None,
    ) -> CoordinateOutcome:
        return _FlatCoordinateRun(
            small_threshold=self.small_threshold,
            evaluator=evaluator,
            progress=progress,
        ).minimize(start=start, candidates=candidates, hints=hints)


class MeasuredCoordinateSearch(CoordinateSearch):
    def __init__(
        self,
        variant: Variant,
        *,
        counts: Callable[[], StageCounts],
    ) -> None:
        super().__init__()
        self._variant = variant
        self._counts = counts
        self.metrics = RequestMetrics()

    def minimize(
        self,
        *,
        start: tuple[VersionPin, ...],
        candidates: tuple[CandidateSnapshot, ...],
        evaluator: VectorEvaluator,
        hints: tuple[VersionPin, ...] = (),
        progress: coordinate_search.CoordinateProgressConsumer | None = None,
    ) -> CoordinateOutcome:
        measured = CountingEvaluator(
            cast(MeasuredEvaluator, evaluator),
            counts=self._counts,
            metrics=self.metrics,
        )
        engine = _FlatCoordinateSearch() if self._variant == "A" else CoordinateSearch()
        started = time.perf_counter()
        outcome = engine.minimize(
            start=start,
            candidates=candidates,
            evaluator=measured,
            hints=hints,
            progress=progress,
        )
        self.metrics.coordinate_wall_seconds = time.perf_counter() - started
        return outcome


def _run_sample(
    scenario: Scenario,
    variant: Variant,
    *,
    repetition: int,
    order: int,
    root: Path,
) -> dict[str, object]:
    evaluation_project = cast(Callable[..., Any], FIXTURES["evaluation_project"])
    evaluation_assembly = cast(Callable[..., Any], FIXTURES["evaluation_assembly"])
    versions = tuple(str(value) for value in range(1, 10))
    highest = tuple(VersionPin(name=name, version="9") for name in scenario.dependencies)
    project = evaluation_project(
        root,
        dependencies=scenario.dependencies,
        search_space="all",
        search_configuration='search-resolution = "patch"',
    )

    def verify(vector: tuple[VersionPin, ...], call: int) -> VerifierRun:
        del call
        observed = {pin.name: int(pin.version) for pin in vector}
        if scenario.accepts(observed):
            return VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
            )
        return VerifierRun(
            authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
            failed_case_additions=("test_floor.py::test_minimum",),
        )

    assembly = evaluation_assembly(
        highest=highest,
        candidate_versions_by_dependency={
            name: versions for name in scenario.dependencies
        },
        verifier_handler=verify,
    )

    def counts() -> StageCounts:
        return (
            len(assembly.uv.exact_selections),
            len(assembly.ty.vectors),
            len(assembly.verifier.vectors),
        )

    measured = MeasuredCoordinateSearch(variant, counts=counts)
    coordinator = SearchCoordinator(
        environments=assembly.environments,
        candidates=assembly.candidate_builder,
        static=assembly.static,
        full=assembly.runtime,
        highest=assembly.highest,
        coordinate_search=measured,
    )
    started = time.perf_counter()
    result = coordinator.search(
        package=project.package,
        cell=project.package.cells[0],
        snapshot=project.snapshot,
        source_plan=project.source_plan,
    )
    cell_seconds = time.perf_counter() - started
    if not isinstance(result, CellSuccess):
        raise AssertionError((scenario.name, variant, result.status))
    if any(path.exists() for path in assembly.uv.environment_roots):
        raise AssertionError((scenario.name, variant, "environment leak"))

    document = measured.metrics.document()
    if repetition != 0:
        document.pop("trace")
    document.update(
        {
            "scenario": scenario.name,
            "variant": variant,
            "repetition": repetition,
            "order": order,
            "status": result.status,
            "final_vector": {
                pin.name: pin.version for pin in result.final_vector
            },
            "sweeps": result.search.sweeps,
            "cell_wall_seconds": cell_seconds,
            "candidate_queries": len(assembly.candidates.queries),
            "failed_case_requests": sum(
                bool(request.failed_case_nodeids)
                for request in assembly.verifier.requests
            ),
            "environment_count": len(assembly.uv.environment_roots),
            "all_environments_closed": True,
        }
    )
    return document


SUMMARY_METRICS = (
    "logical_requests",
    "cache_hits",
    "unique_vectors",
    "prepare_misses",
    "static_misses",
    "runtime_misses",
    "static_guidance",
    "sweeps",
    "coordinate_wall_seconds",
    "cell_wall_seconds",
)


def _summary(samples: list[dict[str, object]]) -> dict[str, object]:
    grouped: dict[tuple[str, Variant], list[dict[str, object]]] = {}
    for sample in samples:
        key = (cast(str, sample["scenario"]), cast(Variant, sample["variant"]))
        grouped.setdefault(key, []).append(sample)
    summary: dict[str, object] = {}
    for scenario in SCENARIOS:
        variants: dict[str, dict[str, object]] = {}
        for variant in VARIANTS:
            rows = grouped[(scenario.name, variant)]
            metrics: dict[str, object] = {}
            for name in SUMMARY_METRICS:
                values = [float(cast(int | float, row[name])) for row in rows]
                metrics[name] = {
                    "median": statistics.median(values),
                    "min": min(values),
                    "max": max(values),
                }
            variants[variant] = metrics
        comparison: dict[str, object] = {}
        for name in SUMMARY_METRICS:
            a = cast(float, cast(dict[str, object], variants["A"][name])["median"])
            b = cast(float, cast(dict[str, object], variants["B"][name])["median"])
            comparison[name] = {
                "b_minus_a": b - a,
                "percent": None if a == 0 else ((b - a) / a) * 100,
            }
        finals = {
            json.dumps(row["final_vector"], sort_keys=True)
            for variant in VARIANTS
            for row in grouped[(scenario.name, variant)]
        }
        if len(finals) != 1:
            raise AssertionError((scenario.name, "A/B final mismatch", finals))
        summary[scenario.name] = {
            "variants": variants,
            "b_minus_a": comparison,
            "final_vector": json.loads(next(iter(finals))),
        }
    return summary


def _source_hashes() -> dict[str, str]:
    paths = (
        "src/pf/coordinate_search.py",
        "src/pf/search.py",
        "tests/evaluation_fixtures.py",
        "scripts/measure_d033_predecessor_revalidate.py",
    )
    return {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in paths
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    samples: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="pf-d033-measure-") as raw:
        temporary = Path(raw)
        for scenario in SCENARIOS:
            for repetition in range(args.repeat):
                order: tuple[Variant, Variant] = (
                    ("A", "B") if repetition % 2 == 0 else ("B", "A")
                )
                for order_index, variant in enumerate(order):
                    samples.append(
                        _run_sample(
                            scenario,
                            variant,
                            repetition=repetition,
                            order=order_index,
                            root=(
                                temporary
                                / scenario.name
                                / str(repetition)
                                / variant
                            ),
                        )
                    )
    document = {
        "schema": "pf:d033-predecessor-measurement:v1",
        "git_commit": subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repeat": args.repeat,
        "run_order": "alternating A/B by repetition",
        "cache_conditions": {
            "registry": "fresh deterministic in-memory adapter per sample; no persistent cache",
            "resolution": "fresh deterministic in-memory adapter per sample; no persistent cache",
            "evaluator": "cold at invocation start; warms only within that Cell search",
        },
        "scope": {
            "product_graph": (
                "SearchCoordinator -> EnvironmentFactory -> StaticEvaluator -> "
                "RuntimeEvaluator"
            ),
            "lower_adapters": "deterministic in-memory uv/ty/verifier/witness",
            "configured_verifier_subprocess": False,
            "network_registry": False,
            "historical_trace_used": False,
            "missing_vectors": [],
            "wall_clock_boundary": (
                "harness overhead only; not a claim about real uv/ty/verifier latency"
            ),
            "trace_scope": "full logical traces retained for repetition 0 only",
        },
        "source_sha256": _source_hashes(),
        "summary": _summary(samples),
        "samples": samples,
    }
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

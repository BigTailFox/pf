from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter
import tracemalloc
from typing import Callable, TypeVar

from pydantic import TypeAdapter

from pf.report import ReportStore
from pf.schemas.project import (
    Cell,
    RequirementDeclaration,
    SourceSnapshotIdentity,
    cell_identity,
)
from pf.schemas.report import (
    CellResult,
    CompleteReportResult,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageIdentity,
    ProjectionEvidence,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "report-schema"
INLINE_PATH = FIXTURE_ROOT / "pf-self-search-inline.json"
SCHEMA2_PATH = FIXTURE_ROOT / "pf-self-search-v2.json"
RECORD_PATH = ROOT / "docs" / "qualification" / "package-floor-v2.json"
INLINE_BYTES = 7_682_528
INLINE_SHA256 = "29dd927eea928d63a555203f35304bea1f927f5e81963bac1b163e2e209af034"
INLINE_COMPACT_BYTES = 4_084_111
INLINE_GENERATION_ID = (
    "cf37b403df8eceb0060afaf95ce30effd2a699b28ce84f551c432aa5ed91342b"
)
SCHEMA2_MAX_BYTES = 2_042_055
SOURCE_SNAPSHOT_DIGEST = (
    "ccb09c63cf0fffb66aca4220a11f04c4231507b21d9b6916497696456a6e92df"
)


T = TypeVar("T")
_CELL_RESULT_ADAPTER = TypeAdapter(CellResult)
_NON_SEMANTIC_PROCESS_FIELDS = {
    "duration_seconds",
    "environment_plan_digest",
    "project_plan_digest",
    "stderr",
    "stdout",
}


def _measure(operation: Callable[[], T]) -> tuple[T, float, int]:
    gc.collect()
    tracemalloc.start()
    started = perf_counter()
    try:
        result = operation()
        elapsed = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, elapsed, peak


def _stable_search_semantics(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _stable_search_semantics(item)
            for key, item in value.items()
            if key not in _NON_SEMANTIC_PROCESS_FIELDS and item is not None
        }
    if isinstance(value, (list, tuple)):
        return [_stable_search_semantics(item) for item in value]
    return value


def qualify() -> dict[str, object]:
    inline_bytes = INLINE_PATH.read_bytes()
    if len(inline_bytes) != INLINE_BYTES:
        raise RuntimeError("inline report fixture byte count changed")
    if hashlib.sha256(inline_bytes).hexdigest() != INLINE_SHA256:
        raise RuntimeError("inline report fixture hash changed")
    inline_document = json.loads(inline_bytes)
    inline_compact = json.dumps(
        inline_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(inline_compact) != INLINE_COMPACT_BYTES:
        raise RuntimeError("inline report compact byte count changed")
    if inline_document.get("report_generation_id") != INLINE_GENERATION_ID:
        raise RuntimeError("inline report generation changed")
    inline_source = inline_document.get("source_snapshot")
    if (
        not isinstance(inline_source, dict)
        or inline_source.get("digest") != SOURCE_SNAPSHOT_DIGEST
    ):
        raise RuntimeError("inline report source snapshot changed")

    schema2_bytes = SCHEMA2_PATH.read_bytes()
    if len(schema2_bytes) > SCHEMA2_MAX_BYTES:
        raise RuntimeError("Schema 2 report exceeds the qualification size target")
    store = ReportStore()
    report, read_seconds, read_peak = _measure(lambda: store.read(SCHEMA2_PATH))
    wire = report._wire
    inline_generation_facts = (
        (
            "generator",
            GeneratorIdentity.model_validate(inline_document.get("generator")),
            report.generator,
        ),
        (
            "package",
            PackageIdentity.model_validate(inline_document.get("package")),
            report.package,
        ),
        (
            "source_snapshot",
            SourceSnapshotIdentity.model_validate(inline_source),
            report.source_snapshot,
        ),
        (
            "policy_identity",
            inline_document.get("policy_identity"),
            report.policy_identity,
        ),
        (
            "requirement_declarations",
            tuple(
                sorted(
                    (
                        RequirementDeclaration.model_validate(item)
                        for item in inline_document.get("requirement_declarations", ())
                    ),
                    key=lambda item: item.declaration_id,
                )
            ),
            report.requirement_declarations,
        ),
        (
            "target_cells",
            tuple(
                sorted(
                    (
                        Cell.model_validate(item)
                        for item in inline_document.get("target_cells", ())
                    ),
                    key=cell_identity,
                )
            ),
            report.target_cells,
        ),
        (
            "projection_evidence",
            tuple(
                sorted(
                    (
                        ProjectionEvidence.model_validate(item)
                        for item in inline_document.get("projection_evidence", ())
                    ),
                    key=lambda item: item.declaration_id,
                )
            ),
            report.projection_evidence,
        ),
        (
            "result",
            TypeAdapter(CompleteReportResult | IncompleteReportResult).validate_python(
                inline_document.get("result")
            ),
            report.result,
        ),
    )
    for field_name, inline_value, schema2_value in inline_generation_facts:
        if inline_value != schema2_value:
            raise RuntimeError(
                f"Schema 2 {field_name} does not match the inline report"
            )
    inline_candidate_ids = tuple(
        sorted(
            item["digest"] for item in inline_document.get("candidate_snapshots", ())
        )
    )
    schema2_candidate_ids = tuple(
        sorted(item.candidate_snapshot_id for item in wire.inputs.candidate_snapshots)
    )
    if inline_candidate_ids != schema2_candidate_ids:
        raise RuntimeError("Schema 2 CandidateSnapshots do not match the inline report")
    inline_cell_results = tuple(
        _CELL_RESULT_ADAPTER.validate_python(item)
        for item in inline_document.get("cell_results", ())
    )
    inline_search_semantics = _stable_search_semantics(
        [
            item.model_dump(mode="json", exclude_none=True)
            for item in inline_cell_results
        ]
    )
    schema2_search_semantics = _stable_search_semantics(
        [
            item.model_dump(mode="json", exclude_none=True)
            for item in report.cell_results
        ]
    )
    if inline_search_semantics != schema2_search_semantics:
        raise RuntimeError("Schema 2 search evidence does not match the inline report")
    canonical_schema2 = (
        json.dumps(
            wire.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if schema2_bytes != canonical_schema2:
        raise RuntimeError("Schema 2 qualification fixture is not canonical JSON")
    if len(wire.inputs.candidate_snapshots) != 24:
        raise RuntimeError("Schema 2 must define exactly 24 CandidateSnapshots")
    if len(wire.inputs.target_cells) != 3:
        raise RuntimeError("Schema 2 must define exactly 3 Cells")

    left = store._reintern(report, report.cell_results[::2])
    right = store._reintern(report, report.cell_results[1::2])
    merged, merge_seconds, merge_peak = _measure(lambda: store.merge((left, right)))
    if merged._wire != report._wire:
        raise RuntimeError("complementary Schema 2 merge changed the report")
    if not len(schema2_bytes) < len(inline_compact):
        raise RuntimeError("Schema 2 is not smaller than the inline compact report")

    return {
        "fixture": {
            "inline_bytes": len(inline_bytes),
            "inline_compact_bytes": len(inline_compact),
            "inline_report_generation_id": INLINE_GENERATION_ID,
            "inline_sha256": INLINE_SHA256,
            "schema2_bytes": len(schema2_bytes),
            "schema2_sha256": hashlib.sha256(schema2_bytes).hexdigest(),
        },
        "schema2_entities": {
            "attempts": len(wire.evidence.attempts),
            "candidate_snapshots": len(wire.inputs.candidate_snapshots),
            "cells": len(wire.inputs.target_cells),
            "proposals": len(wire.evidence.proposals),
            "resolution_graphs": len(wire.evidence.resolution_graphs),
        },
        "read_validate": {
            "peak_bytes": read_peak,
            "seconds": round(read_seconds, 6),
        },
        "merge_complementary": {
            "peak_bytes": merge_peak,
            "seconds": round(merge_seconds, 6),
        },
        "thresholds": {
            "schema2_max_bytes": SCHEMA2_MAX_BYTES,
            "schema2_smaller_than_inline_compact": len(schema2_bytes)
            < len(inline_compact),
        },
    }


def _validate_recorded_performance(recorded: object) -> None:
    if not isinstance(recorded, dict):
        raise RuntimeError("Schema 2 qualification record is invalid")
    for section in ("read_validate", "merge_complementary"):
        performance = recorded.get(section)
        if not isinstance(performance, dict) or set(performance) != {
            "peak_bytes",
            "seconds",
        }:
            raise RuntimeError(f"Schema 2 qualification record {section} is invalid")
        peak_bytes = performance["peak_bytes"]
        seconds = performance["seconds"]
        if (
            isinstance(peak_bytes, bool)
            or not isinstance(peak_bytes, int)
            or peak_bytes < 0
            or isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or seconds < 0
            or not math.isfinite(seconds)
        ):
            raise RuntimeError(f"Schema 2 qualification record {section} is invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = qualify()
    if args.check:
        if not RECORD_PATH.is_file():
            raise RuntimeError("Schema 2 qualification record is missing")
        recorded = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
        for section in ("fixture", "schema2_entities", "thresholds"):
            if recorded.get(section) != result[section]:
                raise RuntimeError(f"Schema 2 qualification record {section} is stale")
        _validate_recorded_performance(recorded)
        return 0
    RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECORD_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest

from pf.errors import ConfigurationError
from pf.report import ReportStore
from pf.schemas.project import SourceSnapshotIdentity
from pf.schemas.report import (
    CellFailure,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
)
from pf.schemas.project import Cell


def incomplete_report() -> PackageFloorReportV1:
    return PackageFloorReportV1(
        generator=GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1"),
        package=PackageIdentity(name="demo", pyproject_path="pyproject.toml"),
        source_snapshot=SourceSnapshotIdentity(digest="snapshot", entries=()),
        policy_identity="policy",
        requirement_declarations=(),
        candidate_snapshots=(),
        cell_results=(),
        projection_evidence=(),
        result=IncompleteReportResult(reasons=("TIMEOUT",)),
    )


def test_report_store_writes_canonical_versioned_json_and_rejects_unknown_schema(
    tmp_path: Path,
) -> None:
    store = ReportStore()
    path = tmp_path / "package-floor.json"
    report = incomplete_report()

    store.write(path, report)

    content = path.read_text(encoding="utf-8")
    assert content.endswith("\n") and not content.endswith("\n\n")
    assert content.startswith('{"candidate_snapshots":')
    assert store.read(path) == report

    document = json.loads(content)
    document["schema_version"] = 2
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="unsupported report schema_version: 2"):
        store.read(path)


def test_report_merge_is_deterministic_and_rejects_conflicting_cells() -> None:
    store = ReportStore()
    cells = tuple(
        Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor=minor,
            extra_surface=(),
        )
        for minor in ("3.10", "3.11")
    )

    def report_for(
        cell: Cell,
        status: Literal["TIMEOUT", "TOOL_ERROR", "SOURCE_ERROR"],
    ) -> PackageFloorReportV1:
        return PackageFloorReportV1(
            generator=GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1"),
            package=PackageIdentity(name="demo", pyproject_path="pyproject.toml"),
            source_snapshot=SourceSnapshotIdentity(digest="snapshot", entries=()),
            policy_identity="policy",
            requirement_declarations=(),
            candidate_snapshots=(),
            target_cells=cells,
            cell_results=(
                CellFailure(status=status, cell=cell, phase="evaluation"),
            ),
            projection_evidence=(),
            result=IncompleteReportResult(reasons=(status, "MISSING_CELL")),
        )

    first = report_for(cells[1], "TIMEOUT")
    second = report_for(cells[0], "TOOL_ERROR")
    merged = store.merge((first, second))

    assert [result.cell.python_minor for result in merged.cell_results] == [
        "3.10",
        "3.11",
    ]
    assert merged.result.status == "incomplete"
    assert merged.result.reasons == ("TIMEOUT", "TOOL_ERROR")

    conflict = report_for(cells[1], "SOURCE_ERROR")
    with pytest.raises(ConfigurationError, match="conflicting result for cell"):
        store.merge((first, conflict))

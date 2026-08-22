from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest

from pf.errors import ConfigurationError
from pf.failure import FailurePolicy
from pf.report import ReportStore
from pf.schemas.evaluation import (
    CellFailureScope,
    FailureCause,
    FailureDetail,
    ProcessResult,
)
from pf.schemas.project import SourceSnapshotIdentity
from pf.schemas.report import (
    CellIndeterminate,
    FloorProjection,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProjectionEvidence,
    report_generation_id,
)
from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    RequirementDeclaration,
    SourceIdentity,
)


def incomplete_report() -> PackageFloorReportV1:
    generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
    package = PackageIdentity(name="demo", pyproject_path="pyproject.toml")
    snapshot = SourceSnapshotIdentity(digest="snapshot", entries=())
    return PackageFloorReportV1(
        report_generation_id=report_generation_id(
            generator=generator,
            package=package,
            source_snapshot=snapshot,
            policy_identity="policy",
            requirement_declarations=(),
            target_cells=(),
        ),
        generator=generator,
        package=package,
        source_snapshot=snapshot,
        policy_identity="policy",
        requirement_declarations=(),
        candidate_snapshots=(),
        cell_results=(),
        projection_evidence=(),
        result=IncompleteReportResult(reasons=("INDETERMINATE",)),
    )


def cell_failure(
    cell: Cell,
    cause: FailureCause,
    *,
    stage: str = "evaluation",
) -> CellIndeterminate:
    failure = FailurePolicy().classify(
        scope=CellFailureScope(
            package=cell.package,
            cell=cell,
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="policy",
        ),
        cause=cause,
        stage=stage,
        process=None,
        detail=FailureDetail(code="test-failure", message="test failure"),
    )
    return CellIndeterminate(
        cell=cell,
        phase=stage,
        failure_id=failure.failure_id,
        failure_records=(failure,),
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
    with pytest.raises(
        ConfigurationError, match="unsupported report schema_version: 2"
    ):
        store.read(path)


def test_report_store_omits_captured_process_output(tmp_path: Path) -> None:
    store = ReportStore()
    path = tmp_path / "package-floor.json"
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    failure = FailurePolicy().classify(
        scope=CellFailureScope(
            package=cell.package,
            cell=cell,
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="policy",
        ),
        cause="TOOL_FAILURE",
        stage="test",
        process=ProcessResult(
            exit_code=0,
            signal=None,
            duration_seconds=11.12,
            stdout="484 passed in 11.12s",
            stderr="secret-noise",
            stdout_complete=False,
        ),
    )
    generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
    package = PackageIdentity(name="demo", pyproject_path="pyproject.toml")
    snapshot = SourceSnapshotIdentity(digest="snapshot", entries=())
    cells = (cell,)
    report = PackageFloorReportV1(
        report_generation_id=report_generation_id(
            generator=generator,
            package=package,
            source_snapshot=snapshot,
            policy_identity="policy",
            requirement_declarations=(),
            target_cells=cells,
        ),
        generator=generator,
        package=package,
        source_snapshot=snapshot,
        policy_identity="policy",
        requirement_declarations=(),
        candidate_snapshots=(),
        target_cells=cells,
        cell_results=(
            CellIndeterminate(
                cell=cell,
                phase="test",
                failure_id=failure.failure_id,
                failure_records=(failure,),
            ),
        ),
        projection_evidence=(),
        result=IncompleteReportResult(reasons=("INDETERMINATE",)),
    )

    store.write(path, report)
    content = path.read_text(encoding="utf-8")
    loaded = store.read(path)
    process = loaded.failure_records[0].process

    assert '"stdout":' not in content
    assert '"stderr":' not in content
    assert "stdout_tail" not in content
    assert "stderr_tail" not in content
    assert "484 passed" not in content
    assert "secret-noise" not in content
    assert process is not None
    assert process.exit_code == 0
    assert process.stdout_complete is False
    assert process.stdout == ""
    assert process.stderr == ""


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
        cause: Literal["TIMEOUT", "TOOL_FAILURE", "SOURCE_FAILURE"],
    ) -> PackageFloorReportV1:
        generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
        package = PackageIdentity(name="demo", pyproject_path="pyproject.toml")
        snapshot = SourceSnapshotIdentity(digest="snapshot", entries=())
        return PackageFloorReportV1(
            report_generation_id=report_generation_id(
                generator=generator,
                package=package,
                source_snapshot=snapshot,
                policy_identity="policy",
                requirement_declarations=(),
                target_cells=cells,
            ),
            generator=generator,
            package=package,
            source_snapshot=snapshot,
            policy_identity="policy",
            requirement_declarations=(),
            candidate_snapshots=(),
            target_cells=cells,
            cell_results=(cell_failure(cell, cause),),
            projection_evidence=(),
            result=IncompleteReportResult(reasons=("INDETERMINATE", "MISSING_CELL")),
        )

    first = report_for(cells[1], "TIMEOUT")
    second = report_for(cells[0], "TOOL_FAILURE")
    merged = store.merge((first, second))

    assert [result.cell.python_minor for result in merged.cell_results] == [
        "3.10",
        "3.11",
    ]
    assert merged.result.status == "incomplete"
    assert merged.result.reasons == ("INDETERMINATE",)

    conflict = report_for(cells[1], "SOURCE_FAILURE")
    with pytest.raises(ConfigurationError, match="conflicting result for cell"):
        store.merge((first, conflict))


def test_report_store_rejects_missing_malformed_and_invalid_reports(
    tmp_path: Path,
) -> None:
    store = ReportStore()
    missing = tmp_path / "missing.json"
    with pytest.raises(ConfigurationError, match="cannot read report"):
        store.read(missing)

    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="invalid report JSON"):
        store.read(malformed)

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version":1}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="invalid v1 report"):
        store.read(invalid)


def test_report_store_rejects_development_era_baseline_failed_status(
    tmp_path: Path,
) -> None:
    store = ReportStore()
    path = tmp_path / "package-floor.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "report_generation_id": "legacy",
                "generator": {"name": "pf", "version": "0.1.0", "algorithm": "v1"},
                "package": {"name": "demo", "pyproject_path": "pyproject.toml"},
                "source_snapshot": {"digest": "snapshot", "entries": []},
                "policy_identity": "policy",
                "requirement_declarations": [],
                "candidate_snapshots": [],
                "target_cells": [],
                "cell_results": [
                    {
                        "status": "BASELINE_FAILED",
                        "cell": {
                            "package": "demo",
                            "target": "x86_64-unknown-linux-gnu",
                            "python_minor": "3.10",
                            "extra_surface": [],
                        },
                        "phase": "baseline-evaluation",
                    }
                ],
                "projection_evidence": [],
                "result": {"status": "incomplete", "reasons": ["BASELINE_FAILED"]},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="invalid v1 report"):
        store.read(path)


def test_report_update_replaces_local_cells_and_retains_other_hosts() -> None:
    cells = (
        Cell(
            package="demo",
            target="aarch64-apple-darwin",
            python_minor="3.10",
            extra_surface=(),
        ),
        Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        ),
    )

    def make_report(results: tuple[CellIndeterminate, ...]) -> PackageFloorReportV1:
        generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
        package = PackageIdentity(name="demo", pyproject_path="pyproject.toml")
        snapshot = SourceSnapshotIdentity(digest="snapshot", entries=())
        return PackageFloorReportV1(
            report_generation_id=report_generation_id(
                generator=generator,
                package=package,
                source_snapshot=snapshot,
                policy_identity="policy",
                requirement_declarations=(),
                target_cells=cells,
            ),
            generator=generator,
            package=package,
            source_snapshot=snapshot,
            policy_identity="policy",
            requirement_declarations=(),
            candidate_snapshots=(),
            target_cells=cells,
            cell_results=results,
            projection_evidence=(),
            result=IncompleteReportResult(reasons=("INDETERMINATE",)),
        )

    existing = make_report(
        tuple(cell_failure(cell, "TIMEOUT", stage="old") for cell in cells)
    )
    replacement = make_report((cell_failure(cells[1], "TOOL_FAILURE", stage="new"),))

    updated = ReportStore().update(existing, replacement)
    failures = tuple(
        result
        for result in updated.cell_results
        if isinstance(result, CellIndeterminate)
    )

    assert len(failures) == len(updated.cell_results)
    assert [(result.cell.target, result.phase) for result in failures] == [
        ("aarch64-apple-darwin", "old"),
        ("x86_64-unknown-linux-gnu", "new"),
    ]


def test_report_update_is_a_noop_when_replacement_has_no_cells() -> None:
    existing = incomplete_report()
    replacement = incomplete_report()

    assert ReportStore().update(existing, replacement) is existing


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "generator",
            GeneratorIdentity(name="other", version="0.1.0", algorithm="v1"),
            "generator",
        ),
        (
            "package",
            PackageIdentity(name="other", pyproject_path="pyproject.toml"),
            "package",
        ),
        (
            "source_snapshot",
            SourceSnapshotIdentity(digest="other", entries=()),
            "source snapshot",
        ),
        ("policy_identity", "other", "policy"),
        (
            "requirement_declarations",
            (
                RequirementDeclaration(
                    declaration_id="demo",
                    package="demo",
                    location="base",
                    name="demo",
                    source=SourceIdentity(kind="registry"),
                    pyproject_path="pyproject.toml",
                    raw="demo",
                    kind="searchable",
                    managed=False,
                ),
            ),
            "declarations",
        ),
        (
            "target_cells",
            (
                Cell(
                    package="demo",
                    target="x86_64-unknown-linux-gnu",
                    python_minor="3.10",
                    extra_surface=(),
                ),
            ),
            "target cell coverage",
        ),
    ),
)
def test_report_merge_and_update_reject_generation_drift(
    field: str,
    value: object,
    message: str,
) -> None:
    original = incomplete_report()
    changed = original.model_copy(update={field: value})
    store = ReportStore()

    with pytest.raises(ConfigurationError, match=message):
        store.merge((original, changed))
    with pytest.raises(ConfigurationError, match=message):
        store.update(original, changed)


def test_report_merge_rejects_conflicting_candidate_and_projection_evidence() -> None:
    store = ReportStore()
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
        active_declaration_ids=("demo",),
    )
    artifact = AvailableArtifact(
        filename="demo.whl",
        kind="wheel",
        content_hash="sha256:abc",
    )

    def candidate(digest: str) -> CandidateSnapshot:
        return CandidateSnapshot(
            dependency="demo",
            cell=cell,
            policy_identity="policy",
            source=SourceIdentity(kind="registry"),
            candidates=(Candidate(version="1.0", series_key="1", artifact=artifact),),
            series_representatives=(("1", "1.0"),),
            digest=digest,
        )

    declaration = RequirementDeclaration(
        declaration_id="demo",
        package="demo",
        location="base",
        name="demo",
        source=SourceIdentity(kind="registry"),
        pyproject_path="pyproject.toml",
        raw="demo",
        kind="searchable",
        managed=True,
    )

    def evidence_report(
        snapshot: CandidateSnapshot,
        floor: str,
    ) -> PackageFloorReportV1:
        generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
        package = PackageIdentity(name="demo", pyproject_path="pyproject.toml")
        source_snapshot = SourceSnapshotIdentity(digest="snapshot", entries=())
        return PackageFloorReportV1(
            report_generation_id=report_generation_id(
                generator=generator,
                package=package,
                source_snapshot=source_snapshot,
                policy_identity="policy",
                requirement_declarations=(declaration,),
                target_cells=(cell,),
            ),
            generator=generator,
            package=package,
            source_snapshot=source_snapshot,
            policy_identity="policy",
            requirement_declarations=(declaration,),
            candidate_snapshots=(snapshot,),
            target_cells=(cell,),
            cell_results=(),
            projection_evidence=(
                ProjectionEvidence(
                    declaration_id="demo",
                    floors=(FloorProjection(cell=cell, version=floor),),
                    projected_requirements=(),
                    representable=False,
                ),
            ),
            result=IncompleteReportResult(reasons=("MISSING_CELL",)),
        )

    first = evidence_report(candidate("first"), "1.0")
    with pytest.raises(ConfigurationError, match="conflicting candidate snapshot"):
        store.merge((first, evidence_report(candidate("second"), "1.0")))
    with pytest.raises(ConfigurationError, match="conflicting projection"):
        store.merge((first, evidence_report(candidate("first"), "2.0")))


def test_report_merge_rejects_unknown_projection_declarations() -> None:
    report = incomplete_report().model_copy(
        update={
            "projection_evidence": (
                ProjectionEvidence(
                    declaration_id="unknown",
                    floors=(),
                    projected_requirements=(),
                    representable=False,
                ),
            )
        }
    )

    with pytest.raises(ConfigurationError, match="unknown projection declaration"):
        ReportStore().merge((report,))

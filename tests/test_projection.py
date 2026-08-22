from __future__ import annotations

from pathlib import Path
from typing import Literal

from packaging.requirements import Requirement
from pydantic import ValidationError
import pytest

from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticPassEvaluation,
    TestPass,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    Cell,
    Proposal,
    RequirementDeclaration,
    SourceIdentity,
    VersionPin,
)
from pf.schemas.report import (
    CellSuccess,
    CoordinateBoundary,
    CoordinateSuccess,
    FloorProjection,
    ProbeObservation,
    ProbePass,
    report_generation_id,
)
from pf.schemas.report import PackageFloorReportV1
from pf.snapshot import SnapshotBuilder


def successful_process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


def passing_evaluation(
    cell: Cell,
    version: str,
    *,
    snapshot_digest: str,
    resolution: Literal["highest", "exact-vector"] = "exact-vector",
    attempt: Attempt | None = None,
) -> PassEvaluation:
    vector = (VersionPin(name="idna", version=version),)
    owned_attempt = attempt or Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot_digest,
            cell=cell,
            requested_resolution=resolution,
            requested_managed_vector=(vector if resolution == "exact-vector" else None),
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )
    proposal = Proposal(
        proposal_id=f"idna={version}",
        attempt_id=owned_attempt.attempt_id,
        snapshot_digest=snapshot_digest,
        cell=cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="policy",
    )
    return PassEvaluation(
        proposal=proposal,
        static=StaticPassEvaluation(
            proposal=proposal,
            ty=TyCheck(process=successful_process(), diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(),
        ),
        test=TestPass(process=successful_process()),
    )


def successful_cell(
    cell: Cell,
    floor: str,
    *,
    snapshot_digest: str,
) -> CellSuccess:
    vector = (VersionPin(name="idna", version=floor),)
    final_attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot_digest,
            cell=cell,
            requested_resolution="exact-vector",
            requested_managed_vector=vector,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )
    final_evaluation = passing_evaluation(
        cell,
        floor,
        snapshot_digest=snapshot_digest,
        attempt=final_attempt,
    )
    search = CoordinateSuccess(
        vector=vector,
        observations=(
            ProbeObservation(
                dependency="idna",
                candidate_version=floor,
                vector=vector,
                evidence=ProbePass(
                    attempt=final_attempt,
                    proposal_id=final_evaluation.proposal.proposal_id,
                    evaluation=final_evaluation,
                ),
            ),
        ),
        boundaries=(CoordinateBoundary(dependency="idna", floor=floor),),
        sweeps=1,
    )
    baseline_attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot_digest,
            cell=cell,
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )
    baseline = passing_evaluation(
        cell,
        "3.11",
        snapshot_digest=snapshot_digest,
        resolution="highest",
        attempt=baseline_attempt,
    )
    return CellSuccess(
        cell=cell,
        baseline_attempt=baseline_attempt,
        static_baseline=StaticBaseline(
            proposal=baseline.proposal,
            ty=baseline.static.ty,
            digest=ty_diagnostic_digest(baseline.static.ty.diagnostics),
        ),
        baseline=baseline,
        candidate_snapshots=(),
        static_search=search,
        final_vector=vector,
        final_evaluation=final_evaluation,
    )


def test_report_builder_projects_exact_floor_and_preserves_constraints(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna>2,!=2.5,<4; python_version >= '3.10'"]

[dependency-groups]
test = ["pytest"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(tmp_path)
    report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot.identity,
        cell_results=(
            successful_cell(
                package.cells[0],
                "3.0",
                snapshot_digest=snapshot.identity.digest,
            ),
        ),
    )

    assert report.result.status == "complete"
    projected = Requirement(report.projection_evidence[0].projected_requirements[0])
    assert str(projected.specifier) == "!=2.5,<4,>=3.0"
    assert str(projected.marker) == 'python_version >= "3.10"'

    incomplete_coverage = report.model_dump(mode="python")
    incomplete_coverage["target_cells"] = (
        *report.target_cells,
        Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        ),
    )
    incomplete_coverage["report_generation_id"] = report_generation_id(
        generator=report.generator,
        package=report.package,
        source_snapshot=report.source_snapshot,
        policy_identity=report.policy_identity,
        requirement_declarations=report.requirement_declarations,
        target_cells=incomplete_coverage["target_cells"],
    )
    with pytest.raises(
        ValidationError, match="complete report requires exact cell coverage"
    ):
        PackageFloorReportV1.model_validate(incomplete_coverage)


def test_report_builder_represents_different_python_floors_with_markers(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna<4"]

[dependency-groups]
test = ["pytest"]

[tool.pf]
python = ["3.10", "3.11"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(tmp_path)
    floors = {"3.10": "2.0", "3.11": "3.0"}

    report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot.identity,
        cell_results=tuple(
            successful_cell(
                cell,
                floors[cell.python_minor],
                snapshot_digest=snapshot.identity.digest,
            )
            for cell in package.cells
        ),
    )

    assert report.result.status == "complete"
    projected = tuple(
        Requirement(raw) for raw in report.projection_evidence[0].projected_requirements
    )
    assert {str(requirement.specifier) for requirement in projected} == {
        "<4,>=2.0",
        "<4,>=3.0",
    }
    assert {str(requirement.marker) for requirement in projected} == {
        'python_version == "3.10"',
        'python_version == "3.11"',
    }


def test_merge_recomputes_projection_after_partial_host_reports_cover_all_cells(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna<4"]

[dependency-groups]
test = []

[tool.pf]
python = ["3.10", "3.11"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(tmp_path)
    builder = PackageReportBuilder()
    first = builder.build(
        package=package,
        source_snapshot=snapshot.identity,
        cell_results=(
            successful_cell(
                package.cells[0],
                "2.0",
                snapshot_digest=snapshot.identity.digest,
            ),
        ),
    )
    second = builder.build(
        package=package,
        source_snapshot=snapshot.identity,
        cell_results=(
            successful_cell(
                package.cells[1],
                "3.0",
                snapshot_digest=snapshot.identity.digest,
            ),
        ),
    )

    merged = ReportStore().merge((first, second))

    assert merged.result.status == "complete"
    assert merged.projection_evidence[0].representable is True
    assert len(merged.projection_evidence[0].projected_requirements) == 2


def test_projection_requires_exact_cell_set_equivalence() -> None:
    declaration = RequirementDeclaration(
        declaration_id="demo:base:idna",
        package="demo",
        pyproject_path="pyproject.toml",
        location="base",
        extra=None,
        name="idna",
        raw="idna",
        source=SourceIdentity(kind="registry"),
        kind="searchable",
        managed=True,
    )
    gnu = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
        active_declaration_ids=(declaration.declaration_id,),
    )
    musl = Cell(
        package="demo",
        target="x86_64-unknown-linux-musl",
        python_minor="3.10",
        extra_surface=(),
    )

    projection = PackageReportBuilder().project(
        declaration=declaration,
        target_cells=(gnu, musl),
        active_cells=(gnu,),
        floors=(FloorProjection(cell=gnu, version="1.5"),),
    )

    assert projection.representable is False
    assert projection.projected_requirements == ()

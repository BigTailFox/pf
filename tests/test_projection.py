from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from packaging.requirements import Requirement
import pytest

from pf.errors import ConfigurationError
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.resolution import environment_identity_digest
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticUnchangedEvaluation,
    TestPass,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    InterpreterIdentity,
    Proposal,
    RequirementDeclaration,
    SourceIdentity,
    VersionPin,
    candidate_snapshot_digest,
    cell_id,
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
from pf.snapshot import SnapshotBuilder


def successful_process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


def candidate_snapshot(
    cell: Cell,
    vector: tuple[VersionPin, ...],
) -> tuple[CandidateSnapshot, ...]:
    pin = vector[0]
    source = SourceIdentity(kind="registry")
    candidates = (
        Candidate(
            version=pin.version,
            series_key=pin.version,
            artifact=AvailableArtifact(
                filename=f"{pin.name}-{pin.version}.whl",
                kind="wheel",
                content_hash=f"sha256:{'a' * 64}",
                locator=f"https://files.example/{pin.name}-{pin.version}.whl",
            ),
        ),
    )
    representatives = ((pin.version, pin.version),)
    return (
        CandidateSnapshot(
            dependency=pin.name,
            cell=cell,
            policy_identity="policy",
            source=source,
            candidates=candidates,
            series_representatives=representatives,
            digest=candidate_snapshot_digest(
                dependency=pin.name,
                cell=cell,
                policy_identity="policy",
                source=source,
                candidates=candidates,
                series_representatives=representatives,
            ),
        ),
    )


def report_attempt(
    *,
    cell: Cell,
    snapshot_digest: str,
    resolution: Literal["highest", "exact-vector"],
    vector: tuple[VersionPin, ...],
) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            identity_version="attempt-v2",
            source_snapshot_digest=snapshot_digest,
            cell=cell,
            requested_resolution=resolution,
            requested_managed_vector=(vector if resolution == "exact-vector" else None),
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
            resolution_context_digest="context",
            harness_policy_identity=(
                "harness-relaxation-v1"
                if resolution == "exact-vector"
                else "original-harness-v1"
            ),
            harness_baseline_digest=(
                "harness-baseline" if resolution == "exact-vector" else None
            ),
            selected_candidate_evidence_digest=(
                "selected-candidate" if resolution == "exact-vector" else None
            ),
        )
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
    owned_attempt = attempt or report_attempt(
        cell=cell,
        snapshot_digest=snapshot_digest,
        resolution=resolution,
        vector=vector,
    )
    project_digest = f"project-{cell_id(cell)}-{version}"
    environment_digest = f"environment-{cell_id(cell)}-{version}"
    proposal = Proposal(
        proposal_id=environment_identity_digest(
            project_plan_digest=project_digest,
            environment_plan_digest=environment_digest,
            graph=(),
        ),
        attempt_id=owned_attempt.attempt_id,
        snapshot_digest=snapshot_digest,
        cell=cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="policy",
        project_plan_digest=project_digest,
        environment_plan_digest=environment_digest,
        interpreter=InterpreterIdentity(
            implementation="cpython",
            version=f"{cell.python_minor}.11",
            abi=f"cpython-{cell.python_minor.replace('.', '')}-{cell.target}",
        ),
    )
    return PassEvaluation(
        proposal=proposal,
        static=StaticUnchangedEvaluation(
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
    final_attempt = report_attempt(
        cell=cell,
        snapshot_digest=snapshot_digest,
        resolution="exact-vector",
        vector=vector,
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
    baseline_attempt = report_attempt(
        cell=cell,
        snapshot_digest=snapshot_digest,
        resolution="highest",
        vector=vector,
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
        candidate_snapshots=candidate_snapshot(cell, vector),
        search=search,
        final_vector=vector,
        final_evaluation=final_evaluation,
    )


class TestReportProjection:
    def test_report_builder_projects_exact_floor_and_preserves_constraints(
        self,
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
        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
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

        extra_cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
            active_declaration_ids=(report.target_cells[0].active_declaration_ids),
        )
        target_cells = (*report.target_cells, extra_cell)
        path = tmp_path / "tampered-coverage.json"
        ReportStore().write(path, report)
        incomplete_coverage = json.loads(path.read_text(encoding="utf-8"))
        incomplete_coverage["inputs"]["target_cells"].append(
            {
                "cell_id": cell_id(extra_cell),
                "package": extra_cell.package,
                "target": extra_cell.target,
                "python_minor": extra_cell.python_minor,
                "extra_surface": list(extra_cell.extra_surface),
                "active_declaration_refs": list(extra_cell.active_declaration_ids),
            }
        )
        incomplete_coverage["identity"]["report_generation_id"] = report_generation_id(
            generator=report.generator,
            package=report.package,
            source_snapshot=report.source_snapshot,
            policy_identity=report.policy_identity,
            requirement_declarations=report.requirement_declarations,
            target_cells=target_cells,
        )
        path.write_text(json.dumps(incomplete_coverage), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="projection evidence mismatch"):
            ReportStore().read(path)

    def test_report_builder_represents_different_python_floors_with_markers(
        self,
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
        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
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
            Requirement(raw)
            for raw in report.projection_evidence[0].projected_requirements
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
        self,
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
        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
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
        merged_path = tmp_path / "merged.json"
        ReportStore().write(merged_path, merged)
        merged_document = json.loads(merged_path.read_text(encoding="utf-8"))
        assert len(merged_document["evidence"]["resolution_graphs"]) == 1
        assert (
            len(
                {
                    proposal["resolution_graph_ref"]
                    for proposal in merged_document["evidence"]["proposals"]
                }
            )
            == 1
        )

        replacement = builder.build(
            package=package,
            source_snapshot=snapshot.identity,
            cell_results=(
                successful_cell(
                    package.cells[0],
                    "2.5",
                    snapshot_digest=snapshot.identity.digest,
                ),
            ),
        )
        updated = ReportStore().update(merged, replacement)
        updated_path = tmp_path / "updated.json"
        ReportStore().write(updated_path, updated)
        updated_document = json.loads(updated_path.read_text(encoding="utf-8"))
        assert len(updated_document["evidence"]["resolution_graphs"]) == 1

    def test_projection_requires_exact_cell_set_equivalence(self) -> None:
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

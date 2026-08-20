from __future__ import annotations

from pathlib import Path

import tomli
import pytest

from pf.editor import ProjectEditor
from pf.errors import ConfigurationError
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder
from pf.schemas.evaluation import (
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticPassEvaluation,
    TestPass,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.project import Cell, Proposal, VersionPin
from pf.schemas.report import (
    CellSuccess,
    CoordinateBoundary,
    CoordinateSuccess,
    PackageFloorReportV1,
)
from pf.snapshot import SnapshotBuilder


def process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout_summary="",
        stderr_summary="",
        stdout_tail="",
        stderr_tail="",
    )


def evaluation(
    cell: Cell,
    version: str,
    *,
    snapshot_digest: str,
) -> PassEvaluation:
    proposal = Proposal(
        proposal_id=f"idna={version}",
        snapshot_digest=snapshot_digest,
        cell=cell,
        managed_vector=(VersionPin(name="idna", version=version),),
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="policy",
    )
    return PassEvaluation(
        proposal=proposal,
        static=StaticPassEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process(), diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(),
        ),
        test=TestPass(process=process()),
    )


def test_project_editor_preserves_toml_comments_and_is_idempotent(
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "demo"
version = "0.1.0" # keep version comment
dependencies = [
    "idna>2,!=2.5,<4", # verified by integration tests
    "click==8.1.8",
]

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
    cell = package.cells[0]
    vector = (VersionPin(name="idna", version="3.0"),)
    search = CoordinateSuccess(
        vector=vector,
        observations=(),
        boundaries=(CoordinateBoundary(dependency="idna", floor="3.0"),),
        sweeps=1,
    )
    baseline_evaluation = evaluation(
        cell,
        "3.11",
        snapshot_digest=snapshot.identity.digest,
    )
    result = CellSuccess(
        cell=cell,
        static_baseline=StaticBaseline(
            proposal=baseline_evaluation.proposal,
            ty=baseline_evaluation.static.ty,
            digest=ty_diagnostic_digest(baseline_evaluation.static.ty.diagnostics),
        ),
        baseline=baseline_evaluation,
        candidate_snapshots=(),
        static_search=search,
        final_vector=vector,
        final_evaluation=evaluation(
            cell,
            "3.0",
            snapshot_digest=snapshot.identity.digest,
        ),
    )
    report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot.identity,
        cell_results=(result,),
    )
    editor = ProjectEditor(snapshots=SnapshotBuilder())

    malicious_document = report.model_dump(mode="python")
    malicious_document["projection_evidence"][0]["projected_requirements"] = (
        "idna>=3.0",
    )
    malicious_report = PackageFloorReportV1.model_validate(malicious_document)
    before = pyproject.read_bytes()
    with pytest.raises(ConfigurationError, match="unauthorized projected requirement"):
        editor.apply(report=malicious_report, root=tmp_path)
    assert pyproject.read_bytes() == before

    first = editor.apply(report=report, root=tmp_path)
    after_first = pyproject.read_bytes()
    second = editor.apply(report=report, root=tmp_path)

    with pyproject.open("rb") as stream:
        document = tomli.load(stream)
    assert first.changed is True
    assert second.changed is False
    assert pyproject.read_bytes() == after_first
    assert document["project"]["dependencies"] == [
        "idna!=2.5,<4,>=3.0",
        "click==8.1.8",
    ]
    content = after_first.decode()
    assert "# verified by integration tests" in content
    assert "# keep version comment" in content
    assert '"click==8.1.8"' in content


def test_project_editor_applies_all_workspace_reports_against_one_snapshot(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.uv.workspace]
members = ["packages/*"]

[dependency-groups]
test = []

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    for name in ("alpha", "beta"):
        package_root = tmp_path / "packages" / name
        package_root.mkdir(parents=True)
        (package_root / "pyproject.toml").write_text(
            f"""
[project]
name = "{name}"
version = "0.1.0"
dependencies = ["idna<4"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
    project = ProjectLoader().load(root=tmp_path, package_selection=None)
    snapshot = SnapshotBuilder().build(tmp_path)
    reports = []
    for package in project.packages:
        cell = package.cells[0]
        vector = (VersionPin(name="idna", version="3.0"),)
        coordinate = CoordinateSuccess(
            vector=vector,
            observations=(),
            boundaries=(CoordinateBoundary(dependency="idna", floor="3.0"),),
            sweeps=1,
        )
        baseline_evaluation = evaluation(
            cell,
            "3.11",
            snapshot_digest=snapshot.identity.digest,
        )
        reports.append(
            PackageReportBuilder().build(
                package=package,
                source_snapshot=snapshot.identity,
                cell_results=(
                    CellSuccess(
                        cell=cell,
                        static_baseline=StaticBaseline(
                            proposal=baseline_evaluation.proposal,
                            ty=baseline_evaluation.static.ty,
                            digest=ty_diagnostic_digest(
                                baseline_evaluation.static.ty.diagnostics
                            ),
                        ),
                        baseline=baseline_evaluation,
                        candidate_snapshots=(),
                        static_search=coordinate,
                        final_vector=vector,
                        final_evaluation=evaluation(
                            cell,
                            "3.0",
                            snapshot_digest=snapshot.identity.digest,
                        ),
                    ),
                ),
            )
        )

    edits = ProjectEditor(snapshots=SnapshotBuilder()).apply_many(
        reports=tuple(reports),
        root=tmp_path,
    )

    assert [edit.changed for edit in edits] == [True, True]
    for name in ("alpha", "beta"):
        with (tmp_path / "packages" / name / "pyproject.toml").open("rb") as stream:
            assert tomli.load(stream)["project"]["dependencies"] == [
                "idna<4,>=3.0"
            ]

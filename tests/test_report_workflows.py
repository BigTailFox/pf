from __future__ import annotations

from pathlib import Path

import pytest

from pf.errors import ConfigurationError
from pf.project import ProjectLoader
from pf.policy import evaluation_policy_identity
from pf.report import ReportStore
from pf.schemas.config import ApplyRequest, MergeRequest, ReportRequest
from pf.schemas.evaluation import StatusEvent
from pf.schemas.project import SourceSnapshotIdentity
from pf.schemas.report import (
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProjectEditResult,
    report_generation_id,
)
from pf.workflow import (
    ApplyCommandWorkflow,
    ExplainCommandWorkflow,
    MergeCommandWorkflow,
)


def report(
    *,
    package_name: str = "demo",
    policy_identity: str = "policy",
) -> PackageFloorReportV1:
    generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
    package = PackageIdentity(
        name=package_name,
        pyproject_path="pyproject.toml",
    )
    snapshot = SourceSnapshotIdentity(digest="snapshot", entries=())
    return PackageFloorReportV1(
        report_generation_id=report_generation_id(
            generator=generator,
            package=package,
            source_snapshot=snapshot,
            policy_identity=policy_identity,
            requirement_declarations=(),
            target_cells=(),
        ),
        generator=generator,
        package=package,
        source_snapshot=snapshot,
        policy_identity=policy_identity,
        requirement_declarations=(),
        candidate_snapshots=(),
        target_cells=(),
        cell_results=(),
        projection_evidence=(),
        result=IncompleteReportResult(reasons=("MISSING_CELL",)),
    )


def test_explain_reads_existing_report_and_merge_writes_requested_output(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    store = ReportStore()
    source = tmp_path / "package-floor.json"
    store.write(source, report())
    before = source.read_bytes()

    explained = ExplainCommandWorkflow(
        projects=ProjectLoader(),
        reports=store,
    ).run(ReportRequest(root=tmp_path.as_posix(), package=None))
    output = tmp_path / "merged.json"
    merged = MergeCommandWorkflow(reports=store).run(
        MergeRequest(reports=(source.as_posix(),), output=output.as_posix())
    )

    assert explained == (report(),)
    assert source.read_bytes() == before
    assert store.read(output) == merged


def test_apply_workflow_validates_reports_then_edits_all_packages(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    store = ReportStore()
    package = (
        ProjectLoader()
        .load(
            root=tmp_path,
            package_selection=None,
        )
        .packages[0]
    )
    current_report = report(policy_identity=evaluation_policy_identity(package.config))
    store.write(tmp_path / "package-floor.json", current_report)

    class Editor:
        def apply_many(
            self,
            *,
            reports: tuple[PackageFloorReportV1, ...],
            root: Path,
        ) -> tuple[ProjectEditResult, ...]:
            assert reports == (current_report,)
            assert root == tmp_path
            return (
                ProjectEditResult(
                    changed=False,
                    pyproject_path="pyproject.toml",
                    recovery_log_path=".pf/apply-recovery.json",
                ),
            )

    class Events:
        def __init__(self) -> None:
            self.items: list[object] = []

        def consume(self, event: object) -> None:
            self.items.append(event)

    events = Events()
    edits = ApplyCommandWorkflow(
        projects=ProjectLoader(),
        reports=store,
        editor=Editor(),
        events=events,
    ).run(ApplyRequest(root=tmp_path.as_posix()))

    assert edits[0].changed is False
    status = [event for event in events.items if isinstance(event, StatusEvent)]
    assert [event.message for event in status] == ["applying floors"]
    assert status[0].total == 1


def test_apply_workflow_rejects_report_from_an_obsolete_evaluation_policy(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    store = ReportStore()
    store.write(tmp_path / "package-floor.json", report())

    class NeverEditor:
        def apply_many(
            self,
            *,
            reports: tuple[PackageFloorReportV1, ...],
            root: Path,
        ) -> tuple[ProjectEditResult, ...]:
            raise AssertionError("policy drift must fail before editing")

    with pytest.raises(ConfigurationError, match="report policy identity mismatch"):
        ApplyCommandWorkflow(
            projects=ProjectLoader(),
            reports=store,
            editor=NeverEditor(),
        ).run(ApplyRequest(root=tmp_path.as_posix()))


def test_apply_workflow_rejects_report_for_another_package(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    store = ReportStore()
    mismatched = report(package_name="other")
    store.write(tmp_path / "package-floor.json", mismatched)

    class NeverEditor:
        def apply_many(
            self,
            *,
            reports: tuple[PackageFloorReportV1, ...],
            root: Path,
        ) -> tuple[ProjectEditResult, ...]:
            raise AssertionError("identity mismatch must fail before editing")

    with pytest.raises(ConfigurationError, match="report package identity mismatch"):
        ApplyCommandWorkflow(
            projects=ProjectLoader(),
            reports=store,
            editor=NeverEditor(),
        ).run(ApplyRequest(root=tmp_path.as_posix()))

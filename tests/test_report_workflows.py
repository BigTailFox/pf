from __future__ import annotations

from pathlib import Path

import pytest

from pf.authorization import ApplyAuthorizer
from pf.errors import ConfigurationError
from pf.project import ProjectLoader
from pf.project_discovery import ProjectDiscovery
from pf.report import PackageReportBuilder, ReportStore, ValidatedReport
from pf.runlog import RunLogStore
from pf.failure import FailurePolicy
from pf.schemas.config import (
    ApplyRequest,
    EffectiveConfig,
    MergeRequest,
    ReportRequest,
)
from pf.schemas.apply import (
    ApplyPresentationFacts,
    AuthorizedPackageApply,
    AuthorizedWorkspaceApply,
)
from pf.schemas.evaluation import (
    CellFailureScope,
    ProcessResult,
    StatusEvent,
    VerificationJournal,
    VerificationJournalEntry,
    VerificationPackagePolicy,
)
from pf.schemas.project import (
    PackagePlan,
    ProjectPlan,
    SourceSnapshotIdentity,
    source_snapshot_digest,
)
from pf.schemas.report import (
    PackageIdentity,
    ProjectEditResult,
)
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.workflow import (
    ApplyCommandWorkflow,
    ExplainCommandWorkflow,
    MergeCommandWorkflow,
)


def report(
    *,
    package_name: str = "demo",
    config: EffectiveConfig | None = None,
) -> ValidatedReport:
    package = PackagePlan(
        name=package_name,
        pyproject_path="pyproject.toml",
        config=config or EffectiveConfig(test_timeout=1),
        declarations=(),
        cells=(),
        source_routes=(),
    )
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest((), ()),
        entries=(),
        pyproject_identities=(),
    )
    return PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(),
    )


class TestReportWorkflows:
    def test_explain_reads_existing_report_and_merge_writes_requested_output(
        self,
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
            discovery=ProjectDiscovery(),
            reports=store,
        ).run(ReportRequest(root=tmp_path.as_posix()))
        output = tmp_path / "merged.json"
        merged = MergeCommandWorkflow(reports=store).run(
            MergeRequest(reports=(source.as_posix(),), output=output.as_posix())
        )

        assert explained == report()
        assert source.read_bytes() == before
        assert store.read(output) == merged

    def test_explain_discovers_reports_without_environment_planning(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=9"\n',
            encoding="utf-8",
        )
        store = ReportStore()
        store.write(tmp_path / "package-floor.json", report())

        explained = ExplainCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=store,
        ).run(ReportRequest(root=tmp_path.as_posix()))

        assert explained == report()

    def test_apply_workflow_validates_report_then_edits_the_target(
        self,
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
            )
            .target
        )
        current_report = report(config=package.config)
        store.write(tmp_path / "package-floor.json", current_report)

        class Editor:
            def apply(
                self,
                *,
                authorization: AuthorizedWorkspaceApply,
                root: Path,
            ) -> ProjectEditResult:
                assert authorization.mode == "DEFAULT"
                assert root == tmp_path
                return ProjectEditResult(
                    changed=False,
                    pyproject_path="pyproject.toml",
                    recovery_log_path=".pf/apply-recovery.json",
                )

        class Authorizer:
            def authorize(
                self,
                *,
                report: ValidatedReport,
                project: ProjectPlan,
                current_snapshot: SourceSnapshot,
                force: bool,
            ) -> AuthorizedWorkspaceApply:
                assert report == current_report
                assert project.target == package
                assert force is False
                return AuthorizedWorkspaceApply(
                    mode="DEFAULT",
                    expected_snapshot=current_snapshot.identity,
                    owned_pyproject_paths=("pyproject.toml",),
                    package_apply=AuthorizedPackageApply(
                        package=PackageIdentity(
                            name=package.name,
                            pyproject_path=package.pyproject_path,
                        ),
                        scope="DECLARED_MATRIX",
                        declared_platforms=(),
                        selected_selectors=(),
                        preserved_selectors=(),
                        dependency_state="NOOP",
                        observed_cells=0,
                        authorized_edits=(),
                    ),
                    presentation_facts=ApplyPresentationFacts(
                        observed_cells=0,
                        selected_selectors=(),
                        preserved_selectors=(),
                    ),
                )

        class Events:
            def __init__(self) -> None:
                self.items: list[object] = []

            def consume(self, event: object) -> None:
                self.items.append(event)

        events = Events()
        result = ApplyCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            reports=store,
            authorizer=Authorizer(),
            editor=Editor(),
            events=events,
        ).run(ApplyRequest(root=tmp_path.as_posix()))

        assert result.edit.changed is False
        status = [event for event in events.items if isinstance(event, StatusEvent)]
        assert [event.message for event in status] == ["applying floors"]
        assert status[0].total == 1

    def test_apply_workflow_rejects_report_from_an_obsolete_evaluation_policy(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        store = ReportStore()
        store.write(tmp_path / "package-floor.json", report())

        class NeverEditor:
            def apply(
                self,
                *,
                authorization: AuthorizedWorkspaceApply,
                root: Path,
            ) -> ProjectEditResult:
                raise AssertionError("policy drift must fail before editing")

        with pytest.raises(
            ConfigurationError, match="report evaluation policy mismatch"
        ):
            ApplyCommandWorkflow(
                projects=ProjectLoader(),
                snapshots=SnapshotBuilder.without_processes(),
                reports=store,
                authorizer=ApplyAuthorizer(),
                editor=NeverEditor(),
            ).run(ApplyRequest(root=tmp_path.as_posix()))

    def test_apply_workflow_rejects_report_for_another_package(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        store = ReportStore()
        mismatched = report(package_name="other")
        store.write(tmp_path / "package-floor.json", mismatched)

        class NeverEditor:
            def apply(
                self,
                *,
                authorization: AuthorizedWorkspaceApply,
                root: Path,
            ) -> ProjectEditResult:
                raise AssertionError("identity mismatch must fail before editing")

        with pytest.raises(
            ConfigurationError, match="report package identity mismatch"
        ):
            ApplyCommandWorkflow(
                projects=ProjectLoader(),
                snapshots=SnapshotBuilder.without_processes(),
                reports=store,
                authorizer=ApplyAuthorizer(),
                editor=NeverEditor(),
            ).run(ApplyRequest(root=tmp_path.as_posix()))

    def test_explain_does_not_treat_a_verification_journal_as_a_report(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

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
        project = ProjectLoader().load(root=tmp_path)
        cell = project.target.cells[0]
        failure = FailurePolicy().classify(
            scope=CellFailureScope(
                package="demo",
                cell=cell,
                source_snapshot_digest="snapshot",
                evaluation_policy_identity="policy",
            ),
            cause="TIMEOUT",
            stage="scheduler-deadline",
            process=ProcessResult(
                exit_code=None,
                signal=9,
                duration_seconds=0,
                stdout="",
                stderr="",
                timed_out=True,
            ),
        )
        logs = RunLogStore(root=tmp_path, run_id="check-run")
        journal_path = logs.write_journal(
            VerificationJournal(
                run_id="check-run",
                command="check",
                source_snapshot_digest="snapshot",
                package_policies=(
                    VerificationPackagePolicy(
                        package="demo",
                        evaluation_policy_identity="policy",
                    ),
                ),
                entries=(
                    VerificationJournalEntry(
                        package="demo",
                        cell=cell,
                        role="declaration",
                        failure=failure,
                    ),
                ),
            )
        )

        with pytest.raises(ConfigurationError, match="cannot read report"):
            ExplainCommandWorkflow(
                discovery=ProjectDiscovery(),
                reports=ReportStore(),
            ).run(ReportRequest(root=tmp_path.as_posix()))

        (tmp_path / "package-floor.json").write_bytes(journal_path.read_bytes())
        with pytest.raises(ConfigurationError, match="unsupported report schema"):
            ExplainCommandWorkflow(
                discovery=ProjectDiscovery(),
                reports=ReportStore(),
            ).run(ReportRequest(root=tmp_path.as_posix()))

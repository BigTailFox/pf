from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from pf.authorization import ApplyAuthorizer
from pf.errors import (
    ConfigurationError,
    ExplainReportError,
    MergeCompatibilityError,
    MergeInputError,
    MergeOutputError,
)
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
    WorkspacePackage,
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
    SourcePlan,
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
        source_plan=SourcePlan.for_package(package, "SEARCH"),
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
        assert store.read(output) == merged.report
        assert merged.input_paths == (source.as_posix(),)
        assert merged.output_path == output.as_posix()

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
        assert result.package == "demo"
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

        with pytest.raises(ExplainReportError) as missing:
            ExplainCommandWorkflow(
                discovery=ProjectDiscovery(),
                reports=ReportStore(),
            ).run(ReportRequest(root=tmp_path.as_posix()))
        assert missing.value.report_path == "package-floor.json"
        assert missing.value.reason == "report is unavailable"
        assert missing.value.recovery_command == "pf search"

        (tmp_path / "package-floor.json").write_bytes(journal_path.read_bytes())
        with pytest.raises(ExplainReportError) as invalid:
            ExplainCommandWorkflow(
                discovery=ProjectDiscovery(),
                reports=ReportStore(),
            ).run(ReportRequest(root=tmp_path.as_posix()))
        assert invalid.value.report_path == "package-floor.json"
        assert invalid.value.reason == "report is unreadable or invalid"
        assert invalid.value.recovery_command is None

    def test_explain_missing_workspace_report_echoes_the_explicit_selector(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "packages/demo").mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["packages/demo"]\n',
            encoding="utf-8",
        )
        (tmp_path / "packages/demo/pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        with pytest.raises(ExplainReportError) as caught:
            ExplainCommandWorkflow(
                discovery=ProjectDiscovery(),
                reports=ReportStore(),
            ).run(
                ReportRequest(
                    root=tmp_path.as_posix(),
                    selector=WorkspacePackage(canonical_name="demo"),
                )
            )

        assert caught.value.report_path == "packages/demo/package-floor.json"
        assert caught.value.recovery_command == "pf search --package demo"

    def test_explain_ignores_unselected_planning_only_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        selected = tmp_path / "packages" / "selected"
        broken = tmp_path / "packages" / "broken"
        selected.mkdir(parents=True)
        broken.mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
            '[tool.uv.sources]\noutside = { path = "../outside" }\n',
            encoding="utf-8",
        )
        (selected / "pyproject.toml").write_text(
            '[project]\nname = "selected"\nversion = "1"\n',
            encoding="utf-8",
        )
        (broken / "pyproject.toml").write_text(
            '[project]\nname = "broken"\nversion = 1\n',
            encoding="utf-8",
        )

        with pytest.raises(ExplainReportError) as caught:
            ExplainCommandWorkflow(
                discovery=ProjectDiscovery(),
                reports=ReportStore(),
            ).run(
                ReportRequest(
                    root=tmp_path.as_posix(),
                    selector=WorkspacePackage(canonical_name="selected"),
                )
            )

        assert caught.value.reason == "report is unavailable"

    def test_merge_stops_at_the_first_invalid_input_and_keeps_all_request_paths(
        self,
    ) -> None:
        class FailingStore:
            def __init__(self) -> None:
                self.read_paths: list[Path] = []

            def read(self, path: Path) -> ValidatedReport:
                self.read_paths.append(path)
                raise ConfigurationError("cannot read report")

        store = FailingStore()
        request = MergeRequest(
            reports=("missing.json", "unread.json"),
            output="merged.json",
        )

        with pytest.raises(MergeInputError) as caught:
            MergeCommandWorkflow(reports=cast(ReportStore, store)).run(request)

        assert store.read_paths == [Path("missing.json")]
        assert caught.value.input_paths == request.reports
        assert caught.value.failed_input_path == "missing.json"
        assert caught.value.output_path == "merged.json"

    def test_merge_maps_compatibility_and_output_failures_to_typed_stages(
        self,
        tmp_path: Path,
    ) -> None:
        store = ReportStore()
        first = tmp_path / "first.json"
        second = tmp_path / "second.json"
        store.write(first, report(package_name="first"))
        store.write(second, report(package_name="second"))
        request = MergeRequest(
            reports=(first.as_posix(), second.as_posix()),
            output=(tmp_path / "merged.json").as_posix(),
        )

        with pytest.raises(MergeCompatibilityError) as incompatible:
            MergeCommandWorkflow(reports=store).run(request)
        assert incompatible.value.input_paths == request.reports
        assert incompatible.value.detail == "report generation identity mismatch"

        class OutputFailingStore(ReportStore):
            def write(self, path: Path, report: ValidatedReport) -> None:
                raise OSError("read-only output")

        output_store = OutputFailingStore()
        valid = tmp_path / "valid.json"
        ReportStore().write(valid, report())
        output_request = MergeRequest(
            reports=(valid.as_posix(),),
            output=(tmp_path / "blocked/merged.json").as_posix(),
        )

        with pytest.raises(MergeOutputError) as output:
            MergeCommandWorkflow(reports=output_store).run(output_request)
        assert output.value.input_paths == output_request.reports
        assert output.value.output_path == output_request.output

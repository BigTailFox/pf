from __future__ import annotations

from pathlib import Path
import sys

import pytest

from pf.errors import ConfigurationError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore, ReportUpdate, ValidatedReport
from pf.runlog import RunLogStore
from pf.schemas.config import SearchRequest
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    CellCompletedEvent,
    CellFailureScope,
    CellMatrixEvent,
    FailureDetail,
    FailureEvaluationRuntimeRun,
    IndeterminateEvaluation,
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
    RuntimeEvaluationRun,
    StaticUnchangedEvaluation,
    TimedOut,
    TyCheck,
    VerifierDiagnostics,
    VerifierIndeterminate,
)
from pf.schemas.project import (
    Cell,
    PackagePlan,
    Proposal,
    SourcePlan,
    selected_candidate_evidence_digest,
)
from pf.schemas.report import CellIndeterminate
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.verification import VerificationRunner
from pf.workflow import SearchCommandWorkflow


class FailedSearch:
    def __init__(self, process: ProcessResult | None = None) -> None:
        self.cells: list[Cell] = []
        self.process = process

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> CellIndeterminate:
        self.cells.append(cell)
        failure = FailurePolicy().classify(
            scope=CellFailureScope(
                package=package.name,
                cell=cell,
                source_snapshot_digest=snapshot.identity.digest,
                evaluation_policy_identity=evaluation_policy_identity(package.config),
            ),
            cause="TIMEOUT",
            stage=f"evaluation-{len(self.cells)}",
            process=self.process,
            detail=(
                None
                if self.process is not None
                else FailureDetail(code="deadline", message="deadline expired")
            ),
        )
        return CellIndeterminate(
            cell=cell,
            phase=f"evaluation-{len(self.cells)}",
            failure_id=failure.failure_id,
            failure_records=(failure,),
        )


class SourceDriftingSearch(FailedSearch):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> CellIndeterminate:
        (self._root / "new-source.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )
        return super().search(
            package=package, cell=cell, snapshot=snapshot, source_plan=source_plan
        )


class TimedOutVerifierSearch:
    def __init__(self, process: ProcessResult) -> None:
        self.process = process

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> CellIndeterminate:
        policy = evaluation_policy_identity(package.config)
        attempt = Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.identity.digest,
                cell=cell,
                requested_resolution="exact-vector",
                requested_managed_vector=(),
                active_declaration_ids=(),
                source_plan_identity=source_plan.identity,
                evaluation_policy_identity=policy,
                resolution_context_digest="context",
                harness_policy_identity="harness-relaxation-v1",
                harness_baseline_digest="baseline",
                selected_candidate_evidence_digest=(
                    selected_candidate_evidence_digest(())
                ),
            )
        )
        proposal = Proposal(
            proposal_id="timed-out-proposal",
            attempt_id=attempt.attempt_id,
            snapshot_digest=snapshot.identity.digest,
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity=policy,
        )
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=TyCheck(
                process=ProcessResult(exit_code=0, duration_seconds=0.1),
                diagnostics=(),
            ),
            baseline_digest="baseline",
        )
        evaluation = IndeterminateEvaluation(
            proposal=proposal,
            cause="TIMEOUT",
            verifier=VerifierIndeterminate(
                terminal=TimedOut(),
                reason="process-timed-out",
            ),
            static=static,
        )
        failure = FailurePolicy().record_evaluation(
            AttemptFailureScope(attempt=attempt),
            evaluation,
        )
        assert failure is not None
        runtime = RuntimeEvaluationRun(
            evaluation=evaluation,
            diagnostics=VerifierDiagnostics(process=self.process),
        )
        return CellIndeterminate(
            cell=cell,
            phase="test",
            failure_id=failure.failure_id,
            failure_records=(failure,),
            failure_runtime_runs=(
                FailureEvaluationRuntimeRun(
                    failure_id=failure.failure_id,
                    runtime=runtime,
                ),
            ),
        )


class UnavailableBaselineSearch:
    def __init__(self, process: ProcessTerminalUnavailable) -> None:
        self.process = process

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> BaselineIndeterminate:
        attempt = Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.identity.digest,
                cell=cell,
                requested_resolution="highest",
                requested_managed_vector=None,
                active_declaration_ids=cell.active_declaration_ids,
                source_plan_identity=source_plan.identity,
                evaluation_policy_identity=evaluation_policy_identity(package.config),
                resolution_context_digest="context",
                harness_policy_identity="original-harness-v1",
            )
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TOOL_FAILURE",
            stage="resolve-project",
            process=self.process,
        )
        return BaselineIndeterminate(
            attempt=attempt,
            failure=failure,
            failure_process=self.process,
        )


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def consume(self, event: object) -> None:
        self.items.append(event)


class TestSearchWorkflow:
    @pytest.mark.parametrize(
        ("failure_stage", "expected_closed"),
        (("operation", 1), ("report", 2)),
    )
    def test_search_closes_snapshot_when_run_or_report_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        failure_stage: str,
        expected_closed: int,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [dependency-groups]
    test = []

    [tool.pf]
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        closed: list[SourceSnapshot] = []
        close_snapshot = SourceSnapshot.close

        def close(snapshot: SourceSnapshot) -> None:
            closed.append(snapshot)
            close_snapshot(snapshot)

        monkeypatch.setattr(SourceSnapshot, "close", close)

        class CrashingSearch:
            def search(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
                source_plan: SourcePlan,
            ) -> CellIndeterminate:
                raise RuntimeError("operation failed")

        class FailingReportStore(ReportStore):
            def update_path(
                self,
                path: Path,
                replacement: ValidatedReport,
            ) -> ReportUpdate:
                raise RuntimeError("report failed")

        coordinator = CrashingSearch() if failure_stage == "operation" else FailedSearch()
        reports = FailingReportStore() if failure_stage == "report" else ReportStore()
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=coordinator,
            verification=VerificationRunner(
                events=Events(),
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            reports=reports,
            report_builder=PackageReportBuilder(),
            events=Events(),
        )

        with pytest.raises(RuntimeError, match=failure_stage):
            workflow.run(SearchRequest(root=tmp_path.as_posix()))

        assert len(closed) == expected_closed

    def test_search_does_not_publish_a_report_if_source_drifts_during_run(
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
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=SourceDriftingSearch(tmp_path),
            verification=VerificationRunner(
                events=Events(),
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=Events(),
        )

        with pytest.raises(
            ConfigurationError,
            match="project source snapshot drifted during search",
        ):
            workflow.run(SearchRequest(root=tmp_path.as_posix()))

        assert not (tmp_path / "package-floor.json").exists()

    def test_search_workflow_schedules_cells_and_writes_incomplete_report(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna"]

    [dependency-groups]
    test = ["pytest"]

    [tool.pf]
    pythons = ["3.10", "3.11"]
    platforms = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        events = Events()
        store = ReportStore()
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=FailedSearch(),
            verification=VerificationRunner(
                events=events,
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            reports=store,
            report_builder=PackageReportBuilder(),
            events=events,
        )

        output = workflow.run(
            SearchRequest(
                root=tmp_path.as_posix(),
                max_cells=2,
                max_duration_seconds=None,
            )
        )

        assert output.result.status == "incomplete"
        assert output.result.reasons == (
            "INDETERMINATE",
            "UNREPRESENTABLE_PROJECTION",
        )
        completions = [
            event for event in events.items if isinstance(event, CellCompletedEvent)
        ]
        assert len(completions) == 2
        matrix = next(
            event for event in events.items if isinstance(event, CellMatrixEvent)
        )
        assert [(cell.python_minor, cell.target) for cell in matrix.cells] == [
            ("3.10", "x86_64-unknown-linux-gnu"),
            ("3.11", "x86_64-unknown-linux-gnu"),
        ]
        assert store.read(tmp_path / "package-floor.json") == output

        repeated = workflow.run(
            SearchRequest(
                root=tmp_path.as_posix(),
                max_cells=2,
                max_duration_seconds=None,
            )
        )
        assert repeated.source_snapshot == output.source_snapshot
        assert repeated.cell_results != output.cell_results

        (tmp_path / "new-source.py").write_text("VALUE = 1\n", encoding="utf-8")
        refreshed = workflow.run(
            SearchRequest(
                root=tmp_path.as_posix(),
                max_cells=2,
                max_duration_seconds=None,
            )
        )
        assert refreshed.source_snapshot != output.source_snapshot
        assert store.read(tmp_path / "package-floor.json") == refreshed

    def test_search_workflow_indexes_failure_logs_after_writing_the_report(
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
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        logs = RunLogStore(root=tmp_path, run_id="search-run")
        process = ProcessResult(
            exit_code=124,
            signal=None,
            duration_seconds=1,
            stdout="",
            stderr="",
            timed_out=True,
        )
        logs.record(
            1,
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=1,
            ),
            process,
        )
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=FailedSearch(process),
            verification=VerificationRunner(
                events=Events(),
                logs=logs,
                host_target="x86_64-unknown-linux-gnu",
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=Events(),
            associations=logs,
        )

        report = workflow.run(SearchRequest(root=tmp_path.as_posix()))
        failure = report.failure_records[0]

        assert logs.lookup(report.report_generation_id, failure.failure_id) == Path(
            ".pf/logs/search-run/process-0001.log"
        )

        replacement = workflow.run(SearchRequest(root=tmp_path.as_posix()))
        replacement_failure = replacement.failure_records[0]

        assert replacement_failure.failure_id != failure.failure_id
        assert logs.lookup(report.report_generation_id, failure.failure_id) is None
        assert logs.lookup(
            replacement.report_generation_id,
            replacement_failure.failure_id,
        ) == Path(".pf/logs/search-run/process-0001.log")
        journal = logs.read_latest_journal("demo")
        assert journal is not None
        assert journal.command == "search"
        assert journal.run_id == "search-run"
        assert {entry.failure.failure_id for entry in journal.entries} == {
            replacement_failure.failure_id
        }

    def test_search_workflow_associates_runtime_only_verifier_process(
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
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        logs = RunLogStore(root=tmp_path, run_id="runtime-search")
        process = ProcessResult(
            signal=9,
            duration_seconds=1,
            timed_out=True,
        )
        logs.record(
            1,
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=1,
            ),
            process,
        )
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=TimedOutVerifierSearch(process),
            verification=VerificationRunner(
                events=Events(),
                logs=logs,
                host_target="x86_64-unknown-linux-gnu",
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=Events(),
            associations=logs,
        )

        report = workflow.run(SearchRequest(root=tmp_path.as_posix()))
        failure = report.failure_records[0]
        expected = Path(".pf/logs/runtime-search/process-0001.log")

        assert failure.authority.kind == "configured-verifier"
        assert logs.lookup(report.report_generation_id, failure.failure_id) == expected
        assert logs.lookup_run("runtime-search", failure.failure_id) == expected
        journal = logs.read_latest_journal("demo")
        assert journal is not None
        assert journal.command == "search"
        assert journal.run_id == "runtime-search"
        assert {entry.failure.failure_id for entry in journal.entries} == {
            failure.failure_id
        }

    def test_search_workflow_associates_unavailable_baseline_process(
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
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        logs = RunLogStore(root=tmp_path, run_id="unavailable-baseline")
        process = ProcessTerminalUnavailable(
            duration_seconds=0.2,
            detail="runner returned no terminal status",
        )
        logs.record(
            1,
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=1,
            ),
            process,
        )
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=UnavailableBaselineSearch(process),
            verification=VerificationRunner(
                events=Events(),
                logs=logs,
                host_target="x86_64-unknown-linux-gnu",
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=Events(),
            associations=logs,
        )

        report = workflow.run(SearchRequest(root=tmp_path.as_posix()))
        failure = report.failure_records[0]
        expected = Path(".pf/logs/unavailable-baseline/process-0001.log")

        assert failure.process is None
        assert failure.detail is not None
        assert failure.detail.code == "terminal-unavailable"
        assert logs.lookup(report.report_generation_id, failure.failure_id) == expected
        assert logs.lookup_run("unavailable-baseline", failure.failure_id) == expected

    def test_search_replaces_a_report_from_an_incompatible_source_generation(
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
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        store = ReportStore()
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=FailedSearch(),
            verification=VerificationRunner(
                events=Events(),
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            reports=store,
            report_builder=PackageReportBuilder(),
            events=Events(),
        )
        request = SearchRequest(root=tmp_path.as_posix())
        current = workflow.run(request)
        report_path = tmp_path / "package-floor.json"
        (tmp_path / "README.md").write_text(
            "new source generation\n",
            encoding="utf-8",
        )

        refreshed = workflow.run(request)

        assert refreshed.policy_identity == current.policy_identity
        assert refreshed.report_generation_id != current.report_generation_id
        assert store.read(report_path) == refreshed

    def test_search_workflow_never_executes_a_non_host_target(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [dependency-groups]
    test = []

    [tool.pf]
    pythons = ["3.10"]
    platforms = ["aarch64-apple-darwin", "x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        coordinator = FailedSearch()
        events = Events()
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=coordinator,
            verification=VerificationRunner(
                events=events,
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=events,
        )

        reports = workflow.run(SearchRequest(root=tmp_path.as_posix()))

        assert [cell.target for cell in coordinator.cells] == [
            "x86_64-unknown-linux-gnu"
        ]
        matrix = next(
            event for event in events.items if isinstance(event, CellMatrixEvent)
        )
        assert [cell.target for cell in matrix.cells] == ["x86_64-unknown-linux-gnu"]
        assert reports.result.status == "incomplete"
        assert "MISSING_CELL" in reports.result.reasons

    def test_search_empty_host_set_rejects_missing_contract_before_snapshot(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        coordinator = FailedSearch()
        events = Events()
        monkeypatch.setattr(
            SnapshotBuilder,
            "build",
            lambda *_args, **_kwargs: pytest.fail(
                "contract admission must precede snapshot construction"
            ),
        )
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=coordinator,
            verification=VerificationRunner(
                events=events,
                logs=None,
                host_target="aarch64-apple-darwin",
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=events,
        )

        with pytest.raises(ConfigurationError, match="test-command"):
            workflow.run(SearchRequest(root=tmp_path.as_posix()))

        assert coordinator.cells == []
        assert not (tmp_path / "package-floor.json").exists()

from __future__ import annotations

from pathlib import Path
from io import StringIO
import re
from typing import Literal

import pytest
from rich.console import Console

from pf.errors import DiagnoseNotFoundError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.project import ProjectLoader
from pf.project_discovery import ProjectDiscovery
from pf.report import PackageReportBuilder, ReportStore
from pf.resolution import environment_identity_digest
from pf.runlog import RunLogStore
from pf.schemas.config import DiagnoseRequest, WorkspacePackage
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    CellFailureScope,
    FailureCause,
    FailureDetail,
    FailureRecord,
    NormalExit,
    PassEvaluation,
    ProcessResult,
    ProcessSpec,
    StaticBaseline,
    StaticUnchangedEvaluation,
    TyCheck,
    VerifierPass,
    VerifierRejected,
    VerifierRejectedEvaluation,
    VerificationJournal,
    VerificationJournalEntry,
    VerificationPackagePolicy,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    InterpreterIdentity,
    Proposal,
    SelectedCandidate,
    SourceIdentity,
    SourcePlan,
    VersionPin,
    candidate_snapshot_digest,
    selected_candidate_evidence_digest,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellSuccess,
    CoordinateBoundary,
    CoordinateSuccess,
    ProbeObservation,
    ProbePass,
    ProbeRejection,
)
from pf.snapshot import SnapshotBuilder
from pf.terminal import TerminalPresenter
from pf.workflow import DiagnoseCommandWorkflow, FailureDiagnosis


class RecordingLogLocator:
    def __init__(self, path: Path | None = None) -> None:
        self.lookups: list[tuple[str, str]] = []
        self.journal_reads: list[str] = []
        self.path = path

    def lookup(self, report_generation_id: str, failure_id: str) -> Path | None:
        self.lookups.append((report_generation_id, failure_id))
        return self.path

    def lookup_run(self, run_id: str, failure_id: str) -> Path | None:
        return None

    def read_latest_journal(self, package: str) -> VerificationJournal | None:
        self.journal_reads.append(package)
        return None

    def read_tail(self, path: Path) -> tuple[str, ...]:
        return ()


def candidate_snapshot(
    cell: Cell,
    vector: tuple[VersionPin, ...],
    policy_identity: str,
    plan_identity: str,
    source: SourceIdentity,
) -> tuple[CandidateSnapshot, ...]:
    pin = vector[0]
    candidates = tuple(
        Candidate(
            version=item.version,
            series_key=item.version,
            artifact=AvailableArtifact(
                filename=f"{item.name}-{item.version}.whl",
                kind="wheel",
                content_hash=f"sha256:{'a' * 64}",
                locator=f"https://files.example/{item.name}-{item.version}.whl",
            ),
        )
        for item in vector
    )
    representatives = tuple((item.version, item.version) for item in vector)
    return (
        CandidateSnapshot(
            dependency=pin.name,
            cell=cell,
            policy_identity=policy_identity,
            source_plan_identity=plan_identity,
            source=source,
            candidates=candidates,
            series_representatives=representatives,
            digest=candidate_snapshot_digest(
                dependency=pin.name,
                cell=cell,
                policy_identity=policy_identity,
                source_plan_identity=plan_identity,
                source=source,
                candidates=candidates,
                series_representatives=representatives,
            ),
        ),
    )


def _write_indeterminate_report(root: Path) -> tuple[str, str]:
    (root / "pyproject.toml").write_text(
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
    project = ProjectLoader().load(root=root)
    package = project.target
    snapshot = SnapshotBuilder.without_processes().build(root)
    try:
        cell = package.cells[0]
        failure = FailurePolicy().classify(
            scope=CellFailureScope(
                package=package.name,
                cell=cell,
                source_snapshot_digest=snapshot.identity.digest,
                evaluation_policy_identity=evaluation_policy_identity(package.config),
            ),
            cause="SOURCE_FAILURE",
            stage="candidate-discovery",
            process=None,
            detail=FailureDetail(
                code="candidate-discovery-failed",
                message="the configured index was unavailable",
            ),
        )
        report = PackageReportBuilder().build(
            package=package,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot.identity,
            cell_results=(
                CellIndeterminate(
                    cell=cell,
                    phase="candidate-discovery",
                    failure_id=failure.failure_id,
                    failure_records=(failure,),
                ),
            ),
        )
        ReportStore().write(root / "package-floor.json", report)
        return report.report_generation_id, failure.failure_id
    finally:
        snapshot.close()


def _write_managed_project(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna<4"]

[dependency-groups]
test = []

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
managed-deps = ["idna"]
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _attempt(
    *,
    cell: Cell,
    snapshot_digest: str,
    vector: tuple[VersionPin, ...] | None,
    policy_identity: str,
    requested_resolution: Literal["highest", "lowest-direct", "exact-vector"]
    | None = None,
    source_plan_identity_value: str = "sources",
) -> Attempt:
    resolution = requested_resolution or (
        "highest" if vector is None else "exact-vector"
    )
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot_digest,
            cell=cell,
            requested_resolution=resolution,
            requested_managed_vector=vector,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity=source_plan_identity_value,
            evaluation_policy_identity=policy_identity,
            resolution_context_digest="context",
            harness_policy_identity=(
                "original-harness-v1"
                if resolution == "highest"
                else "harness-relaxation-v1"
            ),
            harness_baseline_digest=(
                None if resolution == "highest" else "harness-baseline"
            ),
            selected_candidate_evidence_digest=(
                selected_candidate_evidence_digest(
                    tuple(
                        SelectedCandidate(
                            dependency=pin.name,
                            version=pin.version,
                            artifact=AvailableArtifact(
                                filename=f"{pin.name}-{pin.version}.whl",
                                kind="wheel",
                                content_hash=f"sha256:{'a' * 64}",
                                locator=(
                                    f"https://files.example/"
                                    f"{pin.name}-{pin.version}.whl"
                                ),
                            ),
                        )
                        for pin in (vector or ())
                    )
                )
                if resolution == "exact-vector"
                else None
            ),
        )
    )


def _pass_evaluation(
    *,
    attempt: Attempt,
    vector: tuple[VersionPin, ...],
    snapshot_digest: str,
    baseline_check: TyCheck,
    baseline_digest: str,
    policy_identity: str,
) -> PassEvaluation:
    project_digest = f"project-{attempt.attempt_id}"
    environment_digest = f"environment-{attempt.attempt_id}"
    proposal = Proposal(
        proposal_id=environment_identity_digest(
            project_plan_digest=project_digest,
            environment_plan_digest=environment_digest,
            graph=(),
        ),
        attempt_id=attempt.attempt_id,
        snapshot_digest=snapshot_digest,
        cell=attempt.identity.cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity=policy_identity,
        project_plan_digest=project_digest,
        environment_plan_digest=environment_digest,
        interpreter=InterpreterIdentity(
            implementation="cpython",
            version=f"{attempt.identity.cell.python_minor}.11",
            abi=(
                "cpython-"
                f"{attempt.identity.cell.python_minor.replace('.', '')}-"
                f"{attempt.identity.cell.target}"
            ),
        ),
    )
    return PassEvaluation(
        proposal=proposal,
        static=StaticUnchangedEvaluation(
            proposal=proposal,
            ty=baseline_check,
            baseline_digest=baseline_digest,
        ),
        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
    )


def _process(exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="tests failed" if exit_code else "",
    )


def _verifier_failure(attempt: Attempt, *, exit_code: int = 1) -> FailureRecord:
    return FailureRecord.from_verifier(
        scope=AttemptFailureScope(attempt=attempt),
        disposition="REJECTED",
        cause="VERIFIER_EXITED_NONZERO",
        stage="test",
        terminal=NormalExit(exit_code=exit_code),
    )


def _write_success_with_predecessor_report(
    root: Path,
) -> tuple[str, str, str]:
    _write_managed_project(root)
    project = ProjectLoader().load(root=root)
    package = project.target
    snapshot = SnapshotBuilder.without_processes().build(root)
    try:
        cell = package.cells[0]
        policy_identity = evaluation_policy_identity(package.config)
        plan_identity = SourcePlan.for_package(package, "SEARCH").identity
        source = next(
            route.search_source
            for route in package.source_routes
            if route.dependency == "idna"
        )
        baseline_vector = (VersionPin(name="idna", version="3.11"),)
        baseline_attempt = _attempt(
            cell=cell,
            snapshot_digest=snapshot.identity.digest,
            vector=None,
            policy_identity=policy_identity,
            source_plan_identity_value=plan_identity,
        )
        check = TyCheck(process=_process(), diagnostics=())
        digest = ty_diagnostic_digest(check.diagnostics)
        baseline_evaluation = _pass_evaluation(
            attempt=baseline_attempt,
            vector=baseline_vector,
            snapshot_digest=snapshot.identity.digest,
            baseline_check=check,
            baseline_digest=digest,
            policy_identity=policy_identity,
        )
        baseline = StaticBaseline(
            proposal=baseline_evaluation.proposal,
            ty=check,
            digest=digest,
        )

        rejected_vector = (VersionPin(name="idna", version="2.0"),)
        rejected_attempt = _attempt(
            cell=cell,
            snapshot_digest=snapshot.identity.digest,
            vector=rejected_vector,
            policy_identity=policy_identity,
            source_plan_identity_value=plan_identity,
        )
        rejected_pass = _pass_evaluation(
            attempt=rejected_attempt,
            vector=rejected_vector,
            snapshot_digest=snapshot.identity.digest,
            baseline_check=check,
            baseline_digest=digest,
            policy_identity=policy_identity,
        )
        failed_test = VerifierRejectedEvaluation(
            proposal=rejected_pass.proposal,
            static=rejected_pass.static,
            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        failure = _verifier_failure(rejected_attempt)

        final_vector = (VersionPin(name="idna", version="3.0"),)
        final_attempt = _attempt(
            cell=cell,
            snapshot_digest=snapshot.identity.digest,
            vector=final_vector,
            policy_identity=policy_identity,
            source_plan_identity_value=plan_identity,
        )
        final_evaluation = _pass_evaluation(
            attempt=final_attempt,
            vector=final_vector,
            snapshot_digest=snapshot.identity.digest,
            baseline_check=check,
            baseline_digest=digest,
            policy_identity=policy_identity,
        )
        search = CoordinateSuccess(
            vector=final_vector,
            observations=(
                ProbeObservation(
                    dependency="idna",
                    candidate_version="2.0",
                    vector=rejected_vector,
                    evidence=ProbeRejection(
                        attempt=rejected_attempt,
                        proposal_id=failed_test.proposal.proposal_id,
                        failure_id=failure.failure_id,
                        cause=failure.cause,
                        evaluation=failed_test,
                    ),
                ),
                ProbeObservation(
                    dependency="idna",
                    candidate_version="3.0",
                    vector=final_vector,
                    evidence=ProbePass(
                        attempt=final_attempt,
                        proposal_id=final_evaluation.proposal.proposal_id,
                        evaluation=final_evaluation,
                    ),
                ),
            ),
            boundaries=(
                CoordinateBoundary(
                    dependency="idna",
                    floor="3.0",
                    predecessor="2.0",
                    predecessor_failure_id=failure.failure_id,
                ),
            ),
            sweeps=1,
        )
        report = PackageReportBuilder().build(
            package=package,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot.identity,
            cell_results=(
                CellSuccess(
                    cell=cell,
                    baseline_attempt=baseline_attempt,
                    static_baseline=baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshot(
                        cell,
                        (rejected_vector[0], final_vector[0]),
                        policy_identity,
                        plan_identity,
                        source,
                    ),
                    search=search,
                    final_vector=final_vector,
                    final_evaluation=final_evaluation,
                    failure_records=(failure,),
                ),
            ),
        )
        ReportStore().write(root / "package-floor.json", report)
        return (
            report.report_generation_id,
            failure.failure_id,
            failed_test.proposal.proposal_id,
        )
    finally:
        snapshot.close()


def _diagnosis(
    *,
    failure: FailureRecord,
    package: str = "demo",
    log_path: Path | None = None,
) -> FailureDiagnosis:
    return FailureDiagnosis(
        report_generation_id="generation",
        package=package,
        failure=failure,
        proposal_id=None,
        boundary_role=None,
        log_path=log_path,
        source_path="package-floor.json",
    )


class TestDiagnoseWorkflow:
    @pytest.mark.parametrize(
        ("role", "command", "rejected", "expected"),
        (
            ("probe", "search", True, "This candidate was excluded from the search."),
            (
                "probe",
                "search",
                False,
                "Compatibility for this candidate is unknown, so this cell stopped.",
            ),
            (
                "baseline",
                "search",
                True,
                "The highest-version baseline did not pass, so the floor search did not start for this cell.",
            ),
            (
                "baseline",
                "smoke",
                False,
                "Compatibility of the highest-version resolution is unknown.",
            ),
            (
                "declaration-capture",
                "check",
                True,
                "A static baseline could not be captured from the current declarations, so declared lower bounds were not verified for this cell.",
            ),
            (
                "declaration",
                "check",
                False,
                "Compatibility of the declared lower bounds is unknown.",
            ),
        ),
    )
    def test_diagnose_role_impact_matches_the_offline_contract(
        self,
        role: Literal["probe", "baseline", "declaration-capture", "declaration"],
        command: Literal["smoke", "check", "search"],
        rejected: bool,
        expected: str,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = _attempt(
            cell=cell,
            snapshot_digest="snapshot",
            vector=(VersionPin(name="idna", version="2.0"),),
            policy_identity="policy",
        )
        failure = (
            FailureRecord.from_verifier(
                scope=AttemptFailureScope(attempt=attempt),
                disposition="REJECTED",
                cause="VERIFIER_EXITED_NONZERO",
                stage="test",
                terminal=NormalExit(exit_code=1),
            )
            if rejected
            else FailurePolicy().classify(
                scope=AttemptFailureScope(attempt=attempt),
                cause="TOOL_FAILURE",
                stage="test",
                process=_process(exit_code=1),
            )
        )

        view = TerminalPresenter.failure_presentation(
            failure,
            role=role,
            command=command,
        )

        assert view.impact == expected

    @pytest.mark.parametrize(
        ("cause", "expected_title", "expected_next"),
        (
            (
                "RESOLUTION_CONFLICT",
                "This version combination has conflicting dependency requirements and cannot be installed.",
                "Review the conflicting requirements, adjust project constraints if needed, then rerun PF.",
            ),
            (
                "BUILD_FAILURE",
                "This version combination could not be built.",
                "Inspect the build details and log; check build requirements, Python support, and available artifacts.",
            ),
            (
                "HARNESS_CONFLICT",
                "The test dependencies cannot be installed without changing the versions being checked.",
                "Adjust the configured test dependencies so they preserve the dependency graph under test.",
            ),
            (
                "RUNTIME_INTERFACE_MISSING",
                "A required runtime interface is missing from this version combination.",
                "Review the confirmed missing module or member before changing dependency constraints.",
            ),
            (
                "VERIFIER_EXITED_NONZERO",
                "The configured verifier rejected this version combination.",
                "Review the verifier diagnostics and log before changing code or dependency constraints.",
            ),
            (
                "SOURCE_FAILURE",
                "PF could not reach or read a configured package source.",
                "Check the index URL, network, credentials, and source availability, then rerun PF.",
            ),
            (
                "ENVIRONMENT_FAILURE",
                "The current Python or system environment cannot run this check.",
                "Verify the interpreter, platform support, permissions, and required system tools.",
            ),
            (
                "TOOL_FAILURE",
                "PF could not complete a verification tool operation reliably.",
                "Inspect the technical details and log; verify that the named tool can run in this environment.",
            ),
            (
                "TIMEOUT",
                "The operation timed out, so compatibility is unknown.",
                "Inspect the log and increase the relevant timeout only if the operation is expected to finish.",
            ),
            (
                "INTERNAL_INVARIANT",
                "PF detected an inconsistent verification result.",
                "Keep the failure ID and technical details when reporting the problem; do not trust this cell result.",
            ),
            (
                "NONDETERMINISTIC",
                "The same version combination produced conflicting results.",
                "Stabilize flaky tests or external inputs, then rerun the full search.",
            ),
        ),
    )
    def test_diagnose_preserves_the_d005_title_and_next_step(
        self,
        cause: FailureCause,
        expected_title: str,
        expected_next: str,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        if cause == "VERIFIER_EXITED_NONZERO":
            failure = FailureRecord.from_verifier(
                scope=AttemptFailureScope(
                    attempt=_attempt(
                        cell=cell,
                        snapshot_digest="snapshot",
                        vector=(VersionPin(name="idna", version="2.0"),),
                        policy_identity="policy",
                    )
                ),
                disposition="REJECTED",
                cause=cause,
                stage="test",
                terminal=NormalExit(exit_code=1),
            )
        else:
            failure = FailureRecord.from_facts(
                scope=CellFailureScope(
                    package="demo",
                    cell=cell,
                    source_snapshot_digest="snapshot",
                    evaluation_policy_identity="policy",
                ),
                disposition="INDETERMINATE",
                cause=cause,
                stage="operation",
                process=None,
                detail=FailureDetail(code="test-detail", message="test detail"),
            )

        view = TerminalPresenter.failure_presentation(failure)

        assert view.title == expected_title
        assert view.next_step == expected_next

    def test_diagnose_shows_last_three_nonempty_stderr_lines_from_the_log(
        self,
        tmp_path: Path,
    ) -> None:
        _write_managed_project(tmp_path)
        project = ProjectLoader().load(root=tmp_path)
        cell = project.target.cells[0]
        attempt = _attempt(
            cell=cell,
            snapshot_digest="snapshot",
            vector=None,
            policy_identity="policy",
            requested_resolution="lowest-direct",
        )
        process = _process(exit_code=1)
        failure = _verifier_failure(attempt)
        logs = RunLogStore(root=tmp_path, run_id="diagnose-tail")
        logs.record(
            1,
            ProcessSpec(
                argv=("pytest",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=None,
            ),
            process,
            stdout="stdout should not be selected\n",
            stderr=(
                "first\n\nsecond\n\x1b[31mthird\x1b[0m\nfourth [bold]literal[/bold]\n"
            ),
        )
        logs.write_journal(
            VerificationJournal(
                run_id="diagnose-tail",
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
                        attempt=attempt,
                        failure=failure,
                    ),
                ),
            )
        )
        logs.associate("journal:diagnose-tail", failure.failure_id, process)
        diagnosis = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=logs,
        ).run(
            DiagnoseRequest(
                root=tmp_path.as_posix(),
                selector=WorkspacePackage(canonical_name="demo"),
                failure_id=failure.failure_id,
            )
        )
        stdout = StringIO()

        TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
            root=tmp_path,
        ).render_diagnose(diagnosis)

        rendered = stdout.getvalue()
        normalized = " ".join(rendered.split())
        assert "output second third fourth [bold]literal[/bold]" in normalized
        assert "\x1b" not in rendered
        assert "first" not in rendered
        assert "stdout should not be selected" not in rendered
        assert ".pf/logs/diagnose-tail/process-0001.log" in rendered
        assert rendered.rstrip().endswith(
            f"✓  Diagnosis complete · {failure.failure_id}"
        )

    def test_diagnose_reads_portable_failure_facts_without_execution_capabilities(
        self,
        tmp_path: Path,
    ) -> None:
        generation, failure_id = _write_indeterminate_report(tmp_path)
        logs = RecordingLogLocator()
        workflow = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=logs,
        )

        result = workflow.run(
            DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id)
        )

        assert result.failure.failure_id == failure_id
        assert result.proposal_id is None
        assert result.boundary_role is None
        assert result.log_path is None
        assert result.source == "report"
        assert result.source_path == "package-floor.json"
        assert logs.lookups == [(generation, failure_id)]
        assert logs.journal_reads == []

    def test_diagnose_remains_offline_when_no_python_minor_can_be_planned(
        self,
        tmp_path: Path,
    ) -> None:
        _, failure_id = _write_indeterminate_report(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=9"\n',
            encoding="utf-8",
        )

        diagnosis = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=RecordingLogLocator(),
        ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id))

        assert diagnosis.failure.failure_id == failure_id

    def test_diagnose_ignores_planning_only_workspace_and_path_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        _, failure_id = _write_indeterminate_report(tmp_path)
        broken = tmp_path / "packages" / "broken"
        broken.mkdir(parents=True)
        with (tmp_path / "pyproject.toml").open("a", encoding="utf-8") as stream:
            stream.write(
                '\n[tool.uv.workspace]\nmembers = ["packages/*"]\n'
                '[tool.uv.sources]\noutside = { path = "../outside" }\n'
            )
        (broken / "pyproject.toml").write_text(
            '[project]\nname = "broken"\nversion = 1\n',
            encoding="utf-8",
        )

        diagnosis = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=RecordingLogLocator(),
        ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id))

        assert diagnosis.failure.failure_id == failure_id

    def test_diagnose_rejects_an_unknown_failure_id(self, tmp_path: Path) -> None:
        _write_indeterminate_report(tmp_path)
        logs = RecordingLogLocator()
        workflow = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=logs,
        )

        unknown_id = "failure-aaaaaaaaaaaaaaaa"
        with pytest.raises(DiagnoseNotFoundError) as caught:
            workflow.run(
                DiagnoseRequest(
                    root=tmp_path.as_posix(),
                    failure_id=unknown_id,
                )
            )
        assert caught.value.failure_id == unknown_id
        assert caught.value.package == "demo"
        assert caught.value.reason == (
            "failure ID was not found in package-floor.json or the latest local Journal"
        )
        assert logs.journal_reads == ["demo"]

    def test_diagnose_presents_user_guidance_before_technical_enums(
        self,
        tmp_path: Path,
    ) -> None:
        _, failure_id = _write_indeterminate_report(tmp_path)
        diagnosis = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=RecordingLogLocator(),
        ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id))
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
            root=tmp_path,
        )

        exit_code = presenter.render_diagnose(diagnosis)

        rendered = stdout.getvalue()
        assert exit_code == 0
        normalized = " ".join(rendered.split())
        assert f"! {failure_id} · compatibility unknown" in normalized
        assert (
            "What happened PF could not reach or read a configured package source."
            in normalized
        )
        assert "-> Check the index URL" in normalized
        assert "attempt not available" in normalized
        assert "resolution not applicable" in normalized
        assert "vector not applicable" in normalized
        assert "detail code candidate-discovery-failed" in normalized
        assert "detail the configured index was unavailable" in normalized
        assert "Detailed local log is unavailable." in rendered
        assert normalized.index("What happened") < normalized.index(
            "cause SOURCE_FAILURE"
        )

    def test_diagnose_resolves_a_successful_floor_predecessor_and_local_log(
        self,
        tmp_path: Path,
    ) -> None:
        generation, failure_id, proposal_id = _write_success_with_predecessor_report(
            tmp_path
        )
        logs = RecordingLogLocator(Path(".pf/logs/run/process-0001.log"))
        diagnosis = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=logs,
        ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id))

        assert diagnosis.proposal_id == proposal_id
        assert diagnosis.boundary_role == "predecessor"
        assert diagnosis.log_path == Path(".pf/logs/run/process-0001.log")
        assert logs.lookups == [(generation, failure_id)]

        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
            root=tmp_path,
        )
        assert presenter.render_diagnose(diagnosis) == 0
        rendered = " ".join(stdout.getvalue().split())
        assert f"✗ {failure_id} · rejected candidate" in rendered
        assert "resolution exact-vector" in rendered
        assert "vector idna==2.0" in rendered
        assert f"proposal {proposal_id}" in rendered
        assert "boundary predecessor" in rendered
        assert "process exited 1" in rendered
        assert "It helped establish the verified floor." in rendered
        assert "summary: tests failed" not in rendered
        assert ".pf/logs/run/process-0001.log" in rendered

    @pytest.mark.parametrize(
        ("process", "expected"),
        (
            (
                ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                    stdout="",
                    stderr="",
                    timed_out=True,
                ),
                "timed out",
            ),
            (
                ProcessResult(
                    exit_code=None,
                    signal=None,
                    duration_seconds=0.1,
                    stdout="",
                    stderr="uv missing",
                    start_error="Executable not found",
                ),
                "could not start",
            ),
            (
                ProcessResult(
                    exit_code=None,
                    signal=9,
                    duration_seconds=0.1,
                    stdout="",
                    stderr="",
                ),
                "terminated by signal 9",
            ),
            (
                ProcessResult(
                    exit_code=None,
                    signal=None,
                    duration_seconds=0.1,
                    stdout="",
                    stderr="",
                    start_error="",
                ),
                "could not start",
            ),
        ),
    )
    def test_diagnose_describes_incomplete_process_terminals(
        self,
        tmp_path: Path,
        process: ProcessResult,
        expected: str,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(
                attempt=_attempt(
                    cell=cell,
                    snapshot_digest="snapshot",
                    vector=(VersionPin(name="idna", version="2.0"),),
                    policy_identity="policy",
                )
            ),
            cause="TIMEOUT" if process.timed_out else "TOOL_FAILURE",
            stage="test",
            process=process,
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
            root=tmp_path,
        )

        assert presenter.render_diagnose(_diagnosis(failure=failure)) == 0
        assert f"process {expected}" in " ".join(stdout.getvalue().split())

    def test_diagnose_reads_a_check_journal_without_a_floor_report(
        self, tmp_path: Path
    ) -> None:
        _write_managed_project(tmp_path)
        project = ProjectLoader().load(root=tmp_path)
        cell = project.target.cells[0]
        attempt = _attempt(
            cell=cell,
            snapshot_digest="snapshot",
            vector=None,
            policy_identity="policy",
            requested_resolution="lowest-direct",
        )
        failure = _verifier_failure(attempt)
        logs = RunLogStore(root=tmp_path, run_id="check-run")
        logs.write_journal(
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
                        attempt=attempt,
                        failure=failure,
                    ),
                ),
            )
        )

        diagnosis = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=logs,
        ).run(
            DiagnoseRequest(
                root=tmp_path.as_posix(),
                selector=WorkspacePackage(canonical_name="demo"),
                failure_id=failure.failure_id,
            )
        )

        assert diagnosis.source == "journal"
        assert diagnosis.command == "check"
        assert diagnosis.verification_role == "declaration"
        stdout = StringIO()
        exit_code = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
            root=tmp_path,
        ).render_diagnose(diagnosis)
        rendered = " ".join(stdout.getvalue().split())
        assert exit_code == 0
        assert "source latest pf check" in rendered
        assert "The declared lower bounds did not pass the required checks." in rendered

    def test_diagnose_reads_latest_journal_when_floor_report_is_unreadable(
        self, tmp_path: Path
    ) -> None:
        _write_managed_project(tmp_path)
        project = ProjectLoader().load(root=tmp_path)
        cell = project.target.cells[0]
        attempt = _attempt(
            cell=cell,
            snapshot_digest="snapshot",
            vector=None,
            policy_identity="policy",
            requested_resolution="lowest-direct",
        )
        failure = _verifier_failure(attempt)
        (tmp_path / "package-floor.json").write_text(
            '{"schema_version": 2}\n',
            encoding="utf-8",
        )
        logs = RunLogStore(root=tmp_path, run_id="search-run")
        logs.write_journal(
            VerificationJournal(
                run_id="search-run",
                command="search",
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
                        role="probe",
                        attempt=attempt,
                        failure=failure,
                    ),
                ),
            )
        )

        diagnosis = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=logs,
        ).run(
            DiagnoseRequest(
                root=tmp_path.as_posix(),
                selector=WorkspacePackage(canonical_name="demo"),
                failure_id=failure.failure_id,
            )
        )

        assert diagnosis.source == "journal"
        assert diagnosis.command == "search"
        assert diagnosis.verification_role == "probe"
        assert diagnosis.failure.failure_id == failure.failure_id

    def test_diagnose_uses_declaration_capture_impact_for_highest_check_failures(
        self,
        tmp_path: Path,
    ) -> None:
        _write_managed_project(tmp_path)
        project = ProjectLoader().load(root=tmp_path)
        cell = project.target.cells[0]
        attempt = _attempt(
            cell=cell,
            snapshot_digest="snapshot",
            vector=None,
            policy_identity="policy",
            requested_resolution="highest",
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="BUILD_FAILURE",
            stage="install-project",
            process=_process(exit_code=1),
        )
        logs = RunLogStore(root=tmp_path, run_id="check-capture")
        logs.write_journal(
            VerificationJournal(
                run_id="check-capture",
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
                        role="declaration-capture",
                        attempt=attempt,
                        failure=failure,
                    ),
                ),
            )
        )

        diagnosis = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=logs,
        ).run(
            DiagnoseRequest(
                root=tmp_path.as_posix(),
                selector=WorkspacePackage(canonical_name="demo"),
                failure_id=failure.failure_id,
            )
        )

        assert diagnosis.verification_role == "declaration-capture"
        stdout = StringIO()
        TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
            root=tmp_path,
        ).render_diagnose(diagnosis)
        rendered = stdout.getvalue()
        assert (
            "Whether a static baseline can be captured is unknown" in rendered
        )
        assert "declared lower bounds" in rendered
        assert "did not start the floor search" not in rendered

    @pytest.mark.parametrize("width", (56, 80, 120))
    def test_diagnose_card_preserves_fields_at_common_widths(
        self,
        tmp_path: Path,
        width: int,
    ) -> None:
        _, failure_id = _write_indeterminate_report(tmp_path)
        diagnosis = DiagnoseCommandWorkflow(
            discovery=ProjectDiscovery(),
            reports=ReportStore(),
            logs=RecordingLogLocator(),
        ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id))
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                color_system=None,
                width=width,
            ),
            stderr=Console(file=StringIO(), force_terminal=False),
            root=tmp_path,
        )

        assert presenter.render_diagnose(diagnosis) == 0

        rendered = stdout.getvalue()
        assert failure_id in rendered
        assert "Detailed local log is unavailable." in rendered
        plain = re.sub(r"\x1b]8;[^\x1b]*\x1b\\", "", rendered)
        for line in plain.splitlines():
            assert len(line) <= width

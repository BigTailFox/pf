from __future__ import annotations

from pathlib import Path
from io import StringIO

import pytest
from rich.console import Console

from pf.errors import ConfigurationError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.schemas.config import DiagnoseRequest
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    CellFailureScope,
    FailureDetail,
    FailureRecord,
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.project import Cell, Proposal, VersionPin
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
        self.path = path

    def lookup(self, report_generation_id: str, failure_id: str) -> Path | None:
        self.lookups.append((report_generation_id, failure_id))
        return self.path


def _write_indeterminate_report(root: Path) -> tuple[str, str]:
    (root / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = []

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
managed-deps = []
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    project = ProjectLoader().load(root=root, package_selection=None)
    package = project.packages[0]
    snapshot = SnapshotBuilder().build(root)
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


def test_diagnose_reads_portable_failure_facts_without_execution_capabilities(
    tmp_path: Path,
) -> None:
    generation, failure_id = _write_indeterminate_report(tmp_path)
    logs = RecordingLogLocator()
    workflow = DiagnoseCommandWorkflow(
        projects=ProjectLoader(),
        reports=ReportStore(),
        logs=logs,
    )

    result = workflow.run(DiagnoseRequest(root=tmp_path.as_posix()))

    assert len(result) == 1
    assert result[0].failure.failure_id == failure_id
    assert result[0].proposal_id is None
    assert result[0].boundary_role is None
    assert result[0].log_path is None
    assert logs.lookups == [(generation, failure_id)]


def test_diagnose_rejects_an_unknown_failure_id(tmp_path: Path) -> None:
    _write_indeterminate_report(tmp_path)
    workflow = DiagnoseCommandWorkflow(
        projects=ProjectLoader(),
        reports=ReportStore(),
        logs=RecordingLogLocator(),
    )

    with pytest.raises(ConfigurationError, match="failure ID not found"):
        workflow.run(
            DiagnoseRequest(
                root=tmp_path.as_posix(),
                failure_id="failure-missing",
            )
        )


def test_diagnose_presents_user_guidance_before_technical_enums(
    tmp_path: Path,
) -> None:
    _write_indeterminate_report(tmp_path)
    diagnoses = DiagnoseCommandWorkflow(
        projects=ProjectLoader(),
        reports=ReportStore(),
        logs=RecordingLogLocator(),
    ).run(DiagnoseRequest(root=tmp_path.as_posix()))
    stdout = StringIO()
    presenter = TerminalPresenter(
        stdout=Console(file=stdout, force_terminal=False, color_system=None),
        stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
        root=tmp_path,
    )

    exit_code = presenter.render_diagnose(diagnoses)

    rendered = stdout.getvalue()
    assert exit_code == 0
    assert "Outcome: Compatibility is unknown" in rendered
    assert (
        "What happened: PF could not reach or read a configured package source."
        in rendered
    )
    assert "Next step: Check the index URL" in rendered
    assert "attempt: not available" in rendered
    assert "requested vector: not applicable" in rendered
    assert "detail code: candidate-discovery-failed" in rendered
    assert "detail: the configured index was unavailable" in rendered
    assert "Detailed local log is unavailable." in rendered
    assert rendered.index("What happened:") < rendered.index("cause: SOURCE_FAILURE")


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
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
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
) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot_digest,
            cell=cell,
            requested_resolution="highest" if vector is None else "exact-vector",
            requested_managed_vector=vector,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity=policy_identity,
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
    proposal = Proposal(
        proposal_id=";".join(f"{pin.name}={pin.version}" for pin in vector),
        attempt_id=attempt.attempt_id,
        snapshot_digest=snapshot_digest,
        cell=attempt.identity.cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity=policy_identity,
    )
    return PassEvaluation(
        proposal=proposal,
        static=StaticPassEvaluation(
            proposal=proposal,
            ty=baseline_check,
            baseline_digest=baseline_digest,
        ),
        test=TestPass(process=_process()),
    )


def _process(exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0.1,
        stdout_summary="",
        stderr_summary="tests failed" if exit_code else "",
        stdout_tail="",
        stderr_tail="tests failed" if exit_code else "",
    )


def _write_success_with_predecessor_report(root: Path) -> tuple[str, str]:
    _write_managed_project(root)
    project = ProjectLoader().load(root=root, package_selection=None)
    package = project.packages[0]
    snapshot = SnapshotBuilder().build(root)
    try:
        cell = package.cells[0]
        policy_identity = evaluation_policy_identity(package.config)
        baseline_vector = (VersionPin(name="idna", version="3.11"),)
        baseline_attempt = _attempt(
            cell=cell,
            snapshot_digest=snapshot.identity.digest,
            vector=None,
            policy_identity=policy_identity,
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
        )
        rejected_pass = _pass_evaluation(
            attempt=rejected_attempt,
            vector=rejected_vector,
            snapshot_digest=snapshot.identity.digest,
            baseline_check=check,
            baseline_digest=digest,
            policy_identity=policy_identity,
        )
        failed_test = TestFailEvaluation(
            proposal=rejected_pass.proposal,
            static=rejected_pass.static,
            test=TestFail(process=_process(exit_code=1)),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=rejected_attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=failed_test.test.process,
        )

        final_vector = (VersionPin(name="idna", version="3.0"),)
        final_attempt = _attempt(
            cell=cell,
            snapshot_digest=snapshot.identity.digest,
            vector=final_vector,
            policy_identity=policy_identity,
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
            source_snapshot=snapshot.identity,
            cell_results=(
                CellSuccess(
                    cell=cell,
                    baseline_attempt=baseline_attempt,
                    static_baseline=baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=(),
                    static_search=search,
                    final_vector=final_vector,
                    final_evaluation=final_evaluation,
                    failure_records=(failure,),
                ),
            ),
        )
        ReportStore().write(root / "package-floor.json", report)
        return report.report_generation_id, failure.failure_id
    finally:
        snapshot.close()


def test_diagnose_resolves_a_successful_floor_predecessor_and_local_log(
    tmp_path: Path,
) -> None:
    generation, failure_id = _write_success_with_predecessor_report(tmp_path)
    logs = RecordingLogLocator(Path(".pf/logs/run/process-0001.log"))
    diagnoses = DiagnoseCommandWorkflow(
        projects=ProjectLoader(),
        reports=ReportStore(),
        logs=logs,
    ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id))

    assert len(diagnoses) == 1
    diagnosis = diagnoses[0]
    assert diagnosis.proposal_id == "idna=2.0"
    assert diagnosis.boundary_role == "predecessor"
    assert diagnosis.log_path == Path(".pf/logs/run/process-0001.log")
    assert logs.lookups == [(generation, failure_id)]

    stdout = StringIO()
    presenter = TerminalPresenter(
        stdout=Console(file=stdout, force_terminal=False, color_system=None),
        stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
        root=tmp_path,
    )
    assert presenter.render_diagnose(diagnoses) == 0
    rendered = stdout.getvalue()
    assert "Outcome: The verification attempt was rejected" in rendered
    assert "requested resolution: exact-vector" in rendered
    assert "requested vector: idna==2.0" in rendered
    assert "proposal: idna=2.0" in rendered
    assert "boundary role: predecessor" in rendered
    assert "process: exited 1" in rendered
    assert "summary: tests failed" in rendered
    assert ".pf/logs/run/process-0001.log" in rendered


def test_diagnose_presents_an_empty_result_set(tmp_path: Path) -> None:
    presenter = TerminalPresenter(
        stdout=Console(file=(stdout := StringIO()), force_terminal=False),
        stderr=Console(file=StringIO(), force_terminal=False),
        root=tmp_path,
    )

    assert presenter.render_diagnose(()) == 0
    assert stdout.getvalue() == "diagnosed 0 failures\n"


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
    )


@pytest.mark.parametrize(
    ("process", "expected"),
    (
        (
            ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout_summary="",
                stderr_summary="",
                stdout_tail="",
                stderr_tail="",
                timed_out=True,
            ),
            "timed out",
        ),
        (
            ProcessResult(
                exit_code=None,
                signal=None,
                duration_seconds=0.1,
                stdout_summary="",
                stderr_summary="uv missing",
                stdout_tail="",
                stderr_tail="uv missing",
                start_error="Executable not found",
            ),
            "could not start",
        ),
        (
            ProcessResult(
                exit_code=None,
                signal=9,
                duration_seconds=0.1,
                stdout_summary="",
                stderr_summary="",
                stdout_tail="",
                stderr_tail="",
            ),
            "terminated by signal 9",
        ),
        (
            ProcessResult(
                exit_code=None,
                signal=None,
                duration_seconds=0.1,
                stdout_summary="",
                stderr_summary="",
                stdout_tail="",
                stderr_tail="",
                start_error="",
            ),
            "could not start",
        ),
    ),
)
def test_diagnose_describes_incomplete_process_terminals(
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

    assert presenter.render_diagnose((_diagnosis(failure=failure),)) == 0
    assert f"process: {expected}" in stdout.getvalue()


def test_diagnose_separates_multiple_failures(tmp_path: Path) -> None:
    first_generation, first_id = _write_indeterminate_report(tmp_path)
    first = DiagnoseCommandWorkflow(
        projects=ProjectLoader(),
        reports=ReportStore(),
        logs=RecordingLogLocator(),
    ).run(DiagnoseRequest(root=tmp_path.as_posix()))[0]
    second_failure = FailurePolicy().classify(
        scope=AttemptFailureScope(
            attempt=_attempt(
                cell=first.failure.scope.cell
                if isinstance(first.failure.scope, CellFailureScope)
                else first.failure.scope.attempt.identity.cell,
                snapshot_digest="snapshot",
                vector=None,
                policy_identity="policy",
            )
        ),
        cause="TEST_FAILURE",
        stage="test",
        process=_process(exit_code=1),
    )
    stdout = StringIO()
    presenter = TerminalPresenter(
        stdout=Console(file=stdout, force_terminal=False, color_system=None),
        stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
        root=tmp_path,
    )

    exit_code = presenter.render_diagnose(
        (first, _diagnosis(failure=second_failure, package=first.package))
    )

    rendered = stdout.getvalue()
    assert exit_code == 0
    assert rendered.count("Failure: ") == 2
    assert f"Failure: {first.failure.failure_id}\n" in rendered
    assert f"Failure: {second_failure.failure_id}\n" in rendered
    assert "\n\nFailure: " in rendered
    assert first_generation and first_id

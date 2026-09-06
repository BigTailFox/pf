from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from evaluation_fixtures import ScriptedUv, evaluation_project
from pf.environment import EnvironmentFactory, HighestResolution
from pf.failure import FailurePolicy
from pf.project_discovery import ProjectDiscovery
from pf.report import PackageReportBuilder, ReportStore
from pf.resolution import ResolutionFailure
from pf.runlog import RunLogStore
from pf.schemas.config import DiagnoseRequest, RunLimits
from pf.schemas.evaluation import (
    BaselineIndeterminate,
    BaselineRejection,
    CellCompletedEvent,
    CheckCellOutcome,
    CheckCompatibilityFailure,
    CheckIndeterminate,
    ExecutionFailure,
    OperationFailureResult,
    PrepareFailure,
    ProcessResult,
    ProcessSpec,
    SmokeBaselineRejection,
    SmokeIndeterminate,
    Unattributed,
    execution_terminal,
)
from pf.schemas.project import SourcePlan
from pf.terminal import TerminalPresenter
from pf.verification import (
    CheckVerificationRun,
    SearchVerificationRun,
    SmokeVerificationRun,
    VerificationRunner,
)
from pf.workflow import DiagnoseCommandWorkflow, SearchCommandResult


class TestExecutionFailureRun:
    @pytest.mark.parametrize("command", ["smoke", "check", "search"])
    @pytest.mark.parametrize("stage", ["resolve-project", "install-project"])
    @pytest.mark.parametrize("timeout", [False, True])
    def test_run_persists_diagnosable_execution_failure(
        self, tmp_path, command, stage, timeout
    ):
        project = evaluation_project(tmp_path)
        package = project.package
        plan = SourcePlan.for_package(
            package, "DEVELOPMENT" if command == "smoke" else "SEARCH"
        )
        process = ProcessResult(
            exit_code=0 if timeout else 1,
            timed_out=timeout,
            duration_seconds=0.1,
            stderr="controlled backend diagnostic",
        )
        facts = ExecutionFailure(
            terminal=execution_terminal(process), attribution=Unattributed()
        )

        class Uv(ScriptedUv):
            def resolve_project(self, **kwargs):
                if stage == "resolve-project":
                    return ResolutionFailure(
                        stage=stage,
                        request_digest=kwargs["request_digest"],
                        context=kwargs["context"],
                        failure=facts,
                        process=process,
                    )
                return super().resolve_project(**kwargs)

        uv = Uv(
            install_failure=OperationFailureResult(
                stage="install-project", failure=facts, process=process
            )
        )
        logs = RunLogStore(root=tmp_path, run_id=f"{command}-execution")
        try:
            prepared = EnvironmentFactory(uv).prepare(
                package=package,
                cell=package.cells[0],
                snapshot=project.snapshot,
                source_plan=plan,
                resolution=HighestResolution(),
            )
            assert isinstance(prepared, PrepareFailure)
            failure = FailurePolicy().record_prepare(prepared)
            baseline = (BaselineIndeterminate if timeout else BaselineRejection)(
                attempt=prepared.attempt, failure=failure, failure_process=process
            )
            check = CheckCellOutcome(
                status=failure.disposition,
                role="declaration-capture",
                attempt=prepared.attempt,
                failure=failure,
                failure_process=process,
            )

            class Operations:
                def verify(self, **kwargs):
                    return baseline

                def check(self, **kwargs):
                    return check

                def search(self, **kwargs):
                    return baseline

            output = StringIO()
            terminal = TerminalPresenter(
                stdout=Console(file=output, width=120),
                stderr=Console(file=output, width=120),
                logs=logs,
                root=tmp_path,
            )
            events = []

            class Events:
                def consume(self, event):
                    events.append(event)
                    terminal.consume(event)

            log_path = logs.record(
                1,
                ProcessSpec(
                    argv=(
                        "uv",
                        "pip",
                        "compile" if stage == "resolve-project" else "sync",
                    ),
                    cwd=str(tmp_path),
                    timeout_seconds=30,
                ),
                process,
                stderr=process.stderr,
            )
            request_type = {
                "smoke": SmokeVerificationRun,
                "check": CheckVerificationRun,
                "search": SearchVerificationRun,
            }[command]
            VerificationRunner(
                events=Events(), logs=logs, host_target=package.cells[0].target
            ).run(
                request_type(
                    package=package,
                    source_plan=plan,
                    snapshot=project.snapshot,
                    operation=Operations(),
                    limits=RunLimits(
                        max_cells=1, ty_jobs=1, test_jobs=1, max_duration_seconds=None
                    ),
                )
            )
            completion = next(
                event for event in events if isinstance(event, CellCompletedEvent)
            )
            assert completion.outcome.process_failure_id == failure.failure_id
            assert completion.outcome.process == process
            journal = logs.read_latest_journal(package.name)
            assert journal is not None
            assert journal.entries[0].failure == failure
            assert logs.lookup_run(
                logs.run_id, failure.failure_id
            ) == log_path.relative_to(tmp_path)
            assert failure.failure_id in output.getvalue()
            if command == "smoke":
                if isinstance(baseline, BaselineIndeterminate):
                    code = terminal.render_smoke(
                        SmokeIndeterminate(outcomes=(baseline,))
                    )
                else:
                    code = terminal.render_smoke(
                        SmokeBaselineRejection(outcomes=(baseline,))
                    )
            elif command == "check":
                result = (
                    CheckIndeterminate(failure=failure, outcomes=(check,))
                    if timeout
                    else CheckCompatibilityFailure(evaluations=(), outcomes=(check,))
                )
                code = terminal.render_check(result)
            else:
                report = PackageReportBuilder().build(
                    package=package,
                    source_plan=plan,
                    source_snapshot=project.snapshot.identity,
                    cell_results=(baseline,),
                )
                report_path = tmp_path / "package-floor.json"
                ReportStore().write(report_path, report)
                logs.associate(report.report_generation_id, failure.failure_id, process)
                code = terminal.render_search(
                    SearchCommandResult(report=report, report_path=str(report_path))
                )
            assert code == (4 if timeout else 1)
            if command == "check" and not timeout:
                assert "declared lower bounds are incompatible" not in output.getvalue()
                assert "baseline capture did not pass" in output.getvalue()

            class NoProcessLogs:
                def lookup(self, report_generation_id, failure_id):
                    return None

                def lookup_run(self, run_id, failure_id):
                    return None

                def read_latest_journal(self, package):
                    return logs.read_latest_journal(package)

                def read_tail(self, path):
                    return ()

            for locator in (logs, NoProcessLogs()):
                diagnosis = DiagnoseCommandWorkflow(
                    discovery=ProjectDiscovery(), reports=ReportStore(), logs=locator
                ).run(
                    DiagnoseRequest(root=str(tmp_path), failure_id=failure.failure_id)
                )
                assert diagnosis.failure == failure
                assert diagnosis.log_path == (
                    log_path.relative_to(tmp_path) if locator is logs else None
                )
                assert terminal.render_diagnose(diagnosis) == 0
            assert "controlled backend diagnostic" in output.getvalue()
            assert "Detailed local log is unavailable." in output.getvalue()
        finally:
            logs.close()
            project.snapshot.close()

from __future__ import annotations

from pathlib import Path

import pytest

from pf.errors import InfrastructureError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    ActivityEvent,
    Attempt,
    AttemptIdentity,
    CellCompletedEvent,
    CellFailed,
    CellFailureScope,
    CheckCellOutcome,
    FailureDetail,
    ProcessObservation,
    ProcessResult,
    ProcessTerminalUnavailable,
    VerificationJournal,
    VerificationJournalEntry,
    ToolFailure,
)
from pf.schemas.project import Cell, PackagePlan
from pf.schemas.report import CellIndeterminate
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.verification import (
    VerificationRun,
    VerificationRunner,
    VerificationTask,
    completion_outcome,
)


class _NoEvents:
    def consume(self, event: ActivityEvent) -> None:
        return


def _tool_failure() -> ToolFailure:
    return ToolFailure(
        cause="TOOL_FAILURE",
        stage="test",
        process=ProcessResult(
            exit_code=2,
            signal=None,
            duration_seconds=0.1,
        ),
    )


def verification_case(
    tmp_path: Path,
) -> tuple[SourceSnapshot, Cell, PackagePlan, VerificationJournalEntry]:
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = SnapshotBuilder.without_processes().build(tmp_path)
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    package = PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(),
        cells=(cell,),
        source_routes=(),
    )
    failure = FailurePolicy().classify(
        scope=CellFailureScope(
            package="demo",
            cell=cell,
            source_snapshot_digest=snapshot.identity.digest,
            evaluation_policy_identity=evaluation_policy_identity(package.config),
        ),
        cause="SOURCE_FAILURE",
        stage="candidate-discovery",
        process=None,
        detail=FailureDetail(code="offline", message="registry unavailable"),
    )
    return (
        snapshot,
        cell,
        package,
        VerificationJournalEntry(
            package="demo",
            cell=cell,
            role="probe",
            failure=failure,
        ),
    )


class TestVerificationRunner:
    @staticmethod
    def _task(cell: Cell) -> VerificationTask[ToolFailure]:
        return VerificationTask(
            cell=cell,
            execute=_tool_failure,
            journal_entries=lambda outcome: (),
        )

    def test_run_rejects_a_source_mode_that_does_not_match_the_command(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, cell, package, _ = verification_case(tmp_path)
        request = VerificationRun(
            command="search",
            package=package,
            source_mode="DEVELOPMENT",
            snapshot=snapshot,
            tasks=(self._task(cell),),
            jobs=1,
            max_duration_seconds=None,
        )

        with pytest.raises(ValueError, match="source mode does not match"):
            VerificationRunner(events=_NoEvents(), logs=None).run(request)
        snapshot.close()

    def test_run_rejects_duplicate_tasks(self, tmp_path: Path) -> None:
        snapshot, cell, package, _ = verification_case(tmp_path)
        task = self._task(cell)
        request = VerificationRun(
            command="search",
            package=package,
            source_mode="SEARCH",
            snapshot=snapshot,
            tasks=(task, task),
            jobs=1,
            max_duration_seconds=None,
        )

        with pytest.raises(ValueError, match="tasks must have unique cells"):
            VerificationRunner(events=_NoEvents(), logs=None).run(request)
        snapshot.close()

    def test_run_rejects_a_task_outside_the_package_set(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, cell, package, _ = verification_case(tmp_path)
        other_cell = cell.model_copy(update={"package": "other"})
        request = VerificationRun(
            command="search",
            package=package,
            source_mode="SEARCH",
            snapshot=snapshot,
            tasks=(self._task(other_cell),),
            jobs=1,
            max_duration_seconds=None,
        )

        with pytest.raises(ValueError, match="package is outside the run"):
            VerificationRunner(events=_NoEvents(), logs=None).run(request)
        snapshot.close()

    def test_completion_outcome_projects_tool_failure(self) -> None:
        result = _tool_failure()

        outcome = completion_outcome(result)

        assert isinstance(outcome, CellFailed)
        assert outcome.phase == "test"

    def test_completion_outcome_retains_typed_terminal_unavailable(self) -> None:
        unavailable = ProcessTerminalUnavailable(
            duration_seconds=0.2,
            detail="runner returned no terminal status",
        )
        outcome = completion_outcome(
            ToolFailure(
                cause="TOOL_FAILURE",
                stage="test",
                process=unavailable,
            )
        )

        assert isinstance(outcome, CellFailed)
        assert outcome.process == unavailable

    def test_verification_runner_associates_typed_terminal_unavailable(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, cell, package, _ = verification_case(tmp_path)
        attempt = Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.identity.digest,
                cell=cell,
                requested_resolution="lowest-direct",
                requested_managed_vector=None,
                active_declaration_ids=cell.active_declaration_ids,
                source_plan_identity="sources",
                evaluation_policy_identity=evaluation_policy_identity(package.config),
            )
        )
        unavailable = ProcessTerminalUnavailable(
            duration_seconds=0.2,
            detail="runner returned no terminal status",
        )
        later_diagnostics = ProcessTerminalUnavailable(
            duration_seconds=0.3,
            detail="different runtime-only diagnostics",
        )
        failure = FailurePolicy().classify(
            scope=CellFailureScope(
                package=cell.package,
                cell=cell,
                source_snapshot_digest=snapshot.identity.digest,
                evaluation_policy_identity=evaluation_policy_identity(package.config),
            ),
            cause="TOOL_FAILURE",
            stage="prepare",
            process=unavailable,
        )
        entry = VerificationJournalEntry(
            package=cell.package,
            cell=cell,
            role="declaration",
            failure=failure,
        )
        outcome = CheckCellOutcome(
            status="INDETERMINATE",
            role="declaration",
            attempt=attempt,
            failure=failure,
            failure_process=unavailable,
        )
        associations: list[tuple[str, ProcessTerminalUnavailable]] = []

        class Logs:
            run_id = "unavailable-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                return tmp_path / "journal.json"

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessResult | ProcessTerminalUnavailable,
            ) -> None:
                assert report_generation_id == "journal:unavailable-run"
                assert failure_id == failure.failure_id
                assert isinstance(result, ProcessTerminalUnavailable)
                associations.append((failure_id, result))

        VerificationRunner(events=_NoEvents(), logs=Logs()).run(
            VerificationRun(
                command="check",
                package=package,
                source_mode="SEARCH",
                snapshot=snapshot,
                tasks=(
                    VerificationTask(
                        cell=cell,
                        execute=lambda: outcome,
                        journal_entries=lambda result: (entry,),
                        runtime_associations=lambda result: (
                            (failure.failure_id, unavailable),
                            (failure.failure_id, later_diagnostics),
                        ),
                    ),
                ),
                jobs=1,
                max_duration_seconds=None,
            )
        )

        assert associations
        assert all(result == unavailable for _, result in associations)
        snapshot.close()

    def test_completion_outcome_rejects_an_unknown_result(self) -> None:
        with pytest.raises(TypeError, match="unsupported verification result"):
            completion_outcome(object())

    def test_verification_runner_persists_before_diagnose_and_confirms_final_journal(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(),
            declarations=(),
            cells=(cell,),
            source_routes=(),
        )
        policy = evaluation_policy_identity(package.config)
        failure = FailurePolicy().classify(
            scope=CellFailureScope(
                package="demo",
                cell=cell,
                source_snapshot_digest=snapshot.identity.digest,
                evaluation_policy_identity=policy,
            ),
            cause="SOURCE_FAILURE",
            stage="candidate-discovery",
            process=None,
            detail=FailureDetail(code="offline", message="registry unavailable"),
        )
        entry = VerificationJournalEntry(
            package="demo",
            cell=cell,
            role="probe",
            failure=failure,
        )
        timeline: list[tuple[str, object]] = []

        class Events:
            def consume(self, event: ActivityEvent) -> None:
                if isinstance(event, CellCompletedEvent):
                    timeline.append(("completion", event.diagnose_available))

        class Logs:
            run_id = "verification-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                timeline.append(("journal", len(journal.entries)))
                return tmp_path / "journal.json"

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                return

        runner = VerificationRunner(
            events=Events(),
            logs=Logs(),
        )

        outcome = CellIndeterminate(
            cell=cell,
            phase=failure.stage,
            failure_id=failure.failure_id,
            failure_records=(failure,),
        )

        outcomes = runner.run(
            VerificationRun(
                command="search",
                package=package,
                source_mode="SEARCH",
                snapshot=snapshot,
                tasks=(
                    VerificationTask(
                        cell=cell,
                        execute=lambda: outcome,
                        journal_entries=lambda outcome: (entry,),
                    ),
                ),
                jobs=1,
                max_duration_seconds=None,
            )
        )

        assert outcomes == (outcome,)
        assert timeline == [
            ("journal", 1),
            ("completion", True),
            ("journal", 1),
        ]
        snapshot.close()

    def test_verification_runner_without_logs_never_advertises_diagnose(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, cell, package, entry = verification_case(tmp_path)
        completions: list[bool] = []

        class Events:
            def consume(self, event: ActivityEvent) -> None:
                if isinstance(event, CellCompletedEvent):
                    completions.append(event.diagnose_available)

        runner = VerificationRunner(
            events=Events(),
            logs=None,
        )
        outcome = CellIndeterminate(
            cell=cell,
            phase=entry.failure.stage,
            failure_id=entry.failure.failure_id,
            failure_records=(entry.failure,),
        )
        runner.run(
            VerificationRun(
                command="search",
                package=package,
                source_mode="SEARCH",
                snapshot=snapshot,
                tasks=(
                    VerificationTask(
                        cell=cell,
                        execute=lambda: outcome,
                        journal_entries=lambda outcome: (entry,),
                    ),
                ),
                jobs=1,
                max_duration_seconds=None,
            )
        )

        assert completions == [False]
        snapshot.close()

    def test_verification_runner_delays_journal_failure_until_after_completion(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, cell, package, entry = verification_case(tmp_path)
        completions: list[bool] = []

        class Events:
            def consume(self, event: ActivityEvent) -> None:
                if isinstance(event, CellCompletedEvent):
                    completions.append(event.diagnose_available)

        class FailingLogs:
            run_id = "verification-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                raise InfrastructureError("could not write verification journal")

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                raise AssertionError("journal failure must prevent process association")

        runner = VerificationRunner(
            events=Events(),
            logs=FailingLogs(),
        )
        outcome = CellIndeterminate(
            cell=cell,
            phase=entry.failure.stage,
            failure_id=entry.failure.failure_id,
            failure_records=(entry.failure,),
        )
        with pytest.raises(InfrastructureError, match="verification journal"):
            runner.run(
                VerificationRun(
                    command="search",
                    package=package,
                    source_mode="SEARCH",
                    snapshot=snapshot,
                    tasks=(
                        VerificationTask(
                            cell=cell,
                            execute=lambda: outcome,
                            journal_entries=lambda outcome: (entry,),
                        ),
                    ),
                    jobs=1,
                    max_duration_seconds=None,
                )
            )

        assert completions == [False]
        snapshot.close()

    def test_verification_runner_owns_deadline_result_and_completion_projection(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, cell, package, _entry = verification_case(tmp_path)
        completions: list[CellCompletedEvent] = []

        class Events:
            def consume(self, event: ActivityEvent) -> None:
                if isinstance(event, CellCompletedEvent):
                    completions.append(event)

        runner = VerificationRunner(
            events=Events(),
            logs=None,
            monotonic=iter((0.0, 1.0)).__next__,
        )

        outcomes = runner.run(
            VerificationRun(
                command="search",
                package=package,
                source_mode="SEARCH",
                snapshot=snapshot,
                tasks=(
                    VerificationTask(
                        cell=cell,
                        execute=lambda: pytest.fail("deadline task must not run"),
                        journal_entries=lambda result: (
                            VerificationJournalEntry(
                                package=result.cell.package,
                                cell=result.cell,
                                role="probe",
                                failure=result.failure_records[0],
                            ),
                        ),
                        deadline_scope=CellFailureScope(
                            package=cell.package,
                            cell=cell,
                            source_snapshot_digest=snapshot.identity.digest,
                            evaluation_policy_identity=evaluation_policy_identity(
                                package.config
                            ),
                        ),
                    ),
                ),
                jobs=1,
                max_duration_seconds=0.5,
            )
        )

        assert len(outcomes) == 1
        assert isinstance(outcomes[0], CellIndeterminate)
        assert outcomes[0].phase == "scheduler-deadline"
        assert len(completions) == 1
        assert isinstance(completions[0].outcome, CellFailed)
        assert completions[0].outcome.phase == "scheduler-deadline"
        snapshot.close()

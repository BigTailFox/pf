from __future__ import annotations

from pathlib import Path

import pytest

from pf.errors import InfrastructureError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    ActivityEvent,
    CellCompletedEvent,
    CellFailed,
    CellFailureScope,
    FailureDetail,
    VerificationJournal,
    VerificationJournalEntry,
)
from pf.schemas.project import Cell, PackagePlan, SourcePlan
from pf.schemas.report import CellIndeterminate
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.verification import VerificationRun, VerificationRunner, VerificationTask

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
        source_plan=SourcePlan(identities=()),
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
            source_plan=SourcePlan(identities=()),
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
                packages=(package,),
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
                packages=(package,),
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
                    packages=(package,),
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
                packages=(package,),
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

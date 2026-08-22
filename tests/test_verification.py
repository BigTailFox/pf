from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pf.errors import InfrastructureError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    ActivityEvent,
    CellFailureScope,
    FailureDetail,
    ProgressEvent,
    VerificationJournal,
    VerificationJournalEntry,
)
from pf.schemas.project import Cell, PackagePlan, SourcePlan
from pf.scheduling import Scheduler
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.verification import VerificationRun, VerificationRunner, VerificationTask


@dataclass(frozen=True)
class Outcome:
    status: str = "INDETERMINATE"


def verification_case(
    tmp_path: Path,
) -> tuple[SourceSnapshot, Cell, PackagePlan, VerificationJournalEntry]:
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = SnapshotBuilder().build(tmp_path)
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
        snapshot = SnapshotBuilder().build(tmp_path)
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
                if isinstance(event, ProgressEvent) and event.completed:
                    timeline.append(("completion", event.diagnose_available))

        class Logs:
            run_id = "verification-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                timeline.append(("journal", len(journal.entries)))
                return tmp_path / "journal.json"

        runner = VerificationRunner(
            scheduler=Scheduler(),
            events=Events(),
            logs=Logs(),
        )

        outcomes = runner.run(
            VerificationRun(
                command="search",
                packages=(package,),
                snapshot=snapshot,
                tasks=(
                    VerificationTask(
                        cell=cell,
                        execute=Outcome,
                        journal_entries=lambda outcome: (entry,),
                    ),
                ),
                jobs=1,
                max_duration_seconds=None,
            )
        )

        assert outcomes == (Outcome(),)
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
                if isinstance(event, ProgressEvent) and event.completed:
                    completions.append(event.diagnose_available)

        runner = VerificationRunner(
            scheduler=Scheduler(),
            events=Events(),
            logs=None,
        )
        runner.run(
            VerificationRun(
                command="search",
                packages=(package,),
                snapshot=snapshot,
                tasks=(
                    VerificationTask(
                        cell=cell,
                        execute=Outcome,
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
                if isinstance(event, ProgressEvent) and event.completed:
                    completions.append(event.diagnose_available)

        class FailingLogs:
            run_id = "verification-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                raise InfrastructureError("could not write verification journal")

        runner = VerificationRunner(
            scheduler=Scheduler(),
            events=Events(),
            logs=FailingLogs(),
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
                            execute=Outcome,
                            journal_entries=lambda outcome: (entry,),
                        ),
                    ),
                    jobs=1,
                    max_duration_seconds=None,
                )
            )

        assert completions == [False]
        snapshot.close()

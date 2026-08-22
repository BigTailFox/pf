from __future__ import annotations

import json
from pathlib import Path

import pytest

from pf.errors import InfrastructureError
from pf.failure import FailurePolicy
from pf.runlog import RunLogStore
from pf.schemas.evaluation import (
    CellFailureScope,
    FailureDetail,
    ProcessResult,
    ProcessSpec,
    VerificationJournal,
    VerificationJournalEntry,
    VerificationJournalV1,
    VerificationPackagePolicy,
)
from pf.schemas.project import Cell


def _entry(*, package: str, policy: str) -> VerificationJournalEntry:
    cell = Cell(
        package=package,
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    failure = FailurePolicy().classify(
        scope=CellFailureScope(
            package=package,
            cell=cell,
            source_snapshot_digest="snapshot",
            evaluation_policy_identity=policy,
        ),
        cause="SOURCE_FAILURE",
        stage="candidate-discovery",
        process=None,
        detail=FailureDetail(code="offline", message="registry unavailable"),
    )
    return VerificationJournalEntry(
        package=package,
        cell=cell,
        role="probe",
        failure=failure,
    )


class TestRunLogStoreJournal:
    def test_run_log_store_round_trips_a_v2_journal_with_per_package_policies(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="journal-v2")
        journal = VerificationJournal(
            run_id="journal-v2",
            command="search",
            source_snapshot_digest="snapshot",
            package_policies=(
                VerificationPackagePolicy(
                    package="alpha",
                    evaluation_policy_identity="policy-alpha",
                ),
                VerificationPackagePolicy(
                    package="beta",
                    evaluation_policy_identity="policy-beta",
                ),
            ),
            entries=(
                _entry(package="alpha", policy="policy-alpha"),
                _entry(package="beta", policy="policy-beta"),
            ),
        )

        path = store.write_journal(journal)

        assert store.read_latest_journal("alpha") == journal
        assert store.read_latest_journal("beta") == journal
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["schema"] == "verification-journal-v2"
        assert "evaluation_policy_identity" not in document
        assert "packages" not in document

    def test_run_log_store_reads_a_v1_journal_as_historical_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="legacy-run")
        entry = _entry(package="demo", policy="legacy-policy")
        current = VerificationJournal(
            run_id="legacy-run",
            command="check",
            source_snapshot_digest="snapshot",
            package_policies=(
                VerificationPackagePolicy(
                    package="demo",
                    evaluation_policy_identity="legacy-policy",
                ),
            ),
            entries=(entry,),
        )
        path = store.write_journal(current)
        legacy = VerificationJournalV1(
            run_id="legacy-run",
            command="check",
            packages=("demo",),
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="legacy-policy",
            entries=(entry,),
        )
        document = legacy.model_dump(mode="json")
        document["schema"] = document.pop("schema_version")
        path.write_text(json.dumps(document), encoding="utf-8")

        assert store.read_latest_journal("demo") == legacy

    def test_run_log_store_writer_rejects_a_v1_journal(self, tmp_path: Path) -> None:
        store = RunLogStore(root=tmp_path, run_id="v2-writer")
        entry = _entry(package="demo", policy="legacy-policy")
        legacy = VerificationJournalV1(
            run_id="v2-writer",
            command="check",
            packages=("demo",),
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="legacy-policy",
            entries=(entry,),
        )

        with pytest.raises(InfrastructureError, match="verification journal"):
            store.write_journal(legacy)  # type: ignore[arg-type]

        assert not (tmp_path / ".pf/logs/v2-writer/journal.json").exists()

    def test_run_log_store_replaces_and_resolves_report_and_journal_associations(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="association-run")
        spec = ProcessSpec(
            argv=("verification-tool",),
            cwd=tmp_path.as_posix(),
            timeout_seconds=1,
        )
        first = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout="first",
            stderr="",
        )
        second = first.model_copy(update={"stdout": "second"})
        store.record(1, spec, first, stdout="first")
        store.record(2, spec, second, stdout="second")

        store.replace_associations(
            "generation",
            (("failure-first", first), ("failure-second", second)),
        )
        store.replace_associations(
            "journal:association-run",
            (("failure-first", first),),
        )

        assert store.lookup("generation", "failure-first") == Path(
            ".pf/logs/association-run/process-0001.log"
        )
        assert store.lookup_run("association-run", "failure-first") == Path(
            ".pf/logs/association-run/process-0001.log"
        )

        store.replace_associations(
            "generation",
            (("failure-second", second),),
        )
        assert store.lookup("generation", "failure-first") is None
        assert store.lookup("generation", "failure-second") == Path(
            ".pf/logs/association-run/process-0002.log"
        )

    def test_run_log_store_failed_replacement_preserves_the_previous_index(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="atomic-run")
        spec = ProcessSpec(
            argv=("verification-tool",),
            cwd=tmp_path.as_posix(),
            timeout_seconds=1,
        )
        recorded = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout="recorded",
            stderr="",
        )
        unrecorded = recorded.model_copy(update={"stdout": "unrecorded"})
        store.record(1, spec, recorded, stdout="recorded")
        store.replace_associations("generation", (("failure", recorded),))

        with pytest.raises(InfrastructureError, match="diagnosis index"):
            store.replace_associations("generation", (("failure", unrecorded),))

        assert store.lookup("generation", "failure") == Path(
            ".pf/logs/atomic-run/process-0001.log"
        )

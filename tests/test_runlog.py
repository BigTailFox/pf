from __future__ import annotations

import json
from pathlib import Path

import pytest

from pf.errors import ConfigurationError, InfrastructureError
from pf.failure import FailurePolicy
from pf.runlog import RunLogStore
from pf.schemas.evaluation import (
    CellFailureScope,
    FailureDetail,
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
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
    def test_process_log_records_unavailable_terminal_without_fabricated_facts(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="unavailable-terminal")
        result = ProcessTerminalUnavailable(
            duration_seconds=0.2,
            detail="runner returned no terminal status",
        )

        path = store.record(
            1,
            ProcessSpec(
                argv=("tool",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=None,
            ),
            result,
        )
        content = path.read_text(encoding="utf-8")

        assert "terminal_kind: terminal-unavailable\n" in content
        assert "exit_code: null\n" in content
        assert "signal: null\n" in content
        assert "runner returned no terminal status" not in content

    def test_run_log_store_rejects_an_unsafe_run_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="safe filename characters"):
            RunLogStore(root=tmp_path, run_id="../outside")

    def test_diagnose_tail_uses_stdout_when_stderr_has_no_nonempty_lines(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="stdout-tail")
        result = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
        )
        path = store.record(
            1,
            ProcessSpec(
                argv=("tool",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=1,
            ),
            result,
            stdout="one\n\ntwo\nthree\nfour\n",
            stderr="\n",
        )

        assert store.read_tail(path) == ("two", "three", "four")
        with pytest.raises(ConfigurationError, match="could not read PF diagnosis log"):
            store.read_tail(Path("../outside/process-0001.log"))

    def test_diagnose_tail_strips_terminal_control_sequences(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="safe-tail")
        result = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
        )
        path = store.record(
            1,
            ProcessSpec(
                argv=("tool",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=1,
            ),
            result,
            stderr=(
                "\x1b[31mred\x1b[0m\n"
                "\x1b]8;;https://example.invalid\x07linked\x1b]8;;\x07\n"
                "control\x00text \x9b31mc1\x9b0m "
                "\x9d8;;https://example.invalid\x9cwide\x9d8;;\x9c\n"
            ),
        )

        assert store.read_tail(path) == (
            "red",
            "linked",
            "controltext c1 wide",
        )

    def test_diagnose_tail_does_not_treat_c0_or_c1_controls_as_lines(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="control-tail")
        result = ProcessResult(exit_code=1, signal=None, duration_seconds=0.1)
        path = store.record(
            1,
            ProcessSpec(
                argv=("tool",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=1,
            ),
            result,
            stderr="before\x85middle\x1eafter\nlast\n",
        )

        assert store.read_tail(path) == ("beforemiddleafter", "last")

    def test_diagnose_tail_handles_crlf_split_between_read_chunks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="chunked-tail")
        result = ProcessResult(exit_code=1, signal=None, duration_seconds=0.1)
        path = store.record(
            1,
            ProcessSpec(
                argv=("tool",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=1,
            ),
            result,
            stderr="abc\r\ndef\r\nghi\n",
        )
        monkeypatch.setattr(RunLogStore, "_STREAM_CHUNK_SIZE", 4)

        assert store.read_tail(path) == ("abc", "def", "ghi")

    def test_v2_process_log_frames_section_markers_inside_tool_output(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="framed-tail")
        result = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
        )
        stdout = "before\n--- stderr ---\nafter\n"
        stderr = "real\n--- stdout ---\n--- stderr ---\nend\n"
        path = store.record(
            1,
            ProcessSpec(
                argv=("tool",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=1,
            ),
            result,
            stdout=stdout,
            stderr=stderr,
        )

        assert path.read_text(encoding="utf-8").startswith(
            "format: pf-process-log-v2\n"
        )
        assert store.read_output(result) == (stdout, stderr)
        assert store.read_tail(path) == (
            "--- stdout ---",
            "--- stderr ---",
            "end",
        )

    def test_v1_process_log_compatibility_rejects_ambiguous_markers(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="v1-tail")
        result = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
        )
        path = store.record(
            1,
            ProcessSpec(
                argv=("tool",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=None,
            ),
            result,
        )
        path.write_text(
            "format: pf-process-log-v1\n\n"
            "--- stdout ---\nold stdout\n\n"
            "--- stderr ---\nold stderr\n",
            encoding="utf-8",
        )

        assert store.read_tail(path) == ("old stderr",)
        path.write_bytes(
            b"format: pf-process-log-v1\r\n\r\n"
            b"--- stdout ---\r\nold stdout\r\n\r\n"
            b"--- stderr ---\r\nold stderr\r\n"
        )
        assert store.read_output(result) == (
            "old stdout\r\n",
            "old stderr\r\n",
        )
        path.write_text(
            "format: pf-process-log-v1\n\n"
            "--- stdout ---\nbefore\n--- stderr ---\nafter\n\n"
            "--- stderr ---\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="could not read PF diagnosis log"):
            store.read_tail(path)
        path.write_text(
            "format: pf-process-log-v1\n\n"
            "--- stdout ---\nbefore\n--- stdout ---\nafter\n\n"
            "--- stderr ---\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="could not read PF diagnosis log"):
            store.read_tail(path)

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
            store.write_journal(legacy)  # ty: ignore[invalid-argument-type]

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

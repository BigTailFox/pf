from __future__ import annotations

from collections.abc import Callable
from io import StringIO
import json
from pathlib import Path
import stat
from typing import TextIO

import pytest

from pf.errors import ConfigurationError, InfrastructureError
from pf.failure import FailurePolicy
from pf.runlog import RunLogStore
from pf.schemas.evaluation import (
    CellFailureScope,
    EnvironmentVariable,
    FailureDetail,
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
)
from pf.schemas.journal import (
    VerificationJournal,
    VerificationJournalEntry,
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
            execution_policy_identity=policy,
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

    def test_run_log_store_round_trips_a_v3_journal_with_per_package_policies(
        self,
        tmp_path: Path,
    ) -> None:
        store = RunLogStore(root=tmp_path, run_id="journal-v3")
        journal = VerificationJournal(
            static_membership=(),
            run_id="journal-v3",
            command="search",
            source_snapshot_digest="snapshot",
            package_policies=(
                VerificationPackagePolicy(
                    package="alpha",
                    execution_policy_identity="policy-alpha",
                ),
                VerificationPackagePolicy(
                    package="beta",
                    execution_policy_identity="policy-beta",
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
        assert document["schema"] == "verification-journal-v3"
        assert "execution_policy_identity" not in document
        assert "packages" not in document

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


class TestRunLogStoreIndexRejection:
    @pytest.mark.parametrize(
        "document",
        (
            {"format": "unknown", "entries": {}},
            {"format": "pf-diagnosis-index-v1", "entries": []},
            {"format": "pf-diagnosis-index-v1", "entries": {"generation": []}},
            {
                "format": "pf-diagnosis-index-v1",
                "entries": {"generation": {"failure": 1}},
            },
            {
                "format": "pf-diagnosis-index-v1",
                "entries": {"generation": {"failure": "../outside.log"}},
            },
        ),
        ids=(
            "unknown-format",
            "entries-list",
            "generation-list",
            "non-string-path",
            "path-escape",
        ),
    )
    def test_run_log_store_rejects_an_invalid_diagnosis_index(
        self,
        tmp_path: Path,
        document: object,
    ) -> None:
        logs_root = tmp_path / ".pf/logs"
        logs_root.mkdir(parents=True)
        (logs_root / "diagnosis-index.json").write_text(
            json.dumps(document),
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match="could not read PF diagnosis log"):
            RunLogStore(root=tmp_path).lookup("generation", "failure")


def _process_result(*, exit_code: int = 0) -> ProcessResult:
    return ProcessResult(exit_code=exit_code, duration_seconds=0.1)


def _process_spec(tmp_path: Path, *argv: str) -> ProcessSpec:
    return ProcessSpec(
        argv=argv or ("tool",),
        cwd=tmp_path.as_posix(),
        timeout_seconds=5,
    )


class _FakeSecureLogDirectory:
    def __init__(
        self,
        *,
        root: Path,
        run_id: str,
        events: list[str] | None = None,
    ) -> None:
        self._root = root
        self._run_id = run_id
        self._events = events if events is not None else []
        self._identities: dict[Path, tuple[int, int]] = {}

    def _run_root(self) -> Path:
        return self._root / ".pf" / "logs" / self._run_id

    def _logs_root(self) -> Path:
        return self._root / ".pf" / "logs"

    def _remember(self, path: Path) -> None:
        self._identities[path] = (path.stat().st_dev, path.stat().st_ino)

    def _assert_intact(self) -> None:
        for path, identity in self._identities.items():
            linked = path.lstat()
            if path.is_symlink() or (linked.st_dev, linked.st_ino) != identity:
                raise OSError("PF run log directory identity changed")

    def ensure_run(self, manifest: str) -> None:
        run_root = self._run_root()
        run_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in (self._root, self._root / ".pf", self._logs_root(), run_root):
            self._remember(path)
        (run_root / "run.log").write_text(manifest, encoding="utf-8")

    def write_run_text(self, name: str, content: str) -> None:
        self.write_run_stream(name, lambda stream: stream.write(content))

    def write_run_stream(
        self,
        name: str,
        write_body: Callable[[TextIO], None],
    ) -> None:
        self._assert_intact()
        buf = StringIO()
        write_body(buf)
        (self._run_root() / name).write_text(buf.getvalue(), encoding="utf-8")
        self._assert_intact()

    def read_run_text(self, run_id: str, name: str, limit: int | None) -> str:
        text = (self._logs_root() / run_id / name).read_text(encoding="utf-8")
        return text if limit is None else text[:limit]

    def read_run_stream(
        self,
        run_id: str,
        name: str,
        read_body: Callable[[TextIO], object],
    ) -> object:
        with (self._logs_root() / run_id / name).open(encoding="utf-8") as stream:
            return read_body(stream)

    def read_logs_text(self, name: str, limit: int) -> str:
        self._events.append("logs-open")
        try:
            path = self._logs_root() / name
            if not path.is_file():
                raise FileNotFoundError(name)
            return path.read_text(encoding="utf-8")[:limit]
        finally:
            self._events.append("logs-close")

    def write_logs_text(self, name: str, content: str) -> None:
        path = self._logs_root() / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)

    def resolve_regular_log(self, relative: Path) -> Path | None:
        self._events.append("run-open")
        try:
            path = self._logs_root() / relative
            if path.is_symlink() or not path.is_file():
                raise OSError("unsafe PF log file")
            return Path(".pf") / "logs" / relative
        finally:
            self._events.append("run-close")

    def close(self) -> None:
        return


class TestRunLogStoreProcessOutput:
    def test_run_log_store_indexes_a_failure_without_exposing_the_path(
        self,
        tmp_path: Path,
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="diagnosis-run")
        result = _process_result(exit_code=2)
        logs.record(1, _process_spec(tmp_path), result)
        logs.associate("generation-a", "failure-a", result)

        assert logs.lookup("generation-a", "failure-a") == Path(
            ".pf/logs/diagnosis-run/process-0001.log"
        )
        assert logs.lookup("generation-a", "failure-missing") is None
        index = tmp_path / ".pf/logs/diagnosis-index.json"
        assert stat.S_IMODE(index.stat().st_mode) == 0o600
        assert str(tmp_path) not in index.read_text(encoding="utf-8")

    def test_run_log_store_refuses_to_index_an_unrecorded_current_process(
        self,
        tmp_path: Path,
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="diagnosis-run")
        result = _process_result(exit_code=2)

        with pytest.raises(
            InfrastructureError,
            match="could not write PF diagnosis index",
        ):
            logs.associate("generation-a", "failure-a", result)

    def test_run_log_store_replaces_and_removes_generation_associations(
        self,
        tmp_path: Path,
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="diagnosis-run")
        first = _process_result(exit_code=1)
        second = _process_result(exit_code=2)
        logs.record(1, _process_spec(tmp_path), first)
        logs.record(2, _process_spec(tmp_path), second)

        logs.replace_associations(
            "generation-a",
            (("failure-a", first), ("failure-b", second)),
        )
        logs.replace_associations(
            "generation-a",
            (("failure-b", second),),
        )

        assert logs.lookup("generation-a", "failure-a") is None
        assert logs.lookup("generation-a", "failure-b") == Path(
            ".pf/logs/diagnosis-run/process-0002.log"
        )

        logs.replace_associations(
            "generation-a",
            (),
            replace_generation=False,
            remove_failure_ids=("failure-b",),
        )

        assert logs.lookup("generation-a", "failure-b") is None

    def test_run_log_store_ignores_a_remote_failure_without_a_local_process(
        self,
        tmp_path: Path,
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="diagnosis-run")

        logs.replace_associations("generation-a", (("failure-a", None),))

        assert logs.lookup("generation-a", "failure-a") is None
        assert not (tmp_path / ".pf").exists()

    def test_run_log_store_refuses_a_symlinked_pf_directory(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / ".pf").symlink_to(outside, target_is_directory=True)
        logs = RunLogStore(root=tmp_path, run_id="unsafe-run")
        result = _process_result()

        with pytest.raises(InfrastructureError, match="could not write PF process log"):
            logs.record(1, _process_spec(tmp_path), result)

        assert not (outside / "logs").exists()

    def test_run_log_store_refuses_a_replaced_run_directory(
        self, tmp_path: Path
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="stable-run")
        result = _process_result()
        spec = _process_spec(tmp_path)
        logs.record(1, spec, result)
        run_root = tmp_path / ".pf/logs/stable-run"
        run_root.rename(tmp_path / ".pf/logs/original-run")
        outside = tmp_path / "outside"
        outside.mkdir()
        run_root.symlink_to(outside, target_is_directory=True)

        with pytest.raises(InfrastructureError, match="could not write PF process log"):
            logs.record(2, spec, result)

        assert not (outside / "process-0002.log").exists()

    def test_run_log_store_bounds_process_metadata(self, tmp_path: Path) -> None:
        logs = RunLogStore(root=tmp_path, run_id="bounded-run")
        result = _process_result()

        path = logs.record(
            1,
            ProcessSpec(
                argv=("tool", "x" * 200_000),
                cwd="/project/" + "y" * 200_000,
                environment=(EnvironmentVariable(name="Z" * 200_000, value="***"),),
                timeout_seconds=5,
            ),
            result,
        )

        detail = path.read_text(encoding="utf-8")
        assert path.stat().st_size < 100_000
        assert "[truncated by RunLogStore]" in detail

    def test_run_log_store_uses_a_platform_guard_without_dir_fd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "pf.runlog.secure_log_directory",
            lambda **kwargs: _FakeSecureLogDirectory(**kwargs),
        )
        logs = RunLogStore(root=tmp_path, run_id="portable-run")
        result = _process_result()
        spec = _process_spec(tmp_path)
        path = logs.record(1, spec, result)

        assert path.is_file()
        assert logs.reference_for(result) == path

        run_root = path.parent
        run_root.rename(tmp_path / ".pf/logs/portable-original")
        outside = tmp_path / "portable-outside"
        outside.mkdir()
        run_root.symlink_to(outside, target_is_directory=True)
        with pytest.raises(InfrastructureError, match="could not write PF process log"):
            logs.record(2, spec, result)
        assert not (outside / "process-0002.log").exists()

    def test_run_log_store_uses_the_windows_guard_for_index_and_offline_lookup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        guard_events: list[str] = []

        def factory(*, root: Path, run_id: str) -> _FakeSecureLogDirectory:
            return _FakeSecureLogDirectory(
                root=root,
                run_id=run_id,
                events=guard_events,
            )

        monkeypatch.setattr("pf.runlog.secure_log_directory", factory)
        logs = RunLogStore(root=tmp_path, run_id="windows-run")
        result = _process_result(exit_code=2)
        logs.record(1, _process_spec(tmp_path), result)
        logs.associate("generation-a", "failure-a", result)
        logs.close()
        guard_events.clear()

        offline = RunLogStore(root=tmp_path, run_id="offline")
        assert offline.lookup("generation-a", "failure-a") == Path(
            ".pf/logs/windows-run/process-0001.log"
        )
        assert guard_events == ["logs-open", "logs-close", "run-open", "run-close"]

    def test_run_log_store_fails_closed_without_a_secure_platform_backend(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class UnsupportedDirectory:
            def ensure_run(self, manifest: str) -> None:
                raise OSError("secure PF run logs are unsupported")

        monkeypatch.setattr(
            "pf.runlog.secure_log_directory",
            lambda **kwargs: UnsupportedDirectory(),
        )
        logs = RunLogStore(root=tmp_path, run_id="unsupported-run")
        result = _process_result()

        with pytest.raises(InfrastructureError, match="could not write PF process log"):
            logs.record(1, _process_spec(tmp_path), result)

    def test_run_log_store_patches_terminal_facts_without_dropping_streamed_body(
        self,
        tmp_path: Path,
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="patch-run")
        spec = _process_spec(tmp_path)
        writer = logs.begin_record(1, spec)
        writer.write_stdout("alpha" * 4_000)
        writer.write_stderr("beta" * 4_000)
        result = ProcessResult(exit_code=3, signal=None, duration_seconds=1.25)
        path = writer.finish(result)
        detail = path.read_text(encoding="utf-8")
        assert "alpha" * 4_000 in detail
        assert "beta" * 4_000 in detail
        assert "exit_code: 3" in detail
        assert "stdout_complete: true" in detail
        assert "stderr_complete: true" in detail
        assert logs.read_output(result) == ("alpha" * 4_000, "beta" * 4_000)

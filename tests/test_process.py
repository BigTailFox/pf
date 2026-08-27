from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import stat
import sys
from typing import TextIO

import pytest

from pf.adapters import process as process_module
from pf.adapters.process import (
    SecretRedactor,
    SubprocessRunner,
)
from pf._secure_runlog import WindowsDirectoryAdapter
from pf.errors import ConfigurationError, InfrastructureError
from pf.runlog import RunLogStore
from pf.schemas.evaluation import (
    EnvironmentVariable,
    ProcessEvent,
    ProcessResult,
    ProcessSpec,
)


class RecordingListener:
    def __init__(self) -> None:
        self.events: list[ProcessEvent] = []

    def consume(self, event: ProcessEvent) -> None:
        self.events.append(event)


class _ChunkLog:
    """Public Process Log seam that records every streamed chunk."""

    def __init__(self, root: Path) -> None:
        self.stdout_chunks: list[str] = []
        self.stderr_chunks: list[str] = []
        self._path = root / "streamed.log"

    def begin_record(self, process_id: int, spec: ProcessSpec) -> _ChunkLog:
        return self

    def write_stdout(self, chunk: str) -> None:
        self.stdout_chunks.append(chunk)

    def write_stderr(self, chunk: str) -> None:
        self.stderr_chunks.append(chunk)

    def finish(self, result: ProcessResult) -> Path:
        self._path.write_text(
            "".join(self.stdout_chunks) + "".join(self.stderr_chunks),
            encoding="utf-8",
        )
        return self._path

    def record(
        self,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessResult,
        stdout: str = "",
        stderr: str = "",
    ) -> Path:
        raise AssertionError("streaming redaction must not fall back to record()")

    def reference_for(self, result: ProcessResult) -> Path | None:
        return self._path if self._path.is_file() else None

    def read_output(self, result: ProcessResult) -> tuple[str, str] | None:
        return "".join(self.stdout_chunks), "".join(self.stderr_chunks)


def _emit_files(tmp_path: Path, stdout: str = "", stderr: str = "") -> Path:
    stdout_path = tmp_path / "payload.stdout"
    stderr_path = tmp_path / "payload.stderr"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    script = tmp_path / "emit_payload.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write(open({str(stdout_path)!r}, encoding='utf-8').read())\n"
        f"sys.stderr.write(open({str(stderr_path)!r}, encoding='utf-8').read())\n",
        encoding="utf-8",
    )
    return script


class _ExactOverlapRedactor(SecretRedactor):
    """Keep only a short overlap so keep/pending can split a secret or URL."""

    def __init__(self, secrets: tuple[str, ...] = (), overlap: int = 8) -> None:
        super().__init__(secrets)
        self._overlap = overlap

    def overlap_bytes(self) -> int:
        return self._overlap


class TestSubprocessRunner:
    def test_subprocess_runner_captures_and_redacts_external_output(
        self, tmp_path: Path
    ) -> None:
        runner = SubprocessRunner(redactor=SecretRedactor(("top-secret",)))
        result = runner.run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import sys; print('token=top-secret'); print('problem', file=sys.stderr)",
                ),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        assert result.exit_code == 0
        assert result.signal is None
        assert result.timed_out is False
        assert result.stdout == "token=***\n"
        assert result.stderr == "problem\n"
        assert "top-secret" not in result.stdout
        assert "top-secret" not in result.model_dump_json()

    def test_subprocess_runner_records_redacted_bounded_process_logs(
        self,
        tmp_path: Path,
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="test-run")
        runner = SubprocessRunner(
            redactor=SecretRedactor(("top-secret",)),
            logs=logs,
            cache_limit=32,
        )
        result = runner.run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "print('token=top-secret'); print('x' * 100)",
                ),
                cwd=tmp_path.as_posix(),
                environment=(
                    EnvironmentVariable(name="DEMO_TOKEN", value="top-secret"),
                ),
                timeout_seconds=5,
            )
        )

        log_path = logs.reference_for(result)

        assert log_path == tmp_path / ".pf/logs/test-run/process-0001.log"
        assert log_path is not None
        detail = log_path.read_text(encoding="utf-8")
        assert "DEMO_TOKEN" in detail
        assert "top-secret" not in detail
        assert "stdout_complete: true" in detail
        assert "token=***" in detail
        assert (tmp_path / ".pf/logs/test-run/run.log").is_file()
        assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700
        assert ".pf/logs" not in result.model_dump_json()

    def test_subprocess_runner_redacts_environment_values_used_by_this_process(
        self,
        tmp_path: Path,
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="environment-secret")
        runner = SubprocessRunner(logs=logs)

        result = runner.run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import os; print(os.environ['DEMO_TOKEN'])",
                ),
                cwd=tmp_path.as_posix(),
                environment=(
                    EnvironmentVariable(name="DEMO_TOKEN", value="runtime-secret"),
                ),
                timeout_seconds=5,
            )
        )

        assert result.stdout == "***\n"
        log_path = logs.reference_for(result)
        assert log_path is not None
        assert "runtime-secret" not in log_path.read_text(encoding="utf-8")

    def test_subprocess_runner_applies_environment_removals_before_overrides(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PF_TEST_PARENT_ONLY", "parent-only")
        monkeypatch.setenv("PF_TEST_REPLACED", "parent-value")

        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import os; "
                    "print(os.environ.get('PF_TEST_PARENT_ONLY', 'missing'), "
                    "os.environ.get('PF_TEST_REPLACED', 'missing'))",
                ),
                cwd=tmp_path.as_posix(),
                environment=(
                    EnvironmentVariable(name="PF_TEST_REPLACED", value="child-value"),
                ),
                environment_removals=("PF_TEST_PARENT_ONLY", "PF_TEST_REPLACED"),
                timeout_seconds=5,
            )
        )

        assert result.stdout == "missing ***\n"

    def test_subprocess_runner_persists_complete_output_within_the_capture_limit(
        self,
        tmp_path: Path,
    ) -> None:
        payload = ("progress-line\n" * 400) + "484 passed in 11.12s\n"
        script = tmp_path / "emit.py"
        script.write_text(f"print({payload!r}, end='')\n", encoding="utf-8")
        logs = RunLogStore(root=tmp_path, run_id="complete-run")
        result = SubprocessRunner(logs=logs).run(
            ProcessSpec(
                argv=(sys.executable, script.as_posix()),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        log_path = logs.reference_for(result)
        assert result.stdout_complete is True
        assert payload in result.stdout
        assert log_path is not None
        detail = log_path.read_text(encoding="utf-8")
        assert payload in detail
        assert "[truncated by RunLogStore]" not in detail

    def test_subprocess_runner_reports_a_redacted_start_error(
        self, tmp_path: Path
    ) -> None:
        result = SubprocessRunner(
            redactor=SecretRedactor(("missing-secret",)),
        ).run(
            ProcessSpec(
                argv=("missing-secret-executable",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=None,
            )
        )

        assert result.exit_code is None
        assert result.start_error is not None
        assert "missing-secret" not in result.start_error
        assert "***" in result.start_error

    def test_subprocess_runner_emits_finished_activity_after_a_start_error(
        self,
        tmp_path: Path,
    ) -> None:
        listener = RecordingListener()
        SubprocessRunner(listener=listener).run(
            ProcessSpec(
                argv=("missing-executable-for-progress",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=None,
            )
        )

        assert [event.state for event in listener.events] == ["started", "finished"]
        assert listener.events[1].duration_seconds is not None

    def test_subprocess_runner_times_out_and_kills_a_stubborn_process(
        self,
        tmp_path: Path,
    ) -> None:
        result = SubprocessRunner(terminate_grace_seconds=0.01).run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(5)",
                ),
                cwd=tmp_path.as_posix(),
                timeout_seconds=0.1,
                start_new_session=False,
            )
        )

        assert result.timed_out is True
        assert result.signal is not None

    def test_subprocess_runner_reports_a_process_signal(self, tmp_path: Path) -> None:
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import os,signal; os.kill(os.getpid(), signal.SIGTERM)",
                ),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        assert result.exit_code is None
        assert result.signal is not None
        assert result.timed_out is False

    def test_subprocess_runner_cache_keeps_tails_without_marking_logs_incomplete(
        self,
        tmp_path: Path,
    ) -> None:
        payload = "abcdefghij"
        logs = RunLogStore(root=tmp_path, run_id="cache-run")
        runner = SubprocessRunner(logs=logs, cache_limit=4)
        result = runner.run(
            ProcessSpec(
                argv=(sys.executable, "-c", "print('abcdefghij', end='')"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        assert result.stdout_complete is True
        assert result.stdout == "ghij"
        assert len(result.stdout.encode()) <= 4
        log_path = logs.reference_for(result)
        assert log_path is not None
        assert payload in log_path.read_text(encoding="utf-8")
        assert runner.output(result).stdout == payload

    def test_subprocess_runner_output_without_logs_stays_within_cache_limit(
        self,
        tmp_path: Path,
    ) -> None:
        payload = "abcdefghij"
        runner = SubprocessRunner(cache_limit=4)
        result = runner.run(
            ProcessSpec(
                argv=(sys.executable, "-c", "print('abcdefghij', end='')"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        cached = runner.output(result)
        assert result.stdout == "ghij"
        assert cached.stdout == "ghij"
        assert len(cached.stdout.encode()) <= 4
        assert payload not in cached.stdout

    def test_subprocess_runner_honors_a_larger_process_spec_cache_limit(
        self,
        tmp_path: Path,
    ) -> None:
        payload = "abcdefghij"
        result = SubprocessRunner(cache_limit=4).run(
            ProcessSpec(
                argv=(sys.executable, "-c", "print('abcdefghij', end='')"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
                summary_limit=32,
            )
        )

        assert result.stdout == payload
        assert result.stdout_complete is True

    def test_subprocess_runner_passes_the_host_terminal_size(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("COLUMNS", raising=False)
        monkeypatch.delenv("LINES", raising=False)
        monkeypatch.setattr(
            os,
            "get_terminal_size",
            lambda fd=-1: (_ for _ in ()).throw(OSError("not a tty")),
        )
        monkeypatch.setattr(
            "shutil.get_terminal_size",
            lambda fallback=(80, 24): os.terminal_size((120, 40)),
        )
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import os; print(os.environ['COLUMNS'], os.environ['LINES'])",
                ),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        assert result.exit_code == 0
        assert result.stdout == "120 40\n"

    def test_subprocess_runner_uses_stderr_width_when_stdout_is_not_a_terminal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("COLUMNS", raising=False)
        monkeypatch.delenv("LINES", raising=False)

        def terminal_size(fd: int = -1) -> os.terminal_size:
            if fd == sys.stderr.fileno():
                return os.terminal_size((100, 30))
            raise OSError("not a tty")

        monkeypatch.setattr(os, "get_terminal_size", terminal_size)
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import os; print(os.environ['COLUMNS'], os.environ['LINES'])",
                ),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        assert result.exit_code == 0
        assert result.stdout == "100 30\n"

    def test_subprocess_runner_host_terminal_size_overrides_spec_columns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            os,
            "get_terminal_size",
            lambda fd=-1: os.terminal_size((120, 40)),
        )
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import os; print(os.environ['COLUMNS'], os.environ['LINES'])",
                ),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
                environment=(EnvironmentVariable(name="COLUMNS", value="40"),),
            )
        )

        assert result.exit_code == 0
        assert result.stdout == "120 ***\n"

    def test_subprocess_runner_emits_redacted_process_activity(
        self, tmp_path: Path
    ) -> None:
        listener = RecordingListener()
        result = SubprocessRunner(
            redactor=SecretRedactor(("top-secret",)),
            listener=listener,
        ).run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "print('token=top-secret')",
                ),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        assert result.exit_code == 0
        assert [event.state for event in listener.events] == ["started", "finished"]
        assert listener.events[0].process_id == listener.events[1].process_id
        assert listener.events[0].argv == listener.events[1].argv
        assert "top-secret" not in " ".join(listener.events[0].argv)
        assert "***" in " ".join(listener.events[0].argv)
        assert listener.events[1].duration_seconds is not None
        assert listener.events[1].duration_seconds >= 0


class TestRunLogStoreProcessOutput:
    def test_run_log_store_indexes_a_failure_without_exposing_the_path(
        self,
        tmp_path: Path,
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="diagnosis-run")
        runner = SubprocessRunner(logs=logs)
        result = runner.run(
            ProcessSpec(
                argv=(sys.executable, "-c", "raise SystemExit(2)"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

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
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(sys.executable, "-c", "raise SystemExit(2)"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

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
        runner = SubprocessRunner(logs=logs)
        first = runner.run(
            ProcessSpec(
                argv=(sys.executable, "-c", "raise SystemExit(1)"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )
        second = runner.run(
            ProcessSpec(
                argv=(sys.executable, "-c", "raise SystemExit(2)"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

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

    def test_run_log_store_refuses_a_symlinked_pf_directory(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / ".pf").symlink_to(outside, target_is_directory=True)
        logs = RunLogStore(root=tmp_path, run_id="unsafe-run")
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        with pytest.raises(InfrastructureError, match="could not write PF process log"):
            logs.record(
                1,
                ProcessSpec(
                    argv=(sys.executable, "-c", "pass"),
                    cwd=tmp_path.as_posix(),
                    timeout_seconds=5,
                ),
                result,
            )

        assert not (outside / "logs").exists()

    def test_run_log_store_refuses_a_replaced_run_directory(
        self, tmp_path: Path
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="stable-run")
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )
        spec = ProcessSpec(
            argv=(sys.executable, "-c", "pass"),
            cwd=tmp_path.as_posix(),
            timeout_seconds=5,
        )
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
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

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
            lambda **kwargs: WindowsDirectoryAdapter(**kwargs),
        )

        class FakeWindowsRunDirectory:
            def __init__(self, root: Path, run_root: Path | None = None) -> None:
                self.root = root
                self.run_root = run_root
                guarded = (root, root / ".pf", root / ".pf/logs")
                self.paths = (*guarded, *((run_root,) if run_root is not None else ()))
                self.identities = tuple(
                    (path.stat().st_dev, path.stat().st_ino) for path in self.paths
                )

            @classmethod
            def create(
                cls,
                *,
                root: Path,
                run_id: str,
            ) -> "FakeWindowsRunDirectory":
                run_root = root / ".pf/logs" / run_id
                run_root.mkdir(mode=0o700, parents=True)
                return cls(root, run_root)

            @classmethod
            def open_existing(
                cls,
                *,
                root: Path,
                run_id: str | None = None,
            ) -> "FakeWindowsRunDirectory":
                run_root = root / ".pf/logs" / run_id if run_id is not None else None
                return cls(root, run_root)

            @property
            def logs_root(self) -> Path:
                return self.root / ".pf/logs"

            def assert_intact(self) -> None:
                for path, identity in zip(self.paths, self.identities):
                    linked = path.lstat()
                    if path.is_symlink() or (linked.st_dev, linked.st_ino) != identity:
                        raise OSError("PF run log directory identity changed")

            def write_private(self, path: Path, content: str) -> None:
                self.assert_intact()
                path.write_text(content, encoding="utf-8")
                self.assert_intact()

            def write_private_stream(
                self, path: Path, write_body: Callable[[TextIO], None]
            ) -> None:
                from io import StringIO

                buf = StringIO()
                write_body(buf)
                self.write_private(path, buf.getvalue())

            def read_bounded_text(self, path: Path, *, limit: int) -> str:
                self.assert_intact()
                return path.read_text(encoding="utf-8")[: limit + 1]

            def validate_regular_file(self, path: Path) -> None:
                self.assert_intact()
                if path.is_symlink() or not path.is_file():
                    raise OSError("unsafe PF log file")

            def close(self) -> None:
                pass

        monkeypatch.setattr(
            "pf._secure_runlog.WindowsRunDirectory",
            FakeWindowsRunDirectory,
        )
        logs = RunLogStore(root=tmp_path, run_id="portable-run")
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        path = logs.record(
            1,
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            ),
            result,
        )

        assert path.is_file()
        assert logs.reference_for(result) == path

        run_root = path.parent
        run_root.rename(tmp_path / ".pf/logs/portable-original")
        outside = tmp_path / "portable-outside"
        outside.mkdir()
        run_root.symlink_to(outside, target_is_directory=True)
        second_spec = ProcessSpec(
            argv=(sys.executable, "-c", "pass"),
            cwd=tmp_path.as_posix(),
            timeout_seconds=5,
        )
        with pytest.raises(InfrastructureError, match="could not write PF process log"):
            logs.record(2, second_spec, result)
        assert not (outside / "process-0002.log").exists()

    def test_run_log_store_uses_the_windows_guard_for_index_and_offline_lookup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "pf.runlog.secure_log_directory",
            lambda **kwargs: WindowsDirectoryAdapter(**kwargs),
        )
        guard_events: list[str] = []

        class FakeWindowsRunDirectory:
            def __init__(self, root: Path, run_id: str | None) -> None:
                self.root = root
                self.run_id = run_id
                guard_events.append("run-open" if run_id is not None else "logs-open")
                guarded = [root, root / ".pf", root / ".pf/logs"]
                if run_id is not None:
                    guarded.append(root / ".pf/logs" / run_id)
                self.paths = tuple(guarded)
                self.identities = tuple(
                    (path.stat().st_dev, path.stat().st_ino) for path in self.paths
                )

            @classmethod
            def create(
                cls,
                *,
                root: Path,
                run_id: str,
            ) -> "FakeWindowsRunDirectory":
                (root / ".pf/logs" / run_id).mkdir(mode=0o700, parents=True)
                return cls(root, run_id)

            @classmethod
            def open_existing(
                cls,
                *,
                root: Path,
                run_id: str | None = None,
            ) -> "FakeWindowsRunDirectory":
                return cls(root, run_id)

            @property
            def logs_root(self) -> Path:
                return self.root / ".pf/logs"

            def assert_intact(self) -> None:
                for path, identity in zip(self.paths, self.identities):
                    linked = path.lstat()
                    if path.is_symlink() or (linked.st_dev, linked.st_ino) != identity:
                        raise OSError("PF log directory identity changed")

            def write_private(self, path: Path, content: str) -> None:
                self.assert_intact()
                path.write_text(content, encoding="utf-8")

            def write_private_stream(
                self, path: Path, write_body: Callable[[TextIO], None]
            ) -> None:
                from io import StringIO

                buf = StringIO()
                write_body(buf)
                self.write_private(path, buf.getvalue())

            def read_bounded_text(self, path: Path, *, limit: int) -> str:
                self.assert_intact()
                return path.read_text(encoding="utf-8")[: limit + 1]

            def validate_regular_file(self, path: Path) -> None:
                self.assert_intact()
                if path.is_symlink() or not path.is_file():
                    raise OSError("unsafe PF log file")

            def close(self) -> None:
                guard_events.append(
                    "run-close" if self.run_id is not None else "logs-close"
                )

        monkeypatch.setattr(
            "pf._secure_runlog.WindowsRunDirectory",
            FakeWindowsRunDirectory,
        )
        logs = RunLogStore(root=tmp_path, run_id="windows-run")
        runner = SubprocessRunner(logs=logs)
        result = runner.run(
            ProcessSpec(
                argv=(sys.executable, "-c", "raise SystemExit(2)"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

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
        result = SubprocessRunner().run(
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )

        with pytest.raises(InfrastructureError, match="could not write PF process log"):
            logs.record(
                1,
                ProcessSpec(
                    argv=(sys.executable, "-c", "pass"),
                    cwd=tmp_path.as_posix(),
                    timeout_seconds=5,
                ),
                result,
            )

    def test_run_log_store_patches_terminal_facts_without_dropping_streamed_body(
        self,
        tmp_path: Path,
    ) -> None:
        logs = RunLogStore(root=tmp_path, run_id="patch-run")
        spec = ProcessSpec(
            argv=("tool",),
            cwd=tmp_path.as_posix(),
            timeout_seconds=5,
        )
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


class TestStreamRedaction:
    @pytest.mark.parametrize("offset", (0, 8, 20, 23, 24, 27))
    def test_run_matches_one_shot_redaction_at_chunk_boundary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        offset: int,
    ) -> None:
        secret = "top-secret-token"
        chunk = 24
        monkeypatch.setattr(process_module, "_STREAM_CHUNK_SIZE", chunk)
        redactor = _ExactOverlapRedactor((secret,))
        payload = ("P" * offset) + secret + "END"
        logs = _ChunkLog(tmp_path)
        runner = SubprocessRunner(redactor=redactor, logs=logs)
        result = runner.run(
            ProcessSpec(
                argv=(sys.executable, _emit_files(tmp_path, payload).as_posix()),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )
        streamed = "".join(logs.stdout_chunks)
        expected = redactor.redact(payload)
        assert secret not in streamed
        assert streamed == expected
        assert runner.output(result).stdout == expected

    @pytest.mark.parametrize("split", ("none", "scheme", "userinfo", "at"))
    def test_run_hides_url_userinfo_at_chunk_boundary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        split: str,
    ) -> None:
        chunk = 32
        monkeypatch.setattr(process_module, "_STREAM_CHUNK_SIZE", chunk)
        userinfo = "registry-user:p4ssw0rd-across-chunks"
        url = f"https://{userinfo}@pypi.example/simple/pkg"
        prefix_length = {
            "none": 0,
            "scheme": chunk - len("https://"),
            "userinfo": chunk - len("https://registry-user:"),
            "at": (2 * chunk) - url.index("@") - 1,
        }[split]
        payload = ("U" * prefix_length) + url
        redactor = _ExactOverlapRedactor(overlap=8)
        logs = _ChunkLog(tmp_path)
        runner = SubprocessRunner(redactor=redactor, logs=logs)
        result = runner.run(
            ProcessSpec(
                argv=(sys.executable, _emit_files(tmp_path, payload).as_posix()),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )
        streamed = "".join(logs.stdout_chunks)
        expected = redactor.redact(payload)
        assert userinfo not in streamed
        assert userinfo not in result.stdout
        assert streamed == expected

    def test_streamed_multibyte_utf8_next_to_secret_stays_equivalent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(process_module, "_STREAM_CHUNK_SIZE", 16)
        secret = "top-secret-token"
        payload = ("€" * 20) + secret + "Ω"
        redactor = _ExactOverlapRedactor((secret,), overlap=8)
        logs = _ChunkLog(tmp_path)
        runner = SubprocessRunner(redactor=redactor, logs=logs)
        runner.run(
            ProcessSpec(
                argv=(sys.executable, _emit_files(tmp_path, payload).as_posix()),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )
        streamed = "".join(logs.stdout_chunks)
        assert secret not in streamed
        assert streamed == redactor.redact(payload)
        assert streamed.startswith("€" * 20)
        assert streamed.endswith("Ω")

    def test_overlapping_secrets_prefer_the_longest_value_on_every_surface(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(process_module, "_STREAM_CHUNK_SIZE", 8)
        short = "secret-token"
        long = "supersecret-token"
        payload = f"pre {long} mid {short} end"
        redactor = _ExactOverlapRedactor((short, long), overlap=8)
        logs = _ChunkLog(tmp_path)
        runner = SubprocessRunner(redactor=redactor, logs=logs)
        result = runner.run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    _emit_files(tmp_path, payload, payload).as_posix(),
                ),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )
        expected = redactor.redact(payload)
        assert expected == "pre *** mid *** end"
        assert "".join(logs.stdout_chunks) == expected
        assert "".join(logs.stderr_chunks) == expected
        assert runner.output(result).stdout == expected
        assert runner.output(result).stderr == expected
        assert short not in "".join(logs.stdout_chunks)
        assert long not in "".join(logs.stderr_chunks)

    def test_streamed_secret_is_hidden_on_stderr_cache_and_process_log(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(process_module, "_STREAM_CHUNK_SIZE", 32)
        secret = "stderr-secret-token"
        payload = ("E" * 28) + secret
        redactor = _ExactOverlapRedactor((secret,), overlap=8)
        store = RunLogStore(root=tmp_path, run_id="redact-run")
        runner = SubprocessRunner(redactor=redactor, logs=store)
        result = runner.run(
            ProcessSpec(
                argv=(
                    sys.executable,
                    _emit_files(tmp_path, stdout="ok\n", stderr=payload).as_posix(),
                ),
                cwd=tmp_path.as_posix(),
                timeout_seconds=5,
            )
        )
        logged = store.read_output(result)
        log_path = store.reference_for(result)
        assert logged is not None
        assert log_path is not None
        assert secret not in result.stderr
        assert secret not in runner.output(result).stderr
        assert secret not in logged[1]
        assert secret not in log_path.read_text(encoding="utf-8")
        assert redactor.redact(payload) == logged[1]

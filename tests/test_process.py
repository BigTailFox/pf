from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

import pytest

from pf.adapters.process import SecretRedactor, SubprocessRunner
from pf.errors import InfrastructureError
from pf.runlog import RunLogStore
from pf.schemas.evaluation import EnvironmentVariable, ProcessEvent, ProcessSpec


def test_subprocess_runner_captures_and_redacts_external_output(tmp_path: Path) -> None:
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
    assert result.stdout_tail == "token=***\n"
    assert result.stderr_tail == "problem\n"
    assert "top-secret" not in result.model_dump_json()


def test_subprocess_runner_records_redacted_bounded_process_logs(
    tmp_path: Path,
) -> None:
    logs = RunLogStore(root=tmp_path, run_id="test-run")
    runner = SubprocessRunner(
        redactor=SecretRedactor(("top-secret",)),
        logs=logs,
        summary_limit=32,
        tail_limit=12,
    )
    result = runner.run(
        ProcessSpec(
            argv=(
                sys.executable,
                "-c",
                "print('token=top-secret'); print('x' * 100)",
            ),
            cwd=tmp_path.as_posix(),
            environment=(EnvironmentVariable(name="DEMO_TOKEN", value="top-secret"),),
            timeout_seconds=5,
        )
    )

    log_path = logs.reference_for(result)

    assert log_path == tmp_path / ".pf/logs/test-run/process-0001.log"
    assert log_path is not None
    detail = log_path.read_text(encoding="utf-8")
    assert "DEMO_TOKEN" in detail
    assert "top-secret" not in detail
    assert "stdout_truncated: true" in detail
    assert "token=***" in detail
    assert (tmp_path / ".pf/logs/test-run/run.log").is_file()
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700
    assert ".pf/logs" not in result.model_dump_json()


def test_run_log_store_refuses_a_symlinked_pf_directory(tmp_path: Path) -> None:
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


def test_run_log_store_refuses_a_replaced_run_directory(tmp_path: Path) -> None:
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


def test_run_log_store_bounds_process_metadata(tmp_path: Path) -> None:
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        RunLogStore,
        "_supports_secure_dir_fd",
        staticmethod(lambda: False),
    )
    monkeypatch.setattr(
        RunLogStore,
        "_supports_windows_guard",
        staticmethod(lambda: True),
    )

    class FakeWindowsRunDirectory:
        def __init__(self, run_root: Path) -> None:
            self.run_root = run_root
            opened = run_root.stat()
            self.identity = opened.st_dev, opened.st_ino

        @classmethod
        def create(
            cls,
            *,
            root: Path,
            run_id: str,
        ) -> "FakeWindowsRunDirectory":
            run_root = root / ".pf/logs" / run_id
            run_root.mkdir(mode=0o700, parents=True)
            return cls(run_root)

        def assert_intact(self) -> None:
            linked = self.run_root.lstat()
            if self.run_root.is_symlink() or (
                linked.st_dev,
                linked.st_ino,
            ) != self.identity:
                raise OSError("PF run log directory identity changed")

    monkeypatch.setattr(
        "pf.runlog.WindowsRunDirectory",
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


def test_run_log_store_fails_closed_without_a_secure_platform_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        RunLogStore,
        "_supports_secure_dir_fd",
        staticmethod(lambda: False),
    )
    monkeypatch.setattr(
        RunLogStore,
        "_supports_windows_guard",
        staticmethod(lambda: False),
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


def test_subprocess_runner_reports_a_redacted_start_error(tmp_path: Path) -> None:
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
            timeout_seconds=1,
            start_new_session=False,
        )
    )

    assert result.timed_out is True
    assert result.signal is not None


def test_subprocess_runner_reports_a_process_signal(tmp_path: Path) -> None:
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


def test_subprocess_runner_bounds_summary_and_keeps_the_tail(tmp_path: Path) -> None:
    result = SubprocessRunner(summary_limit=4, tail_limit=5).run(
        ProcessSpec(
            argv=(sys.executable, "-c", "print('abcdefghij', end='')"),
            cwd=tmp_path.as_posix(),
            timeout_seconds=5,
        )
    )

    assert result.stdout_summary == "abcd"
    assert result.stdout_tail == "fghij"
    assert result.stdout_truncated is True


def test_subprocess_runner_honors_a_larger_process_spec_summary_limit(
    tmp_path: Path,
) -> None:
    payload = "abcdefghij"
    result = SubprocessRunner(summary_limit=4, tail_limit=5).run(
        ProcessSpec(
            argv=(sys.executable, "-c", "print('abcdefghij', end='')"),
            cwd=tmp_path.as_posix(),
            timeout_seconds=5,
            summary_limit=32,
        )
    )

    assert result.stdout_summary == payload
    assert result.stdout_truncated is False


def test_subprocess_runner_passes_the_host_terminal_size(
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
    assert result.stdout_summary == "120 40\n"


def test_subprocess_runner_uses_stderr_width_when_stdout_is_not_a_terminal(
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
    assert result.stdout_summary == "100 30\n"


def test_subprocess_runner_host_terminal_size_overrides_spec_columns(
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
    assert result.stdout_summary == "120 40\n"


class RecordingListener:
    def __init__(self) -> None:
        self.events: list[ProcessEvent] = []

    def consume(self, event: ProcessEvent) -> None:
        self.events.append(event)


def test_subprocess_runner_emits_redacted_process_activity(tmp_path: Path) -> None:
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

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from pf.adapters.process import SecretRedactor, SubprocessRunner
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

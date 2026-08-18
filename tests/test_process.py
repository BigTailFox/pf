from __future__ import annotations

from pathlib import Path
import sys

from pf.adapters.process import SecretRedactor, SubprocessRunner
from pf.schemas.evaluation import ProcessSpec


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

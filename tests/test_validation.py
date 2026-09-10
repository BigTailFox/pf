from __future__ import annotations

from pathlib import Path
from runpy import run_path
import subprocess
from typing import Callable, TextIO, cast

import pytest


ROOT = Path(__file__).resolve().parents[1]
main = cast(Callable[..., int], run_path(str(ROOT / "scripts/validate.py"))["main"])
pytestmark = pytest.mark.infra


class TestValidationExecution:
    def test_main_retains_complete_logs_for_successful_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def run(command: tuple[str, ...], *, cwd: Path, stdout: TextIO, stderr: int):
            assert cwd == ROOT
            assert stderr == subprocess.STDOUT
            stdout.write("complete successful diagnostic\n")
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(subprocess, "run", run)
        assert main(["docs", "--log-dir", str(tmp_path)]) == 0
        logs = list(tmp_path.glob("*/*.log"))
        assert logs
        assert all(p.read_text() == "complete successful diagnostic\n" for p in logs)
        output = capsys.readouterr().out
        assert "docs: PASS" in output
        assert "complete successful diagnostic" not in output
        assert all(str(p) in output for p in logs)

    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [(7, 7), (-15, 143), (FileNotFoundError("missing tool"), 127), (KeyboardInterrupt(), 130)],
        ids=["child-failure", "signal", "missing-tool", "interrupted"],
    )
    def test_main_stops_at_failure_and_preserves_exit_status(
        self, outcome: int | BaseException, expected: int,
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls = []

        def run(command: tuple[str, ...], *, cwd: Path, stdout: TextIO, stderr: int):
            calls.append(command)
            stdout.write("first diagnostic\n" + "detail\n" * 50 + "failure context\n")
            if isinstance(outcome, BaseException):
                raise outcome
            return subprocess.CompletedProcess(command, outcome)

        monkeypatch.setattr(subprocess, "run", run)
        assert main(["docs", "--log-dir", str(tmp_path)]) == expected
        assert len(calls) == 1
        log, = tmp_path.glob("*/*.log")
        assert "first diagnostic" in log.read_text()
        output = capsys.readouterr().out
        assert "first diagnostic" not in output
        assert "failure context" in output
        assert f"exit={expected}" in output
        assert "PASS" not in output

    def test_main_dry_run_explains_without_execution_or_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def run(*args, **kwargs):
            pytest.fail("dry-run must not execute commands")

        monkeypatch.setattr(subprocess, "run", run)
        logs = tmp_path / "logs"
        assert main(["coverage", "--dry-run", "--log-dir", str(logs)]) == 0
        assert not logs.exists()
        output = capsys.readouterr().out
        assert str(ROOT) in output
        assert "--cov=pf" in output
        assert "PASS" not in output

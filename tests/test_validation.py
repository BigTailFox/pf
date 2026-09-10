from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from runpy import run_path
import shlex
import shutil
import subprocess
from typing import Callable, TextIO, cast

import pytest


ROOT = Path(__file__).resolve().parents[1]
main = cast(Callable[..., int], run_path(str(ROOT / "scripts/validate.py"))["main"])
pytestmark = pytest.mark.infra


@pytest.fixture
def validation_repo(tmp_path: Path) -> tuple[Path, Callable[..., int]]:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/validate.py", root / "scripts/validate.py")
    (root / "input.txt").write_text("committed\n")
    for args in (
        ("init",), ("add", "."),
        ("-c", "user.name=Validation Test", "-c", "user.email=validation@example.invalid",
         "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={root / '.no-hooks'}", "commit", "-m", "baseline"),
    ):
        subprocess.run(("git", *args), cwd=root, capture_output=True, check=True)
    return root, cast(Callable[..., int], run_path(str(root / "scripts/validate.py"))["main"])


def read_manifest(logs: Path) -> dict:
    path, = logs.glob("*/manifest.json")
    return json.loads(path.read_text())


class TestValidationExecution:
    def test_main_retains_complete_logs_for_successful_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        validation_repo: tuple[Path, Callable[..., int]],
    ) -> None:
        root, validate = validation_repo
        real_run = subprocess.run

        def run(command: tuple[str, ...], *, cwd: Path, stdout: TextIO | None = None, stderr: int | None = None, **kwargs):
            if command[0] == "git":
                return real_run(command, cwd=cwd, **kwargs)
            assert cwd == root
            assert stderr == subprocess.STDOUT
            assert stdout is not None
            manifest = read_manifest(tmp_path / "logs")
            assert any(step["status"] == "running" for step in manifest["steps"])
            stdout.write("complete successful diagnostic\n")
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(subprocess, "run", run)
        assert validate(["docs", "--log-dir", str(tmp_path / "logs")]) == 0
        logs = list((tmp_path / "logs").glob("*/*.log"))
        assert logs
        assert all(p.read_text() == "complete successful diagnostic\n" for p in logs)
        output = capsys.readouterr().out
        assert "docs: PASS" in output
        assert "complete successful diagnostic" not in output
        assert all(str(p) in output for p in logs)
        manifest = read_manifest(tmp_path / "logs")
        assert manifest["status"] == "passed"
        assert manifest["exit_code"] == 0
        assert manifest["cwd"] == str(root)
        assert datetime.fromisoformat(manifest["finished_at"]) >= datetime.fromisoformat(manifest["started_at"])
        assert manifest["environment"]["python"]["executable"]
        assert manifest["environment"]["tools"]["git"]["version"].startswith("git version ")
        assert any(package["name"] == "pytest" for package in manifest["environment"]["packages"])
        for step in manifest["steps"]:
            assert step["status"] == "passed" and step["exit_code"] == 0
            assert step["source_unchanged"] is True
            assert step["source_before"]["status"] == ""
            assert step["duration_seconds"] >= 0
            assert datetime.fromisoformat(step["finished_at"]) >= datetime.fromisoformat(step["started_at"])
            assert any(p.name == step["log"] for p in logs)

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
        real_run = subprocess.run

        def run(command: tuple[str, ...], *, cwd: Path, stdout: TextIO | None = None, stderr: int | None = None, **kwargs):
            if command[0] == "git":
                return real_run(command, cwd=cwd, **kwargs)
            calls.append(command)
            assert stdout is not None
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
        manifest = read_manifest(tmp_path)
        assert manifest["status"] == "failed" and manifest["exit_code"] == expected
        failed, pending = manifest["steps"]
        assert failed["status"] == "failed" and failed["exit_code"] == expected
        assert pending["status"] == "pending" and pending["exit_code"] is None
        assert not (log.parent / pending["log"]).exists()

    def test_main_records_tracked_and_untracked_input_changes_during_a_step(
        self, validation_repo: tuple[Path, Callable[..., int]], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root, validate = validation_repo
        (root / "input.txt").write_text("dirty before\n")
        (root / "extra.txt").write_text("before\n")
        real_run = subprocess.run

        def run(command, **kwargs):
            if command[0] == "git":
                return real_run(command, **kwargs)
            (root / "input.txt").write_text("dirty after\n")
            (root / "extra.txt").write_text("after\n")
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(subprocess, "run", run)
        logs = root / "validation"
        assert validate(["docs", "--base", "HEAD", "--log-dir", str(logs)]) == 0
        manifest = read_manifest(logs)
        first, second = manifest["steps"]
        assert manifest["base"] == "HEAD"
        assert manifest["base_commit"] == first["source_before"]["head"]
        assert first["command"][-2:] == ["--base", manifest["base_commit"]]
        assert "--base" not in second["command"]
        assert first["source_unchanged"] is False
        assert second["source_unchanged"] is True
        before, after = first["source_before"], first["source_after"]
        assert before["head"] == after["head"]
        assert before["status"] == after["status"]
        assert before["worktree_diff_sha256"] != after["worktree_diff_sha256"]
        assert before["untracked"]["extra.txt"] != after["untracked"]["extra.txt"]
        assert "validation/" not in after["status"]

    def test_main_marks_source_identity_unavailable_without_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def run(command, **kwargs):
            if command[0] == "git":
                raise FileNotFoundError("git unavailable")
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(subprocess, "run", run)
        assert main(["docs", "--log-dir", str(tmp_path)]) == 0
        manifest = read_manifest(tmp_path)
        assert "error" in manifest["environment"]["tools"]["git"]
        for step in manifest["steps"]:
            assert step["source_unchanged"] is None
            assert "git unavailable" in step["source_before"]["error"]

    @pytest.mark.parametrize("phase", ["base", "environment", "source-before", "source-after"])
    def test_main_retains_completed_evidence_when_metadata_collection_is_interrupted(
        self, validation_repo: tuple[Path, Callable[..., int]], monkeypatch: pytest.MonkeyPatch, phase: str,
    ) -> None:
        root, validate = validation_repo
        real_run = subprocess.run
        source_reads = 0

        def run(command, **kwargs):
            nonlocal source_reads
            if command[0] == "git":
                if phase == "base" and "rev-parse" in command:
                    raise KeyboardInterrupt()
                if phase == "environment" and "--version" in command:
                    raise KeyboardInterrupt()
                if "ls-files" in command:
                    source_reads += 1
                    if (phase == "source-before" and source_reads == 1) or (phase == "source-after" and source_reads == 2):
                        raise KeyboardInterrupt()
                return real_run(command, **kwargs)
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(subprocess, "run", run)
        logs = root / "validation"
        assert validate(["docs", "--base", "HEAD", "--log-dir", str(logs)]) == 130
        manifest = read_manifest(logs)
        assert manifest["status"] == "failed" and manifest["exit_code"] == 130
        assert manifest["finished_at"]
        first, second = manifest["steps"]
        assert second["status"] == "pending" and second["exit_code"] is None
        if phase == "source-after":
            assert first["status"] == "passed" and first["exit_code"] == 0
            assert "error" in first["source_after"]
            assert first["source_unchanged"] is None
        else:
            assert first["status"] == "pending" and first["exit_code"] is None

    def test_main_rejects_unavailable_base_before_running_checks(
        self, validation_repo: tuple[Path, Callable[..., int]],
    ) -> None:
        root, validate = validation_repo
        logs = root / "validation"
        assert validate(["docs", "--base", "missing-revision", "--log-dir", str(logs)]) == 2
        manifest = read_manifest(logs)
        assert manifest["status"] == "failed"
        assert manifest["base_commit"] is None
        assert "invalid --base" in manifest["error"]
        assert all(step["status"] == "pending" for step in manifest["steps"])

    def test_main_dry_run_explains_without_execution_or_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def run(*args, **kwargs):
            pytest.fail("dry-run must not execute commands")

        monkeypatch.setattr(subprocess, "run", run)
        logs = tmp_path / "logs"
        assert main(["coverage", "--base", "HEAD~1", "--dry-run", "--log-dir", str(logs)]) == 0
        assert not logs.exists()
        output = capsys.readouterr().out
        assert str(ROOT) in output
        assert "--cov=pf" in output
        commands = [shlex.split(line) for line in output.splitlines()[1:]]
        docs_command, = [command for command in commands if "scripts/check_docs.py" in command]
        assert docs_command[-2:] == ["--base", "HEAD~1"]
        assert "PASS" not in output

    def test_main_rejects_a_base_for_lanes_without_documentation_checks(self) -> None:
        with pytest.raises(SystemExit) as error:
            main(["process", "--base", "HEAD", "--dry-run"])
        assert error.value.code == 2

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from pf.errors import InfrastructureError
from pf.schemas.evaluation import ProcessResult, ProcessSpec
from pf.snapshot import SnapshotBuilder


def test_snapshot_materializes_source_and_excludes_runtime_state(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    for excluded in (".venv", ".pf"):
        directory = root / excluded
        directory.mkdir()
        (directory / "state.txt").write_text("runtime\n", encoding="utf-8")
    (root / "package-floor.json").write_text("{}\n", encoding="utf-8")

    snapshot = SnapshotBuilder().build(root)
    destination = tmp_path / "proposal"
    snapshot.materialize(destination)

    assert [entry.path for entry in snapshot.identity.entries] == [
        "src",
        "src/app.py",
    ]
    assert (destination / "src" / "app.py").read_text(encoding="utf-8") == (
        "VALUE = 1\n"
    )
    assert not (destination / ".venv").exists()
    assert not (destination / ".pf").exists()
    assert not (destination / "package-floor.json").exists()


def test_non_git_snapshot_honors_gitignore_rules(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".gitignore").write_text("ignored/\n*.generated\n", encoding="utf-8")
    (root / "keep.py").write_text("KEEP = True\n", encoding="utf-8")
    (root / "skip.generated").write_text("generated\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "secret.txt").write_text("secret\n", encoding="utf-8")

    snapshot = SnapshotBuilder().build(root)

    paths = {entry.path for entry in snapshot.identity.entries}
    assert {".gitignore", "keep.py"} <= paths
    assert "skip.generated" not in paths
    assert not any(path.startswith("ignored") for path in paths)


def test_git_snapshot_uses_tracked_and_unignored_worktree_manifest(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", root], check=True)
    (root / ".gitignore").write_text("ignored.txt\ntracked.py\n", encoding="utf-8")
    (root / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", root, "add", ".gitignore"],
        check=True,
    )
    subprocess.run(["git", "-C", root, "add", "-f", "tracked.py"], check=True)
    (root / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")
    (root / "staged.py").write_text("STAGED = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", root, "add", "staged.py"], check=True)
    (root / "untracked.py").write_text("UNTRACKED = True\n", encoding="utf-8")
    (root / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    snapshot = SnapshotBuilder().build(root)

    paths = {entry.path for entry in snapshot.identity.entries}
    assert {".gitignore", "tracked.py", "staged.py", "untracked.py"} <= paths
    assert "ignored.txt" not in paths
    destination = tmp_path / "proposal"
    snapshot.materialize(destination)
    assert (destination / "tracked.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_git_snapshot_failure_includes_process_diagnostic(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return ProcessResult(
                exit_code=128,
                signal=None,
                duration_seconds=0.1,
                stdout_summary="",
                stderr_summary="fatal: not a git repository",
                stdout_tail="",
                stderr_tail="fatal: not a git repository",
            )

    with pytest.raises(InfrastructureError) as caught:
        SnapshotBuilder(Runner()).build(root)

    assert str(caught.value) == "git could not enumerate the source snapshot"
    assert caught.value.detail == "fatal: not a git repository"

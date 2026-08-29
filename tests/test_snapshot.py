from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from pf.adapters.process import SubprocessRunner
from pf.errors import ConfigurationError
from pf.errors import InfrastructureError
from pf.schemas.evaluation import (
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
)
from pf.snapshot import SnapshotBuilder


class TestSnapshotBuilder:
    def test_owned_pyproject_separates_dependency_and_remainder_identity(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        pyproject = root / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "demo"\ndependencies = ["httpx<1"]\n'
            '[project.scripts]\ndemo = "demo:main"\n',
            encoding="utf-8",
        )

        original = SnapshotBuilder.without_processes().build(
            root,
            owned_pyproject_paths=("pyproject.toml",),
        )
        original_identity = original.identity.pyproject_identities[0]
        original.close()
        pyproject.write_text(
            '[project]\nname = "demo"\ndependencies = ["httpx>=0.27,<1"]\n'
            '[project.scripts]\ndemo = "demo:main"\n',
            encoding="utf-8",
        )
        dependency_edit = SnapshotBuilder.without_processes().build(
            root,
            owned_pyproject_paths=("pyproject.toml",),
        )
        dependency_identity = dependency_edit.identity.pyproject_identities[0]
        dependency_entry = next(
            entry
            for entry in dependency_edit.identity.entries
            if entry.path == "pyproject.toml"
        )
        dependency_edit.close()

        assert (
            dependency_identity.remainder_digest == original_identity.remainder_digest
        )
        assert (
            dependency_identity.dependency_arrays_digest
            != original_identity.dependency_arrays_digest
        )
        assert dependency_entry.content_digest is None

        pyproject.write_text(
            '[project]\nname = "demo"\ndependencies = ["httpx>=0.27,<1"]\n'
            '[project.scripts]\ndemo = "demo.cli:main"\n',
            encoding="utf-8",
        )
        remainder_edit = SnapshotBuilder.without_processes().build(
            root,
            owned_pyproject_paths=("pyproject.toml",),
        )
        remainder_identity = remainder_edit.identity.pyproject_identities[0]
        remainder_edit.close()

        assert (
            remainder_identity.dependency_arrays_digest
            == dependency_identity.dependency_arrays_digest
        )
        assert (
            remainder_identity.remainder_digest != dependency_identity.remainder_digest
        )

    def test_pyproject_identity_is_stable_for_equal_parsed_toml_types(
        self,
    ) -> None:
        first = b"""# layout does not matter
[identity]
text = "1"
truth = true
integer = 1
float = 1.0
negative_zero = -0.0
infinity = inf
not_a_number = nan
offset = 1979-05-27T07:32:00Z
local_datetime = 1979-05-27T07:32:00
local_date = 1979-05-27
local_time = 07:32:00
values = [1, "1", true]
"""
        second = b"""[identity]
values=[1,"1",true]
local_time=07:32:00
local_date=1979-05-27
local_datetime=1979-05-27T07:32:00
offset=1979-05-27T07:32:00+00:00
not_a_number=+nan
infinity=+inf
negative_zero=-0.0
float=1.0
integer=1
truth=true
text="1"
"""

        left = SnapshotBuilder.pyproject_identity(
            path="pyproject.toml",
            mode=0o644,
            content=first,
        )
        right = SnapshotBuilder.pyproject_identity(
            path="pyproject.toml",
            mode=0o644,
            content=second,
        )
        positive_zero = SnapshotBuilder.pyproject_identity(
            path="pyproject.toml",
            mode=0o644,
            content=first.replace(b"negative_zero = -0.0", b"negative_zero = 0.0"),
        )

        assert left == right
        assert left.remainder_digest != positive_zero.remainder_digest

    def test_dependency_identity_preserves_field_presence(self) -> None:
        missing = SnapshotBuilder.pyproject_identity(
            path="pyproject.toml",
            mode=0o644,
            content=b'[project]\nname = "demo"\n',
        )
        present_empty = SnapshotBuilder.pyproject_identity(
            path="pyproject.toml",
            mode=0o644,
            content=b'[project]\nname = "demo"\ndependencies = []\n',
        )

        assert missing.remainder_digest == present_empty.remainder_digest
        assert (
            missing.dependency_arrays_digest != present_empty.dependency_arrays_digest
        )

    def test_git_snapshot_handles_unavailable_process_terminal(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / ".git").mkdir()

        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessTerminalUnavailable:
                return ProcessTerminalUnavailable()

        with pytest.raises(
            InfrastructureError,
            match="git could not enumerate the source snapshot",
        ):
            SnapshotBuilder(Runner()).build(root)

    @pytest.mark.parametrize("target", ("/outside", "../../outside"))
    def test_snapshot_rejects_a_symlink_outside_the_source_root(
        self,
        tmp_path: Path,
        target: str,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "unsafe").symlink_to(target)

        with pytest.raises(ConfigurationError):
            SnapshotBuilder.without_processes().build(root)

    def test_snapshot_rejects_a_special_source_file(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        os.mkfifo(root / "pipe")

        with pytest.raises(ConfigurationError, match="unsupported special source file"):
            SnapshotBuilder.without_processes().build(root)

    def test_git_snapshot_rejects_an_incomplete_manifest(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / ".git").mkdir()

        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return ProcessResult(
                    exit_code=0,
                    signal=None,
                    duration_seconds=0.1,
                    stdout_complete=False,
                )

        with pytest.raises(InfrastructureError):
            SnapshotBuilder(Runner()).build(root)

    def test_git_snapshot_rejects_an_unsafe_path(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / ".git").mkdir()

        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return ProcessResult(
                    exit_code=0,
                    signal=None,
                    duration_seconds=0.1,
                    stdout="../outside\0",
                )

        with pytest.raises(ConfigurationError):
            SnapshotBuilder(Runner()).build(root)

    def test_snapshot_materializes_source_and_excludes_runtime_state(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        for excluded in (".venv", ".pf"):
            directory = root / excluded
            directory.mkdir()
            (directory / "state.txt").write_text("runtime\n", encoding="utf-8")
        (root / "package-floor.json").write_text("{}\n", encoding="utf-8")

        snapshot = SnapshotBuilder.without_processes().build(root)
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

    def test_non_git_snapshot_honors_gitignore_rules(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / ".gitignore").write_text("ignored/\n*.generated\n", encoding="utf-8")
        (root / "keep.py").write_text("KEEP = True\n", encoding="utf-8")
        (root / "skip.generated").write_text("generated\n", encoding="utf-8")
        (root / "ignored").mkdir()
        (root / "ignored" / "secret.txt").write_text("secret\n", encoding="utf-8")

        snapshot = SnapshotBuilder.without_processes().build(root)

        paths = {entry.path for entry in snapshot.identity.entries}
        assert {".gitignore", "keep.py"} <= paths
        assert "skip.generated" not in paths
        assert not any(path.startswith("ignored") for path in paths)

    def test_git_snapshot_uses_tracked_and_unignored_worktree_manifest(
        self, tmp_path: Path
    ) -> None:
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

        snapshot = SnapshotBuilder(SubprocessRunner()).build(root)

        paths = {entry.path for entry in snapshot.identity.entries}
        assert {".gitignore", "tracked.py", "staged.py", "untracked.py"} <= paths
        assert "ignored.txt" not in paths
        destination = tmp_path / "proposal"
        snapshot.materialize(destination)
        assert (destination / "tracked.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    def test_git_snapshot_failure_includes_process_diagnostic(
        self, tmp_path: Path
    ) -> None:
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
                    stdout="",
                    stderr="fatal: not a git repository",
                )

        with pytest.raises(InfrastructureError) as caught:
            SnapshotBuilder(Runner()).build(root)

        assert caught.value.detail == "fatal: not a git repository"

    def test_no_process_snapshot_fails_closed_for_git_root(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / ".git").mkdir()

        with pytest.raises(ConfigurationError, match="explicit process runner"):
            SnapshotBuilder.without_processes().build(root)

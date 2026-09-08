from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pf.static_ignores import (
    TyGlobalIgnoreInputs, TyGlobalIgnoreMaterialization, TyIgnoreUnavailable,
    capture_ty_global_ignores, materialize_ty_global_ignores,
    TyIgnoreBoundaries, capture_ty_ignore_boundaries, materialize_ty_ignore_boundaries,
)
from pf.schemas.static import StaticContentManifest
from pf.static_subject import StaticContentCollector


class TestLocalIgnoreBoundaries:
    def test_owned_local_ignores_and_git_directory_stay_in_content(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        (source / ".git/info").mkdir(parents=True)
        (source / ".git/info/exclude").write_text("generated.py\n")
        (source / ".ignore").write_text("local.py\n")
        roots = {"source": source}
        content = StaticContentCollector().collect(roots)
        assert isinstance(content, StaticContentManifest)
        boundaries = capture_ty_ignore_boundaries(roots=roots, content=content)
        assert isinstance(boundaries, TyIgnoreBoundaries)
        assert boundaries.revalidate()
        assert source / ".ignore" not in boundaries.external_absences
        assert tmp_path / ".ignore" in boundaries.external_absences
        saved = materialize_ty_ignore_boundaries(boundaries, directory=tmp_path / "boundaries")
        assert isinstance(saved, StaticContentManifest)
        assert str(tmp_path) not in saved.model_dump_json()
        # Appearance of an outer input invalidates the previous admission.
        (tmp_path / ".ignore").write_text("*.py\n")
        assert not boundaries.revalidate()
        assert isinstance(materialize_ty_ignore_boundaries(boundaries, directory=tmp_path / "invalid"), TyIgnoreUnavailable)
        assert not (tmp_path / "invalid").exists()

    @pytest.mark.parametrize("external", [".ignore", ".gitignore", ".git"])
    def test_external_ancestor_inputs_are_not_silently_discarded(self, tmp_path: Path, external: str) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (tmp_path / external).write_text("outside input\n")
        content = StaticContentCollector().collect({"source": source})
        assert isinstance(content, StaticContentManifest)
        assert isinstance(capture_ty_ignore_boundaries(roots={"source": source}, content=content), TyIgnoreUnavailable)

    def test_gitdir_indirection_requires_additional_closed_inputs(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / ".git").write_text("gitdir: ../outside\n")
        content = StaticContentCollector().collect({"source": source})
        assert isinstance(content, StaticContentManifest)
        assert isinstance(capture_ty_ignore_boundaries(roots={"source": source}, content=content), TyIgnoreUnavailable)

    def test_empty_external_git_marker_is_guarded_without_discarding_excludes(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        git = tmp_path / ".git"
        (git / "info").mkdir(parents=True)
        content = StaticContentCollector().collect({"source": source})
        assert isinstance(content, StaticContentManifest)
        boundaries = capture_ty_ignore_boundaries(roots={"source": source}, content=content)
        assert isinstance(boundaries, TyIgnoreBoundaries)
        assert git in boundaries.external_git_directories
        assert boundaries.revalidate()
        (git / "info/exclude").write_text("*.py\n")
        assert not boundaries.revalidate()
        assert isinstance(capture_ty_ignore_boundaries(roots={"source": source}, content=content), TyIgnoreUnavailable)


class TestGlobalIgnoreCapture:
    def test_frozen_content_survives_original_deletion_and_relocation(self, tmp_path: Path) -> None:
        home = tmp_path / "original"
        home.mkdir()
        config = home / ".gitconfig"
        config.write_text('[core]\nexcludesFile=~/patterns\n')
        patterns = home / "patterns"
        patterns.write_text("*.generated.py\n!keep.generated.py\n")
        captured = capture_ty_global_ignores(environment={"HOME": str(home)}, cwd=tmp_path)
        assert isinstance(captured, TyGlobalIgnoreInputs)
        assert [file.role for file in captured.files] == ["home", "patterns"]
        shutil.rmtree(home)
        first = materialize_ty_global_ignores(captured, directory=tmp_path / "first")
        second = materialize_ty_global_ignores(captured, directory=tmp_path / "second-longer")
        assert isinstance(first, TyGlobalIgnoreMaterialization)
        assert isinstance(second, TyGlobalIgnoreMaterialization)
        assert first.content == second.content
        assert first.content.identity == second.content.identity
        assert (first.directory / "home/patterns").read_bytes() == captured.patterns
        assert isinstance(materialize_ty_global_ignores(captured, directory=first.directory), TyIgnoreUnavailable)

    @pytest.mark.parametrize("setting", ['"path with space"', '$ROOT/ignore', '~other/ignore', 'ignore # comment', ''])
    def test_unclosed_excludes_setting_has_no_materialization(self, tmp_path: Path, setting: str) -> None:
        (tmp_path / ".gitconfig").write_text(f'[core]\nexcludesFile={setting}\n')
        assert isinstance(capture_ty_global_ignores(environment={"HOME": str(tmp_path)}, cwd=tmp_path), TyIgnoreUnavailable)

    @pytest.mark.parametrize("environment", [{}, {"HOME": "relative"}, {"HOME": "/home/fixed", "XDG_CONFIG_HOME": "relative"}])
    def test_missing_fixed_environment_does_not_query_host_home(self, tmp_path: Path, environment: dict[str, str]) -> None:
        assert isinstance(capture_ty_global_ignores(environment=environment, cwd=tmp_path), TyIgnoreUnavailable)


class TestRealTyGlobalIgnores:
    def test_frozen_global_and_closed_local_inputs_preserve_default_selection(self, tmp_path: Path) -> None:
        source, home = tmp_path / "source", tmp_path / "home"
        (source / ".git/info").mkdir(parents=True)
        (home / ".config/git").mkdir(parents=True)
        (source / "ty.toml").write_text("")
        (source / ".ignore").write_text("local.py\n")
        (source / ".gitignore").write_text("git.py\n")
        (source / ".git/info/exclude").write_text("info.py\n")
        (home / ".config/git/ignore").write_text("global.py\n")
        for name in ("local", "git", "info", "global", "kept"):
            (source / f"{name}.py").write_text('value: int = "wrong"\n')
        content = StaticContentCollector().collect({"source": source})
        assert isinstance(content, StaticContentManifest)
        boundaries = capture_ty_ignore_boundaries(roots={"source": source}, content=content)
        assert isinstance(boundaries, TyIgnoreBoundaries)
        environment = {"HOME": str(home), "GIT_CONFIG_SYSTEM": str(tmp_path / "no-system-config")}
        global_inputs = capture_ty_global_ignores(environment=environment, cwd=source)
        assert isinstance(global_inputs, TyGlobalIgnoreInputs)
        frozen = materialize_ty_global_ignores(global_inputs, directory=tmp_path / "global-frozen")
        assert isinstance(frozen, TyGlobalIgnoreMaterialization)
        executable = shutil.which("ty")
        assert executable is not None
        command = (executable, "check", "--project", str(source), "--config-file", str(source / "ty.toml"),
                   "--python", sys.executable, "--output-format", "gitlab", "--no-progress", "--color", "never", str(source))
        native = subprocess.run(command, cwd=source, env=environment, capture_output=True, text=True, timeout=30, check=False)
        assert native.returncode == 1, (native.stdout, native.stderr)
        expected = json.loads(native.stdout)
        assert [item["location"]["path"] for item in expected] == ["kept.py"]
        shutil.rmtree(home)
        assert boundaries.revalidate()
        replay = subprocess.run(command, cwd=source, env=dict(frozen.environment), capture_output=True, text=True, timeout=30, check=False)
        assert boundaries.revalidate()
        assert replay.returncode == 1, (replay.stdout, replay.stderr)
        assert json.loads(replay.stdout) == expected

    @pytest.mark.parametrize("selected", ["xdg", "absent"])
    def test_native_and_frozen_discovery_have_same_diagnostics(self, tmp_path: Path, selected: str) -> None:
        project, home, xdg = tmp_path / "project", tmp_path / "home", tmp_path / "xdg"
        project.mkdir()
        home.mkdir()
        (xdg / "git").mkdir(parents=True)
        (project / "ty.toml").write_text("")
        (project / "bad.py").write_text('value: int = "wrong"\n')
        (project / "keep.py").write_text('value: int = "wrong"\n')
        candidates = (("global", tmp_path / "global-config"), ("home", home / ".gitconfig"),
                      ("xdg", xdg / "git/config"), ("system", tmp_path / "system-config"))
        environment = {"HOME": str(home), "XDG_CONFIG_HOME": str(xdg),
                       "GIT_CONFIG_GLOBAL": str(candidates[0][1]), "GIT_CONFIG_SYSTEM": str(candidates[-1][1])}
        if selected in dict(candidates):
            index = [role for role, _ in candidates].index(selected)
            for number, (_, path) in enumerate(candidates):
                if number < index:
                    path.write_text('[user]\nname=unrelated\n')
                else:
                    patterns = tmp_path / f"patterns-{number}"
                    patterns.write_text("bad.py\n" if number == index else "keep.py\n")
                    path.write_text(f'[core]\nexcludesFile={patterns}\n')
        elif selected == "default":
            (xdg / "git/ignore").write_text("bad.py\n")
        captured = capture_ty_global_ignores(environment=environment, cwd=project)
        assert isinstance(captured, TyGlobalIgnoreInputs)
        frozen = materialize_ty_global_ignores(captured, directory=tmp_path / "frozen")
        assert isinstance(frozen, TyGlobalIgnoreMaterialization)
        executable = shutil.which("ty")
        assert executable is not None
        command = (executable, "check", "--project", str(project), "--config-file", str(project / "ty.toml"),
                   "--python", sys.executable, "--output-format", "gitlab", "--no-progress", "--color", "never", str(project))
        native = subprocess.run(command, cwd=project, env=environment, capture_output=True, text=True, timeout=30, check=False)
        assert native.returncode == 1, (native.stdout, native.stderr)
        expected = json.loads(native.stdout)
        assert {item["location"]["path"] for item in expected} == ({"bad.py", "keep.py"} if selected == "absent" else {"keep.py"})
        # Frozen replay must not need any of the original global input files.
        for file in captured.files:
            if file.content is not None:
                file.path.unlink()
        replay = subprocess.run(command, cwd=project, env=dict(frozen.environment), capture_output=True, text=True, timeout=30, check=False)
        assert replay.returncode == 1, (replay.stdout, replay.stderr)
        assert json.loads(replay.stdout) == expected

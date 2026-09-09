from __future__ import annotations

from pathlib import Path
import shutil

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

    def test_absolute_excludes_file_does_not_expand_home(self, tmp_path: Path) -> None:
        patterns = tmp_path / "patterns"
        patterns.write_text("*.generated.py\n")
        (tmp_path / ".gitconfig").write_text(f"[core]\nexcludesFile={patterns}\n")
        captured = capture_ty_global_ignores(
            environment={"HOME": str(tmp_path)}, cwd=tmp_path,
        )
        assert isinstance(captured, TyGlobalIgnoreInputs)
        assert captured.files[-1].path == patterns

    def test_explicit_global_gitconfig_is_a_closed_input(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        (home / ".gitconfig").write_text("[user]\nname = demo\n")
        extra = tmp_path / "extra.gitconfig"
        extra.write_text("[user]\nemail = demo@example.test\n")
        captured = capture_ty_global_ignores(
            environment={"HOME": str(home), "GIT_CONFIG_GLOBAL": str(extra)},
            cwd=tmp_path,
        )
        assert isinstance(captured, TyGlobalIgnoreInputs)
        assert [file.role for file in captured.files][0] == "global"

    @pytest.mark.parametrize("setting", ['"path with space"', '$ROOT/ignore', '~other/ignore', 'ignore # comment', ''])
    def test_unclosed_excludes_setting_has_no_materialization(self, tmp_path: Path, setting: str) -> None:
        (tmp_path / ".gitconfig").write_text(f'[core]\nexcludesFile={setting}\n')
        assert isinstance(capture_ty_global_ignores(environment={"HOME": str(tmp_path)}, cwd=tmp_path), TyIgnoreUnavailable)

    @pytest.mark.parametrize("environment", [{}, {"HOME": "relative"}, {"HOME": "/home/fixed", "XDG_CONFIG_HOME": "relative"}])
    def test_missing_fixed_environment_does_not_query_host_home(self, tmp_path: Path, environment: dict[str, str]) -> None:
        assert isinstance(capture_ty_global_ignores(environment=environment, cwd=tmp_path), TyIgnoreUnavailable)

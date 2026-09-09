from __future__ import annotations

from pathlib import Path

import pytest
import tomli

from pf.static_configuration import (
    TyConfigurationResolution, TyConfigurationResolver, TyConfigurationUnavailable,
    TyConfigurationMaterialization, materialize_ty_configuration, ty_config_digest,
)


class TestTyConfigurationResolver:
    def test_host_user_config_is_undeclared_analysis_root(self, tmp_path: Path) -> None:
        user = tmp_path / "home" / ".config" / "ty"
        user.mkdir(parents=True)
        (user / "ty.toml").write_text('[rules]\ninvalid-assignment="ignore"\n')
        project = tmp_path / "project"
        project.mkdir()
        (project / "ty.toml").write_text('[rules]\ninvalid-assignment="error"\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=project,
            environment={"HOME": str(tmp_path / "home")},
            platform="posix",
            snapshot_root=project,
        )
        assert resolved == TyConfigurationUnavailable(detail="undeclared-analysis-root")

    def test_ty_file_inside_snapshot_materializes_without_owned_overrides(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "ty.toml").write_text('[rules]\ninvalid-assignment="warn"\n')
        (project / "pyproject.toml").write_text('[tool.ty.rules]\ninvalid-assignment="ignore"\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=project,
            environment={"HOME": str(tmp_path / "home")},
            platform="posix",
            snapshot_root=project,
        )
        assert isinstance(resolved, TyConfigurationResolution)
        assert [file.role for file in resolved.files] == ["project"]
        assert tomli.loads(resolved.effective_toml)["rules"]["invalid-assignment"] == "warn"
        first = materialize_ty_configuration(resolved, directory=tmp_path / "frozen-first")
        second = materialize_ty_configuration(resolved, directory=tmp_path / "frozen-second")
        assert isinstance(first, TyConfigurationMaterialization)
        assert isinstance(second, TyConfigurationMaterialization)
        assert first.digest == second.digest == ty_config_digest(first.content)
        assert first.content == resolved.effective_toml.encode("utf-8")
        assert "--python" not in first.content.decode()
        assert "adapter-cli-overrides" not in first.content.decode()

    def test_snapshot_ty_config_file_is_highest_priority(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        config = project / "explicit.toml"
        config.write_text('[rules]\ninvalid-assignment="ignore"\n')
        (project / "ty.toml").write_text('[rules]\ninvalid-assignment="warn"\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=project,
            environment={"HOME": str(tmp_path / "home"), "TY_CONFIG_FILE": str(config)},
            platform="posix",
            snapshot_root=project,
        )
        assert isinstance(resolved, TyConfigurationResolution)
        assert resolved.files[0].role == "explicit"
        assert tomli.loads(resolved.effective_toml)["rules"]["invalid-assignment"] == "ignore"

    def test_ty_config_file_outside_snapshot_is_undeclared(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        outside = tmp_path / "outside.toml"
        outside.write_text('[rules]\ninvalid-assignment="ignore"\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=project,
            environment={"HOME": str(tmp_path / "home"), "TY_CONFIG_FILE": str(outside)},
            platform="posix",
            snapshot_root=project,
        )
        assert resolved == TyConfigurationUnavailable(detail="undeclared-analysis-root")

    def test_parent_walk_does_not_read_ty_config_outside_snapshot(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "snapshot"
        project = snapshot / "member"
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text('[project]\nname="demo"\n')
        (tmp_path / "ty.toml").write_text('[rules]\ninvalid-assignment="error"\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=project,
            environment={"HOME": str(tmp_path / "home")},
            platform="posix",
            snapshot_root=snapshot,
        )
        assert isinstance(resolved, TyConfigurationResolution)
        assert "invalid-assignment" not in resolved.effective_toml

    @pytest.mark.parametrize("environment", [{}, {"TY_CONFIG_FILE": "$UNBOUND/ty.toml"}, {"XDG_CONFIG_HOME": "relative"}])
    def test_missing_host_probe_context_does_not_fall_back(self, tmp_path, environment) -> None:
        assert isinstance(TyConfigurationResolver().resolve(
            project_directory=tmp_path, environment=environment, platform="posix",
            snapshot_root=tmp_path,
        ), TyConfigurationUnavailable)

    def test_windows_user_configuration_presence_is_undeclared(self, tmp_path) -> None:
        appdata = tmp_path / "appdata"
        (appdata / "ty").mkdir(parents=True)
        (appdata / "ty" / "ty.toml").write_text('[rules]\ninvalid-assignment="warn"\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=tmp_path, environment={"APPDATA": str(appdata)},
            platform="windows", snapshot_root=tmp_path,
        )
        assert resolved == TyConfigurationUnavailable(detail="undeclared-analysis-root")

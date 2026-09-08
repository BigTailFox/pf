from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest
import tomli

from pf.adapters.process import SubprocessRunner
from pf.schemas.evaluation import EnvironmentVariable, ProcessResult, ProcessSpec
from pf.static_configuration import (
    TyConfigurationResolution, TyConfigurationResolver, TyConfigurationUnavailable,
    TyConfigurationMaterialization, materialize_ty_configuration,
)


class TestTyConfigurationResolver:
    def test_project_selection_skips_unconfigured_pyproject_and_merges_user_arrays(self, tmp_path: Path) -> None:
        user = tmp_path / "home" / ".config" / "ty"
        user.mkdir(parents=True)
        (user / "ty.toml").write_text('[rules]\ninvalid-assignment="ignore"\n[analysis]\nallowed-unresolved-imports=["from_user"]\n')
        project = tmp_path / "workspace" / "member"
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text('[project]\nname="demo"\nversion="1"\n')
        (project.parent / "pyproject.toml").write_text('[tool.ty.rules]\ninvalid-assignment="error"\n[tool.ty.analysis]\nallowed-unresolved-imports=["from_project"]\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=project, environment={"HOME": str(tmp_path / "home")}, platform="posix",
        )
        assert isinstance(resolved, TyConfigurationResolution)
        assert [file.role for file in resolved.files] == ["user", "project"]
        assert resolved.files[-1].path == project.parent / "pyproject.toml"
        assert resolved.analysis_root == project.parent
        settings = tomli.loads(resolved.effective_toml)
        assert settings["rules"]["invalid-assignment"] == "error"
        assert settings["analysis"]["allowed-unresolved-imports"] == ["from_user", "from_project"]
        assert project / "ty.toml" in resolved.queried_paths
        assert project / "pyproject.toml" in resolved.queried_paths

    def test_ty_file_takes_precedence_and_capture_is_independent_of_later_host_edits(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "ty.toml").write_text('[rules]\ninvalid-assignment="warn"\n')
        (project / "pyproject.toml").write_text('[tool.ty.rules]\ninvalid-assignment="ignore"\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=project, environment={"HOME": str(tmp_path / "home")}, platform="posix",
        )
        assert isinstance(resolved, TyConfigurationResolution)
        assert resolved.files[-1].format == "ty"
        (project / "ty.toml").unlink()
        assert tomli.loads(resolved.effective_toml)["rules"]["invalid-assignment"] == "warn"
        assert 'invalid-assignment="warn"' in resolved.files[-1].content
        first = materialize_ty_configuration(resolved, directory=tmp_path / "frozen-first")
        second = materialize_ty_configuration(resolved, directory=tmp_path / "frozen-second")
        assert isinstance(first, TyConfigurationMaterialization)
        assert isinstance(second, TyConfigurationMaterialization)
        assert first.content == second.content
        assert first.content.identity == second.content.identity
        assert (first.directory / first.effective_file.path).read_text() == resolved.effective_toml
        assert materialize_ty_configuration(resolved, directory=first.directory) == TyConfigurationUnavailable()

    def test_explicit_environment_configuration_uses_only_bound_expansion(self, tmp_path: Path, monkeypatch) -> None:
        config = tmp_path / "explicit.toml"
        config.write_text('[rules]\ninvalid-assignment="ignore"\n')
        monkeypatch.setenv("PF_CONFIG_ROOT", "/unregistered-host-value")
        resolved = TyConfigurationResolver().resolve(
            project_directory=tmp_path,
            environment={"TY_CONFIG_FILE": "${PF_CONFIG_ROOT}/explicit.toml", "PF_CONFIG_ROOT": str(tmp_path)},
            platform="posix",
        )
        assert isinstance(resolved, TyConfigurationResolution)
        assert len(resolved.files) == 1
        assert resolved.files[0].role == "explicit"
        assert resolved.files[0].path == config
        assert resolved.queried_paths == (config,)

    @pytest.mark.parametrize("environment", [{}, {"TY_CONFIG_FILE": "$UNBOUND/ty.toml"}, {"TY_CONFIG_FILE": ""}, {"XDG_CONFIG_HOME": "relative"}])
    def test_missing_explicit_context_does_not_fall_back_to_host_environment(self, tmp_path, environment) -> None:
        assert isinstance(TyConfigurationResolver().resolve(
            project_directory=tmp_path, environment=environment, platform="posix",
        ), TyConfigurationUnavailable)

    def test_windows_user_configuration_uses_explicit_appdata(self, tmp_path) -> None:
        appdata = tmp_path / "appdata"
        (appdata / "ty").mkdir(parents=True)
        (appdata / "ty" / "ty.toml").write_text('[rules]\ninvalid-assignment="warn"\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=tmp_path, environment={"APPDATA": str(appdata)}, platform="windows",
        )
        assert isinstance(resolved, TyConfigurationResolution)
        assert resolved.files[0].path == appdata / "ty" / "ty.toml"


class TestRealTyConfigurationEquivalence:
    def test_compiled_configuration_preserves_native_selection_and_merge(self, tmp_path: Path) -> None:
        format = "ty"
        config_location = "package"
        project = tmp_path / "project"
        project.mkdir()
        home = tmp_path / "home"
        (home / ".config" / "ty").mkdir(parents=True)
        (home / ".config" / "ty" / "ty.toml").write_text('[rules]\ninvalid-assignment="ignore"\n[analysis]\nallowed-unresolved-imports=["from_user"]\n')
        prefix = "tool.ty." if format == "pyproject" else ""
        config = f'[{prefix}rules]\ninvalid-assignment="error"\n[{prefix}analysis]\nallowed-unresolved-imports=["from_project"]\n[{prefix}environment]\nextra-paths=["./vendor"]\n'
        pyproject = '[project]\nname="demo"\nversion="1"\n'
        config_root = project if config_location == "package" else project.parent
        (project / "pyproject.toml").write_text(pyproject)
        if format == "pyproject":
            (config_root / "pyproject.toml").write_text((pyproject if config_location == "package" else "") + config)
        else:
            (config_root / "ty.toml").write_text(config)
        (config_root / "vendor").mkdir()
        (config_root / "vendor" / "sample.pyi").write_text("VALUE: int\n")
        if config_location == "parent":
            (project / "vendor").mkdir()
            (project / "vendor" / "sample.pyi").write_text("VALUE: str\n")
        (project / "demo.py").write_text('import from_user, from_project, sample\nvalue: int = sample.VALUE\nwrong: str = 1\n')
        resolved = TyConfigurationResolver().resolve(
            project_directory=project, environment={"HOME": str(home)}, platform="posix",
        )
        assert isinstance(resolved, TyConfigurationResolution)
        assert resolved.analysis_root == config_root
        materialized = materialize_ty_configuration(resolved, directory=tmp_path / "frozen")
        assert isinstance(materialized, TyConfigurationMaterialization)
        frozen = materialized.directory / materialized.effective_file.path
        executable = shutil.which("ty")
        assert executable is not None
        outputs = []
        for extra in ((), ("--config-file", str(frozen))):
            result = SubprocessRunner().run(ProcessSpec(
                argv=(executable, "check", "--project", str(resolved.analysis_root if extra else project), "--python", sys.executable,
                      "--output-format", "gitlab", "--no-progress", "--color", "never",
                      "--no-respect-ignore-files", *extra, str(project / "demo.py")),
                cwd=str(project), environment_mode="explicit",
                environment=(EnvironmentVariable(name="HOME", value=str(home)),),
                timeout_seconds=30,
            ))
            assert isinstance(result, ProcessResult)
            assert result.exit_code == 1, (result.stdout, result.stderr)
            outputs.append(json.loads(result.stdout))
        assert outputs[0] == outputs[1]
        assert [item["check_name"] for item in outputs[0]] == ["invalid-assignment"]

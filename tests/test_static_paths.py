from __future__ import annotations

from pathlib import Path
import os

import pytest

from pf.errors import ConfigurationError
from pf.static_configuration import TyConfigurationResolution
from pf.static_paths import TySearchPaths, TySearchPathsUnavailable, resolve_ty_search_paths


def configuration(root: Path, text: str) -> TyConfigurationResolution:
    return TyConfigurationResolution((), text, root, ())


class TestTySearchPaths:
    @pytest.mark.parametrize("reverse", [False, True])
    def test_config_overrides_precede_dedicated_flags_independently_of_argv_order(self, tmp_path: Path, reverse: bool) -> None:
        project = tmp_path / "project"
        cwd = project / "member"
        for path in (project / "configured", cwd / "override", cwd / "dedicated"):
            path.mkdir(parents=True)
        override = ("--config", 'environment.extra-paths=["./override"]')
        dedicated = ("--extra-search-path", "./dedicated")
        result = resolve_ty_search_paths(
            configuration(project, '[environment]\nextra-paths=["./configured"]\n'),
            args=dedicated + override if reverse else override + dedicated,
            environment={}, cwd=cwd, package_name="demo",
        )
        assert isinstance(result, TySearchPaths)
        assert result.extra == (project / "configured", cwd / "override", cwd / "dedicated")
        assert result.first_party == (project,)

    def test_default_roots_preserve_src_named_python_project_order(self, tmp_path: Path) -> None:
        for relative in ("src", "demo/demo", "python"):
            (tmp_path / relative).mkdir(parents=True)
        result = resolve_ty_search_paths(configuration(tmp_path, ""), args=(), environment={}, cwd=tmp_path, package_name="demo")
        assert isinstance(result, TySearchPaths)
        assert result.first_party == (tmp_path / "src", tmp_path / "demo", tmp_path / "python", tmp_path)
        (tmp_path / "src" / "__init__.pyi").touch()
        changed = resolve_ty_search_paths(configuration(tmp_path, ""), args=(), environment={}, cwd=tmp_path, package_name="demo")
        assert isinstance(changed, TySearchPaths)
        assert changed.first_party == (tmp_path / "demo", tmp_path / "python", tmp_path)

    def test_explicit_empty_roots_and_path_expansion_use_only_fixed_inputs(self, tmp_path: Path, monkeypatch) -> None:
        bound = tmp_path / "bound"
        bound.mkdir()
        monkeypatch.setenv("ROOT", "/not-the-request-value")
        result = resolve_ty_search_paths(
            configuration(tmp_path, '[environment]\nroot=[]\nextra-paths=["${ROOT}"]\n'),
            args=(), environment={"ROOT": str(bound)}, cwd=tmp_path, package_name="demo",
        )
        assert isinstance(result, TySearchPaths)
        assert result.first_party == ()
        assert result.extra == (bound,)
        assert result.expansion_variables == ("ROOT",)

    def test_pythonpath_is_literal_and_records_ignored_inputs(self, tmp_path: Path) -> None:
        literal = tmp_path / "$ROOT"
        literal.mkdir()
        missing = tmp_path / "missing"
        result = resolve_ty_search_paths(
            configuration(tmp_path, ""), args=(),
            environment={"ROOT": "somewhere-else", "PYTHONPATH": os.pathsep.join(("$ROOT", str(missing)))},
            cwd=tmp_path, package_name="demo",
        )
        assert isinstance(result, TySearchPaths)
        assert result.pythonpath == (literal,)
        assert result.ignored_pythonpath == (missing,)
        assert result.expansion_variables == ("PYTHONPATH",)

    def test_scalar_dedicated_flags_override_config_pairs(self, tmp_path: Path) -> None:
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        result = resolve_ty_search_paths(
            configuration(tmp_path, '[src]\nrespect-ignore-files=true\n'),
            args=("--typeshed", str(second), "--config", f'environment.typeshed="{first}"', "--no-respect-ignore-files"),
            environment={}, cwd=tmp_path, package_name="demo",
        )
        assert isinstance(result, TySearchPaths)
        assert result.typeshed == second
        assert result.respect_ignore_files is False

    @pytest.mark.parametrize("text,args", [
        ('[environment]\nextra-paths=["$MISSING"]', ()),
        ('[environment]\nextra-paths=["missing"]', ()),
        ('src="not-a-table"', ()),
        ('', ("--unsupported-path-mode",)),
        ('', ("--typeshed",)),
    ])
    def test_unclosed_paths_do_not_produce_an_observation_request(self, tmp_path, text, args) -> None:
        result = resolve_ty_search_paths(configuration(tmp_path, text), args=args, environment={}, cwd=tmp_path, package_name="demo")
        assert isinstance(result, TySearchPathsUnavailable)

    @pytest.mark.parametrize("args", [
        ("--fix",), ("--add-ignore",), ("--watch",), ("-W",),
        ("--project", "elsewhere"), ("-c=environment.python='other'",),
    ])
    def test_owned_scope_and_immutable_observation_options_remain_configuration_errors(self, tmp_path, args) -> None:
        with pytest.raises(ConfigurationError, match="adapter-owned"):
            resolve_ty_search_paths(configuration(tmp_path, ""), args=args, environment={}, cwd=tmp_path, package_name="demo")

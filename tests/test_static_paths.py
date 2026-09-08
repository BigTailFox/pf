from __future__ import annotations

from pathlib import Path
import os
import json
import shutil
import subprocess
import sys

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


class TestRealTySearchPaths:
    def test_search_precedence_and_relative_bases(self, tmp_path: Path) -> None:
        reverse = False
        selected = "configured"
        project = tmp_path / "project"
        cwd = project / "member"
        ordered = (project / "configured", cwd / "override", cwd / "dedicated")
        names = ("configured", "override", "dedicated")
        for index, path in enumerate(ordered):
            path.mkdir(parents=True, exist_ok=True)
            if index >= names.index(selected):
                (path / "sample.pyi").write_text("VALUE: int\n" if names[index] == selected else "VALUE: str\n")
        # Wrong-base candidates make a relative-path regression observable.
        for path in (cwd / "configured", project / "override", project / "dedicated"):
            path.mkdir()
            (path / "sample.pyi").write_text("VALUE: str\n")
        text = '[environment]\nroot=[]\nextra-paths=["./configured"]\n'
        (project / "ty.toml").write_text(text)
        (cwd / "demo.py").write_text("import sample\nvalue: int = sample.VALUE\n")
        override = ("--config", 'environment.extra-paths=["./override"]')
        dedicated = ("--extra-search-path", "./dedicated")
        args = dedicated + override if reverse else override + dedicated
        resolved = resolve_ty_search_paths(configuration(project, text), args=args, environment={}, cwd=cwd, package_name="demo")
        assert isinstance(resolved, TySearchPaths)
        assert resolved.extra == ordered
        executable = shutil.which("ty")
        assert executable is not None
        result = subprocess.run(
            (executable, "check", "--project", str(project), "--python", sys.executable,
             "--output-format", "gitlab", "--no-progress", "--color", "never",
             "--no-respect-ignore-files", *args, str(cwd / "demo.py")),
            cwd=cwd, env={}, capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert json.loads(result.stdout) == []

    def test_cli_expansion_and_literal_pythonpath(self, tmp_path: Path) -> None:
        mode = "pythonpath"
        project, external = tmp_path / "project", tmp_path / "external"
        literal = project / "$ROOT"
        literal.mkdir(parents=True)
        external.mkdir()
        (literal / "sample.pyi").write_text("VALUE: str\n")
        (external / "sample.pyi").write_text("VALUE: int\n")
        text = '[environment]\nroot=[]\n'
        (project / "ty.toml").write_text(text)
        (project / "demo.py").write_text("import sample\nvalue: int = sample.VALUE\n")
        environment = {"ROOT": str(external)}
        args = {"dedicated": ("--extra-search-path", "$ROOT"),
                "config": ("--config", 'environment.extra-paths=["$ROOT"]'), "pythonpath": ()}[mode]
        if mode == "pythonpath":
            environment["PYTHONPATH"] = "$ROOT"
        resolved = resolve_ty_search_paths(configuration(project, text), args=args, environment=environment, cwd=project, package_name="demo")
        assert isinstance(resolved, TySearchPaths)
        assert resolved.extra + resolved.pythonpath == ((literal,) if mode == "pythonpath" else (external,))
        executable = shutil.which("ty")
        assert executable is not None
        result = subprocess.run(
            (executable, "check", "--project", str(project), "--python", sys.executable,
             "--output-format", "gitlab", "--no-progress", "--color", "never",
             "--no-respect-ignore-files", *args, str(project / "demo.py")),
            cwd=project, env=environment, capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == (1 if mode == "pythonpath" else 0), (result.stdout, result.stderr)
        assert [item["check_name"] for item in json.loads(result.stdout)] == (["invalid-assignment"] if mode == "pythonpath" else [])

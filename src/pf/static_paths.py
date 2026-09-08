"""Resolve ty's ordered path inputs from frozen configuration and environment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Literal

import tomli

from pf.static_configuration import TyConfigurationResolution
from pf.ty_options import validate_ty_args


@dataclass(frozen=True)
class TySearchPaths:
    first_party: tuple[Path, ...]
    extra: tuple[Path, ...]
    pythonpath: tuple[Path, ...]
    ignored_pythonpath: tuple[Path, ...]
    typeshed: Path | None
    respect_ignore_files: bool
    expansion_variables: tuple[str, ...]


@dataclass(frozen=True)
class TySearchPathsUnavailable:
    reason: Literal["static-subject-unavailable"] = "static-subject-unavailable"
    detail: Literal["unclosed-path-input"] = "unclosed-path-input"


_VALUE_OPTIONS = frozenset({"--error", "--warn", "--ignore", "--exclude"})
_FLAGS = frozenset({
    "--error-on-warning", "--exit-zero", "--exit-zero-on-warning",
    "--respect-ignore-files", "--no-respect-ignore-files", "--force-exclude",
    "--no-force-exclude", "--exclude-scripts", "--include-scripts",
    "--verbose", "--quiet", "-v", "-vv", "-vvv", "-q", "-qq",
})


def resolve_ty_search_paths(
    configuration: TyConfigurationResolution, *, args: tuple[str, ...],
    environment: Mapping[str, str], cwd: Path, package_name: str,
) -> TySearchPaths | TySearchPathsUnavailable:
    validate_ty_args(args)
    try:
        return _resolve(configuration, args, environment, cwd, package_name)
    except (OSError, ValueError, TypeError):
        return TySearchPathsUnavailable()


def _resolve(configuration, args, environment, cwd, package_name) -> TySearchPaths:
    if not cwd.is_absolute() or not configuration.analysis_root.is_absolute():
        raise ValueError("path input needs absolute execution roots")
    settings = tomli.loads(configuration.effective_toml)
    configured = settings.get("environment", {})
    if not isinstance(configured, dict):
        raise ValueError("environment configuration must be a table")
    used: set[str] = set()

    def path(value: str, base: Path) -> Path:
        if not isinstance(value, str):
            raise ValueError("search path must be text")
        if value == "~" or value.startswith("~/"):
            used.add("HOME")
            if "HOME" not in environment:
                raise ValueError("HOME is not bound")
            value = environment["HOME"] + value[1:]
        elif value.startswith("~"):
            raise ValueError("named-user expansion is not closed")

        def replace(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2)
            used.add(name)
            if name not in environment:
                raise ValueError("path expansion variable is not bound")
            return environment[name]

        value = re.sub(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)", replace, value)
        candidate = Path(value)
        return Path(os.path.abspath(candidate if candidate.is_absolute() else base / candidate))

    def paths(values, base):
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError("path configuration must be an array of strings")
        return [path(value, base) for value in values]

    root_explicit = "root" in configured
    roots = paths(configured.get("root", []), configuration.analysis_root)
    extra = paths(configured.get("extra-paths", []), configuration.analysis_root)
    typeshed = path(configured["typeshed"], configuration.analysis_root) if "typeshed" in configured else None
    src_settings = settings.get("src", {})
    if not isinstance(src_settings, dict):
        raise ValueError("src configuration must be a table")
    respect = src_settings.get("respect-ignore-files", True)
    dedicated_extra: list[Path] = []
    dedicated_typeshed = None
    dedicated_respect = None
    index = 0
    while index < len(args):
        argument = args[index]
        option, equals, value = argument.partition("=")
        if option in _FLAGS:
            if equals:
                raise ValueError("flag has unexpected value")
            if option in {"--respect-ignore-files", "--no-respect-ignore-files"}:
                dedicated_respect = option == "--respect-ignore-files"
            index += 1
            continue
        if option not in _VALUE_OPTIONS | {"--config", "-c", "--extra-search-path", "--typeshed", "--custom-typeshed-dir"}:
            raise ValueError("unsupported static observation argument")
        if not equals:
            index += 1
            if index == len(args):
                raise ValueError("missing option value")
            value = args[index]
        if option in {"--config", "-c"}:
            override = tomli.loads(value)
            env = override.get("environment", {})
            if not isinstance(env, dict):
                raise ValueError("environment override must be a table")
            if "root" in env:
                root_explicit = True
                roots.extend(paths(env["root"], cwd))
            extra.extend(paths(env.get("extra-paths", []), cwd))
            if "typeshed" in env:
                typeshed = path(env["typeshed"], cwd)
            src = override.get("src", {})
            if not isinstance(src, dict):
                raise ValueError("src override must be a table")
            if "respect-ignore-files" in src:
                respect = src["respect-ignore-files"]
        elif option == "--extra-search-path":
            dedicated_extra.append(path(value, cwd))
        elif option in {"--typeshed", "--custom-typeshed-dir"}:
            dedicated_typeshed = path(value, cwd)
        index += 1
    extra.extend(dedicated_extra)
    typeshed = dedicated_typeshed or typeshed
    if dedicated_respect is not None:
        respect = dedicated_respect
    if not isinstance(respect, bool):
        raise ValueError("respect-ignore-files must be Boolean")
    if not root_explicit:
        root = configuration.analysis_root
        candidates = [root / "src"]
        if (root / package_name / package_name).is_dir():
            candidates.append(root / package_name)
        candidates.append(root / "python")
        roots = [candidate for candidate in candidates if candidate.is_dir() and not any(
            (candidate / name).exists() for name in ("__init__.py", "__init__.pyi")
        )] + [root]
    for candidate in (*roots, *extra):
        if not candidate.is_dir():
            raise ValueError("configured search path is not a directory")
    if typeshed is not None and not typeshed.is_dir():
        raise ValueError("typeshed is not a directory")
    pythonpath = []
    ignored = []
    if "PYTHONPATH" in environment:
        used.add("PYTHONPATH")
        for raw in environment["PYTHONPATH"].split(os.pathsep):
            value = Path(raw)
            candidate = Path(os.path.abspath(value if value.is_absolute() else cwd / value))
            (pythonpath if candidate.is_dir() else ignored).append(candidate)
    return TySearchPaths(tuple(roots), tuple(extra), tuple(pythonpath), tuple(ignored), typeshed, respect, tuple(sorted(used)))

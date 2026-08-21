from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import tomli

from pf.adapters.process import ProcessRunner
from pf.errors import ConfigurationError
from pf.schemas.evaluation import (
    ProcessSpec,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
)


_OWNED_OPTIONS = frozenset(
    {
        "--color",
        "--no-progress",
        "--output-format",
        "--platform",
        "--progress",
        "--python",
        "--python-platform",
        "--python-version",
        "--target-version",
        "--venv",
    }
)
_OWNED_CONFIGURATION_KEYS = frozenset(
    {
        "color",
        "no-progress",
        "output-format",
        "platform",
        "progress",
        "python",
        "python-platform",
        "python-version",
        "target-version",
        "venv",
    }
)


class TyAdapter:
    """Run a complete ty check and distinguish diagnostics from tool failures."""

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def check(
        self,
        *,
        interpreter: Path,
        package: Path,
        python_minor: str,
        target: str,
        args: tuple[str, ...],
        timeout_seconds: int | None,
        snapshot_root: Path | None = None,
    ) -> TyCheck | ToolFailure:
        self._validate_args(args)
        self._validate_project_configuration(
            package,
            snapshot_root=snapshot_root or package,
        )
        result = self._runner.run(
            ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    interpreter.as_posix(),
                    "--python-version",
                    python_minor,
                    "--python-platform",
                    self._python_platform(target),
                    "--no-progress",
                    "--color",
                    "never",
                    *args,
                    package.as_posix(),
                ),
                cwd=package.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        if result.timed_out:
            return ToolFailure(cause="TIMEOUT", stage="ty", process=result)
        if result.exit_code not in {0, 1} or result.stdout_truncated:
            return ToolFailure(cause="TOOL_FAILURE", stage="ty", process=result)
        try:
            document = json.loads(result.stdout_summary)
            if not isinstance(document, list):
                raise ValueError("ty GitLab output must be a JSON array")
            diagnostics = tuple(
                sorted(
                    (
                        self._diagnostic(
                            record,
                            diagnostic_root=package,
                            snapshot_root=snapshot_root or package,
                            environment_root=interpreter.parent.parent,
                        )
                        for record in document
                    ),
                    key=lambda item: (item.identity, item.severity, item.message),
                )
            )
        except (KeyError, TypeError, ValueError):
            return ToolFailure(cause="TOOL_FAILURE", stage="ty", process=result)
        return TyCheck(process=result, diagnostics=diagnostics)

    @staticmethod
    def _validate_args(args: tuple[str, ...]) -> None:
        for index, argument in enumerate(args):
            option = argument.partition("=")[0]
            if option in _OWNED_OPTIONS:
                raise ConfigurationError(
                    f"adapter-owned ty option is not allowed: {option}"
                )
            if option == "--config-file":
                raise ConfigurationError(
                    "adapter-owned ty option may be changed by --config-file"
                )
            if option not in {"-c", "--config"}:
                continue
            if "=" in argument and option == "--config":
                override = argument.partition("=")[2]
            elif index + 1 < len(args):
                override = args[index + 1]
            else:
                continue
            key = override.partition("=")[0].strip().replace("_", "-")
            leaf_key = key.rpartition(".")[2]
            if leaf_key in _OWNED_CONFIGURATION_KEYS:
                raise ConfigurationError(
                    f"adapter-owned ty option is not allowed in config override: {key}"
                )

    @staticmethod
    def _validate_project_configuration(
        package: Path,
        *,
        snapshot_root: Path,
    ) -> None:
        current = package.resolve(strict=False)
        root = snapshot_root.resolve(strict=False)
        directories = []
        if current == root or root in current.parents:
            while True:
                directories.append(current)
                if current == root:
                    break
                current = current.parent
        else:
            directories.append(current)
        for directory in directories:
            pyproject = directory / "pyproject.toml"
            if not pyproject.is_file():
                continue
            document = tomli.loads(pyproject.read_text(encoding="utf-8"))
            terminal = document.get("tool", {}).get("ty", {}).get("terminal", {})
            if not isinstance(terminal, dict):
                continue
            normalized_keys = {key.replace("_", "-") for key in terminal}
            conflicts = sorted(_OWNED_CONFIGURATION_KEYS.intersection(normalized_keys))
            if conflicts:
                raise ConfigurationError(
                    "adapter-owned ty configuration is not allowed: "
                    + ", ".join(conflicts)
                )

    @classmethod
    def _diagnostic(
        cls,
        record: Any,
        *,
        diagnostic_root: Path,
        snapshot_root: Path,
        environment_root: Path,
    ) -> TyDiagnostic:
        if not isinstance(record, dict):
            raise ValueError("ty diagnostic must be an object")
        code = cls._required_text(record, "check_name")
        message = cls._required_text(record, "description")
        severity = cls._required_text(record, "severity")
        location = record.get("location")
        if not isinstance(location, dict):
            raise ValueError("ty diagnostic location must be an object")
        raw_path = cls._required_text(location, "path")
        begin = cls._begin(location)
        line = cls._positive_integer(begin, "line")
        column = cls._optional_positive_integer(begin, "column")
        origin, path = cls._normalize_path(
            raw_path,
            diagnostic_root=diagnostic_root,
            snapshot_root=snapshot_root,
            environment_root=environment_root,
        )
        if origin == "snapshot":
            identity_parts = (origin, path, str(line))
            if column is not None:
                identity_parts += (str(column),)
            identity = "|".join((*identity_parts, code))
            normalized_line = line
            normalized_column = column
        else:
            identity = "|".join((origin, path, code))
            normalized_line = None
            normalized_column = None
        return TyDiagnostic(
            identity=identity,
            origin=origin,
            path=path,
            line=normalized_line,
            column=normalized_column,
            code=code,
            severity=severity,
            message=message,
        )

    @staticmethod
    def _required_text(document: dict[str, Any], key: str) -> str:
        value = document.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"ty diagnostic {key} must be non-empty text")
        return value

    @staticmethod
    def _begin(location: dict[str, Any]) -> dict[str, Any]:
        positions = location.get("positions")
        if isinstance(positions, dict) and isinstance(positions.get("begin"), dict):
            return positions["begin"]
        lines = location.get("lines")
        if isinstance(lines, dict) and "begin" in lines:
            return {"line": lines["begin"]}
        raise ValueError("ty diagnostic location has no begin position")

    @staticmethod
    def _positive_integer(document: dict[str, Any], key: str) -> int:
        value = document.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"ty diagnostic {key} must be a positive integer")
        return value

    @classmethod
    def _optional_positive_integer(
        cls,
        document: dict[str, Any],
        key: str,
    ) -> int | None:
        if key not in document:
            return None
        return cls._positive_integer(document, key)

    @staticmethod
    def _normalize_path(
        raw_path: str,
        *,
        diagnostic_root: Path,
        snapshot_root: Path,
        environment_root: Path,
    ) -> tuple[Literal["snapshot", "external"], str]:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = diagnostic_root / candidate
        normalized = candidate.resolve(strict=False)
        snapshot = snapshot_root.resolve(strict=False)
        environment = environment_root.resolve(strict=False)
        try:
            return "snapshot", normalized.relative_to(snapshot).as_posix()
        except ValueError:
            pass
        parts = normalized.parts
        for marker in ("site-packages", "dist-packages"):
            if marker in parts:
                relative = parts[parts.index(marker) + 1 :]
                if not relative:
                    raise ValueError("external site-packages path has no relative file")
                path = Path("site-packages") / Path(*relative)
                return "external", path.as_posix()
        if "typeshed" in parts:
            relative = parts[parts.index("typeshed") + 1 :]
            if not relative:
                raise ValueError("external typeshed path has no relative file")
            return "external", (Path("typeshed") / Path(*relative)).as_posix()
        try:
            relative = normalized.relative_to(environment)
        except ValueError as error:
            raise ValueError(
                "external diagnostic path has no stable namespace"
            ) from error
        if not relative.parts:
            raise ValueError("external interpreter path has no relative file")
        return "external", (Path("interpreter") / relative).as_posix()

    @staticmethod
    def _python_platform(target: str) -> str:
        if "-linux-" in target:
            return "linux"
        if "-apple-darwin" in target:
            return "darwin"
        if "-windows-" in target:
            return "win32"
        return "all"

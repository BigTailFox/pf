from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pf.adapters.process import ProcessRunner, read_process_output
from pf.schemas.evaluation import (
    ProcessTerminalUnavailable,
    ProcessObservation,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
)
from pf.schemas.static import StaticContentUnavailable
from pf.cancellation import Cancellation

if TYPE_CHECKING:
    from pf.static_request import StaticTyRequest


class TyAdapter:
    """Run a complete ty check and distinguish diagnostics from tool failures."""

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def observe(
        self, request: StaticTyRequest, *, cancellation: Cancellation | None = None
    ) -> TyCheck | ToolFailure | StaticContentUnavailable:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        with request.prepared.static_use(cancellation=cancellation) as available:
            if not available:
                return StaticContentUnavailable(detail="content-changed")
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if not request.revalidate():
                return StaticContentUnavailable(detail="content-changed")
            result = self._runner.run(request.spec, cancellation=cancellation)
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if not request.revalidate():
                return StaticContentUnavailable(detail="content-changed")
            return TyOutputDecoder(self._runner).decode(
                result,
                diagnostic_root=Path(request.spec.cwd),
                snapshot_root=request.snapshot_root,
                environment_root=request.environment_root,
            )


class TyOutputDecoder:
    """Decode one actual process observation; never execute or infer a request."""

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def decode(
        self,
        result: ProcessObservation,
        *,
        diagnostic_root: Path,
        snapshot_root: Path,
        environment_root: Path,
    ) -> TyCheck | ToolFailure:
        if isinstance(result, ProcessTerminalUnavailable):
            return ToolFailure(cause="TOOL_FAILURE", stage="ty", process=result)
        if result.timed_out:
            return ToolFailure(cause="TIMEOUT", stage="ty", process=result)
        if result.exit_code not in {0, 1} or not result.stdout_complete:
            return ToolFailure(cause="TOOL_FAILURE", stage="ty", process=result)
        try:
            document = json.loads(read_process_output(self._runner, result).stdout)
            if not isinstance(document, list):
                raise ValueError("ty GitLab output must be a JSON array")
            diagnostics = tuple(
                sorted(
                    (
                        self._diagnostic(
                            record,
                            diagnostic_root=diagnostic_root,
                            snapshot_root=snapshot_root,
                            environment_root=environment_root,
                        )
                        for record in document
                    ),
                    key=lambda item: (item.identity, item.severity, item.message),
                )
            )
        except (KeyError, TypeError, ValueError):
            return ToolFailure(cause="TOOL_FAILURE", stage="ty", process=result)
        return TyCheck(process=result, diagnostics=diagnostics)

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

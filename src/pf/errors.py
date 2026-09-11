from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    COMPATIBILITY_FAILED = 1
    NO_APPLICABLE_FLOOR = 2
    INVALID_INPUT = 3
    INDETERMINATE = 4
    INTERRUPTED = 130


class PfError(Exception):
    """Base class for expected PF failures rendered at the CLI seam."""

    category = "pf"
    exit_code = ExitCode.INDETERMINATE

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class NoApplicableFloorError(PfError):
    category = "no-applicable-floor"
    exit_code = ExitCode.NO_APPLICABLE_FLOOR


class ConfigurationError(PfError):
    category = "configuration"
    exit_code = ExitCode.INVALID_INPUT

    def __init__(
        self,
        message: str = "",
        *,
        detail: str | None = None,
        candidates: tuple[str, ...] = (),
        reason: str | None = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.candidates = candidates
        self.reason = reason


class SearchSpaceResolutionError(PfError):
    category = "search-space-resolution"
    exit_code = ExitCode.NO_APPLICABLE_FLOOR

    def __init__(
        self, *, dependency: str, cell: object, expression: str, reason: str,
        anchors: tuple[tuple[str, str], ...], series_keys: tuple[tuple[int, ...], ...],
        source: object,
    ) -> None:
        self.dependency = dependency
        self.cell = cell
        self.expression = expression
        self.reason = reason
        self.anchors = anchors
        self.series_keys = series_keys
        self.source = source
        super().__init__(
            f"cannot resolve search-space for {dependency}: {reason}",
            detail=f"Cell: {cell}; expression: {expression}; anchors: {anchors}; "
            f"series: {series_keys}; source: {source}",
        )


class JournalReadError(ConfigurationError):
    """A present Journal failed current-contract or evidence admission."""

    def __init__(self, *, run_id: str, reason: str) -> None:
        self.run_id = run_id
        super().__init__(
            f"cannot read verification journal: {reason}",
            detail=f"run: {run_id}",
            reason=reason,
        )


class ApplyAuthorizationError(ConfigurationError):
    """A report has evidence, but current apply authorization failed."""


class DiagnoseNotFoundError(ConfigurationError):
    """The selected package's current diagnostic stores do not contain an ID."""

    def __init__(self, *, failure_id: str, package: str) -> None:
        self.failure_id = failure_id
        self.package = package
        super().__init__(
            f"failure ID not found: {failure_id}",
            reason=(
                "failure ID was not found in package-floor.json or the latest local Journal"
            ),
        )


class ExplainReportError(ConfigurationError):
    """An explain request selected a report that cannot be presented."""

    def __init__(
        self,
        *,
        report_path: str,
        reason: str,
        recovery_command: str | None = None,
    ) -> None:
        self.report_path = report_path
        self.recovery_command = recovery_command
        super().__init__(f"cannot explain {report_path}: {reason}", reason=reason)


class MergeInputError(ConfigurationError):
    """A merge input could not be read as a validated report."""

    def __init__(
        self,
        *,
        input_paths: tuple[str, ...],
        output_path: str,
        failed_input_path: str,
    ) -> None:
        self.input_paths = input_paths
        self.output_path = output_path
        self.failed_input_path = failed_input_path
        super().__init__("input report is unavailable or invalid")


class MergeCompatibilityError(ConfigurationError):
    """Validated merge inputs cannot form one report generation."""

    def __init__(
        self,
        *,
        input_paths: tuple[str, ...],
        output_path: str,
        detail: str,
        reason: str | None = None,
    ) -> None:
        self.input_paths = input_paths
        self.output_path = output_path
        super().__init__(
            "reports are incompatible and cannot be merged",
            detail=detail,
            reason=reason,
        )


class InvocationError(ConfigurationError):
    """A user-correctable CLI usage error rendered as Error/Usage/Try."""

    exit_code = ExitCode.COMPATIBILITY_FAILED


class InfrastructureError(PfError):
    category = "infrastructure"
    exit_code = ExitCode.INDETERMINATE


class MaterializationIntegrityError(InfrastructureError):
    """Owned execution inputs changed after the prepared environment was verified."""


class MergeOutputError(InfrastructureError):
    """A compatible merged report could not be written atomically."""

    def __init__(
        self,
        *,
        input_paths: tuple[str, ...],
        output_path: str,
    ) -> None:
        self.input_paths = input_paths
        self.output_path = output_path
        super().__init__("merged report could not be written reliably")

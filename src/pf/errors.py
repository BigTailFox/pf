from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    COMPATIBILITY_FAILED = 1
    NO_APPLICABLE_FLOOR = 2
    INVALID_INPUT = 3
    INDETERMINATE = 4


class PfError(Exception):
    """Base class for expected PF failures rendered at the CLI seam."""

    category = "pf"
    exit_code = ExitCode.INDETERMINATE

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class CompatibilityError(PfError):
    category = "compatibility"
    exit_code = ExitCode.COMPATIBILITY_FAILED


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
    ) -> None:
        super().__init__(message, detail=detail)
        self.candidates = candidates


class ApplyAuthorizationError(ConfigurationError):
    """A report has evidence, but current apply authorization failed."""


class DiagnoseNotFoundError(ConfigurationError):
    """The selected package's current diagnostic stores do not contain an ID."""

    def __init__(self, *, failure_id: str, package: str) -> None:
        self.failure_id = failure_id
        self.package = package
        self.reason = (
            "failure ID was not found in package-floor.json or the latest local Journal"
        )
        super().__init__(f"failure ID not found: {failure_id}")


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
        self.reason = reason
        self.recovery_command = recovery_command
        super().__init__(f"cannot explain {report_path}: {reason}")


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
    ) -> None:
        self.input_paths = input_paths
        self.output_path = output_path
        super().__init__(
            "reports are incompatible and cannot be merged",
            detail=detail,
        )


class InvocationError(ConfigurationError):
    """A user-correctable CLI usage error rendered as Error/Usage/Try."""

    exit_code = ExitCode.COMPATIBILITY_FAILED


class InfrastructureError(PfError):
    category = "infrastructure"
    exit_code = ExitCode.INDETERMINATE


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

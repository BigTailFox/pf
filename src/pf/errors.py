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


class InvocationError(ConfigurationError):
    """A user-correctable CLI usage error rendered as Error/Usage/Try."""


class InfrastructureError(PfError):
    category = "infrastructure"
    exit_code = ExitCode.INDETERMINATE

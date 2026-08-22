from __future__ import annotations

from pf.schemas.evaluation import (
    AttemptFailureScope,
    FailureCause,
    FailureDetail,
    FailureRecord,
    FailureScope,
    ProcessResult,
    rejection_is_supported,
)


class FailurePolicy:
    """Turn scoped operation facts into one conservative search disposition."""

    identity = "failure-v1"

    def classify(
        self,
        *,
        scope: FailureScope,
        cause: FailureCause,
        stage: str,
        process: ProcessResult | None,
        summary_code: str | None = None,
        detail: FailureDetail | None = None,
    ) -> FailureRecord:
        requested_resolution = (
            scope.attempt.identity.requested_resolution
            if isinstance(scope, AttemptFailureScope)
            else None
        )
        supported = process is not None and rejection_is_supported(
            requested_resolution=requested_resolution,
            cause=cause,
            stage=stage,
            exit_code=process.exit_code,
            signal=process.signal,
            start_error=process.start_error,
            timed_out=process.timed_out,
            stdout_complete=process.stdout_complete,
            stderr_complete=process.stderr_complete,
        )
        return FailureRecord.from_facts(
            scope=scope,
            disposition="REJECTED" if supported else "INDETERMINATE",
            cause=cause,
            stage=stage,
            process=process,
            summary_code=summary_code,
            detail=detail,
        )

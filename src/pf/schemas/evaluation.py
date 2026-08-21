from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Annotated, ClassVar, Literal, Union

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.project import (
    Cell,
    InterpreterIdentity,
    Proposal,
    ResolvedNode,
    VersionPin,
)


class EnvironmentVariable(FrozenSchema):
    name: str
    value: str


class ProcessSpec(FrozenSchema):
    argv: tuple[str, ...]
    cwd: str
    environment: tuple[EnvironmentVariable, ...] = ()
    timeout_seconds: int | None
    start_new_session: bool = True
    redaction_policy_identity: str = "pf-default-v1"
    summary_limit: int | None = None

    @model_validator(mode="after")
    def validate_process_spec(self) -> "ProcessSpec":
        if not self.argv:
            raise ValueError("process argv cannot be empty")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("process timeout must be positive or None")
        if self.summary_limit is not None and self.summary_limit <= 0:
            raise ValueError("summary limit must be positive or None")
        return self


class ProcessResult(FrozenSchema):
    exit_code: int | None
    signal: int | None
    duration_seconds: float
    stdout_summary: str
    stderr_summary: str
    stdout_tail: str
    stderr_tail: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    start_error: str | None = None

    @model_validator(mode="after")
    def validate_process_result(self) -> "ProcessResult":
        facts = sum(
            value is not None
            for value in (self.exit_code, self.signal, self.start_error)
        )
        if facts != 1:
            raise ValueError("process result must have exactly one terminal fact")
        return self

    def diagnostic(self) -> str:
        parts: list[str] = []
        if self.start_error:
            parts.append(self.start_error)
        text = self.stderr_summary.strip() or self.stdout_summary.strip()
        if not text:
            text = self.stderr_tail.strip() or self.stdout_tail.strip()
        if text and text not in parts:
            parts.append(text)
        if parts:
            return "\n".join(parts)
        if self.timed_out:
            return "process timed out"
        if self.signal is not None:
            return f"terminated by signal {self.signal}"
        if self.exit_code is not None:
            return f"exit code {self.exit_code}"
        return ""


FailureCause = Literal[
    "RESOLUTION_CONFLICT",
    "BUILD_FAILURE",
    "HARNESS_CONFLICT",
    "STATIC_REGRESSION",
    "TEST_FAILURE",
    "SOURCE_FAILURE",
    "ENVIRONMENT_FAILURE",
    "TOOL_FAILURE",
    "TIMEOUT",
    "INTERNAL_INVARIANT",
    "NONDETERMINISTIC",
]


_REJECTION_STAGES: dict[str, frozenset[str]] = {
    "RESOLUTION_CONFLICT": frozenset({"install", "install-project"}),
    "BUILD_FAILURE": frozenset({"install", "install-project", "install-harness"}),
    "HARNESS_CONFLICT": frozenset({"install-harness"}),
    "STATIC_REGRESSION": frozenset({"ty"}),
    "TEST_FAILURE": frozenset({"test"}),
}


def rejection_is_supported(
    *,
    requested_resolution: str | None,
    cause: str,
    stage: str,
    exit_code: int | None,
    signal: int | None,
    start_error: str | None,
    timed_out: bool,
    stdout_truncated: bool,
    stderr_truncated: bool,
) -> bool:
    """Return whether portable facts are sufficient for a v1 Rejection."""
    if requested_resolution not in {"highest", "exact-vector"}:
        return False
    if stage not in _REJECTION_STAGES.get(cause, ()):
        return False
    if requested_resolution == "highest" and cause == "STATIC_REGRESSION":
        return False
    if (
        exit_code is None
        or signal is not None
        or start_error is not None
        or timed_out
        or stdout_truncated
        or stderr_truncated
    ):
        return False
    return exit_code != 0 or cause in {"HARNESS_CONFLICT", "STATIC_REGRESSION"}


class AttemptIdentity(FrozenSchema):
    source_snapshot_digest: str
    cell: Cell
    requested_resolution: Literal["highest", "exact-vector"]
    requested_managed_vector: tuple[VersionPin, ...] | None
    active_declaration_ids: tuple[str, ...]
    source_plan_identity: str
    evaluation_policy_identity: str

    @model_validator(mode="after")
    def validate_requested_resolution(self) -> "AttemptIdentity":
        if not self.source_snapshot_digest:
            raise ValueError("attempt source snapshot digest cannot be empty")
        if not self.source_plan_identity or not self.evaluation_policy_identity:
            raise ValueError("attempt source and policy identities cannot be empty")
        if self.active_declaration_ids != self.cell.active_declaration_ids:
            raise ValueError("attempt declarations must match its cell")
        if self.requested_resolution == "highest":
            if self.requested_managed_vector is not None:
                raise ValueError("highest attempt cannot contain an exact vector")
        elif self.requested_managed_vector is None:
            raise ValueError("exact-vector attempt requires a managed vector")
        if self.requested_managed_vector is not None:
            names = tuple(pin.name for pin in self.requested_managed_vector)
            if names != tuple(sorted(set(names))):
                raise ValueError("attempt managed vector must be sorted and unique")
        return self


class Attempt(FrozenSchema):
    attempt_id: str
    identity: AttemptIdentity

    @classmethod
    def from_identity(cls, identity: AttemptIdentity) -> "Attempt":
        return cls(attempt_id=cls._identity_digest(identity), identity=identity)

    @staticmethod
    def _identity_digest(identity: AttemptIdentity) -> str:
        canonical = json.dumps(
            identity.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(b"pf:attempt:v1\0" + canonical).hexdigest()

    @model_validator(mode="after")
    def validate_attempt_id(self) -> "Attempt":
        expected = self._identity_digest(self.identity)
        if self.attempt_id != expected:
            raise ValueError("attempt ID does not match its identity")
        return self


class AttemptFailureScope(FrozenSchema):
    kind: Literal["attempt"] = "attempt"
    attempt: Attempt


class CellFailureScope(FrozenSchema):
    kind: Literal["cell"] = "cell"
    package: str
    cell: Cell
    source_snapshot_digest: str
    evaluation_policy_identity: str

    @model_validator(mode="after")
    def validate_cell_scope(self) -> "CellFailureScope":
        if self.package != self.cell.package:
            raise ValueError("cell failure package must match its cell")
        if not self.source_snapshot_digest or not self.evaluation_policy_identity:
            raise ValueError(
                "cell failure source and policy identities cannot be empty"
            )
        return self


FailureScope = Annotated[
    Union[AttemptFailureScope, CellFailureScope],
    Field(discriminator="kind"),
]


class FailureDetail(FrozenSchema):
    code: str
    message: str

    @model_validator(mode="after")
    def validate_detail(self) -> "FailureDetail":
        if not self.code.strip() or not self.message.strip():
            raise ValueError("failure detail fields cannot be empty")
        return self


class FailureRecord(FrozenSchema):
    failure_id: str
    scope: FailureScope
    disposition: Literal["REJECTED", "INDETERMINATE"]
    cause: FailureCause
    stage: str
    process: ProcessResult | None = None
    summary_code: str | None = None
    detail: FailureDetail | None = None

    @classmethod
    def from_facts(
        cls,
        *,
        scope: FailureScope,
        disposition: Literal["REJECTED", "INDETERMINATE"],
        cause: FailureCause,
        stage: str,
        process: ProcessResult | None,
        summary_code: str | None = None,
        detail: FailureDetail | None = None,
    ) -> "FailureRecord":
        failure_id = cls._failure_id(
            scope=scope,
            disposition=disposition,
            cause=cause,
            stage=stage,
            process=process,
            summary_code=summary_code,
            detail=detail,
        )
        return cls(
            failure_id=failure_id,
            scope=scope,
            disposition=disposition,
            cause=cause,
            stage=stage,
            process=process,
            summary_code=summary_code,
            detail=detail,
        )

    @staticmethod
    def _failure_id(
        *,
        scope: FailureScope,
        disposition: Literal["REJECTED", "INDETERMINATE"],
        cause: FailureCause,
        stage: str,
        process: ProcessResult | None,
        summary_code: str | None,
        detail: FailureDetail | None,
    ) -> str:
        payload = {
            "scope": scope.model_dump(mode="json"),
            "disposition": disposition,
            "cause": cause,
            "stage": stage,
            "process": (
                process.model_dump(mode="json") if process is not None else None
            ),
            "summary_code": summary_code,
            "detail": detail.model_dump(mode="json") if detail is not None else None,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return (
            "failure-" + hashlib.sha256(b"pf:failure:v1\0" + canonical).hexdigest()[:16]
        )

    @model_validator(mode="after")
    def validate_failure_record(self) -> "FailureRecord":
        expected = self._failure_id(
            scope=self.scope,
            disposition=self.disposition,
            cause=self.cause,
            stage=self.stage,
            process=self.process,
            summary_code=self.summary_code,
            detail=self.detail,
        )
        if self.failure_id != expected:
            raise ValueError("failure ID does not match its structured facts")
        if not self.stage.strip():
            raise ValueError("failure stage cannot be empty")
        if self.process is None and self.detail is None:
            raise ValueError("failure requires process facts or structured detail")
        if (
            isinstance(self.scope, CellFailureScope)
            and self.disposition != "INDETERMINATE"
        ):
            raise ValueError("cell-scoped failure must be indeterminate")
        if self.disposition == "REJECTED":
            process = self.process
            requested_resolution = (
                self.scope.attempt.identity.requested_resolution
                if isinstance(self.scope, AttemptFailureScope)
                else None
            )
            if process is None or not rejection_is_supported(
                requested_resolution=requested_resolution,
                cause=self.cause,
                stage=self.stage,
                exit_code=process.exit_code,
                signal=process.signal,
                start_error=process.start_error,
                timed_out=process.timed_out,
                stdout_truncated=process.stdout_truncated,
                stderr_truncated=process.stderr_truncated,
            ):
                raise ValueError("REJECTED disposition is not supported by its facts")
        return self


class ToolSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    stage: str
    process: ProcessResult


class ToolFailure(FrozenSchema):
    status: Literal["FAILURE"] = "FAILURE"
    cause: FailureCause
    stage: str
    process: ProcessResult
    summary_code: str | None = None


class PrepareFailure(FrozenSchema):
    attempt: Attempt
    failure: ToolFailure


ToolOutcome = Annotated[
    Union[ToolSuccess, ToolFailure],
    Field(discriminator="status"),
]


class TyDiagnostic(FrozenSchema):
    identity: str
    origin: Literal["snapshot", "external"]
    path: str
    line: int | None
    column: int | None
    code: str
    severity: str
    message: str

    @model_validator(mode="after")
    def validate_identity(self) -> "TyDiagnostic":
        if not self.path.strip() or not self.code.strip():
            raise ValueError("ty diagnostic path and code must be non-empty")
        if not self.severity.strip() or not self.message.strip():
            raise ValueError("ty diagnostic severity and message must be non-empty")
        if self.origin == "snapshot":
            if self.line is None or self.line <= 0:
                raise ValueError("snapshot ty diagnostic requires a positive line")
            if self.column is not None and self.column <= 0:
                raise ValueError("snapshot ty diagnostic column must be positive")
            parts = (self.origin, self.path, str(self.line))
            if self.column is not None:
                parts += (str(self.column),)
            expected = "|".join((*parts, self.code))
        else:
            if self.line is not None or self.column is not None:
                raise ValueError("external ty diagnostic cannot retain line or column")
            expected = "|".join((self.origin, self.path, self.code))
        if self.identity != expected:
            raise ValueError("ty diagnostic identity does not match normalized fields")
        return self


def ty_diagnostic_digest(diagnostics: tuple[TyDiagnostic, ...]) -> str:
    identities = [item.identity for item in diagnostics]
    canonical = json.dumps(identities, separators=(",", ":")).encode()
    return hashlib.sha256(
        b"pf:ty-diagnostic-baseline:increment-v2\0" + canonical
    ).hexdigest()


class TyCheck(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    process: ProcessResult
    diagnostics: tuple[TyDiagnostic, ...]

    @model_validator(mode="after")
    def validate_diagnostic_order(self) -> "TyCheck":
        if (
            self.process.exit_code not in {0, 1}
            or self.process.timed_out
            or self.process.stdout_truncated
        ):
            raise ValueError(
                "TyCheck requires complete output from successful exit 0 or 1"
            )
        identities = tuple(item.identity for item in self.diagnostics)
        if identities != tuple(sorted(identities)):
            raise ValueError("ty diagnostics must be sorted by stable identity")
        return self


class StaticBaseline(FrozenSchema):
    proposal: "Proposal"
    ty: TyCheck
    digest: str

    @model_validator(mode="after")
    def validate_baseline(self) -> "StaticBaseline":
        if self.digest != ty_diagnostic_digest(self.ty.diagnostics):
            raise ValueError("static baseline digest does not match its diagnostics")
        return self

    @property
    def diagnostics(self) -> tuple[TyDiagnostic, ...]:
        return self.ty.diagnostics


class InterpreterSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    process: ProcessResult
    interpreter: InterpreterIdentity


InterpreterOutcome = Annotated[
    Union[InterpreterSuccess, ToolFailure],
    Field(discriminator="status"),
]


class TestPass(FrozenSchema):
    __test__: ClassVar[bool] = False
    status: Literal["TEST_PASS"] = "TEST_PASS"
    process: ProcessResult

    @model_validator(mode="after")
    def validate_test_pass(self) -> "TestPass":
        process = self.process
        if (
            process.exit_code != 0
            or process.signal is not None
            or process.start_error is not None
            or process.timed_out
            or process.stdout_truncated
            or process.stderr_truncated
        ):
            raise ValueError("TEST_PASS requires a complete normal exit 0")
        return self


class TestFail(FrozenSchema):
    __test__: ClassVar[bool] = False
    status: Literal["TEST_FAIL"] = "TEST_FAIL"
    process: ProcessResult

    @model_validator(mode="after")
    def validate_test_fail(self) -> "TestFail":
        process = self.process
        if (
            process.exit_code in {None, 0}
            or process.signal is not None
            or process.start_error is not None
            or process.timed_out
            or process.stdout_truncated
            or process.stderr_truncated
        ):
            raise ValueError("TEST_FAIL requires a complete normal non-zero exit")
        return self


TestOutcome = Annotated[
    Union[TestPass, TestFail, ToolFailure],
    Field(discriminator="status"),
]


class GraphSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    process: ProcessResult
    nodes: tuple[ResolvedNode, ...]


GraphOutcome = Annotated[
    Union[GraphSuccess, ToolFailure],
    Field(discriminator="status"),
]


class StaticPassEvaluation(FrozenSchema):
    status: Literal["STATIC_PASS"] = "STATIC_PASS"
    proposal: "Proposal"
    ty: TyCheck
    baseline_digest: str
    incremental: tuple[TyDiagnostic, ...] = ()

    @model_validator(mode="after")
    def validate_static_pass(self) -> "StaticPassEvaluation":
        if not self.baseline_digest:
            raise ValueError("static evaluation baseline digest cannot be empty")
        if self.incremental:
            raise ValueError("STATIC_PASS requires an empty diagnostic increment")
        return self


class StaticFailEvaluation(FrozenSchema):
    status: Literal["STATIC_FAIL"] = "STATIC_FAIL"
    proposal: "Proposal"
    ty: TyCheck
    baseline_digest: str
    incremental: tuple[TyDiagnostic, ...]

    @model_validator(mode="after")
    def validate_static_fail(self) -> "StaticFailEvaluation":
        if not self.baseline_digest:
            raise ValueError("static evaluation baseline digest cannot be empty")
        if not self.incremental:
            raise ValueError("STATIC_FAIL requires a non-empty diagnostic increment")
        raw = Counter(item.identity for item in self.ty.diagnostics)
        increment = Counter(item.identity for item in self.incremental)
        if increment - raw:
            raise ValueError(
                "static increment must be a sub-multiset of ty diagnostics"
            )
        return self


class StaticBaselineCapture(FrozenSchema):
    baseline: StaticBaseline
    static: StaticPassEvaluation

    @model_validator(mode="after")
    def validate_capture(self) -> "StaticBaselineCapture":
        if self.baseline.proposal != self.static.proposal:
            raise ValueError("static capture proposal must match its baseline")
        if self.baseline.ty != self.static.ty:
            raise ValueError("static capture must reuse the baseline TyCheck")
        if self.baseline.digest != self.static.baseline_digest:
            raise ValueError("static capture baseline digest must match evaluation")
        return self


class PassEvaluation(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    proposal: "Proposal"
    static: StaticPassEvaluation
    test: TestPass


class TestFailEvaluation(FrozenSchema):
    __test__: ClassVar[bool] = False
    status: Literal["TEST_FAIL"] = "TEST_FAIL"
    proposal: "Proposal"
    static: StaticPassEvaluation
    test: TestFail


class IndeterminateEvaluation(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    proposal: "Proposal"
    cause: FailureCause
    failure: ToolFailure

    @model_validator(mode="after")
    def validate_failure_cause(self) -> "IndeterminateEvaluation":
        if self.cause != self.failure.cause:
            raise ValueError("indeterminate evaluation must retain its tool cause")
        return self


StaticEvaluation = Annotated[
    Union[StaticPassEvaluation, StaticFailEvaluation, IndeterminateEvaluation],
    Field(discriminator="status"),
]


Evaluation = Annotated[
    Union[
        PassEvaluation,
        StaticFailEvaluation,
        TestFailEvaluation,
        IndeterminateEvaluation,
    ],
    Field(discriminator="status"),
]


class CheckPass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    evaluations: tuple[PassEvaluation, ...]


class CheckCompatibilityFailure(FrozenSchema):
    status: Literal["COMPATIBILITY_FAILED"] = "COMPATIBILITY_FAILED"
    evaluations: tuple[Evaluation, ...]


class CheckIndeterminate(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    evaluations: tuple[Evaluation, ...] = ()
    failure: ToolFailure


CheckResult = Annotated[
    Union[CheckPass, CheckCompatibilityFailure, CheckIndeterminate],
    Field(discriminator="status"),
]


class HighestVersionPass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    attempt: Attempt
    baseline: StaticBaseline
    evaluation: PassEvaluation

    @model_validator(mode="after")
    def validate_highest_evaluation(self) -> "HighestVersionPass":
        if self.attempt.identity.requested_resolution != "highest":
            raise ValueError("highest-version pass requires a highest Attempt")
        if self.evaluation.proposal != self.baseline.proposal:
            raise ValueError("highest-version evaluation must match its baseline")
        if self.baseline.proposal.attempt_id != self.attempt.attempt_id:
            raise ValueError("highest-version proposal must reference its attempt")
        if self.evaluation.static.ty != self.baseline.ty:
            raise ValueError(
                "highest-version full evaluation must reuse the captured TyCheck"
            )
        if self.evaluation.static.baseline_digest != self.baseline.digest:
            raise ValueError(
                "highest-version full evaluation must reuse the baseline digest"
            )
        return self


class BaselineRejection(FrozenSchema):
    status: Literal["BASELINE_REJECTION"] = "BASELINE_REJECTION"
    attempt: Attempt
    failure: FailureRecord
    static_baseline: StaticBaseline | None = None
    evaluation: StaticFailEvaluation | TestFailEvaluation | None = None

    @property
    def cell(self) -> Cell:
        return self.attempt.identity.cell

    @model_validator(mode="after")
    def validate_rejection(self) -> "BaselineRejection":
        if self.attempt.identity.requested_resolution != "highest":
            raise ValueError("baseline rejection requires a highest Attempt")
        if self.failure.disposition != "REJECTED":
            raise ValueError("baseline rejection requires REJECTED disposition")
        if not isinstance(self.failure.scope, AttemptFailureScope):
            raise ValueError("baseline rejection requires attempt scope")
        if self.failure.scope.attempt != self.attempt:
            raise ValueError("baseline rejection failure must match its attempt")
        if self.failure.cause in {"STATIC_REGRESSION", "TEST_FAILURE"} and (
            self.evaluation is None
        ):
            raise ValueError(
                "baseline static/test rejection requires structured evaluation"
            )
        if isinstance(self.evaluation, StaticFailEvaluation) and (
            self.failure.cause != "STATIC_REGRESSION"
        ):
            raise ValueError("baseline static rejection cause must match evaluation")
        if isinstance(self.evaluation, TestFailEvaluation) and (
            self.failure.cause != "TEST_FAILURE"
        ):
            raise ValueError("baseline test rejection cause must match evaluation")
        if isinstance(self.evaluation, StaticFailEvaluation) and (
            self.failure.stage != "ty"
            or self.failure.process != self.evaluation.ty.process
        ):
            raise ValueError("baseline static diagnosis must match its evaluation")
        if isinstance(self.evaluation, TestFailEvaluation) and (
            self.failure.stage != "test"
            or self.failure.process != self.evaluation.test.process
        ):
            raise ValueError("baseline test diagnosis must match its evaluation")
        if self.evaluation is not None and (
            self.evaluation.proposal.attempt_id != self.attempt.attempt_id
        ):
            raise ValueError("baseline rejection evaluation must match its attempt")
        if self.evaluation is not None:
            if self.static_baseline is None:
                raise ValueError("baseline evaluation requires its static baseline")
            if self.evaluation.proposal != self.static_baseline.proposal:
                raise ValueError("baseline rejection must identify captured V_hi")
            if isinstance(self.evaluation, TestFailEvaluation):
                if self.evaluation.static.ty != self.static_baseline.ty:
                    raise ValueError(
                        "baseline rejection must reuse captured V_hi TyCheck"
                    )
                if (
                    self.evaluation.static.baseline_digest
                    != self.static_baseline.digest
                ):
                    raise ValueError(
                        "baseline rejection must reuse captured V_hi digest"
                    )
        return self


class BaselineIndeterminate(FrozenSchema):
    status: Literal["BASELINE_INDETERMINATE"] = "BASELINE_INDETERMINATE"
    attempt: Attempt
    failure: FailureRecord
    static_baseline: StaticBaseline | None = None
    evaluation: IndeterminateEvaluation | None = None

    @property
    def cell(self) -> Cell:
        return self.attempt.identity.cell

    @model_validator(mode="after")
    def validate_indeterminate(self) -> "BaselineIndeterminate":
        if self.attempt.identity.requested_resolution != "highest":
            raise ValueError("baseline indeterminate requires a highest Attempt")
        if self.failure.disposition != "INDETERMINATE":
            raise ValueError(
                "baseline indeterminate requires INDETERMINATE disposition"
            )
        if not isinstance(self.failure.scope, AttemptFailureScope):
            raise ValueError("baseline indeterminate requires attempt scope")
        if self.failure.scope.attempt != self.attempt:
            raise ValueError("baseline indeterminate failure must match its attempt")
        if self.evaluation is not None and (
            self.evaluation.proposal.attempt_id != self.attempt.attempt_id
        ):
            raise ValueError("baseline indeterminate evaluation must match its attempt")
        if (
            self.evaluation is not None
            and self.static_baseline is not None
            and (self.evaluation.proposal != self.static_baseline.proposal)
        ):
            raise ValueError("baseline indeterminate must identify captured V_hi")
        if self.evaluation is not None and (
            self.failure.cause != self.evaluation.cause
            or self.failure.stage != self.evaluation.failure.stage
            or self.failure.process != self.evaluation.failure.process
        ):
            raise ValueError(
                "baseline indeterminate diagnosis must match its evaluation"
            )
        return self


HighestVersionOutcome = Annotated[
    Union[HighestVersionPass, BaselineRejection, BaselineIndeterminate],
    Field(discriminator="status"),
]


class SmokePass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    outcomes: tuple[HighestVersionPass, ...]


class SmokeBaselineRejection(FrozenSchema):
    status: Literal["BASELINE_REJECTION"] = "BASELINE_REJECTION"
    outcomes: tuple[HighestVersionOutcome, ...]

    @model_validator(mode="after")
    def validate_rejection(self) -> "SmokeBaselineRejection":
        if not any(isinstance(item, BaselineRejection) for item in self.outcomes):
            raise ValueError("smoke baseline rejection requires rejected evidence")
        return self


class SmokeIndeterminate(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    outcomes: tuple[HighestVersionPass | BaselineIndeterminate, ...]

    @model_validator(mode="after")
    def validate_indeterminate(self) -> "SmokeIndeterminate":
        if not any(isinstance(item, BaselineIndeterminate) for item in self.outcomes):
            raise ValueError("smoke indeterminate requires indeterminate evidence")
        return self


SmokeResult = Annotated[
    Union[SmokePass, SmokeBaselineRejection, SmokeIndeterminate],
    Field(discriminator="status"),
]


class CacheConflict(FrozenSchema):
    status: Literal["NONDETERMINISTIC"] = "NONDETERMINISTIC"
    proposal_id: str
    observed_statuses: tuple[str, str]


class ProgressEvent(FrozenSchema):
    package: str
    cell: Cell
    phase: str
    completed: int
    total: int
    message: str
    detail: str = ""
    diagnostics: tuple[TyDiagnostic, ...] = ()
    process: ProcessResult | None = None
    failure: FailureRecord | None = None


class StatusEvent(FrozenSchema):
    message: str
    package: str | None = None
    completed: int = 0
    total: int | None = None


class CellMatrixEvent(FrozenSchema):
    cells: tuple[Cell, ...]


class ProcessEvent(FrozenSchema):
    process_id: int
    argv: tuple[str, ...]
    state: Literal["started", "finished"]
    duration_seconds: float | None = None


class SearchFailureEvent(FrozenSchema):
    kind: Literal["failure"] = "failure"
    cell: Cell
    failure: FailureRecord
    evaluation: (
        StaticFailEvaluation | TestFailEvaluation | IndeterminateEvaluation | None
    ) = None

    @model_validator(mode="after")
    def validate_cell(self) -> "SearchFailureEvent":
        scope_cell = (
            self.failure.scope.attempt.identity.cell
            if isinstance(self.failure.scope, AttemptFailureScope)
            else self.failure.scope.cell
        )
        if scope_cell != self.cell:
            raise ValueError("search failure event must match its failure scope")
        if self.evaluation is not None:
            if not isinstance(self.failure.scope, AttemptFailureScope):
                raise ValueError("search evaluation failure requires attempt scope")
            if (
                self.evaluation.proposal.attempt_id
                != self.failure.scope.attempt.attempt_id
            ):
                raise ValueError("search failure evaluation must match its attempt")
        return self


ActivityEvent = (
    ProgressEvent | StatusEvent | CellMatrixEvent | ProcessEvent | SearchFailureEvent
)

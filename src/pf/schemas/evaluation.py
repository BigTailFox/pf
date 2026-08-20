from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Annotated, ClassVar, Literal, Union

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.project import Cell, InterpreterIdentity, Proposal, ResolvedNode


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


class ToolSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    stage: str
    process: ProcessResult


class ToolFailure(FrozenSchema):
    status: Literal[
        "UNAVAILABLE",
        "BUILD_UNAVAILABLE",
        "UNRESOLVABLE",
        "HARNESS_ERROR",
        "SOURCE_ERROR",
        "TOOL_ERROR",
        "TIMEOUT",
    ]
    stage: str
    process: ProcessResult


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


class TestFail(FrozenSchema):
    __test__: ClassVar[bool] = False
    status: Literal["TEST_FAIL"] = "TEST_FAIL"
    process: ProcessResult


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
    status: Literal[
        "UNAVAILABLE",
        "BUILD_UNAVAILABLE",
        "UNRESOLVABLE",
        "HARNESS_ERROR",
        "SOURCE_ERROR",
        "TOOL_ERROR",
        "TIMEOUT",
    ]
    proposal: "Proposal"
    failure: ToolFailure


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


class HighestVersionVerification(FrozenSchema):
    baseline: StaticBaseline
    evaluation: Evaluation

    @model_validator(mode="after")
    def validate_highest_evaluation(self) -> "HighestVersionVerification":
        if isinstance(self.evaluation, StaticFailEvaluation):
            raise ValueError("highest-version capture cannot produce STATIC_FAIL")
        if self.evaluation.proposal != self.baseline.proposal:
            raise ValueError("highest-version evaluation must match its baseline")
        if isinstance(self.evaluation, (PassEvaluation, TestFailEvaluation)):
            if self.evaluation.static.ty != self.baseline.ty:
                raise ValueError(
                    "highest-version full evaluation must reuse the captured TyCheck"
                )
            if self.evaluation.static.baseline_digest != self.baseline.digest:
                raise ValueError(
                    "highest-version full evaluation must reuse the baseline digest"
                )
        return self


class SmokePass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    evaluations: tuple[PassEvaluation, ...]


class SmokeTestFailure(FrozenSchema):
    status: Literal["TEST_FAILED"] = "TEST_FAILED"
    evaluations: tuple[Evaluation, ...]

    @model_validator(mode="after")
    def validate_test_failure(self) -> "SmokeTestFailure":
        if not any(
            isinstance(evaluation, TestFailEvaluation)
            for evaluation in self.evaluations
        ):
            raise ValueError("smoke TEST_FAILED requires a failed test evaluation")
        return self


class SmokeIndeterminate(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    evaluations: tuple[PassEvaluation, ...] = ()
    failure: ToolFailure


SmokeResult = Annotated[
    Union[SmokePass, SmokeTestFailure, SmokeIndeterminate],
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


ActivityEvent = ProgressEvent | StatusEvent | CellMatrixEvent | ProcessEvent

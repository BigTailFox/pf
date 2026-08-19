from __future__ import annotations

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
            value is not None for value in (self.exit_code, self.signal, self.start_error)
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


class InterpreterSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    process: ProcessResult
    interpreter: InterpreterIdentity


InterpreterOutcome = Annotated[
    Union[InterpreterSuccess, ToolFailure],
    Field(discriminator="status"),
]


class TyPass(FrozenSchema):
    status: Literal["STATIC_PASS"] = "STATIC_PASS"
    process: ProcessResult


class TyFail(FrozenSchema):
    status: Literal["STATIC_FAIL"] = "STATIC_FAIL"
    process: ProcessResult


TyOutcome = Annotated[
    Union[TyPass, TyFail, ToolFailure],
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
    ty: TyPass


class StaticFailEvaluation(FrozenSchema):
    status: Literal["STATIC_FAIL"] = "STATIC_FAIL"
    proposal: "Proposal"
    ty: TyFail


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

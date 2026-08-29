from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Annotated, Literal, Union

from packaging.version import Version
from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.project import (
    Cell,
    HarnessBaseline,
    InterpreterIdentity,
    Proposal,
    ResolvedNode,
    VersionPin,
)
from pf.static_transition import (
    STATIC_POLICY_VERSION,
    static_fingerprint as compute_static_fingerprint,
)


class EnvironmentVariable(FrozenSchema):
    name: str
    value: str


class ProcessSpec(FrozenSchema):
    argv: tuple[str, ...]
    cwd: str
    environment: tuple[EnvironmentVariable, ...] = ()
    environment_removals: tuple[str, ...] = ()
    timeout_seconds: int | float | None
    start_new_session: bool = True
    redaction_policy_identity: str = "pf-default-v1"
    summary_limit: int | None = None

    @model_validator(mode="after")
    def validate_process_spec(self) -> "ProcessSpec":
        if not self.argv:
            raise ValueError("process argv cannot be empty")
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("process timeout must be positive or None")
        if self.summary_limit is not None and self.summary_limit <= 0:
            raise ValueError("summary limit must be positive or None")
        return self


class ProcessResult(FrozenSchema):
    exit_code: int | None = None
    signal: int | None = None
    duration_seconds: float
    stdout: str = Field(default="", exclude=True)
    stderr: str = Field(default="", exclude=True)
    stdout_complete: bool = True
    stderr_complete: bool = True
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
        if self.start_error is not None and self.timed_out:
            raise ValueError("process start failure cannot also be timed out")
        return self

    def diagnostic(self) -> str:
        parts: list[str] = []
        if self.start_error:
            parts.append(self.start_error)
        text = self.stderr.strip() or self.stdout.strip()
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


class ProcessTerminalUnavailable(FrozenSchema):
    kind: Literal["terminal-unavailable"] = "terminal-unavailable"
    duration_seconds: float = Field(default=0.0, ge=0, exclude=True)
    detail: str | None = Field(default=None, exclude=True)


ProcessObservation = ProcessResult | ProcessTerminalUnavailable


class NormalExit(FrozenSchema):
    kind: Literal["normal-exit"] = "normal-exit"
    exit_code: int


class StartFailed(FrozenSchema):
    kind: Literal["start-failed"] = "start-failed"


class TimedOut(FrozenSchema):
    kind: Literal["timed-out"] = "timed-out"


class Signaled(FrozenSchema):
    kind: Literal["signaled"] = "signaled"
    signal: int = Field(gt=0, strict=True)


class Unavailable(FrozenSchema):
    kind: Literal["unavailable"] = "unavailable"


VerifierTerminal = Annotated[
    Union[NormalExit, StartFailed, TimedOut, Signaled, Unavailable],
    Field(discriminator="kind"),
]


class VerifierPass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    terminal: NormalExit

    @model_validator(mode="after")
    def validate_terminal(self) -> "VerifierPass":
        if self.terminal.exit_code != 0:
            raise ValueError("verifier pass requires normal exit 0")
        return self


class VerifierRejected(FrozenSchema):
    status: Literal["REJECTED"] = "REJECTED"
    terminal: NormalExit
    reason: Literal["verifier-exited-nonzero"] = "verifier-exited-nonzero"

    @model_validator(mode="after")
    def validate_terminal(self) -> "VerifierRejected":
        if self.terminal.exit_code == 0:
            raise ValueError("verifier rejection requires a nonzero normal exit")
        return self


class VerifierIndeterminate(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    terminal: StartFailed | TimedOut | Signaled | Unavailable
    reason: Literal[
        "process-start-failed",
        "process-timed-out",
        "process-signaled",
        "terminal-unavailable",
    ]

    @model_validator(mode="after")
    def validate_reason(self) -> "VerifierIndeterminate":
        if isinstance(self.terminal, StartFailed):
            expected = "process-start-failed"
        elif isinstance(self.terminal, TimedOut):
            expected = "process-timed-out"
        elif isinstance(self.terminal, Signaled):
            expected = "process-signaled"
        else:
            expected = "terminal-unavailable"
        if self.reason != expected:
            raise ValueError("verifier indeterminate reason must match its terminal")
        return self


VerifierOutcome = Annotated[
    Union[VerifierPass, VerifierRejected, VerifierIndeterminate],
    Field(discriminator="status"),
]


class VerifierDiagnostics(FrozenSchema):
    process: ProcessObservation
    detail: "PytestFailureDetail | None" = None
    summary_code: str | None = None
    pytest_execution_mode: Literal["serial", "xdist", "unknown"] | None = None
    pytest_facts: tuple[tuple[str, str], ...] = ()
    pytest_version: str | None = None
    python_minor: str | None = None


class VerifierRequest(FrozenSchema):
    command: tuple[str, ...]
    cwd: Path
    environment: tuple[EnvironmentVariable, ...] = ()
    timeout_seconds: int | None

    @model_validator(mode="after")
    def validate_request(self) -> "VerifierRequest":
        if not self.command:
            raise ValueError("verifier command cannot be empty")
        return self


class VerifierRun(FrozenSchema):
    authoritative: VerifierOutcome
    diagnostics: VerifierDiagnostics | None = Field(default=None, exclude=True)


def process_facts_match(
    left: ProcessObservation | None,
    right: ProcessObservation | None,
) -> bool:
    """Return whether two results have the same report-portable process facts."""
    if left is None or right is None:
        return left is right
    return left.model_dump(mode="json") == right.model_dump(mode="json")


FailureCause = Literal[
    "RESOLUTION_CONFLICT",
    "BUILD_FAILURE",
    "HARNESS_CONFLICT",
    "RUNTIME_INTERFACE_MISSING",
    "VERIFIER_EXITED_NONZERO",
    "SOURCE_FAILURE",
    "ENVIRONMENT_FAILURE",
    "TOOL_FAILURE",
    "TIMEOUT",
    "INTERNAL_INVARIANT",
    "NONDETERMINISTIC",
]


_REJECTION_STAGES: dict[str, frozenset[str]] = {
    "RESOLUTION_CONFLICT": frozenset({"resolve-project"}),
    "HARNESS_CONFLICT": frozenset({"resolve-environment"}),
    "RUNTIME_INTERFACE_MISSING": frozenset({"witness"}),
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
    stdout_complete: bool,
    stderr_complete: bool,
) -> bool:
    """Return whether portable facts are sufficient for a v1 Rejection."""
    if requested_resolution not in {"highest", "exact-vector", "lowest-direct"}:
        return False
    if stage not in _REJECTION_STAGES.get(cause, ()):
        return False
    if (
        exit_code is None
        or signal is not None
        or start_error is not None
        or timed_out
        or not stdout_complete
        or not stderr_complete
    ):
        return False
    return exit_code != 0 or cause in {
        "HARNESS_CONFLICT",
        "RUNTIME_INTERFACE_MISSING",
    }


class AttemptIdentity(FrozenSchema):
    identity_version: Literal["attempt-v1", "attempt-v2"] = "attempt-v1"
    source_snapshot_digest: str
    cell: Cell
    requested_resolution: Literal["highest", "lowest-direct", "exact-vector"]
    requested_managed_vector: tuple[VersionPin, ...] | None
    active_declaration_ids: tuple[str, ...]
    source_plan_identity: str
    evaluation_policy_identity: str
    resolution_context_digest: str | None = None
    harness_policy_identity: (
        Literal["original-harness-v1", "harness-relaxation-v1"] | None
    ) = None
    harness_declaration_ids: tuple[str, ...] = ()
    harness_baseline_digest: str | None = None
    selected_candidate_evidence_digest: str | None = None

    @model_validator(mode="after")
    def validate_requested_resolution(self) -> "AttemptIdentity":
        if not self.source_snapshot_digest:
            raise ValueError("attempt source snapshot digest cannot be empty")
        if not self.source_plan_identity or not self.evaluation_policy_identity:
            raise ValueError("attempt source and policy identities cannot be empty")
        if self.active_declaration_ids != self.cell.active_declaration_ids:
            raise ValueError("attempt declarations must match its cell")
        if self.requested_resolution in {"highest", "lowest-direct"}:
            if self.requested_managed_vector is not None:
                raise ValueError(
                    f"{self.requested_resolution} attempt cannot contain an exact vector"
                )
        elif self.requested_managed_vector is None:
            raise ValueError("exact-vector attempt requires a managed vector")
        if self.requested_managed_vector is not None:
            names = tuple(pin.name for pin in self.requested_managed_vector)
            if names != tuple(sorted(set(names))):
                raise ValueError("attempt managed vector must be sorted and unique")
        if self.identity_version == "attempt-v2":
            if not self.resolution_context_digest or not self.harness_policy_identity:
                raise ValueError(
                    "v2 attempt requires resolution context and harness policy"
                )
            if self.harness_declaration_ids != tuple(
                sorted(set(self.harness_declaration_ids))
            ):
                raise ValueError(
                    "attempt harness declarations must be sorted and unique"
                )
            if self.requested_resolution == "highest":
                if (
                    self.harness_policy_identity != "original-harness-v1"
                    or self.harness_baseline_digest is not None
                    or self.selected_candidate_evidence_digest is not None
                ):
                    raise ValueError(
                        "highest attempt requires original harness without baseline"
                    )
            else:
                if (
                    self.harness_policy_identity != "harness-relaxation-v1"
                    or not self.harness_baseline_digest
                ):
                    raise ValueError("relaxed attempt requires a harness baseline")
                if (self.requested_resolution == "exact-vector") != (
                    self.selected_candidate_evidence_digest is not None
                ):
                    raise ValueError(
                        "only exact attempts carry selected candidate evidence"
                    )
        return self


class Attempt(FrozenSchema):
    attempt_id: str
    identity: AttemptIdentity

    @classmethod
    def from_identity(cls, identity: AttemptIdentity) -> "Attempt":
        return cls(attempt_id=cls._identity_digest(identity), identity=identity)

    @staticmethod
    def _identity_digest(identity: AttemptIdentity) -> str:
        exclude = (
            {
                "identity_version",
                "resolution_context_digest",
                "harness_policy_identity",
                "harness_declaration_ids",
                "harness_baseline_digest",
                "selected_candidate_evidence_digest",
            }
            if identity.identity_version == "attempt-v1"
            else None
        )
        canonical = json.dumps(
            identity.model_dump(mode="json", exclude=exclude),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        prefix = (
            b"pf:attempt:v1\0"
            if identity.identity_version == "attempt-v1"
            else b"pf:attempt:v2\0"
        )
        return hashlib.sha256(prefix + canonical).hexdigest()

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


class ProcessFailureAuthority(FrozenSchema):
    kind: Literal["process"] = "process"
    process: ProcessResult
    summary_code: str | None = None
    detail: FailureDetail | None = None


class ConfiguredVerifierFailureAuthority(FrozenSchema):
    kind: Literal["configured-verifier"] = "configured-verifier"
    terminal: VerifierTerminal


class StructuredFailureAuthority(FrozenSchema):
    kind: Literal["structured"] = "structured"
    detail: FailureDetail
    summary_code: str | None = None


FailureAuthority = Annotated[
    Union[
        ProcessFailureAuthority,
        ConfiguredVerifierFailureAuthority,
        StructuredFailureAuthority,
    ],
    Field(discriminator="kind"),
]


class FailureRecord(FrozenSchema):
    failure_id: str
    scope: FailureScope
    disposition: Literal["REJECTED", "INDETERMINATE"]
    cause: FailureCause
    stage: str
    authority: FailureAuthority
    project_plan_digest: str | None = None
    environment_plan_digest: str | None = None

    @property
    def process(self) -> ProcessResult | None:
        authority = self.authority
        return (
            authority.process
            if isinstance(authority, ProcessFailureAuthority)
            else None
        )

    @property
    def summary_code(self) -> str | None:
        authority = self.authority
        return (
            authority.summary_code
            if isinstance(
                authority, (ProcessFailureAuthority, StructuredFailureAuthority)
            )
            else None
        )

    @property
    def detail(self) -> FailureDetail | None:
        authority = self.authority
        return (
            authority.detail
            if isinstance(
                authority, (ProcessFailureAuthority, StructuredFailureAuthority)
            )
            else None
        )

    @classmethod
    def from_facts(
        cls,
        *,
        scope: FailureScope,
        disposition: Literal["REJECTED", "INDETERMINATE"],
        cause: FailureCause,
        stage: str,
        process: ProcessObservation | None,
        summary_code: str | None = None,
        detail: FailureDetail | None = None,
        project_plan_digest: str | None = None,
        environment_plan_digest: str | None = None,
    ) -> "FailureRecord":
        if isinstance(process, ProcessResult):
            authority: FailureAuthority = ProcessFailureAuthority(
                process=process,
                summary_code=summary_code,
                detail=detail,
            )
        elif isinstance(process, ProcessTerminalUnavailable):
            authority = StructuredFailureAuthority(
                detail=FailureDetail(
                    code="terminal-unavailable",
                    message="the process terminal observation was unavailable",
                ),
                summary_code=summary_code,
            )
        elif detail is not None:
            authority = StructuredFailureAuthority(
                detail=detail,
                summary_code=summary_code,
            )
        else:
            raise ValueError("failure requires process or structured authority")
        return cls.from_authority(
            scope=scope,
            disposition=disposition,
            cause=cause,
            stage=stage,
            authority=authority,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )

    @classmethod
    def from_verifier(
        cls,
        *,
        scope: FailureScope,
        disposition: Literal["REJECTED", "INDETERMINATE"],
        cause: FailureCause,
        stage: str,
        terminal: VerifierTerminal,
        project_plan_digest: str | None = None,
        environment_plan_digest: str | None = None,
    ) -> "FailureRecord":
        return cls.from_authority(
            scope=scope,
            disposition=disposition,
            cause=cause,
            stage=stage,
            authority=ConfiguredVerifierFailureAuthority(terminal=terminal),
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )

    @classmethod
    def from_authority(
        cls,
        *,
        scope: FailureScope,
        disposition: Literal["REJECTED", "INDETERMINATE"],
        cause: FailureCause,
        stage: str,
        authority: FailureAuthority,
        project_plan_digest: str | None = None,
        environment_plan_digest: str | None = None,
    ) -> "FailureRecord":
        failure_id = cls._failure_id(
            scope=scope,
            disposition=disposition,
            cause=cause,
            stage=stage,
            authority=authority,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )
        return cls(
            failure_id=failure_id,
            scope=scope,
            disposition=disposition,
            cause=cause,
            stage=stage,
            authority=authority,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )

    @staticmethod
    def _failure_id(
        *,
        scope: FailureScope,
        disposition: Literal["REJECTED", "INDETERMINATE"],
        cause: FailureCause,
        stage: str,
        authority: FailureAuthority,
        project_plan_digest: str | None,
        environment_plan_digest: str | None,
    ) -> str:
        payload = {
            "scope": scope.model_dump(mode="json"),
            "disposition": disposition,
            "cause": cause,
            "stage": stage,
            "authority": authority.model_dump(mode="json"),
        }
        if project_plan_digest is not None:
            payload["project_plan_digest"] = project_plan_digest
        if environment_plan_digest is not None:
            payload["environment_plan_digest"] = environment_plan_digest
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return (
            "failure-" + hashlib.sha256(b"pf:failure:v2\0" + canonical).hexdigest()[:16]
        )

    @model_validator(mode="after")
    def validate_failure_record(self) -> "FailureRecord":
        expected = self._failure_id(
            scope=self.scope,
            disposition=self.disposition,
            cause=self.cause,
            stage=self.stage,
            authority=self.authority,
            project_plan_digest=self.project_plan_digest,
            environment_plan_digest=self.environment_plan_digest,
        )
        if self.failure_id != expected:
            raise ValueError("failure ID does not match its structured facts")
        if not self.stage.strip():
            raise ValueError("failure stage cannot be empty")
        if (
            self.environment_plan_digest is not None
            and self.project_plan_digest is None
        ):
            raise ValueError("environment plan evidence requires a project plan")
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
            if isinstance(self.authority, ConfiguredVerifierFailureAuthority):
                if not (
                    self.cause == "VERIFIER_EXITED_NONZERO"
                    and self.stage == "test"
                    and isinstance(self.authority.terminal, NormalExit)
                    and self.authority.terminal.exit_code != 0
                ):
                    raise ValueError(
                        "configured verifier rejection does not match its authority"
                    )
            elif process is None or not rejection_is_supported(
                requested_resolution=requested_resolution,
                cause=self.cause,
                stage=self.stage,
                exit_code=process.exit_code,
                signal=process.signal,
                start_error=process.start_error,
                timed_out=process.timed_out,
                stdout_complete=process.stdout_complete,
                stderr_complete=process.stderr_complete,
            ):
                raise ValueError("REJECTED disposition is not supported by its facts")
        if isinstance(self.authority, ConfiguredVerifierFailureAuthority):
            terminal = self.authority.terminal
            if isinstance(terminal, NormalExit):
                if terminal.exit_code == 0:
                    raise ValueError("passing verifier terminal cannot form a failure")
                expected = ("REJECTED", "VERIFIER_EXITED_NONZERO")
            elif isinstance(terminal, TimedOut):
                expected = ("INDETERMINATE", "TIMEOUT")
            else:
                expected = ("INDETERMINATE", "TOOL_FAILURE")
            if self.stage != "test" or (self.disposition, self.cause) != expected:
                raise ValueError(
                    "configured verifier failure does not match its terminal"
                )
        elif self.cause == "VERIFIER_EXITED_NONZERO":
            raise ValueError(
                "verifier exit cause requires configured-verifier authority"
            )
        return self


class ToolSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    stage: str
    process: ProcessResult


class ToolFailure(FrozenSchema):
    status: Literal["FAILURE"] = "FAILURE"
    cause: FailureCause
    stage: str
    process: ProcessObservation | None
    summary_code: str | None = None
    detail: FailureDetail | None = None

    @model_validator(mode="after")
    def validate_authority(self) -> "ToolFailure":
        if self.process is None and self.detail is None:
            raise ValueError("tool failure requires process or structured detail")
        return self


class PrepareFailure(FrozenSchema):
    attempt: Attempt
    failure: ToolFailure
    project_plan_digest: str | None = None
    environment_plan_digest: str | None = None

    @model_validator(mode="after")
    def validate_plan_evidence(self) -> "PrepareFailure":
        if (
            self.environment_plan_digest is not None
            and self.project_plan_digest is None
        ):
            raise ValueError("environment plan evidence requires a project plan")
        return self


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
        f"pf:ty-diagnostic-baseline:{STATIC_POLICY_VERSION}\0".encode() + canonical
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
            or not self.process.stdout_complete
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


class PytestFailureCase(FrozenSchema):
    nodeid: str
    phase: Literal["collect", "setup", "call", "teardown"]

    @model_validator(mode="after")
    def validate_nodeid(self) -> "PytestFailureCase":
        if (
            not self.nodeid
            or len(self.nodeid) > 4_096
            or any(
                ord(character) < 32
                or 127 <= ord(character) <= 159
                or 0xD800 <= ord(character) <= 0xDFFF
                for character in self.nodeid
            )
        ):
            raise ValueError("pytest failure nodeid must be bounded display text")
        return self


class PytestFailureDetail(FrozenSchema):
    kind: Literal["pytest-failure"] = "pytest-failure"
    first: PytestFailureCase
    total: int = Field(gt=0, le=10_000, strict=True)


class GraphSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    process: ProcessResult
    nodes: tuple[ResolvedNode, ...]


GraphOutcome = Annotated[
    Union[GraphSuccess, ToolFailure],
    Field(discriminator="status"),
]


class RuntimeWitnessPlan(FrozenSchema):
    diagnostic_identities: tuple[str, ...]
    managed_dependency: str
    operation: Literal["import-module", "import-symbol", "has-member"]
    module: str
    owner: str | None = None
    symbol_or_member: str | None = None
    planner_policy_version: str = "witness-planner-v1"

    @model_validator(mode="after")
    def validate_plan(self) -> "RuntimeWitnessPlan":
        if not self.diagnostic_identities:
            raise ValueError("runtime witness plan requires diagnostic identities")
        if self.diagnostic_identities != tuple(sorted(self.diagnostic_identities)):
            raise ValueError("runtime witness diagnostics must use canonical order")
        if not self.managed_dependency.strip() or not self.module.strip():
            raise ValueError("runtime witness dependency and module cannot be empty")
        if self.planner_policy_version != "witness-planner-v1":
            raise ValueError("unsupported runtime witness planner policy")
        if self.operation == "import-module":
            if self.owner is not None or self.symbol_or_member is not None:
                raise ValueError(
                    "import-module witness cannot retain an owner or symbol"
                )
        elif self.operation == "import-symbol":
            if self.owner is not None or not self.symbol_or_member:
                raise ValueError("import-symbol witness requires only a symbol")
        elif not self.owner or not self.symbol_or_member:
            raise ValueError("has-member witness requires an owner and member")
        return self


class DiagnosticClassification(FrozenSchema):
    diagnostic_identity: str
    classification: Literal["strong", "general"]
    reason_code: str
    witness_plan: RuntimeWitnessPlan | None = None
    classifier_policy_version: str = "strong-classifier-v1"

    @model_validator(mode="after")
    def validate_classification(self) -> "DiagnosticClassification":
        if not self.diagnostic_identity or not self.reason_code:
            raise ValueError("diagnostic classification facts cannot be empty")
        if self.classifier_policy_version != "strong-classifier-v1":
            raise ValueError("unsupported strong classifier policy")
        if self.classification == "strong":
            if self.witness_plan is None:
                raise ValueError(
                    "strong diagnostic classification requires a witness plan"
                )
            if self.diagnostic_identity not in self.witness_plan.diagnostic_identities:
                raise ValueError(
                    "strong diagnostic must be covered by its witness plan"
                )
        elif self.witness_plan is not None:
            raise ValueError("general diagnostic cannot retain a witness plan")
        return self


class RuntimeWitnessResult(FrozenSchema):
    status: Literal["PRESENT", "CONFIRMED_MISSING", "NOT_APPLICABLE"]
    plan: RuntimeWitnessPlan
    process: ProcessResult

    @model_validator(mode="after")
    def validate_result(self) -> "RuntimeWitnessResult":
        process = self.process
        if (
            process.exit_code != 0
            or process.signal is not None
            or process.start_error is not None
            or process.timed_out
            or not process.stdout_complete
            or not process.stderr_complete
        ):
            raise ValueError("runtime witness result requires a complete normal exit 0")
        return self


RuntimeWitnessOutcome = Annotated[
    Union[RuntimeWitnessResult, ToolFailure],
    Field(discriminator="status"),
]


class RuntimeWitnessAttempt(FrozenSchema):
    plan: RuntimeWitnessPlan
    outcome: RuntimeWitnessOutcome

    @model_validator(mode="after")
    def validate_attempt(self) -> "RuntimeWitnessAttempt":
        if isinstance(self.outcome, RuntimeWitnessResult):
            if self.outcome.plan != self.plan:
                raise ValueError("runtime witness result must match its plan")
        elif self.outcome.stage != "witness":
            raise ValueError("runtime witness tool failure must use witness stage")
        return self


class StaticUnchangedEvaluation(FrozenSchema):
    status: Literal["STATIC_UNCHANGED"] = "STATIC_UNCHANGED"
    proposal: "Proposal"
    ty: TyCheck
    baseline_digest: str
    incremental: tuple[TyDiagnostic, ...] = ()
    static_fingerprint: str = compute_static_fingerprint(())

    @model_validator(mode="after")
    def validate_static_unchanged(self) -> "StaticUnchangedEvaluation":
        if not self.baseline_digest:
            raise ValueError("static evaluation baseline digest cannot be empty")
        if self.incremental:
            raise ValueError("STATIC_UNCHANGED requires an empty diagnostic increment")
        if self.static_fingerprint != compute_static_fingerprint(()):
            raise ValueError("static fingerprint does not match its increment")
        return self


class StaticRegressionEvaluation(FrozenSchema):
    status: Literal["STATIC_REGRESSION"] = "STATIC_REGRESSION"
    proposal: "Proposal"
    ty: TyCheck
    baseline_digest: str
    incremental: tuple[TyDiagnostic, ...]
    static_fingerprint: str
    classifications: tuple[DiagnosticClassification, ...]

    @model_validator(mode="after")
    def validate_static_regression(self) -> "StaticRegressionEvaluation":
        if not self.baseline_digest:
            raise ValueError("static evaluation baseline digest cannot be empty")
        if not self.incremental:
            raise ValueError(
                "STATIC_REGRESSION requires a non-empty diagnostic increment"
            )
        raw = Counter(item.identity for item in self.ty.diagnostics)
        increment = Counter(item.identity for item in self.incremental)
        if increment - raw:
            raise ValueError(
                "static increment must be a sub-multiset of ty diagnostics"
            )
        identities = tuple(item.identity for item in self.incremental)
        if identities != tuple(sorted(identities)):
            raise ValueError("static increment must use canonical diagnostic order")
        expected = compute_static_fingerprint(identities)
        if self.static_fingerprint != expected:
            raise ValueError("static fingerprint does not match its increment")
        classified = tuple(item.diagnostic_identity for item in self.classifications)
        if classified != identities:
            raise ValueError(
                "static diagnostic classifications must match the ordered increment"
            )
        return self


def _require_witness_prefix(
    static: StaticUnchangedEvaluation | StaticRegressionEvaluation,
    witnesses: tuple[RuntimeWitnessAttempt, ...],
) -> tuple[RuntimeWitnessPlan, ...]:
    allowed_list: list[RuntimeWitnessPlan] = []
    if isinstance(static, StaticRegressionEvaluation):
        for classification in static.classifications:
            plan = classification.witness_plan
            if plan is not None and plan not in allowed_list:
                allowed_list.append(plan)
    allowed = tuple(allowed_list)
    plans = tuple(attempt.plan for attempt in witnesses)
    if plans != allowed[: len(plans)] or len(set(plans)) != len(plans):
        raise ValueError(
            "runtime witness attempts must follow this Proposal's classified plans"
        )
    return allowed


class StaticBaselineCapture(FrozenSchema):
    baseline: StaticBaseline
    static: StaticUnchangedEvaluation

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
    static: StaticUnchangedEvaluation | StaticRegressionEvaluation
    witnesses: tuple[RuntimeWitnessAttempt, ...] = ()
    verifier: VerifierPass

    @model_validator(mode="after")
    def validate_pass(self) -> "PassEvaluation":
        if self.static.proposal != self.proposal:
            raise ValueError("pass static evidence must match its proposal")
        if any(
            isinstance(attempt.outcome, RuntimeWitnessResult)
            and attempt.outcome.status == "CONFIRMED_MISSING"
            for attempt in self.witnesses
        ):
            raise ValueError("pass cannot retain confirmed-missing witness evidence")
        if any(isinstance(attempt.outcome, ToolFailure) for attempt in self.witnesses):
            raise ValueError("pass cannot retain witness tool failure")
        allowed = _require_witness_prefix(self.static, self.witnesses)
        if self.witnesses and len(self.witnesses) != len(allowed):
            raise ValueError("pass must complete every selected witness plan")
        return self


class VerifierRejectedEvaluation(FrozenSchema):
    status: Literal["VERIFIER_REJECTED"] = "VERIFIER_REJECTED"
    proposal: "Proposal"
    static: StaticUnchangedEvaluation | StaticRegressionEvaluation
    witnesses: tuple[RuntimeWitnessAttempt, ...] = ()
    verifier: VerifierRejected

    @model_validator(mode="after")
    def validate_verifier_rejection(self) -> "VerifierRejectedEvaluation":
        if self.static.proposal != self.proposal:
            raise ValueError(
                "verifier rejection static evidence must match its proposal"
            )
        if any(
            isinstance(attempt.outcome, RuntimeWitnessResult)
            and attempt.outcome.status == "CONFIRMED_MISSING"
            for attempt in self.witnesses
        ):
            raise ValueError(
                "verifier rejection cannot follow confirmed-missing witness evidence"
            )
        if any(isinstance(attempt.outcome, ToolFailure) for attempt in self.witnesses):
            raise ValueError("verifier rejection cannot retain witness tool failure")
        allowed = _require_witness_prefix(self.static, self.witnesses)
        if self.witnesses and len(self.witnesses) != len(allowed):
            raise ValueError("verifier rejection must complete every witness plan")
        return self


class RuntimeInterfaceMissingEvaluation(FrozenSchema):
    status: Literal["RUNTIME_INTERFACE_MISSING"] = "RUNTIME_INTERFACE_MISSING"
    proposal: "Proposal"
    static: StaticRegressionEvaluation
    witnesses: tuple[RuntimeWitnessAttempt, ...]

    @model_validator(mode="after")
    def validate_runtime_missing(self) -> "RuntimeInterfaceMissingEvaluation":
        if self.static.proposal != self.proposal:
            raise ValueError("runtime missing static evidence must match its proposal")
        _require_witness_prefix(self.static, self.witnesses)
        if not self.witnesses or not (
            isinstance(self.witnesses[-1].outcome, RuntimeWitnessResult)
            and self.witnesses[-1].outcome.status == "CONFIRMED_MISSING"
        ):
            raise ValueError(
                "runtime interface missing requires confirmed-missing witness evidence"
            )
        if any(
            isinstance(attempt.outcome, ToolFailure)
            or (
                isinstance(attempt.outcome, RuntimeWitnessResult)
                and attempt.outcome.status == "CONFIRMED_MISSING"
            )
            for attempt in self.witnesses[:-1]
        ):
            raise ValueError(
                "runtime evaluation must stop at its first terminal witness"
            )
        return self


class IndeterminateEvaluation(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    proposal: "Proposal"
    cause: FailureCause
    failure: ToolFailure | None = None
    verifier: VerifierIndeterminate | None = None
    static: StaticUnchangedEvaluation | StaticRegressionEvaluation | None = None
    witnesses: tuple[RuntimeWitnessAttempt, ...] = ()

    @model_validator(mode="after")
    def validate_failure_cause(self) -> "IndeterminateEvaluation":
        if (self.failure is None) == (self.verifier is None):
            raise ValueError("indeterminate evaluation requires exactly one authority")
        if self.failure is not None and self.cause != self.failure.cause:
            raise ValueError("indeterminate evaluation must retain its tool cause")
        if self.verifier is not None:
            expected = (
                "TIMEOUT"
                if isinstance(self.verifier.terminal, TimedOut)
                else "TOOL_FAILURE"
            )
            if self.cause != expected:
                raise ValueError("verifier indeterminate cause must match its terminal")
        if (
            (self.failure is not None and self.failure.stage in {"witness", "test"})
            or self.verifier is not None
        ) and self.static is None:
            raise ValueError(
                "runtime indeterminate evaluation requires its static evidence"
            )
        if self.static is not None and self.static.proposal != self.proposal:
            raise ValueError("indeterminate static evidence must match its proposal")
        if self.witnesses:
            if self.static is None:
                raise ValueError("witness indeterminate requires static evidence")
            _require_witness_prefix(self.static, self.witnesses)
            if self.failure is not None and self.failure.stage == "witness":
                last = self.witnesses[-1].outcome
                if not isinstance(last, ToolFailure) or last != self.failure:
                    raise ValueError(
                        "witness indeterminate must end with its retained tool failure"
                    )
                prior = self.witnesses[:-1]
            else:
                prior = self.witnesses
                allowed = _require_witness_prefix(self.static, self.witnesses)
                if len(self.witnesses) != len(allowed):
                    raise ValueError(
                        "verifier indeterminate must complete every witness plan"
                    )
            if any(
                isinstance(attempt.outcome, ToolFailure)
                or (
                    isinstance(attempt.outcome, RuntimeWitnessResult)
                    and attempt.outcome.status == "CONFIRMED_MISSING"
                )
                for attempt in prior
            ):
                raise ValueError(
                    "witness evaluation must stop at its first terminal outcome"
                )
        return self


StaticEvaluation = Annotated[
    Union[
        StaticUnchangedEvaluation,
        StaticRegressionEvaluation,
        IndeterminateEvaluation,
    ],
    Field(discriminator="status"),
]


Evaluation = Annotated[
    Union[
        PassEvaluation,
        RuntimeInterfaceMissingEvaluation,
        VerifierRejectedEvaluation,
        IndeterminateEvaluation,
    ],
    Field(discriminator="status"),
]


class RuntimeEvaluationRun(FrozenSchema):
    evaluation: Evaluation
    diagnostics: VerifierDiagnostics | None = Field(default=None, exclude=True)


def runtime_process_observation(
    runtime: RuntimeEvaluationRun,
) -> ProcessObservation | None:
    if runtime.diagnostics is not None:
        return runtime.diagnostics.process
    evaluation = runtime.evaluation
    if isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
        confirmed = evaluation.witnesses[-1].outcome
        assert isinstance(confirmed, RuntimeWitnessResult)
        return confirmed.process
    if isinstance(evaluation, IndeterminateEvaluation):
        return None if evaluation.failure is None else evaluation.failure.process
    return None


class FailureEvaluationRuntimeRun(FrozenSchema):
    kind: Literal["evaluation"] = "evaluation"
    failure_id: str
    runtime: RuntimeEvaluationRun

    @property
    def process_observation(self) -> ProcessObservation | None:
        return runtime_process_observation(self.runtime)

    @model_validator(mode="after")
    def validate_process_association(self) -> "FailureEvaluationRuntimeRun":
        if self.process_observation is None:
            raise ValueError("failure evaluation runtime requires a process")
        return self


class FailureProcessRuntimeRun(FrozenSchema):
    kind: Literal["process"] = "process"
    failure_id: str
    process: ProcessTerminalUnavailable = Field(exclude=True)

    @property
    def process_observation(self) -> ProcessTerminalUnavailable:
        return self.process


FailureRuntimeRun = Annotated[
    Union[FailureEvaluationRuntimeRun, FailureProcessRuntimeRun],
    Field(discriminator="kind"),
]


class CheckPass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    evaluations: tuple[PassEvaluation, ...]
    outcomes: tuple["CheckCellOutcome", ...] = ()


class CheckCompatibilityFailure(FrozenSchema):
    status: Literal["COMPATIBILITY_FAILED"] = "COMPATIBILITY_FAILED"
    evaluations: tuple[Evaluation, ...]
    outcomes: tuple["CheckCellOutcome", ...] = ()


class CheckIndeterminate(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    evaluations: tuple[Evaluation, ...] = ()
    failure: FailureRecord
    outcomes: tuple["CheckCellOutcome", ...] = ()


CheckResult = Annotated[
    Union[CheckPass, CheckCompatibilityFailure, CheckIndeterminate],
    Field(discriminator="status"),
]


VerificationRole = Literal[
    "baseline",
    "declaration-capture",
    "declaration",
    "probe",
]


class CheckCellOutcome(FrozenSchema):
    status: Literal["PASS", "REJECTED", "INDETERMINATE"]
    role: Literal["declaration-capture", "declaration"]
    attempt: Attempt
    failure: FailureRecord | None = None
    evaluation: Evaluation | None = None
    static_baseline: StaticBaseline | None = None
    runtime: RuntimeEvaluationRun | None = Field(default=None, exclude=True)
    failure_process: ProcessTerminalUnavailable | None = Field(
        default=None,
        exclude=True,
    )

    @model_validator(mode="after")
    def validate_check_cell_outcome(self) -> "CheckCellOutcome":
        if self.runtime is not None and self.runtime.evaluation != self.evaluation:
            raise ValueError("check runtime wrapper must match its evaluation")
        if self.failure_process is not None:
            if self.failure is None or self.runtime is not None:
                raise ValueError(
                    "check process sidecar requires one non-runtime FailureRecord"
                )
            authority = self.failure.authority
            if not (
                isinstance(self.failure_process, ProcessTerminalUnavailable)
                and isinstance(authority, StructuredFailureAuthority)
                and authority.detail.code == "terminal-unavailable"
            ):
                raise ValueError(
                    "check process sidecar must match terminal-unavailable authority"
                )
        if self.status == "PASS":
            if self.failure is not None:
                raise ValueError("passing check cell cannot carry a failure")
            if not isinstance(self.evaluation, PassEvaluation):
                raise ValueError("passing check cell requires a pass evaluation")
        else:
            if self.failure is None:
                raise ValueError("non-passing check cell requires a FailureRecord")
            if self.failure.disposition != self.status:
                raise ValueError("check cell status must match failure disposition")
        return self


class VerificationJournalEntry(FrozenSchema):
    package: str
    cell: Cell
    role: VerificationRole
    attempt: Attempt | None = None
    failure: FailureRecord

    @model_validator(mode="after")
    def validate_entry_identity(self) -> "VerificationJournalEntry":
        if self.package != self.cell.package:
            raise ValueError("journal entry package must match its cell")
        scope = self.failure.scope
        if isinstance(scope, AttemptFailureScope):
            if self.attempt != scope.attempt:
                raise ValueError("journal entry attempt must match its failure scope")
            if scope.attempt.identity.cell != self.cell:
                raise ValueError("journal entry cell must match its attempt")
        else:
            if self.attempt is not None:
                raise ValueError("cell-scoped journal entry cannot contain an attempt")
            if scope.cell != self.cell or scope.package != self.package:
                raise ValueError("journal entry cell must match its failure scope")
        return self


class VerificationPackagePolicy(FrozenSchema):
    package: str
    evaluation_policy_identity: str


class VerificationJournal(FrozenSchema):
    schema_version: Literal["verification-journal-v2"] = "verification-journal-v2"
    run_id: str
    command: Literal["smoke", "check", "search"]
    source_snapshot_digest: str
    package_policies: tuple[VerificationPackagePolicy, ...]
    entries: tuple[VerificationJournalEntry, ...]

    @property
    def packages(self) -> tuple[str, ...]:
        return tuple(item.package for item in self.package_policies)

    @model_validator(mode="after")
    def validate_package_policies(self) -> "VerificationJournal":
        packages = self.packages
        if not packages or packages != tuple(sorted(set(packages))):
            raise ValueError("journal package policies must be sorted and unique")
        policies = {
            item.package: item.evaluation_policy_identity
            for item in self.package_policies
        }
        for entry in self.entries:
            policy = policies.get(entry.package)
            if policy is None:
                raise ValueError("journal entry package has no policy identity")
            scope = entry.failure.scope
            if isinstance(scope, AttemptFailureScope):
                identity = scope.attempt.identity
                entry_policy = identity.evaluation_policy_identity
                snapshot_digest = identity.source_snapshot_digest
            else:
                entry_policy = scope.evaluation_policy_identity
                snapshot_digest = scope.source_snapshot_digest
            if entry_policy != policy:
                raise ValueError("journal entry policy identity does not match package")
            if snapshot_digest != self.source_snapshot_digest:
                raise ValueError("journal entry snapshot does not match its run")
        return self


class VerificationJournalV1(FrozenSchema):
    schema_version: Literal["verification-journal-v1"] = "verification-journal-v1"
    run_id: str
    command: Literal["smoke", "check", "search"]
    packages: tuple[str, ...]
    source_snapshot_digest: str
    evaluation_policy_identity: str
    entries: tuple[VerificationJournalEntry, ...]


VerificationJournalRecord = VerificationJournal | VerificationJournalV1


class HighestVersionPass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    attempt: Attempt
    baseline: StaticBaseline
    harness_baseline: HarnessBaseline
    evaluation: PassEvaluation

    @model_validator(mode="after")
    def validate_highest_evaluation(self) -> "HighestVersionPass":
        if self.attempt.identity.requested_resolution != "highest":
            raise ValueError("highest-version pass requires a highest Attempt")
        if self.harness_baseline.cell != self.attempt.identity.cell:
            raise ValueError("highest-version harness baseline must match its cell")
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
    evaluation: VerifierRejectedEvaluation | None = None
    runtime: RuntimeEvaluationRun | None = Field(default=None, exclude=True)

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
        if self.failure.cause == "VERIFIER_EXITED_NONZERO" and self.evaluation is None:
            raise ValueError("baseline verifier rejection requires its evaluation")
        if self.evaluation is not None:
            authority = self.failure.authority
            if not (
                self.failure.cause == "VERIFIER_EXITED_NONZERO"
                and self.failure.stage == "test"
                and isinstance(authority, ConfiguredVerifierFailureAuthority)
                and authority.terminal == self.evaluation.verifier.terminal
            ):
                raise ValueError(
                    "baseline verifier diagnosis must match its evaluation"
                )
        if self.evaluation is not None and (
            self.evaluation.proposal.attempt_id != self.attempt.attempt_id
        ):
            raise ValueError("baseline rejection evaluation must match its attempt")
        if self.evaluation is not None:
            if self.static_baseline is None:
                raise ValueError("baseline evaluation requires its static baseline")
            if self.evaluation.proposal != self.static_baseline.proposal:
                raise ValueError("baseline rejection must identify captured V_hi")
            if self.evaluation.static.ty != self.static_baseline.ty:
                raise ValueError("baseline rejection must reuse captured V_hi TyCheck")
            if self.evaluation.static.baseline_digest != self.static_baseline.digest:
                raise ValueError("baseline rejection must reuse captured V_hi digest")
        if self.runtime is not None and self.runtime.evaluation != self.evaluation:
            raise ValueError("baseline runtime wrapper must match its evaluation")
        return self


class BaselineIndeterminate(FrozenSchema):
    status: Literal["BASELINE_INDETERMINATE"] = "BASELINE_INDETERMINATE"
    attempt: Attempt
    failure: FailureRecord
    static_baseline: StaticBaseline | None = None
    evaluation: IndeterminateEvaluation | None = None
    runtime: RuntimeEvaluationRun | None = Field(default=None, exclude=True)
    failure_process: ProcessTerminalUnavailable | None = Field(
        default=None,
        exclude=True,
    )

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
        if self.failure_process is not None:
            authority = self.failure.authority
            if not (
                self.runtime is None
                and isinstance(self.failure_process, ProcessTerminalUnavailable)
                and isinstance(authority, StructuredFailureAuthority)
                and authority.detail.code == "terminal-unavailable"
            ):
                raise ValueError(
                    "baseline process sidecar must match terminal-unavailable authority"
                )
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
        if self.evaluation is not None:
            if self.evaluation.verifier is not None:
                authority = self.failure.authority
                matches = (
                    isinstance(authority, ConfiguredVerifierFailureAuthority)
                    and authority.terminal == self.evaluation.verifier.terminal
                    and self.failure.stage == "test"
                    and self.failure.cause == self.evaluation.cause
                )
            else:
                assert self.evaluation.failure is not None
                matches = (
                    self.failure.cause == self.evaluation.cause
                    and self.failure.stage == self.evaluation.failure.stage
                    and process_facts_match(
                        self.failure.process,
                        self.evaluation.failure.process,
                    )
                )
            if not matches:
                raise ValueError(
                    "baseline indeterminate diagnosis must match its evaluation"
                )
        if self.runtime is not None and self.runtime.evaluation != self.evaluation:
            raise ValueError("baseline runtime wrapper must match its evaluation")
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


class SearchProbeRequest(FrozenSchema):
    """One exact active-coordinate probe and its unresolved candidate window."""

    vector: tuple[VersionPin, ...]
    active_dependency: str
    candidate_version: str
    lower_version: str
    upper_version: str
    candidate_count: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def validate_window(self) -> "SearchProbeRequest":
        versions = {pin.name: pin.version for pin in self.vector}
        if len(versions) != len(self.vector):
            raise ValueError("search probe vector dependencies must be unique")
        if versions.get(self.active_dependency) != self.candidate_version:
            raise ValueError("search probe candidate must match its active coordinate")
        if not (
            Version(self.lower_version)
            <= Version(self.candidate_version)
            <= Version(self.upper_version)
        ):
            raise ValueError("search probe candidate must be inside its window")
        return self


class BaselineDetailIdentity(FrozenSchema):
    kind: Literal["baseline"] = "baseline"


class DeclarationDetailIdentity(FrozenSchema):
    kind: Literal["declaration"] = "declaration"


class SearchProbeDetailIdentity(FrozenSchema):
    kind: Literal["search-probe"] = "search-probe"
    dependency: str
    version: str
    lower_version: str
    upper_version: str
    candidate_count: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def validate_window(self) -> "SearchProbeDetailIdentity":
        if not self.dependency or not self.version:
            raise ValueError("search probe detail identity cannot be empty")
        if not (
            Version(self.lower_version)
            <= Version(self.version)
            <= Version(self.upper_version)
        ):
            raise ValueError("search probe detail version must be inside its window")
        return self


CellDetailIdentity = Annotated[
    Union[
        BaselineDetailIdentity,
        DeclarationDetailIdentity,
        SearchProbeDetailIdentity,
    ],
    Field(discriminator="kind"),
]


class CellContextEvent(FrozenSchema):
    kind: Literal["context"] = "context"
    cell: Cell
    detail: CellDetailIdentity | None


class CellSearchProgressEvent(FrozenSchema):
    kind: Literal["search-progress"] = "search-progress"
    cell: Cell
    packages: tuple[VersionPin, ...]
    completed_packages: tuple[VersionPin, ...]

    @model_validator(mode="after")
    def validate_completed_packages(self) -> "CellSearchProgressEvent":
        package_names = tuple(pin.name for pin in self.packages)
        completed_names = tuple(pin.name for pin in self.completed_packages)
        if len(set(package_names)) != len(package_names):
            raise ValueError("search vector packages must be unique")
        if self.completed_packages != self.packages[: len(completed_names)]:
            raise ValueError(
                "completed search packages must be a current vector prefix"
            )
        return self


class StageProgress(FrozenSchema):
    completed: int = Field(ge=0, strict=True)
    total: int = Field(ge=0, strict=True)
    unit: Literal["tests"]

    @model_validator(mode="after")
    def validate_completed(self) -> "StageProgress":
        if self.completed > self.total:
            raise ValueError("stage progress cannot exceed its total")
        return self


class CellStageEvent(FrozenSchema):
    kind: Literal["stage"] = "stage"
    cell: Cell
    stage: str
    progress: StageProgress | None = None


class StaticIssueDetail(FrozenSchema):
    kind: Literal["static-issue"] = "static-issue"
    first: TyDiagnostic
    total: int = Field(gt=0, strict=True)


CellResultDetail = Annotated[
    Union[PytestFailureDetail, StaticIssueDetail],
    Field(discriminator="kind"),
]


class CellSucceeded(FrozenSchema):
    kind: Literal["succeeded"] = "succeeded"
    status: str
    phase: str


class CellFailed(FrozenSchema):
    kind: Literal["failed"] = "failed"
    status: str
    phase: str
    detail: CellResultDetail | None = Field(default=None, exclude=True)
    detail_failure_id: str | None = Field(default=None, exclude=True)
    process: ProcessObservation | None = Field(default=None, exclude=True)
    process_failure_id: str | None = Field(default=None, exclude=True)
    failures: tuple[FailureRecord, ...] = ()
    verification_role: VerificationRole | None = None

    @model_validator(mode="after")
    def validate_detail_source(self) -> "CellFailed":
        if self.process is None and self.process_failure_id is not None:
            raise ValueError(
                "cell process observation and failure source must be retained together"
            )
        if (
            self.process is not None
            and self.failures
            and self.process_failure_id is None
        ):
            raise ValueError(
                "retained cell process requires an explicit failure source"
            )
        if self.process_failure_id is not None and not any(
            failure.failure_id == self.process_failure_id for failure in self.failures
        ):
            raise ValueError("cell process source must reference a retained failure")
        if self.detail is not None and self.failures and self.detail_failure_id is None:
            raise ValueError("retained cell detail requires an explicit failure source")
        if self.detail_failure_id is None:
            return self
        if self.detail is None:
            raise ValueError("cell detail source requires structured detail")
        if not any(
            failure.failure_id == self.detail_failure_id for failure in self.failures
        ):
            raise ValueError("cell detail source must name one retained failure")
        return self


CellCompletionOutcome = Annotated[
    Union[CellSucceeded, CellFailed],
    Field(discriminator="kind"),
]


class CellCompletedEvent(FrozenSchema):
    kind: Literal["completed"] = "completed"
    cell: Cell
    completed: int
    total: int
    outcome: CellCompletionOutcome
    diagnose_available: bool = False

    @model_validator(mode="after")
    def validate_progress(self) -> "CellCompletedEvent":
        if self.total <= 0 or self.completed <= 0 or self.completed > self.total:
            raise ValueError(
                "cell completion counters must satisfy 0 < completed <= total"
            )
        return self


class StatusEvent(FrozenSchema):
    message: str
    package: str | None = None
    completed: int = 0
    total: int | None = None


class CellMatrixEvent(FrozenSchema):
    cells: tuple[Cell, ...]
    active_packages: int = 0
    pinned_packages: int = 0

    @model_validator(mode="after")
    def validate_package_counts(self) -> "CellMatrixEvent":
        if (
            self.active_packages < 0
            or not 0 <= self.pinned_packages <= self.active_packages
        ):
            raise ValueError(
                "cell matrix package counts must satisfy 0 <= pinned <= active"
            )
        return self


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
        RuntimeInterfaceMissingEvaluation
        | VerifierRejectedEvaluation
        | IndeterminateEvaluation
        | None
    ) = None
    runtime: RuntimeEvaluationRun | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_cell(self) -> "SearchFailureEvent":
        if self.runtime is not None and self.runtime.evaluation != self.evaluation:
            raise ValueError("search runtime wrapper must match its evaluation")
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
            if isinstance(self.evaluation, VerifierRejectedEvaluation):
                authority = self.failure.authority
                if not (
                    self.failure.cause == "VERIFIER_EXITED_NONZERO"
                    and self.failure.stage == "test"
                    and isinstance(authority, ConfiguredVerifierFailureAuthority)
                    and authority.terminal == self.evaluation.verifier.terminal
                ):
                    raise ValueError(
                        "search test evaluation must match its failure facts"
                    )
            elif isinstance(self.evaluation, RuntimeInterfaceMissingEvaluation):
                witness = self.evaluation.witnesses[-1].outcome
                if not isinstance(witness, RuntimeWitnessResult):
                    raise ValueError(
                        "search runtime evaluation requires a terminal witness"
                    )
                if (
                    self.failure.cause != "RUNTIME_INTERFACE_MISSING"
                    or self.failure.stage != "witness"
                    or self.failure.process != witness.process
                ):
                    raise ValueError(
                        "search runtime evaluation must match its failure facts"
                    )
            elif self.evaluation.verifier is not None:
                authority = self.failure.authority
                if not (
                    isinstance(authority, ConfiguredVerifierFailureAuthority)
                    and authority.terminal == self.evaluation.verifier.terminal
                    and self.failure.stage == "test"
                    and self.failure.cause == self.evaluation.cause
                ):
                    raise ValueError(
                        "search verifier indeterminate must match its failure facts"
                    )
            else:
                assert self.evaluation.failure is not None
                if (
                    self.failure.cause != self.evaluation.failure.cause
                    or self.failure.stage != self.evaluation.failure.stage
                    or self.failure.process != self.evaluation.failure.process
                ):
                    raise ValueError(
                        "search indeterminate evaluation must match its failure facts"
                    )
        return self


ActivityEvent = (
    CellContextEvent
    | CellSearchProgressEvent
    | CellStageEvent
    | CellCompletedEvent
    | StatusEvent
    | CellMatrixEvent
    | ProcessEvent
    | SearchFailureEvent
)

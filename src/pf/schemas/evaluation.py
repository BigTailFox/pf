from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Annotated, Literal, Union

from packaging.version import Version
from pydantic import Field, model_validator, model_serializer

from pf.schemas.base import FrozenSchema
from pf.schemas.project import (
    Cell,
    HarnessBaseline,
    InterpreterIdentity,
    Proposal,
    ResolvedNode,
    VersionPin,
)


class EnvironmentVariable(FrozenSchema):
    name: str
    value: str
    sensitive: bool = True


class ProcessSpec(FrozenSchema):
    argv: tuple[str, ...]
    cwd: str
    environment: tuple[EnvironmentVariable, ...] = ()
    environment_removals: tuple[str, ...] = ()
    environment_mode: Literal["inherited", "explicit"] = "inherited"
    timeout_seconds: int | float | None
    start_new_session: bool = True
    redaction_policy_identity: str = "pf-default-v1"
    summary_limit: int | None = None

    @model_validator(mode="after")
    def validate_process_spec(self) -> "ProcessSpec":
        if not self.argv:
            raise ValueError("process argv cannot be empty")
        if self.environment_mode == "explicit":
            if self.environment_removals:
                raise ValueError("explicit process environment cannot have removals")
            names = [item.name for item in self.environment]
            if len(names) != len(set(names)):
                raise ValueError("explicit process environment names must be unique")
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
    exit_code: int = Field(ge=0, strict=True)


class StartFailed(FrozenSchema):
    kind: Literal["start-failed"] = "start-failed"


class TimedOut(FrozenSchema):
    kind: Literal["timed-out"] = "timed-out"


class Signaled(FrozenSchema):
    kind: Literal["signaled"] = "signaled"
    signal: int = Field(gt=0, strict=True)


class Unavailable(FrozenSchema):
    kind: Literal["unavailable"] = "unavailable"


ExecutionTerminal = Annotated[
    Union[NormalExit, StartFailed, TimedOut, Signaled, Unavailable],
    Field(discriminator="kind"),
]


def execution_terminal(process: ProcessObservation) -> ExecutionTerminal:
    """Extract only process facts; timeout precedes its cleanup terminal."""
    if isinstance(process, ProcessTerminalUnavailable):
        return Unavailable()
    if process.timed_out:
        return TimedOut()
    if process.start_error is not None:
        return StartFailed()
    if process.signal is not None:
        return Signaled(signal=process.signal)
    if process.exit_code is not None:
        return NormalExit(exit_code=process.exit_code)
    raise ValueError("process observation has no terminal")


ResolutionStage = Literal["resolve-project", "resolve-environment"]
InstallationStage = Literal["install-project", "install-environment"]
OperationStage = Literal[
    "resolve-project", "resolve-environment", "install-project", "install-environment",
    "create-environment", "inspect-interpreter", "inspect",
    "inspect-project-plan", "inspect-environment-plan", "proposal-vector",
]
RESOLUTION_STAGES = frozenset({"resolve-project", "resolve-environment"})
INSTALLATION_STAGES = frozenset({"install-project", "install-environment"})
AUXILIARY_STAGES = frozenset({"create-environment", "inspect-interpreter", "inspect"})
CHECK_STAGES = frozenset({"inspect-project-plan", "inspect-environment-plan", "proposal-vector"})
OPERATION_STAGES = RESOLUTION_STAGES | INSTALLATION_STAGES | AUXILIARY_STAGES | CHECK_STAGES
PlanDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class OperationRequestBinding(FrozenSchema):
    attempt_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    stage: ResolutionStage | InstallationStage
    project_plan_digest: PlanDigest | None = Field(json_schema_extra={"x-pf-preserve-null": True})
    environment_plan_digest: PlanDigest | None = Field(json_schema_extra={"x-pf-preserve-null": True})

    @model_serializer(mode="wrap")
    def serialize_required_nulls(self, handler):
        result = handler(self)
        result["project_plan_digest"] = self.project_plan_digest
        result["environment_plan_digest"] = self.environment_plan_digest
        return result

    @model_validator(mode="after")
    def validate_plan_timing(self) -> "OperationRequestBinding":
        if (self.project_plan_digest is not None) != (self.stage != "resolve-project"):
            raise ValueError("operation binding project plan does not match stage")
        if (self.environment_plan_digest is not None) != (self.stage == "install-environment"):
            raise ValueError("operation binding environment plan does not match stage")
        return self


class Unattributed(FrozenSchema):
    kind: Literal["unattributed"] = "unattributed"


class _CompleteContradictionFacts(FrozenSchema):
    stdout_complete: Literal[True]
    stderr_complete: Literal[True]

    @model_validator(mode="before")
    @classmethod
    def validate_strict_completeness(cls, value: object) -> object:
        if isinstance(value, dict) and any(
            value.get(key) is not True for key in ("stdout_complete", "stderr_complete")
        ):
            raise ValueError("UNSAT completeness must be JSON boolean true")
        return value


class DirectContradictionFacts(_CompleteContradictionFacts):
    code: Literal["direct-version-contradiction"] = "direct-version-contradiction"


class TransitiveContradictionFacts(_CompleteContradictionFacts):
    code: Literal["transitive-version-contradiction"] = "transitive-version-contradiction"


class UvUnsatAttribution(FrozenSchema):
    kind: Literal["uv-unsat"] = "uv-unsat"
    tool: Literal["uv"]
    tool_version: Literal["0.12.5"]
    protocol: Literal["uv-pip-compile-pylock-v1"]
    profile: Literal["uv-diagnostics-0.12.5-v1"]
    request_binding: OperationRequestBinding
    facts: Annotated[
        Union[DirectContradictionFacts, TransitiveContradictionFacts],
        Field(discriminator="code"),
    ]


ExecutionAttribution = Annotated[Union[Unattributed, UvUnsatAttribution], Field(discriminator="kind")]


class RequestInvariantFact(FrozenSchema):
    code: Literal["request-invariant"] = "request-invariant"


class EvidenceConflictFact(FrozenSchema):
    code: Literal["evidence-conflict"] = "evidence-conflict"


class SourceAccessFailedFact(FrozenSchema):
    code: Literal["source-access-failed"] = "source-access-failed"


class EnvironmentAccessFailedFact(FrozenSchema):
    code: Literal["environment-access-failed"] = "environment-access-failed"


class ArtifactInvalidFact(FrozenSchema):
    code: Literal["artifact-invalid"] = "artifact-invalid"


class ResolutionOutputIncompleteFact(FrozenSchema):
    code: Literal["resolution-output-incomplete"] = "resolution-output-incomplete"


class ResolutionPlanInvalidFact(FrozenSchema):
    code: Literal["resolution-plan-invalid"] = "resolution-plan-invalid"


class ArtifactPolicyMismatchFact(FrozenSchema):
    code: Literal["artifact-policy-mismatch"] = "artifact-policy-mismatch"


class ManagedSourceLeakageFact(FrozenSchema):
    code: Literal["managed-source-leakage"] = "managed-source-leakage"


class ManagedSourceMismatchFact(FrozenSchema):
    code: Literal["managed-source-mismatch"] = "managed-source-mismatch"


class InterpreterObservationInvalidFact(FrozenSchema):
    code: Literal["interpreter-observation-invalid"] = "interpreter-observation-invalid"


class InterpreterMismatchFact(FrozenSchema):
    code: Literal["interpreter-mismatch"] = "interpreter-mismatch"


class GraphObservationInvalidFact(FrozenSchema):
    code: Literal["graph-observation-invalid"] = "graph-observation-invalid"


class InstalledGraphMismatchFact(FrozenSchema):
    code: Literal["installed-graph-mismatch"] = "installed-graph-mismatch"


class ProposalVectorMismatchFact(FrozenSchema):
    code: Literal["proposal-vector-mismatch"] = "proposal-vector-mismatch"


StructuredOperationFact = Annotated[
    Union[
        RequestInvariantFact, EvidenceConflictFact, SourceAccessFailedFact,
        EnvironmentAccessFailedFact, ArtifactInvalidFact, ResolutionOutputIncompleteFact,
        ResolutionPlanInvalidFact, ArtifactPolicyMismatchFact, ManagedSourceLeakageFact,
        ManagedSourceMismatchFact, InterpreterObservationInvalidFact, InterpreterMismatchFact,
        GraphObservationInvalidFact, InstalledGraphMismatchFact, ProposalVectorMismatchFact,
    ],
    Field(discriminator="code"),
]


class ExecutionFailure(FrozenSchema):
    kind: Literal["execution"] = "execution"
    terminal: ExecutionTerminal
    attribution: ExecutionAttribution

    @model_validator(mode="after")
    def validate_terminal_attribution(self) -> "ExecutionFailure":
        if isinstance(self.terminal, NormalExit) and self.terminal.exit_code == 0:
            raise ValueError("normal exit zero cannot form an execution failure")
        if isinstance(self.attribution, UvUnsatAttribution) and (
            not isinstance(self.terminal, NormalExit)
            or self.terminal.exit_code != 1
            or self.attribution.request_binding.stage not in RESOLUTION_STAGES
        ):
            raise ValueError("UNSAT requires a resolution normal exit one")
        return self


class StructuredOperationFailure(FrozenSchema):
    kind: Literal["operation-structured"] = "operation-structured"
    fact: StructuredOperationFact
    terminal: ExecutionTerminal | None = Field(json_schema_extra={"x-pf-preserve-null": True})

    @model_serializer(mode="wrap")
    def serialize_required_null(self, handler):
        result = handler(self)
        if self.terminal is None:
            result["terminal"] = None
        return result


OperationFailure = Annotated[
    Union[ExecutionFailure, StructuredOperationFailure], Field(discriminator="kind")
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
    failed_case_nodeids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_request(self) -> "VerifierRequest":
        if not self.command:
            raise ValueError("verifier command cannot be empty")
        return self


class VerifierRun(FrozenSchema):
    authoritative: VerifierOutcome
    diagnostics: VerifierDiagnostics | None = Field(default=None, exclude=True)
    failed_case_additions: tuple[str, ...] = Field(default=(), exclude=True)


def process_facts_match(
    left: ProcessObservation | None,
    right: ProcessObservation | None,
) -> bool:
    """Return whether two results have the same report-portable process facts."""
    if left is None or right is None:
        return left is right
    return left.model_dump(mode="json") == right.model_dump(mode="json")


FailureCause = Literal[
    "RESOLUTION_FAILED",
    "INSTALLATION_FAILED",
    "RESOLUTION_CONFLICT",
    "HARNESS_CONFLICT",
    "VERIFIER_EXITED_NONZERO",
    "SOURCE_FAILURE",
    "ENVIRONMENT_FAILURE",
    "TOOL_FAILURE",
    "TIMEOUT",
    "INTERNAL_INVARIANT",
    "NONDETERMINISTIC",
]


def classify_execution_terminal(
    stage: str, terminal: ExecutionTerminal, attribution: ExecutionAttribution,
) -> tuple[Literal["PASS", "REJECTED", "INDETERMINATE"], FailureCause | None]:
    """Classify an admitted operation terminal, never a diagnostic message.

    PASS means only terminal success. The caller still owes every required
    success artifact, inspection and complete-verifier check.
    """
    if stage not in RESOLUTION_STAGES | INSTALLATION_STAGES | AUXILIARY_STAGES | {"test"}:
        raise ValueError("stage does not admit execution terminals")
    if isinstance(attribution, UvUnsatAttribution):
        if (
            stage not in RESOLUTION_STAGES
            or attribution.request_binding.stage != stage
            or not isinstance(terminal, NormalExit)
            or terminal.exit_code != 1
        ):
            raise ValueError("UNSAT attribution requires its bound resolution exit one")
        return "REJECTED", (
            "RESOLUTION_CONFLICT" if stage == "resolve-project" else "HARNESS_CONFLICT"
        )
    if isinstance(terminal, TimedOut):
        return "INDETERMINATE", "TIMEOUT"
    if not isinstance(terminal, NormalExit):
        return "INDETERMINATE", "TOOL_FAILURE"
    if terminal.exit_code == 0:
        return "PASS", None
    if stage in RESOLUTION_STAGES:
        return "REJECTED", "RESOLUTION_FAILED"
    if stage in INSTALLATION_STAGES:
        return "REJECTED", "INSTALLATION_FAILED"
    if stage == "test":
        return "REJECTED", "VERIFIER_EXITED_NONZERO"
    return "INDETERMINATE", "TOOL_FAILURE"


# The order is the priority for independently observed structured facts.
# Terminal modes: any = this operation's observation or null, zero = normal 0,
# absent = no process belonging to this operation.
STRUCTURED_OPERATION_RULES: dict[
    str, tuple[frozenset[str], Literal["any", "zero", "absent"], FailureCause]
] = {
    "request-invariant": (OPERATION_STAGES, "any", "INTERNAL_INVARIANT"),
    "evidence-conflict": (OPERATION_STAGES, "any", "INTERNAL_INVARIANT"),
    "source-access-failed": (RESOLUTION_STAGES | INSTALLATION_STAGES, "any", "SOURCE_FAILURE"),
    "environment-access-failed": (RESOLUTION_STAGES | INSTALLATION_STAGES | AUXILIARY_STAGES, "any", "ENVIRONMENT_FAILURE"),
    "artifact-invalid": (RESOLUTION_STAGES | INSTALLATION_STAGES, "any", "SOURCE_FAILURE"),
    "resolution-output-incomplete": (RESOLUTION_STAGES, "zero", "TOOL_FAILURE"),
    "resolution-plan-invalid": (RESOLUTION_STAGES, "zero", "TOOL_FAILURE"),
    "artifact-policy-mismatch": (RESOLUTION_STAGES, "zero", "INTERNAL_INVARIANT"),
    "managed-source-leakage": (frozenset({"resolve-project"}), "zero", "INTERNAL_INVARIANT"),
    "managed-source-mismatch": (RESOLUTION_STAGES, "zero", "INTERNAL_INVARIANT"),
    "interpreter-observation-invalid": (frozenset({"inspect-interpreter"}), "zero", "TOOL_FAILURE"),
    "interpreter-mismatch": (frozenset({"inspect-interpreter"}), "zero", "ENVIRONMENT_FAILURE"),
    "graph-observation-invalid": (frozenset({"inspect"}), "zero", "TOOL_FAILURE"),
    "installed-graph-mismatch": (frozenset({"inspect-project-plan", "inspect-environment-plan"}), "absent", "INTERNAL_INVARIANT"),
    "proposal-vector-mismatch": (frozenset({"proposal-vector"}), "absent", "INTERNAL_INVARIANT"),
}


def classify_operation_failure(
    stage: str, failure: OperationFailure,
) -> tuple[Literal["REJECTED", "INDETERMINATE"], FailureCause]:
    """The same closed rules serve production projection and offline reading."""
    if isinstance(failure, ExecutionFailure):
        disposition, cause = classify_execution_terminal(stage, failure.terminal, failure.attribution)
        if disposition == "PASS" or cause is None or stage == "test":
            raise ValueError("operation execution failure requires a failed prepare terminal")
        return disposition, cause
    stages, mode, cause = STRUCTURED_OPERATION_RULES[failure.fact.code]
    if stage not in stages:
        raise ValueError("structured operation fact is not admitted at this stage")
    if mode == "absent" and failure.terminal is not None:
        raise ValueError("structured check cannot borrow another operation's terminal")
    if mode == "zero" and not (
        isinstance(failure.terminal, NormalExit) and failure.terminal.exit_code == 0
    ):
        raise ValueError("success inspection fact requires normal exit zero")
    return "INDETERMINATE", cause


def configured_verifier_outcome(terminal: ExecutionTerminal) -> VerifierOutcome:
    disposition, _ = classify_execution_terminal("test", terminal, Unattributed())
    if isinstance(terminal, NormalExit):
        return (
            VerifierPass(terminal=terminal)
            if disposition == "PASS" else VerifierRejected(terminal=terminal)
        )
    reason: Literal[
        "process-start-failed", "process-timed-out", "process-signaled", "terminal-unavailable"
    ]
    if isinstance(terminal, TimedOut):
        reason = "process-timed-out"
    elif isinstance(terminal, Signaled):
        reason = "process-signaled"
    elif isinstance(terminal, StartFailed):
        reason = "process-start-failed"
    else:
        reason = "terminal-unavailable"
    return VerifierIndeterminate(terminal=terminal, reason=reason)


class AttemptIdentity(FrozenSchema):
    identity_version: Literal["attempt-v1"] = "attempt-v1"
    source_snapshot_digest: str
    cell: Cell
    requested_resolution: Literal["highest", "lowest-direct", "exact-vector"]
    requested_managed_vector: tuple[VersionPin, ...] | None
    active_declaration_ids: tuple[str, ...]
    source_plan_identity: str
    execution_policy_identity: str
    resolution_context_digest: str
    harness_policy_identity: Literal[
        "original-harness-v1", "harness-relaxation-v1"
    ]
    harness_declaration_ids: tuple[str, ...] = ()
    harness_baseline_digest: str | None = None
    selected_candidate_evidence_digest: str | None = None

    @model_validator(mode="after")
    def validate_requested_resolution(self) -> "AttemptIdentity":
        if not self.source_snapshot_digest:
            raise ValueError("attempt source snapshot digest cannot be empty")
        if not self.source_plan_identity or not self.execution_policy_identity:
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
        if not self.resolution_context_digest:
            raise ValueError("attempt requires a resolution context")
        if self.harness_declaration_ids != tuple(
            sorted(set(self.harness_declaration_ids))
        ):
            raise ValueError("attempt harness declarations must be sorted and unique")
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
                raise ValueError("only exact attempts carry selected candidate evidence")
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


def validate_operation_binding(
    *,
    attempt: Attempt,
    stage: str,
    project_plan_digest: str | None,
    environment_plan_digest: str | None,
    attribution: ExecutionAttribution,
) -> None:
    """Validate portable operation scope and only fully admitted plan digests.

    ResolutionContext remains opaque offline. Its nonempty digest and the
    Attempt identity are validated by Attempt; runtime context equality belongs
    to the adapter/factory, which actually holds the context preimage.
    """
    if stage not in OPERATION_STAGES:
        raise ValueError("unknown prepare operation stage")
    for digest in (project_plan_digest, environment_plan_digest):
        if digest is not None and (
            len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("operation plan digest must be canonical SHA-256")
    harness = bool(attempt.identity.harness_declaration_ids)
    if stage in {"resolve-environment", "install-environment", "inspect-environment-plan"} and not harness:
        raise ValueError("environment operation requires active external harness")
    if stage in {"install-project", "inspect-project-plan"} and harness:
        raise ValueError("project-only operation requires empty external harness")
    needs_project = stage not in {"create-environment", "inspect-interpreter", "resolve-project"}
    needs_environment = stage == "install-environment" or (
        harness and stage in CHECK_STAGES | {"inspect"}
    )
    if (project_plan_digest is not None) != needs_project:
        raise ValueError("operation project plan timing does not match its stage")
    if (environment_plan_digest is not None) != needs_environment:
        raise ValueError("operation environment plan timing does not match its stage")
    if isinstance(attribution, UvUnsatAttribution):
        binding = attribution.request_binding
        if (
            binding.attempt_id != attempt.attempt_id
            or binding.stage != stage
            or binding.project_plan_digest != project_plan_digest
            or binding.environment_plan_digest != environment_plan_digest
        ):
            raise ValueError("operation attribution does not match its Attempt binding")


class AttemptFailureScope(FrozenSchema):
    kind: Literal["attempt"] = "attempt"
    attempt: Attempt


class CellFailureScope(FrozenSchema):
    kind: Literal["cell"] = "cell"
    package: str
    cell: Cell
    source_snapshot_digest: str
    execution_policy_identity: str

    @model_validator(mode="after")
    def validate_cell_scope(self) -> "CellFailureScope":
        if self.package != self.cell.package:
            raise ValueError("cell failure package must match its cell")
        if not self.source_snapshot_digest or not self.execution_policy_identity:
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
    terminal: ExecutionTerminal


class StructuredFailureAuthority(FrozenSchema):
    kind: Literal["structured"] = "structured"
    detail: FailureDetail
    summary_code: str | None = None


class ExecutionFailureAuthority(ExecutionFailure):
    """Portable execution facts adopted by FailurePolicy."""


class StructuredOperationFailureAuthority(StructuredOperationFailure):
    """Portable PF-observed operation facts adopted by FailurePolicy."""


FailureAuthority = Annotated[
    Union[
        ProcessFailureAuthority,
        ConfiguredVerifierFailureAuthority,
        StructuredFailureAuthority,
        ExecutionFailureAuthority,
        StructuredOperationFailureAuthority,
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
        terminal: ExecutionTerminal,
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
            "failure-" + hashlib.sha256(b"pf:failure:v3\0" + canonical).hexdigest()[:16]
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
        if self.stage == "ty":
            raise ValueError("static collection cannot create a dynamic FailureRecord")
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
        if isinstance(self.authority, (ExecutionFailureAuthority, StructuredOperationFailureAuthority)):
            if not isinstance(self.scope, AttemptFailureScope):
                raise ValueError("operation authority requires an Attempt")
            validate_operation_binding(
                attempt=self.scope.attempt, stage=self.stage,
                project_plan_digest=self.project_plan_digest,
                environment_plan_digest=self.environment_plan_digest,
                attribution=(self.authority.attribution if isinstance(self.authority, ExecutionFailureAuthority) else Unattributed()),
            )
            if (self.disposition, self.cause) != classify_operation_failure(self.stage, self.authority):
                raise ValueError("operation disposition and cause do not match its facts")
            return self
        if self.stage in OPERATION_STAGES:
            raise ValueError("prepare operation requires operation authority")
        if self.stage == "test" and not isinstance(self.authority, ConfiguredVerifierFailureAuthority):
            raise ValueError("test failure requires configured-verifier authority")
        if self.disposition == "REJECTED":
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
            else:
                raise ValueError("REJECTED disposition is not supported by its facts")
        if isinstance(self.authority, ConfiguredVerifierFailureAuthority):
            terminal = self.authority.terminal
            if not isinstance(self.scope, AttemptFailureScope):
                raise ValueError("configured verifier failure requires an Attempt")
            expected = classify_execution_terminal("test", terminal, Unattributed())
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

    @model_validator(mode="after")
    def validate_success_terminal(self) -> "ToolSuccess":
        if execution_terminal(self.process) != NormalExit(exit_code=0):
            raise ValueError("tool success requires a successful process")
        return self


class ToolFailure(FrozenSchema):
    status: Literal["FAILURE"] = "FAILURE"
    cause: FailureCause
    stage: str
    process: ProcessObservation | None = Field(json_schema_extra={"x-pf-preserve-null": True})
    summary_code: str | None = None
    detail: FailureDetail | None = None

    @model_validator(mode="after")
    def validate_authority(self) -> "ToolFailure":
        if self.process is None and self.detail is None:
            raise ValueError("tool failure requires process or structured detail")
        return self


    @model_serializer(mode="wrap")
    def preserve_process_availability(self, handler):
        result = handler(self)
        if self.process is None:
            result["process"] = None
        return result


class PrepareFailure(FrozenSchema):
    attempt: Attempt
    stage: OperationStage
    failure: OperationFailure
    project_plan_digest: PlanDigest | None
    environment_plan_digest: PlanDigest | None
    process: ProcessObservation | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="after")
    def validate_plan_evidence(self) -> "PrepareFailure":
        classify_operation_failure(self.stage, self.failure)
        validate_operation_binding(
            attempt=self.attempt, stage=self.stage,
            project_plan_digest=self.project_plan_digest,
            environment_plan_digest=self.environment_plan_digest,
            attribution=self.failure.attribution if isinstance(self.failure, ExecutionFailure) else Unattributed(),
        )
        return self


class OperationFailureResult(FrozenSchema):
    status: Literal["OPERATION_FAILURE"] = "OPERATION_FAILURE"
    stage: OperationStage
    failure: OperationFailure
    process: ProcessObservation | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="after")
    def validate_operation(self) -> "OperationFailureResult":
        classify_operation_failure(self.stage, self.failure)
        return self


AuxiliaryOutcome = Annotated[
    Union[ToolSuccess, OperationFailureResult], Field(discriminator="status")
]


ToolOutcome = Annotated[
    Union[ToolSuccess, ToolFailure],
    Field(discriminator="status"),
]


class TyDiagnostic(FrozenSchema):
    identity: str
    origin: Literal["snapshot", "external"]
    path: str
    line: int | None = Field(json_schema_extra={"x-pf-preserve-null": True})
    column: int | None = Field(json_schema_extra={"x-pf-preserve-null": True})
    code: str
    severity: str
    message: str

    @model_serializer(mode="wrap")
    def serialize_required_nulls(self, handler):
        result = handler(self)
        for name in ("line", "column"):
            if getattr(self, name) is None:
                result[name] = None
        return result

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




class InterpreterSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    process: ProcessResult
    interpreter: InterpreterIdentity

    @model_validator(mode="after")
    def validate_success_terminal(self) -> "InterpreterSuccess":
        if execution_terminal(self.process) != NormalExit(exit_code=0):
            raise ValueError("interpreter success requires a successful process")
        return self


InterpreterOutcome = Annotated[
    Union[InterpreterSuccess, OperationFailureResult],
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

    @model_validator(mode="after")
    def validate_success_terminal(self) -> "GraphSuccess":
        if execution_terminal(self.process) != NormalExit(exit_code=0):
            raise ValueError("graph success requires a successful process")
        return self


GraphOutcome = Annotated[
    Union[GraphSuccess, OperationFailureResult],
    Field(discriminator="status"),
]






























class PassEvaluation(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    proposal: "Proposal"
    verifier: VerifierPass

class VerifierRejectedEvaluation(FrozenSchema):
    status: Literal["VERIFIER_REJECTED"] = "VERIFIER_REJECTED"
    proposal: "Proposal"
    verifier: VerifierRejected

class IndeterminateEvaluation(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    proposal: "Proposal"
    cause: Literal["TIMEOUT", "TOOL_FAILURE"]
    verifier: VerifierIndeterminate

    @model_validator(mode="after")
    def validate_failure_cause(self) -> "IndeterminateEvaluation":
        expected = "TIMEOUT" if isinstance(self.verifier.terminal, TimedOut) else "TOOL_FAILURE"
        if self.cause != expected:
            raise ValueError("verifier indeterminate cause must match its terminal")
        return self


Evaluation = Annotated[
    Union[
        PassEvaluation,
        VerifierRejectedEvaluation,
        IndeterminateEvaluation,
    ],
    Field(discriminator="status"),
]


class RuntimeEvaluationRun(FrozenSchema):
    evaluation: Evaluation
    diagnostics: VerifierDiagnostics | None = Field(default=None, exclude=True)
    failed_case_additions: tuple[str, ...] = Field(default=(), exclude=True)



def runtime_process_observation(
    runtime: RuntimeEvaluationRun,
) -> ProcessObservation | None:
    if runtime.diagnostics is not None:
        return runtime.diagnostics.process
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
    process: ProcessObservation = Field(exclude=True)

    @property
    def process_observation(self) -> ProcessObservation:
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
    "harness-prepare",
    "declaration",
    "probe",
]


def failure_process_matches(record: FailureRecord, process: ProcessObservation) -> bool:
    authority = record.authority
    if isinstance(authority, (ExecutionFailureAuthority, StructuredOperationFailureAuthority)):
        return authority.terminal == execution_terminal(process)
    return (
        isinstance(process, ProcessTerminalUnavailable)
        and isinstance(authority, StructuredFailureAuthority)
        and authority.detail.code == "terminal-unavailable"
    )


class CheckCellOutcome(FrozenSchema):
    status: Literal["PASS", "REJECTED", "INDETERMINATE"]
    role: Literal["harness-prepare", "declaration"]
    attempt: Attempt
    failure: FailureRecord | None = None
    evaluation: Evaluation | None = None
    runtime: RuntimeEvaluationRun | None = Field(default=None, exclude=True)
    failure_process: ProcessObservation | None = Field(
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
            if not failure_process_matches(self.failure, self.failure_process):
                raise ValueError(
                    "check process sidecar must match failure authority"
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


class HighestVersionPass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    attempt: Attempt
    harness_baseline: HarnessBaseline
    evaluation: PassEvaluation

    @model_validator(mode="after")
    def validate_highest_evaluation(self) -> "HighestVersionPass":
        if self.attempt.identity.requested_resolution != "highest":
            raise ValueError("highest-version pass requires a highest Attempt")
        if self.harness_baseline.cell != self.attempt.identity.cell:
            raise ValueError("highest-version harness baseline must match its cell")
        if self.evaluation.proposal.cell != self.attempt.identity.cell:
            raise ValueError("highest-version evaluation must match its cell")
        if self.evaluation.proposal.attempt_id != self.attempt.attempt_id:
            raise ValueError("highest-version proposal must reference its attempt")
        return self


class BaselineRejection(FrozenSchema):
    status: Literal["BASELINE_REJECTION"] = "BASELINE_REJECTION"
    attempt: Attempt
    failure: FailureRecord
    evaluation: VerifierRejectedEvaluation | None = None
    runtime: RuntimeEvaluationRun | None = Field(default=None, exclude=True)
    failure_process: ProcessObservation | None = Field(default=None, exclude=True)

    @property
    def cell(self) -> Cell:
        return self.attempt.identity.cell

    @model_validator(mode="after")
    def validate_rejection(self) -> "BaselineRejection":
        if self.failure_process is not None and (
            self.runtime is not None or not failure_process_matches(self.failure, self.failure_process)
        ):
            raise ValueError("baseline process sidecar must match failure authority")
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
        if self.runtime is not None and self.runtime.evaluation != self.evaluation:
            raise ValueError("baseline runtime wrapper must match its evaluation")
        return self


class BaselineIndeterminate(FrozenSchema):
    status: Literal["BASELINE_INDETERMINATE"] = "BASELINE_INDETERMINATE"
    attempt: Attempt
    failure: FailureRecord
    evaluation: IndeterminateEvaluation | None = None
    runtime: RuntimeEvaluationRun | None = Field(default=None, exclude=True)
    failure_process: ProcessObservation | None = Field(
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
            if not (
                self.runtime is None
                and failure_process_matches(self.failure, self.failure_process)
            ):
                raise ValueError(
                    "baseline process sidecar must match failure authority"
                )
        if self.evaluation is not None and (
            self.evaluation.proposal.attempt_id != self.attempt.attempt_id
        ):
            raise ValueError("baseline indeterminate evaluation must match its attempt")
        if self.evaluation is not None:
            authority = self.failure.authority
            matches = (
                isinstance(authority, ConfiguredVerifierFailureAuthority)
                and authority.terminal == self.evaluation.verifier.terminal
                and self.failure.stage == "test"
                and self.failure.cause == self.evaluation.cause
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


class SmokeCellPass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    attempt: Attempt
    evaluation: PassEvaluation

    @model_validator(mode="after")
    def validate_smoke_evaluation(self) -> "SmokeCellPass":
        if self.attempt.identity.requested_resolution != "highest":
            raise ValueError("smoke pass requires a highest Attempt")
        if self.evaluation.proposal.cell != self.attempt.identity.cell:
            raise ValueError("smoke evaluation must match its cell")
        if self.evaluation.proposal.attempt_id != self.attempt.attempt_id:
            raise ValueError("smoke proposal must reference its attempt")
        return self


SmokeCellOutcome = Annotated[
    Union[SmokeCellPass, BaselineRejection, BaselineIndeterminate],
    Field(discriminator="status"),
]


class SmokePass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    outcomes: tuple[SmokeCellPass, ...]


class SmokeBaselineRejection(FrozenSchema):
    status: Literal["BASELINE_REJECTION"] = "BASELINE_REJECTION"
    outcomes: tuple[SmokeCellOutcome, ...]

    @model_validator(mode="after")
    def validate_rejection(self) -> "SmokeBaselineRejection":
        if not any(isinstance(item, BaselineRejection) for item in self.outcomes):
            raise ValueError("smoke baseline rejection requires rejected evidence")
        return self


class SmokeIndeterminate(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    outcomes: tuple[SmokeCellPass | BaselineIndeterminate, ...]

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


CoordinateSelectionReason = Literal[
    "mechanical-lowest",
    "mechanical-midpoint",
    "history",
    "static-suspect",
    "static-clean-neighbor",
    "direct-existing",
    "external-hint",
    "current-upper",
]


class SearchProbeRequest(FrozenSchema):
    """One exact active-coordinate probe and its unresolved candidate window."""

    vector: tuple[VersionPin, ...]
    active_dependency: str
    candidate_version: str
    lower_version: str
    upper_version: str
    candidate_count: int = Field(gt=0, strict=True)
    selection_reason: CoordinateSelectionReason = "mechanical-lowest"
    static_search_ref: str | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "SearchProbeRequest":
        if self.selection_reason.startswith("static-") != (self.static_search_ref is not None):
            raise ValueError("static selection requires exactly its completed search reference")
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
    window: Literal["static", "oracle"] = "oracle"

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






class CellSucceeded(FrozenSchema):
    kind: Literal["succeeded"] = "succeeded"
    status: str
    phase: str


class CellFailed(FrozenSchema):
    kind: Literal["failed"] = "failed"
    status: str
    phase: str
    detail: PytestFailureDetail | None = Field(default=None, exclude=True)
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
        VerifierRejectedEvaluation
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
            else:
                authority = self.failure.authority
                if not (
                    isinstance(authority, ConfiguredVerifierFailureAuthority)
                    and authority.terminal == self.evaluation.verifier.terminal
                    and self.failure.stage == "test"
                    and self.failure.cause == self.evaluation.cause
                ):
                    raise ValueError("search verifier indeterminate must match its failure facts")
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

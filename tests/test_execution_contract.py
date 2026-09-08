from __future__ import annotations

import pytest
from execution_fixtures import FACT_CASES
from pydantic import TypeAdapter

from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    ExecutionFailure,
    ExecutionTerminal,
    GraphSuccess,
    InterpreterSuccess,
    NormalExit,
    OperationRequestBinding,
    ProcessResult,
    ProcessTerminalUnavailable,
    Signaled,
    StartFailed,
    StructuredOperationFailure,
    TimedOut,
    ToolSuccess,
    Unattributed,
    Unavailable,
    UvUnsatAttribution,
    classify_execution_terminal,
    classify_operation_failure,
    execution_terminal,
    validate_operation_binding,
)
from pf.schemas.project import Cell


class TestExecutionTerminals:
    @pytest.mark.parametrize(("model", "fields"), [
        (ToolSuccess, {"stage": "create-environment"}),
        (GraphSuccess, {"nodes": ()}),
        (InterpreterSuccess, {"interpreter": {"implementation": "cpython", "version": "3.11.15", "abi": "cp311"}}),
    ])
    @pytest.mark.parametrize("terminal", [
        {"exit_code": 1}, {"exit_code": 0, "timed_out": True},
        {"signal": 9}, {"start_error": "unavailable"},
    ])
    def test_success_observations_require_normal_zero(self, model, fields, terminal):
        with pytest.raises(ValueError, match="successful process"):
            model.model_validate({**fields, "process": {**terminal, "duration_seconds": 0}})

    @pytest.mark.parametrize(
        ("stage", "nonzero"),
        [
            ("resolve-project", ("REJECTED", "RESOLUTION_FAILED")),
            ("resolve-environment", ("REJECTED", "RESOLUTION_FAILED")),
            ("install-project", ("REJECTED", "INSTALLATION_FAILED")),
            ("install-environment", ("REJECTED", "INSTALLATION_FAILED")),
            ("create-environment", ("INDETERMINATE", "TOOL_FAILURE")),
            ("inspect-interpreter", ("INDETERMINATE", "TOOL_FAILURE")),
            ("inspect", ("INDETERMINATE", "TOOL_FAILURE")),
            ("test", ("REJECTED", "VERIFIER_EXITED_NONZERO")),
        ],
    )
    def test_stage_terminal_matrix(self, stage, nonzero):
        assert classify_execution_terminal(stage, NormalExit(exit_code=0), Unattributed()) == ("PASS", None)
        for code in (1, 2, 3, 4, 5, 127, 255):
            assert classify_execution_terminal(stage, NormalExit(exit_code=code), Unattributed()) == nonzero
        assert classify_execution_terminal(stage, TimedOut(), Unattributed()) == ("INDETERMINATE", "TIMEOUT")
        for terminal in (Signaled(signal=9), StartFailed(), Unavailable()):
            assert classify_execution_terminal(stage, terminal, Unattributed()) == ("INDETERMINATE", "TOOL_FAILURE")

    @pytest.mark.parametrize("stage", ["inspect-project-plan", "inspect-environment-plan", "proposal-vector", "planning", "ty", "resolve-other"])
    def test_excluded_operations_do_not_admit_execution_fallback(self, stage):
        with pytest.raises(ValueError, match="stage"):
            classify_execution_terminal(stage, NormalExit(exit_code=1), Unattributed())

    @pytest.mark.parametrize("cleanup", [{"exit_code": 0}, {"exit_code": 1}, {"signal": 9}])
    def test_timeout_precedes_cleanup_terminal(self, cleanup):
        result = ProcessResult(**cleanup, timed_out=True, duration_seconds=1)
        assert execution_terminal(result) == TimedOut()

    def test_logs_are_not_terminal_facts(self):
        complete = ProcessResult(exit_code=4, duration_seconds=1, stderr="build details")
        truncated = ProcessResult(exit_code=4, duration_seconds=99, stdout_complete=False, stderr_complete=False)
        assert execution_terminal(complete) == execution_terminal(truncated) == NormalExit(exit_code=4)
        assert execution_terminal(ProcessTerminalUnavailable(detail="unavailable log")) == Unavailable()

    @pytest.mark.parametrize("value", [-1, True, "1", 1.0])
    def test_exit_code_requires_nonnegative_strict_integer(self, value):
        with pytest.raises(ValueError):
            TypeAdapter(ExecutionTerminal).validate_python({"kind": "normal-exit", "exit_code": value})

    @pytest.mark.parametrize("value", [0, -1, True, "9", 9.0])
    def test_signal_requires_positive_strict_integer(self, value):
        with pytest.raises(ValueError):
            TypeAdapter(ExecutionTerminal).validate_python({"kind": "signaled", "signal": value})


def _attribution(stage="resolve-project", code="direct-version-contradiction"):
    return UvUnsatAttribution.model_validate({
        "kind": "uv-unsat", "tool": "uv", "tool_version": "0.12.5",
        "protocol": "uv-pip-compile-pylock-v1", "profile": "uv-diagnostics-0.12.5-v1",
        "request_binding": {
            "attempt_id": "a" * 64, "stage": stage,
            "project_plan_digest": "b" * 64 if stage != "resolve-project" else None,
            "environment_plan_digest": None,
        },
        "facts": {"code": code, "stdout_complete": True, "stderr_complete": True},
    })


class TestQualifiedAttribution:
    @pytest.mark.parametrize("code", ["direct-version-contradiction", "transitive-version-contradiction"])
    @pytest.mark.parametrize(("stage", "cause"), [("resolve-project", "RESOLUTION_CONFLICT"), ("resolve-environment", "HARNESS_CONFLICT")])
    def test_frozen_credentials_determine_conflict(self, stage, cause, code):
        failure = ExecutionFailure(terminal=NormalExit(exit_code=1), attribution=_attribution(stage, code))
        assert classify_operation_failure(stage, failure) == ("REJECTED", cause)

    @pytest.mark.parametrize("terminal", [NormalExit(exit_code=0), NormalExit(exit_code=2), TimedOut(), Signaled(signal=9), StartFailed(), Unavailable()])
    def test_unsat_requires_normal_exit_one(self, terminal):
        with pytest.raises(ValueError):
            ExecutionFailure(terminal=terminal, attribution=_attribution())

    @pytest.mark.parametrize("stage", ["install-project", "install-environment", "inspect", "test", "resolve-environment"])
    def test_cannot_reuse_credentials_for_another_operation(self, stage):
        with pytest.raises(ValueError):
            classify_operation_failure(stage, ExecutionFailure(terminal=NormalExit(exit_code=1), attribution=_attribution()))

    @pytest.mark.parametrize(("field", "value"), [("tool", "pip"), ("tool_version", "0.12.6"), ("protocol", "other"), ("profile", "other")])
    def test_registry_is_closed(self, field, value):
        document = _attribution().model_dump(mode="json")
        document[field] = value
        with pytest.raises(ValueError):
            UvUnsatAttribution.model_validate(document)

    @pytest.mark.parametrize("value", [False, 1, "true", None])
    @pytest.mark.parametrize("field", ["stdout_complete", "stderr_complete"])
    def test_completeness_is_required_boolean_true(self, field, value):
        document = _attribution().model_dump(mode="json")
        document["facts"][field] = value
        with pytest.raises(ValueError):
            UvUnsatAttribution.model_validate(document)

    def test_unknown_fact_fields_and_codes_are_rejected(self):
        document = _attribution().model_dump(mode="json")
        document["facts"]["diagnostic_digest"] = "a" * 64
        with pytest.raises(ValueError):
            UvUnsatAttribution.model_validate(document)
        document["facts"] = {"code": "unknown", "stdout_complete": True, "stderr_complete": True}
        with pytest.raises(ValueError):
            UvUnsatAttribution.model_validate(document)


class TestStructuredOperationFacts:
    @pytest.mark.parametrize(("code", "stage", "terminal", "cause"), FACT_CASES)
    def test_structured_fact_public_wire_and_cause(self, code, stage, terminal, cause):
        document = {"kind": "operation-structured", "fact": {"code": code}, "terminal": terminal}
        failure = StructuredOperationFailure.model_validate(document)
        assert classify_operation_failure(stage, failure) == ("INDETERMINATE", cause)
        assert failure.model_dump(mode="json") == document
        with pytest.raises(ValueError, match="stage"):
            classify_operation_failure("test", failure)

    @pytest.mark.parametrize(("code", "stage", "terminal", "cause"), FACT_CASES[5:])
    def test_structured_fact_rejects_wrong_terminal(self, code, stage, terminal, cause):
        failure = StructuredOperationFailure.model_validate({
            "kind": "operation-structured", "fact": {"code": code},
            "terminal": {"kind": "normal-exit", "exit_code": 1},
        })
        with pytest.raises(ValueError):
            classify_operation_failure(stage, failure)

    def test_required_nullable_terminal_and_binding_fields(self):
        with pytest.raises(ValueError):
            StructuredOperationFailure.model_validate({"kind": "operation-structured", "fact": {"code": "request-invariant"}})
        binding = OperationRequestBinding(attempt_id="a" * 64, stage="resolve-project", project_plan_digest=None, environment_plan_digest=None)
        assert binding.model_dump(mode="json")["environment_plan_digest"] is None
        document = binding.model_dump(mode="json")
        del document["project_plan_digest"]
        with pytest.raises(ValueError):
            OperationRequestBinding.model_validate(document)


def _attempt(*, harness=False):
    return Attempt.from_identity(AttemptIdentity(
        source_snapshot_digest="snapshot", cell=Cell(
            package="demo", target="x86_64-unknown-linux-gnu", python_minor="3.11", extra_surface=(),
        ), requested_resolution="highest", requested_managed_vector=None,
        active_declaration_ids=(), source_plan_identity="source-plan",
        execution_policy_identity="policy", resolution_context_digest="opaque-context",
        harness_policy_identity="original-harness-v1", harness_declaration_ids=("pytest",) if harness else (),
    ))


class TestOperationBinding:
    @pytest.mark.parametrize(("stage", "harness", "project", "environment"), [
        ("create-environment", False, False, False),
        ("create-environment", True, False, False),
        ("inspect-interpreter", False, False, False),
        ("inspect-interpreter", True, False, False),
        ("resolve-project", False, False, False),
        ("resolve-project", True, False, False),
        ("resolve-environment", True, True, False),
        ("install-project", False, True, False),
        ("install-environment", True, True, True),
        ("inspect", False, True, False),
        ("inspect", True, True, True),
        ("inspect-project-plan", False, True, False),
        ("inspect-environment-plan", True, True, True),
        ("proposal-vector", False, True, False),
        ("proposal-vector", True, True, True),
    ])
    def test_only_committed_plans_belong_to_operation(self, stage, harness, project, environment):
        project_digest = "b" * 64 if project else None
        environment_digest = "c" * 64 if environment else None
        validate_operation_binding(
            attempt=_attempt(harness=harness), stage=stage,
            project_plan_digest=project_digest, environment_plan_digest=environment_digest,
            attribution=Unattributed(),
        )
        for invalid_project, invalid_environment in (
            (None if project else "d" * 64, environment_digest),
            (project_digest, None if environment else "d" * 64),
        ):
            with pytest.raises(ValueError, match="plan timing"):
                validate_operation_binding(
                    attempt=_attempt(harness=harness), stage=stage,
                    project_plan_digest=invalid_project, environment_plan_digest=invalid_environment,
                    attribution=Unattributed(),
                )

    @pytest.mark.parametrize(("stage", "harness"), [
        ("resolve-environment", False), ("install-environment", False),
        ("inspect-environment-plan", False), ("install-project", True),
        ("inspect-project-plan", True),
    ])
    def test_operation_must_match_harness_branch(self, stage, harness):
        with pytest.raises(ValueError, match="harness"):
            validate_operation_binding(attempt=_attempt(harness=harness), stage=stage,
                                       project_plan_digest="b" * 64, environment_plan_digest=None,
                                       attribution=Unattributed())

    def test_credentials_are_bound_to_exact_attempt_without_context_preimage(self):
        attempt = _attempt()
        document = _attribution().model_dump(mode="json")
        document["request_binding"]["attempt_id"] = attempt.attempt_id
        attribution = UvUnsatAttribution.model_validate(document)
        validate_operation_binding(attempt=attempt, stage="resolve-project",
                                   project_plan_digest=None, environment_plan_digest=None,
                                   attribution=attribution)
        with pytest.raises(ValueError, match="Attempt binding"):
            validate_operation_binding(attempt=_attempt(harness=True), stage="resolve-project",
                                       project_plan_digest=None, environment_plan_digest=None,
                                       attribution=attribution)

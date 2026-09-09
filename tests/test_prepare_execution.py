from __future__ import annotations

from pf.cancellation import Cancellation

from pathlib import Path
import hashlib
import json

import pytest

from evaluation_fixtures import ScriptedUv, evaluation_project
from execution_fixtures import FACT_CASES
from pf.adapters.uv import UvAdapter
from pf.environment import EnvironmentFactory, HighestResolution, PreparedEnvironment
from pf.failure import FailurePolicy
from pf.errors import ConfigurationError
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.resolution import ResolutionContext, ResolutionFailure, ResolutionRunContext, InstallFailure
from pf.schemas.evaluation import (
    ExecutionFailureAuthority,
    AttemptFailureScope,
    BaselineRejection,
    BaselineIndeterminate,
    NormalExit,
    PrepareFailure,
    ProcessResult,
    ProcessTerminalUnavailable,
    StructuredOperationFailureAuthority,
    TimedOut,
    Unattributed,
    UvUnsatAttribution,
    ExecutionFailure,
)
from pf.schemas.project import SourcePlan
from pf.snapshot import SnapshotBuilder


DIAGNOSTIC_FIXTURES = json.loads(Path(
    "tests/execution_qualification/2026-09-06-uv-diagnostics.json"
).read_text())["matrix"]


def failure_id_for_wire(record, scope):
    payload = {key: record[key] for key in ("disposition", "cause", "stage", "authority")}
    payload["scope"] = scope.model_dump(mode="json")
    for key in ("project_plan_digest", "environment_plan_digest"):
        if record.get(key) is not None:
            payload[key] = record[key]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "failure-" + hashlib.sha256(b"pf:failure:v3\0" + canonical).hexdigest()[:16]


class _Runner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, spec, *, cancellation: Cancellation | None = None):
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        self.calls.append(spec)
        return self.result


class _Operations(ScriptedUv):
    def __init__(self, stage, adapter):
        super().__init__()
        self.stage = stage
        self.adapter = adapter

    def resolve_project(self, **kwargs):
        if self.stage == "resolve-project":
            return self.adapter.resolve_project(**kwargs)
        return super().resolve_project(**kwargs)

    def resolve_environment(self, **kwargs):
        if self.stage == "resolve-environment":
            return self.adapter.resolve_environment(**kwargs)
        return super().resolve_environment(**kwargs)

    def install_resolution(self, **kwargs):
        if self.stage in {"install-project", "install-environment"}:
            return self.adapter.install_resolution(**kwargs)
        return super().install_resolution(**kwargs)

    def create_environment(self, **kwargs):
        if self.stage == "create-environment":
            return self.adapter.create_environment(**kwargs)
        return super().create_environment(**kwargs)

    def inspect_interpreter(self, **kwargs):
        if self.stage == "inspect-interpreter":
            return self.adapter.inspect_interpreter(**kwargs)
        return super().inspect_interpreter(**kwargs)

    def inspect_environment(self, **kwargs):
        if self.stage == "inspect":
            return self.adapter.inspect_environment(**kwargs)
        return super().inspect_environment(**kwargs)


def _prepare(root: Path, stage: str, process, *, harness=False):
    evaluation_project(root)
    if harness:
        path = root / "pyproject.toml"
        path.write_text(path.read_text().replace("test = []", 'test = ["pytest"]'))
    package = ProjectLoader().load(root=root).target
    runner = _Runner(process)
    factory = EnvironmentFactory(_Operations(stage, UvAdapter(runner)))
    prepared = factory.prepare(
        package=package, cell=package.cells[0],
        snapshot=SnapshotBuilder.without_processes().build(root),
        source_plan=SourcePlan.for_package(package, "SEARCH"),
        resolution=HighestResolution(),
    )
    return prepared, runner


class TestPrepareExecution:
    def test_unavailable_uv_version_output_is_pre_attempt_configuration_failure(self, tmp_path, monkeypatch):
        project = evaluation_project(tmp_path)
        runner = _Runner(ProcessResult(exit_code=0, duration_seconds=0, stdout="uv 0.12.5\n"))

        def unavailable(*args):
            raise OSError("version output unavailable")

        monkeypatch.setattr("pf.adapters.uv.read_process_output", unavailable)
        with pytest.raises(ConfigurationError, match="protocol could not be established"):
            EnvironmentFactory(UvAdapter(runner)).prepare(
                package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
                resolution=HighestResolution(),
            )
        assert len(runner.calls) == 1
        assert runner.calls[0].argv[1:] == ("--version",)

    @pytest.mark.parametrize(("code", "stage", "terminal", "cause"), FACT_CASES)
    def test_every_structured_fact_roundtrips_and_rehashed_semantic_forgery_is_rejected(self, tmp_path, code, stage, terminal, cause):
        root = tmp_path / "project"
        harness = stage in {"resolve-environment", "install-environment", "inspect-environment-plan"}
        prepared, _ = _prepare(root, "install-environment" if harness else "install-project", ProcessResult(exit_code=1, duration_seconds=0), harness=harness)
        assert isinstance(prepared, PrepareFailure)
        needs_project = stage not in {"create-environment", "inspect-interpreter", "resolve-project"}
        needs_environment = stage == "install-environment"
        operation = {"kind": "operation-structured", "fact": {"code": code}, "terminal": terminal}
        failed = PrepareFailure.model_validate({
            "attempt": prepared.attempt, "stage": stage, "failure": operation,
            "project_plan_digest": prepared.project_plan_digest if needs_project else None,
            "environment_plan_digest": prepared.environment_plan_digest if needs_environment else None,
        })
        failure = FailurePolicy().record_prepare(failed)
        assert (failure.disposition, failure.cause) == ("INDETERMINATE", cause)
        assert failure.authority.model_dump(mode="json", exclude_none=True) == operation
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        try:
            report = PackageReportBuilder().build(
                package=package, source_plan=SourcePlan.for_package(package, "SEARCH"),
                source_snapshot=snapshot.identity,
                cell_results=(BaselineIndeterminate(attempt=prepared.attempt, failure=failure),),
            )
        finally:
            snapshot.close()
        path = tmp_path / "package-floor.json"
        ReportStore().write(path, report)
        assert ReportStore().read(path) == report
        original = json.loads(path.read_text())
        assert failure_id_for_wire(original["evidence"]["failures"][0], failure.scope) == failure.failure_id
        for field, value in (("cause", "TIMEOUT"), ("stage", "test")):
            document = json.loads(json.dumps(original))
            wire = document["evidence"]["failures"][0]
            wire[field] = value
            new_id = failure_id_for_wire(wire, failure.scope)
            path.write_text(json.dumps(document).replace(failure.failure_id, new_id))
            with pytest.raises(ConfigurationError):
                ReportStore().read(path)

    @pytest.mark.parametrize("mismatch", ["request", "context", "stage", "credential", "install-plan"])
    def test_factory_closes_mismatched_failure_envelopes_under_expected_request(self, tmp_path, monkeypatch, mismatch):
        def resolution_failure(self, **kwargs):
            context = kwargs["context"]
            failure = ExecutionFailure(terminal=NormalExit(exit_code=1), attribution=Unattributed())
            if mismatch == "context":
                context = ResolutionContext.from_inputs(
                    run=ResolutionRunContext(uv_version="0.12.5", release_cutoff="2026-01-01T00:00:00+00:00"),
                    cell=context.cell, source_plan_identity=context.source_plan_identity,
                    uv_project_configuration_identity=context.uv_project_configuration_identity,
                    interpreter=context.interpreter,
                )
            if mismatch == "credential":
                binding = kwargs["request_binding"].model_dump(mode="json")
                binding["attempt_id"] = "f" * 64
                failure = ExecutionFailure(terminal=NormalExit(exit_code=1), attribution=UvUnsatAttribution.model_validate({
                    "kind": "uv-unsat", "tool": "uv", "tool_version": "0.12.5",
                    "protocol": "uv-pip-compile-pylock-v1", "profile": "uv-diagnostics-0.12.5-v1",
                    "request_binding": binding,
                    "facts": {"code": "direct-version-contradiction", "stdout_complete": True, "stderr_complete": True},
                }))
            return ResolutionFailure(
                stage="resolve-environment" if mismatch == "stage" else "resolve-project",
                request_digest="different-request" if mismatch == "request" else kwargs["request_digest"],
                context=context, failure=failure,
            )

        def install_failure(self, **kwargs):
            return InstallFailure(
                stage="install-project", plan_digest="different-plan",
                failure=ExecutionFailure(terminal=NormalExit(exit_code=1), attribution=Unattributed()),
            )

        monkeypatch.setattr(ScriptedUv, "install_resolution" if mismatch == "install-plan" else "resolve_project", install_failure if mismatch == "install-plan" else resolution_failure)
        prepared, _ = _prepare(tmp_path, "none", ProcessResult(exit_code=0, duration_seconds=0))
        assert isinstance(prepared, PrepareFailure)
        failure = FailurePolicy().record_prepare(prepared)
        assert (failure.disposition, failure.cause) == ("INDETERMINATE", "INTERNAL_INVARIANT")
        assert isinstance(failure.authority, StructuredOperationFailureAuthority)
        assert failure.authority.fact.code == "request-invariant"
        assert failure.stage == ("install-project" if mismatch == "install-plan" else "resolve-project")
        assert (failure.project_plan_digest is not None) == (mismatch == "install-plan")
        assert failure.environment_plan_digest is None

    @pytest.mark.parametrize("case", DIAGNOSTIC_FIXTURES, ids=lambda case: case["case"])
    @pytest.mark.parametrize("branch", ["project", "environment"])
    @pytest.mark.parametrize("complete", [True, False])
    def test_recorded_uv_profile_flows_through_typed_prepare_authority(self, tmp_path, case, branch, complete):
        operation = "install" if case["command"][1:3] == ["pip", "sync"] else "resolve"
        stage = f"{operation}-{branch}"
        root = tmp_path / "project"
        prepared, _ = _prepare(root, stage, ProcessResult(
            exit_code=case["exit_code"], duration_seconds=0,
            stdout=case["stdout"], stderr=case["stderr"],
            stdout_complete=complete, stderr_complete=complete,
        ), harness=branch == "environment")
        assert isinstance(prepared, PrepareFailure)
        failure = FailurePolicy().record_prepare(prepared)
        assert failure.disposition == "REJECTED"
        assert isinstance(failure.authority, ExecutionFailureAuthority)
        proof_code = case["pf_classification"]["proof_code"]
        if proof_code is not None and complete:
            attribution = failure.authority.attribution
            assert isinstance(attribution, UvUnsatAttribution)
            assert attribution.facts.code == proof_code
            assert attribution.request_binding.attempt_id == prepared.attempt.attempt_id
            assert attribution.request_binding.stage == stage
            assert failure.cause == ("RESOLUTION_CONFLICT" if branch == "project" else "HARNESS_CONFLICT")
            package = ProjectLoader().load(root=root).target
            snapshot = SnapshotBuilder.without_processes().build(root)
            try:
                report = PackageReportBuilder().build(
                    package=package, source_plan=SourcePlan.for_package(package, "SEARCH"),
                    source_snapshot=snapshot.identity,
                    cell_results=(BaselineRejection(attempt=prepared.attempt, failure=failure),),
                )
                path = tmp_path / "package-floor.json"
                ReportStore().write(path, report)
                assert ReportStore().read(path) == report
            finally:
                snapshot.close()
        else:
            assert failure.authority.attribution == Unattributed()
            assert failure.cause == ("RESOLUTION_FAILED" if operation == "resolve" else "INSTALLATION_FAILED")

    @pytest.mark.parametrize(("path", "value"), [
        (("cause",), "INSTALLATION_FAILED"),
        (("disposition",), "INDETERMINATE"),
        (("stage",), "test"),
        (("stage",), "inspect-project-plan"),
        (("stage",), "resolve-environment"),
        (("project_plan_digest",), "a" * 64),
        (("environment_plan_digest",), "b" * 64),
        (("authority", "terminal"), {"kind": "normal-exit", "exit_code": 0}),
        (("authority", "terminal"), {"kind": "normal-exit", "exit_code": 2}),
        (("authority", "terminal"), {"kind": "timed-out"}),
        (("authority", "attribution", "tool"), "other"),
        (("authority", "attribution", "tool_version"), "0.12.4"),
        (("authority", "attribution", "protocol"), "other"),
        (("authority", "attribution", "profile"), "other"),
        (("authority", "attribution", "facts", "code"), "unknown"),
        (("authority", "attribution", "facts", "stdout_complete"), False),
        (("authority", "attribution", "facts", "stderr_complete"), False),
        (("authority", "attribution", "request_binding", "attempt_id"), "a" * 64),
        (("authority", "attribution", "request_binding", "stage"), "install-project"),
        (("authority", "attribution", "request_binding", "project_plan_digest"), "b" * 64),
    ])
    def test_offline_report_rejects_invalid_execution_facts_even_after_rehash(self, tmp_path, path, value):
        root = tmp_path / "project"
        prepared, _ = _prepare(root, "resolve-project", ProcessResult(
            exit_code=1, duration_seconds=0,
            stderr="× No solution found when resolving dependencies: Because you require demo==1 and demo==2, we can conclude that your requirements are unsatisfiable.",
        ))
        assert isinstance(prepared, PrepareFailure)
        failure = FailurePolicy().record_prepare(prepared)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        try:
            report = PackageReportBuilder().build(
                package=package, source_plan=SourcePlan.for_package(package, "SEARCH"),
                source_snapshot=snapshot.identity,
                cell_results=(BaselineRejection(attempt=prepared.attempt, failure=failure),),
            )
        finally:
            snapshot.close()
        report_path = tmp_path / "package-floor.json"
        store = ReportStore()
        store.write(report_path, report)
        assert store.read(report_path).cell_results[0].status == "BASELINE_REJECTION"
        document = json.loads(report_path.read_text())
        wire = document["evidence"]["failures"][0]

        assert failure_id_for_wire(wire, failure.scope) == failure.failure_id
        target = wire
        for key in path[:-1]:
            target = target[key]
        if path[-1] not in {"project_plan_digest", "environment_plan_digest"}:
            assert path[-1] in target
        target[path[-1]] = value
        new_id = failure_id_for_wire(wire, failure.scope)
        assert new_id != failure.failure_id
        # Replace every failure reference, not only the evidence record ID.
        report_path.write_text(json.dumps(document).replace(failure.failure_id, new_id))
        with pytest.raises(ConfigurationError):
            store.read(report_path)

    @pytest.mark.parametrize("process", [
        ProcessResult(exit_code=0, timed_out=True, duration_seconds=0, stdout="uv 0.12.5\n"),
        ProcessResult(exit_code=0, duration_seconds=0, stdout="uv 0.12.5\n", stdout_complete=False),
        ProcessResult(exit_code=0, duration_seconds=0, stdout="uv 0.12.4\n"),
        ProcessTerminalUnavailable(),
    ])
    def test_uv_admission_failure_stops_before_environment_or_attempt(self, tmp_path, process):
        evaluation_project(tmp_path)
        package = ProjectLoader().load(root=tmp_path).target
        runner = _Runner(process)
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        try:
            with pytest.raises(ConfigurationError, match="protocol could not be established"):
                EnvironmentFactory(UvAdapter(runner)).prepare(
                    package=package, cell=package.cells[0], snapshot=snapshot,
                    source_plan=SourcePlan.for_package(package, "SEARCH"),
                    resolution=HighestResolution(),
                )
            assert len(runner.calls) == 1
            assert runner.calls[0].argv[1:] == ("--version",)
        finally:
            snapshot.close()

    @pytest.mark.parametrize(("stage", "filename"), [
        ("resolve-project", "project-requirements.in"),
        ("resolve-environment", "project-constraints.in"),
        ("install-project", "pylock.pf-install.toml"),
        ("install-environment", "pylock.pf-install.toml"),
    ])
    def test_direct_environment_io_failure_has_no_borrowed_terminal(self, tmp_path, monkeypatch, stage, filename):
        write_text = Path.write_text

        def deny_operation_input(path, *args, **kwargs):
            if path.name == filename:
                raise PermissionError("controlled environment input denial")
            return write_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", deny_operation_input)
        prepared, runner = _prepare(tmp_path, stage, ProcessResult(exit_code=1, duration_seconds=0), harness=stage in {"resolve-environment", "install-environment"})
        assert isinstance(prepared, PrepareFailure)
        failure = FailurePolicy().record_prepare(prepared)
        assert (failure.disposition, failure.cause) == ("INDETERMINATE", "ENVIRONMENT_FAILURE")
        assert isinstance(failure.authority, StructuredOperationFailureAuthority)
        assert failure.authority.fact.code == "environment-access-failed"
        assert failure.authority.model_dump(mode="json", exclude_none=True)["terminal"] is None
        assert runner.calls == []

    def test_missing_optional_diagnostic_does_not_erase_normal_nonzero(self, tmp_path, monkeypatch):
        def unavailable_output(self, process):
            raise FileNotFoundError("optional diagnostic unavailable")

        monkeypatch.setattr(_Runner, "output", unavailable_output, raising=False)
        prepared, _ = _prepare(tmp_path, "resolve-project", ProcessResult(exit_code=1, duration_seconds=0))
        assert isinstance(prepared, PrepareFailure)
        failure = FailurePolicy().record_prepare(prepared)
        assert (failure.disposition, failure.cause) == ("REJECTED", "RESOLUTION_FAILED")
        assert isinstance(failure.authority, ExecutionFailureAuthority)
        assert failure.authority.attribution == Unattributed()

    @pytest.mark.parametrize(
        ("stage", "harness", "cause", "has_project", "has_environment"),
        [
            ("resolve-project", False, "RESOLUTION_FAILED", False, False),
            ("resolve-project", True, "RESOLUTION_FAILED", False, False),
            ("resolve-environment", True, "RESOLUTION_FAILED", True, False),
            ("install-project", False, "INSTALLATION_FAILED", True, False),
            ("install-environment", True, "INSTALLATION_FAILED", True, True),
        ],
        ids=(
            "resolve-project-no-harness-no-plans",
            "resolve-project-harness-no-plans",
            "resolve-environment-harness-project-only",
            "install-project-no-harness-project-only",
            "install-environment-harness-both-plans",
        ),
    )
    @pytest.mark.parametrize("complete", [False, True], ids=("truncated", "complete"))
    def test_unattributed_normal_nonzero_rejects_through_factory_and_policy(
        self, tmp_path, stage, harness, cause, has_project, has_environment, complete,
    ):
        prepared, runner = _prepare(tmp_path, stage, ProcessResult(
            exit_code=7, duration_seconds=1, stderr="build backend failed",
            stdout_complete=complete, stderr_complete=complete,
        ), harness=harness)
        assert isinstance(prepared, PrepareFailure)
        assert len(runner.calls) == 1
        record = FailurePolicy().record_prepare(prepared)
        assert (record.stage, record.cause, record.disposition) == (stage, cause, "REJECTED")
        assert isinstance(record.authority, ExecutionFailureAuthority)
        assert record.authority.terminal == NormalExit(exit_code=7)
        assert record.authority.attribution == Unattributed()
        assert (record.project_plan_digest is not None) == has_project
        assert (record.environment_plan_digest is not None) == has_environment
        assert isinstance(record.scope, AttemptFailureScope)
        assert record.scope.attempt == prepared.attempt
        assert prepared.process is runner.result

    @pytest.mark.parametrize("stage", ["resolve-project", "resolve-environment", "install-project", "install-environment", "create-environment", "inspect-interpreter", "inspect"])
    def test_timeout_cleanup_zero_never_becomes_success(self, tmp_path, stage):
        prepared, _ = _prepare(tmp_path, stage, ProcessResult(exit_code=0, timed_out=True, duration_seconds=1), harness="environment" in stage and stage != "create-environment")
        assert isinstance(prepared, PrepareFailure)
        record = FailurePolicy().record_prepare(prepared)
        assert (record.disposition, record.cause) == ("INDETERMINATE", "TIMEOUT")
        assert isinstance(record.authority, ExecutionFailureAuthority)
        assert record.authority.terminal == TimedOut()

    @pytest.mark.parametrize("stage", ["create-environment", "inspect-interpreter", "inspect"])
    @pytest.mark.parametrize("process", [ProcessResult(exit_code=9, duration_seconds=0), ProcessTerminalUnavailable()])
    def test_auxiliary_failure_does_not_reject(self, tmp_path, stage, process):
        prepared, _ = _prepare(tmp_path, stage, process)
        assert isinstance(prepared, PrepareFailure)
        record = FailurePolicy().record_prepare(prepared)
        assert (record.disposition, record.cause) == ("INDETERMINATE", "TOOL_FAILURE")

    @pytest.mark.parametrize(("stage", "code"), [
        ("resolve-project", "resolution-plan-invalid"),
        ("resolve-environment", "resolution-plan-invalid"),
        ("inspect-interpreter", "interpreter-observation-invalid"),
        ("inspect", "graph-observation-invalid"),
    ])
    def test_bad_success_observation_retains_normal_zero(self, tmp_path, stage, code):
        prepared, _ = _prepare(tmp_path, stage, ProcessResult(exit_code=0, duration_seconds=0, stdout="invalid"), harness=stage == "resolve-environment")
        assert isinstance(prepared, PrepareFailure)
        record = FailurePolicy().record_prepare(prepared)
        assert isinstance(record.authority, StructuredOperationFailureAuthority)
        assert record.authority.fact.code == code
        assert record.authority.terminal == NormalExit(exit_code=0)
        assert (record.disposition, record.cause) == ("INDETERMINATE", "TOOL_FAILURE")

    @pytest.mark.parametrize(("stage", "cause"), [("resolve-project", "RESOLUTION_CONFLICT"), ("resolve-environment", "HARNESS_CONFLICT")])
    def test_qualified_unsat_keeps_attempt_binding(self, tmp_path, stage, cause):
        prepared, _ = _prepare(tmp_path, stage, ProcessResult(
            exit_code=1, duration_seconds=0,
            stderr="× No solution found when resolving dependencies: Because you require demo==1 and demo==2, we can conclude that your requirements are unsatisfiable.",
        ), harness=stage == "resolve-environment")
        assert isinstance(prepared, PrepareFailure)
        record = FailurePolicy().record_prepare(prepared)
        assert (record.disposition, record.cause) == ("REJECTED", cause)
        assert isinstance(record.authority, ExecutionFailureAuthority)
        attribution = record.authority.attribution
        assert isinstance(attribution, UvUnsatAttribution)
        assert attribution.request_binding.attempt_id == prepared.attempt.attempt_id
        assert attribution.facts.code == "direct-version-contradiction"

    def test_successful_prepare_still_requires_installed_graph(self, tmp_path):
        prepared, runner = _prepare(tmp_path, "none", ProcessResult(exit_code=0, duration_seconds=0))
        assert isinstance(prepared, PreparedEnvironment)
        try:
            assert prepared.proposal.managed_vector
            assert prepared.proposal.environment_plan_digest is None
            assert runner.calls == []
        finally:
            prepared.close()

    @pytest.mark.parametrize("process", [
        ProcessResult(exit_code=7, duration_seconds=0),
        ProcessResult(exit_code=0, duration_seconds=0),
        ProcessTerminalUnavailable(),
        ProcessResult(exit_code=1, duration_seconds=0, stderr="× No solution found when resolving dependencies: Because you require demo==1 and demo==2, we can conclude that your requirements are unsatisfiable."),
    ])
    @pytest.mark.parametrize("stage", ["resolve-project", "resolve-environment"])
    def test_attempt_only_report_write_read_preserves_authority(self, tmp_path, process, stage):
        prepared, _ = _prepare(tmp_path / "project", stage, process, harness=stage == "resolve-environment")
        assert isinstance(prepared, PrepareFailure)
        failure = FailurePolicy().record_prepare(prepared)
        outcome_type = BaselineRejection if failure.disposition == "REJECTED" else BaselineIndeterminate
        outcome = outcome_type(attempt=prepared.attempt, failure=failure, failure_process=prepared.process)
        package = ProjectLoader().load(root=tmp_path / "project").target
        report = PackageReportBuilder().build(
            package=package, source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=SnapshotBuilder.without_processes().build(tmp_path / "project").identity,
            cell_results=(outcome,),
        )
        path = tmp_path / "package-floor.json"
        store = ReportStore()
        store.write(path, report)
        restored = store.read(path)
        restored_outcome = restored.cell_results[0]
        assert isinstance(restored_outcome, (BaselineRejection, BaselineIndeterminate))
        assert restored_outcome.failure == failure
        document = json.loads(path.read_text())
        assert document["identity"]["failure_policy"] == "failure-execution-v4"
        authority = document["evidence"]["failures"][0]["authority"]
        assert authority == failure.authority.model_dump(mode="json")

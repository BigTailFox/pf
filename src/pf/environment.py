from __future__ import annotations

from pf.errors import MaterializationIntegrityError

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import threading
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from packaging.requirements import Requirement
import tomlkit
from tomlkit.items import Array

from pf.cancellation import Cancellation
from pf.errors import ConfigurationError, InfrastructureError
from pf.harness import active_harness_requirements, original_harness, relax_harness
from pf.policy import execution_policy_identity
from pf.resolution import (
    EnvironmentIdentity,
    InstalledResolution,
    InstallFailure,
    InstallOutcome,
    ResolutionContext,
    ResolutionFailure,
    ResolutionOutcome,
    ResolutionPlan,
    ResolutionRunContext,
    resolution_request_digest,
)
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    GraphOutcome,
    InterpreterOutcome,
    InterpreterSuccess,
    CellStageEvent,
    PrepareFailure,
    StageProgress,
    ToolFailure,
    ToolSuccess,
    AuxiliaryOutcome,
    OperationFailureResult,
    OperationRequestBinding,
    OperationStage,
    StructuredOperationFailure,
    ExecutionFailure,
    UvUnsatAttribution,
    RequestInvariantFact,
    InterpreterMismatchFact,
    ArtifactPolicyMismatchFact,
    ManagedSourceMismatchFact,
    ManagedSourceLeakageFact,
    InstalledGraphMismatchFact,
    ProposalVectorMismatchFact,
    execution_terminal,
)
from pf.schemas.project import (
    Cell,
    HarnessBaseline,
    HarnessResolutionRequirement,
    PackagePlan,
    Proposal,
    SelectedCandidate,
    SourceIdentity,
    SourcePlan,
    VersionPin,
    selected_candidate_evidence_digest,
)
from pf.snapshot import SourceSnapshot, cleanup_temporary_directory


if TYPE_CHECKING:
    from pf.static_request import StaticRequestMaterialization
    from pf.static_cache import RunStaticConsumerRef


@dataclass(frozen=True)
class HighestResolution:
    kind: Literal["highest"] = "highest"


@dataclass(frozen=True)
class LowestDirectResolution:
    harness_baseline: HarnessBaseline
    kind: Literal["lowest-direct"] = "lowest-direct"


@dataclass(frozen=True)
class ExactSelection:
    selection: tuple[SelectedCandidate, ...]
    harness_baseline: HarnessBaseline
    kind: Literal["exact-selection"] = "exact-selection"


ResolutionRequest = HighestResolution | LowestDirectResolution | ExactSelection


class StageConsumer(Protocol):
    def consume(self, event: CellStageEvent) -> None: ...


def emit_cell_stage(
    events: StageConsumer | None,
    cell: Cell,
    stage: str,
    *,
    progress: StageProgress | None = None,
) -> None:
    if events is None:
        return
    events.consume(
        CellStageEvent(
            cell=cell,
            stage=stage,
            progress=progress,
        )
    )


class UvOperations(Protocol):
    def resolution_run_context(
        self,
        *,
        root: Path,
        timeout_seconds: int | None,
    ) -> ResolutionRunContext | ToolFailure: ...

    def resolve_project(
        self,
        *,
        package: Path,
        package_name: str,
        interpreter: Path,
        cell: Cell,
        resolution: ResolutionRequest,
        context: ResolutionContext,
        request_digest: str,
        request_binding: OperationRequestBinding,
        work_directory: Path,
        artifact_policy: Literal["wheel", "sdist", "any"],
        timeout_seconds: int | None,
        source_plan: SourcePlan,
    ) -> ResolutionOutcome: ...

    def resolve_environment(
        self,
        *,
        package: Path,
        package_name: str,
        interpreter: Path,
        cell: Cell,
        resolution: ResolutionRequest,
        context: ResolutionContext,
        request_digest: str,
        request_binding: OperationRequestBinding,
        project_plan: ResolutionPlan,
        harness: tuple[HarnessResolutionRequirement, ...],
        work_directory: Path,
        artifact_policy: Literal["wheel", "sdist", "any"],
        timeout_seconds: int | None,
        source_plan: SourcePlan,
    ) -> ResolutionOutcome: ...

    def create_environment(
        self,
        *,
        environment: Path,
        python_minor: str,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> AuxiliaryOutcome: ...

    def install_resolution(
        self,
        *,
        plan: ResolutionPlan,
        request_binding: OperationRequestBinding,
        interpreter: Path,
        cwd: Path,
        work_directory: Path,
        timeout_seconds: int | None,
    ) -> InstallOutcome: ...

    def inspect_interpreter(
        self,
        *,
        interpreter: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> InterpreterOutcome: ...

    def inspect_environment(
        self,
        *,
        interpreter: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> GraphOutcome: ...


class PreparedEnvironment:
    """Runtime resources for one exact Proposal."""

    def __init__(
        self,
        *,
        attempt: Attempt,
        proposal: Proposal,
        source_plan: SourcePlan,
        proposal_root: Path,
        package_root: Path,
        environment_root: Path,
        interpreter: Path,
        project_plan: ResolutionPlan,
        environment_plan: ResolutionPlan | None,
        environment_identity: EnvironmentIdentity,
        harness_baseline: HarnessBaseline,
        selected_candidates: tuple[SelectedCandidate, ...] | None,
        temporary_directory: tempfile.TemporaryDirectory[str],
    ) -> None:
        if source_plan.identity != attempt.identity.source_plan_identity:
            raise ValueError("prepared SourcePlan must match its actual Attempt")
        self.attempt = attempt
        self.source_plan = source_plan
        self.proposal = proposal
        self.proposal_root = proposal_root
        self.package_root = package_root
        self.environment_root = environment_root
        self.interpreter = interpreter
        self.project_plan = project_plan
        self.environment_plan = environment_plan
        self.environment_identity = environment_identity
        self.harness_baseline = harness_baseline
        self.selected_candidates = selected_candidates
        self._temporary_directory = temporary_directory
        self._use_lock = threading.RLock()
        self._operation: Literal["static", "verifier"] | None = None
        self.static_materialization: StaticRequestMaterialization | None = None
        self.static_consumer: RunStaticConsumerRef | None = None
        self._tested = False
        self._inputs_valid = True
        self._closed = False

    def invalidate_inputs(self) -> None:
        """Record independently observed changes to owned execution inputs."""
        with self._use_lock:
            self._inputs_valid = False
            self.static_consumer = None

    @property
    def inputs_valid(self) -> bool:
        with self._use_lock:
            return self._inputs_valid

    @property
    def tested(self) -> bool:
        with self._use_lock:
            return self._tested

    @property
    def closed(self) -> bool:
        with self._use_lock:
            return self._closed

    @contextmanager
    def static_use(self, *, cancellation: Cancellation | None = None) -> Iterator[bool]:
        """Borrow clean inputs through complete collection and process cleanup."""
        with self._static_lock(cancellation):
            if self._closed or self._tested or not self._inputs_valid or self._operation == "verifier":
                yield False
                return
            previous = self._operation
            self._operation = "static"
            try:
                yield True
            finally:
                self._operation = previous

    @contextmanager
    def _static_lock(self, cancellation: Cancellation | None) -> Iterator[None]:
        if cancellation is None:
            self._use_lock.acquire()
        else:
            cancellation.raise_if_cancelled()
            while not self._use_lock.acquire(timeout=0.05):
                cancellation.raise_if_cancelled()
        try:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            yield
        finally:
            self._use_lock.release()

    @contextmanager
    def verifier_use(self) -> Iterator[None]:
        with self._use_lock:
            if not self._inputs_valid:
                raise MaterializationIntegrityError("prepared execution inputs changed during static collection")
            if self._closed or self._tested or self._operation is not None:
                raise RuntimeError("verifier requires an unused, available environment")
            self._tested = True
            self._operation = "verifier"
            try:
                yield
            finally:
                self._operation = None

    def mark_tested(self) -> None:
        with self._use_lock:
            if self._closed or self._operation == "static":
                raise RuntimeError("cannot mark an unavailable environment tested")
            self._tested = True

    def close(self) -> None:
        with self._use_lock:
            if self._operation is not None:
                raise RuntimeError("cannot close an environment from its active operation")
            if not self._closed:
                self._closed = True
                self.static_materialization = None
                self.static_consumer = None
                cleanup_temporary_directory(self._temporary_directory)


class EnvironmentFactory:
    """Create an isolated writable source tree and environment for one Proposal."""

    def __init__(
        self, uv: UvOperations, *, events: StageConsumer | None = None
    ) -> None:
        self._uv = uv
        self._events = events
        self._plan_lock = threading.Lock()
        self._plans: dict[tuple[str, str], ResolutionOutcome] = {}

    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: ResolutionRequest,
        source_plan: SourcePlan,
    ) -> PreparedEnvironment | PrepareFailure:
        run = self._uv.resolution_run_context(
            root=Path.cwd(),
            timeout_seconds=package.config.resolution.timeout_seconds,
        )
        if isinstance(run, ToolFailure):
            raise ConfigurationError("uv resolver protocol could not be established")
        context = ResolutionContext.from_inputs(
            run=run,
            cell=cell,
            source_plan_identity=source_plan.identity,
            uv_project_configuration_identity=(
                snapshot.uv_project_configuration_identity(package.pyproject_path)
            ),
        )
        managed_vector = (
            self._selection_vector(resolution.selection)
            if isinstance(resolution, ExactSelection)
            else None
        )
        attempt = self._attempt(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=resolution,
            managed_vector=managed_vector,
            context=context,
            source_plan=source_plan,
        )

        project_plan_digest: str | None = None
        environment_plan_digest: str | None = None

        def failed(failure: OperationFailureResult | ResolutionFailure | InstallFailure) -> PrepareFailure:
            return PrepareFailure(
                attempt=attempt,
                stage=failure.stage,
                failure=failure.failure,
                process=failure.process,
                project_plan_digest=project_plan_digest,
                environment_plan_digest=environment_plan_digest,
            )

        def binding(stage: Literal["resolve-project", "resolve-environment", "install-project", "install-environment"]) -> OperationRequestBinding:
            return OperationRequestBinding(
                attempt_id=attempt.attempt_id, stage=stage,
                project_plan_digest=project_plan_digest,
                environment_plan_digest=environment_plan_digest,
            )

        def invariant(stage: OperationStage) -> OperationFailureResult:
            return OperationFailureResult(
                stage=stage, failure=StructuredOperationFailure(fact=RequestInvariantFact(), terminal=None),
            )

        def resolution_envelope_valid(outcome: ResolutionOutcome, expected_request: str, expected_binding: OperationRequestBinding) -> bool:
            expected_kind = "project" if expected_binding.stage == "resolve-project" else "environment"
            if outcome.context != context or outcome.request_digest != expected_request:
                return False
            if isinstance(outcome, ResolutionPlan):
                return outcome.kind == expected_kind
            if outcome.stage != expected_binding.stage:
                return False
            if isinstance(outcome.failure, ExecutionFailure) and isinstance(outcome.failure.attribution, UvUnsatAttribution):
                return outcome.failure.attribution.request_binding == expected_binding
            return True

        temporary_directory = tempfile.TemporaryDirectory(prefix="pf-proposal-")
        runtime_root = Path(temporary_directory.name)
        proposal_root = runtime_root / "source"
        environment_root = runtime_root / "environment"
        try:
            snapshot.materialize(proposal_root)
            package_root = proposal_root / Path(package.pyproject_path).parent
            self._materialize_managed_vector(
                package=package,
                cell=cell,
                package_root=package_root,
                managed_vector=managed_vector,
            )
            emit_cell_stage(self._events, cell, "preparing environment")
            create = self._uv.create_environment(
                environment=environment_root,
                python_minor=cell.python_minor,
                cwd=proposal_root,
                timeout_seconds=package.config.resolution.timeout_seconds,
            )
            if not isinstance(create, (ToolSuccess, OperationFailureResult)):
                raise InfrastructureError("uv create returned an unsupported outcome")
            if create.stage != "create-environment":
                cleanup_temporary_directory(temporary_directory)
                return failed(invariant("create-environment"))
            if isinstance(create, OperationFailureResult):
                cleanup_temporary_directory(temporary_directory)
                return failed(create)
            interpreter = self._interpreter(environment_root)
            interpreter_result = self._uv.inspect_interpreter(
                interpreter=interpreter,
                cwd=package_root,
                timeout_seconds=package.config.resolution.timeout_seconds,
            )
            if not isinstance(interpreter_result, InterpreterSuccess):
                cleanup_temporary_directory(temporary_directory)
                if interpreter_result.stage != "inspect-interpreter":
                    return failed(invariant("inspect-interpreter"))
                return failed(interpreter_result)
            if (
                interpreter_result.interpreter.implementation != "cpython"
                or not interpreter_result.interpreter.version.startswith(
                    f"{cell.python_minor}."
                )
            ):
                cleanup_temporary_directory(temporary_directory)
                return failed(
                    OperationFailureResult(
                        stage="inspect-interpreter",
                        failure=StructuredOperationFailure(fact=InterpreterMismatchFact(), terminal=execution_terminal(interpreter_result.process)),
                        process=interpreter_result.process,
                    )
                )
            context = ResolutionContext.from_inputs(
                run=run,
                cell=cell,
                source_plan_identity=source_plan.identity,
                uv_project_configuration_identity=context.uv_project_configuration_identity,
                interpreter=interpreter_result.interpreter,
            )
            attempt = self._attempt(
                package=package,
                cell=cell,
                snapshot=snapshot,
                resolution=resolution,
                managed_vector=managed_vector,
                context=context,
                source_plan=source_plan,
            )
            project_request = resolution_request_digest(
                kind="project",
                package_name=package.name,
                snapshot_digest=snapshot.identity.digest,
                cell=cell,
                resolution_kind=resolution.kind,
                selection=(
                    resolution.selection if isinstance(resolution, ExactSelection) else None
                ),
                baseline_digest=(
                    resolution.harness_baseline.digest
                    if isinstance(resolution, (ExactSelection, LowestDirectResolution))
                    else None
                ),
                context_digest=context.digest,
                project_plan_digest=None,
                harness=(),
                source_plan_identity=source_plan.identity,
            )
            emit_cell_stage(self._events, cell, "resolving project")
            project_outcome = self._resolve_once(
                key=("project", project_request),
                resolve=lambda: self._uv.resolve_project(
                    package=package_root,
                    package_name=package.name,
                    interpreter=interpreter,
                    cell=cell,
                    resolution=resolution,
                    context=context,
                    request_digest=project_request,
                    request_binding=binding("resolve-project"),
                    work_directory=runtime_root,
                    artifact_policy=package.config.resolution.artifact,
                    timeout_seconds=package.config.resolution.timeout_seconds,
                    source_plan=source_plan,
                ),
            )
            if not resolution_envelope_valid(project_outcome, project_request, binding("resolve-project")):
                cleanup_temporary_directory(temporary_directory)
                return failed(invariant("resolve-project"))
            if not isinstance(project_outcome, ResolutionPlan):
                cleanup_temporary_directory(temporary_directory)
                return failed(project_outcome)
            artifact_failure = self._artifact_policy_failure(
                project_outcome,
                policy=package.config.resolution.artifact,
            )
            if artifact_failure is not None:
                cleanup_temporary_directory(temporary_directory)
                return failed(artifact_failure)
            source_failure = self._managed_source_failure(
                package=package,
                cell=cell,
                source_plan=source_plan,
                resolution=resolution,
                plan=project_outcome,
            )
            if source_failure is not None:
                cleanup_temporary_directory(temporary_directory)
                return failed(source_failure)
            project_plan_digest = project_outcome.semantic_digest

            environment_outcome: ResolutionOutcome | None = None
            if attempt.identity.harness_declaration_ids:
                harness = self._harness_for_resolution(
                    package=package,
                    cell=cell,
                    resolution=resolution,
                    source_plan=source_plan,
                    project_plan=project_outcome,
                )
                environment_request = resolution_request_digest(
                    kind="environment",
                    package_name=package.name,
                    snapshot_digest=snapshot.identity.digest,
                    cell=cell,
                    resolution_kind=resolution.kind,
                    selection=(
                        resolution.selection if isinstance(resolution, ExactSelection) else None
                    ),
                    baseline_digest=(
                        resolution.harness_baseline.digest
                        if isinstance(resolution, (ExactSelection, LowestDirectResolution))
                        else None
                    ),
                    context_digest=context.digest,
                    project_plan_digest=project_outcome.semantic_digest,
                    harness=harness,
                    source_plan_identity=source_plan.identity,
                )
                emit_cell_stage(self._events, cell, "resolving environment")
                environment_outcome = self._resolve_once(
                    key=("environment", environment_request),
                    resolve=lambda: self._uv.resolve_environment(
                        package=package_root,
                        package_name=package.name,
                        interpreter=interpreter,
                        cell=cell,
                        resolution=resolution,
                        context=context,
                        request_digest=environment_request,
                        request_binding=binding("resolve-environment"),
                        project_plan=project_outcome,
                        harness=harness,
                        work_directory=runtime_root,
                        artifact_policy=package.config.resolution.artifact,
                        timeout_seconds=package.config.resolution.timeout_seconds,
                        source_plan=source_plan,
                    ),
                )
                if not resolution_envelope_valid(environment_outcome, environment_request, binding("resolve-environment")):
                    cleanup_temporary_directory(temporary_directory)
                    return failed(invariant("resolve-environment"))
                if not isinstance(environment_outcome, ResolutionPlan):
                    cleanup_temporary_directory(temporary_directory)
                    return failed(environment_outcome)
                artifact_failure = self._artifact_policy_failure(
                    environment_outcome,
                    policy=package.config.resolution.artifact,
                )
                if artifact_failure is not None:
                    cleanup_temporary_directory(temporary_directory)
                    return failed(artifact_failure)
                if not self._project_graph_is_exact(project_outcome, environment_outcome):
                    cleanup_temporary_directory(temporary_directory)
                    return failed(
                        OperationFailureResult(
                            stage="resolve-environment",
                            process=environment_outcome.process,
                            failure=StructuredOperationFailure(
                                fact=ManagedSourceMismatchFact(), terminal=execution_terminal(environment_outcome.process),
                            ),
                        )
                    )
                environment_plan_digest = environment_outcome.semantic_digest

            final_plan = environment_outcome or project_outcome
            emit_cell_stage(self._events, cell, f"installing {final_plan.kind} plan")
            install = self._uv.install_resolution(
                plan=final_plan,
                request_binding=binding("install-project" if final_plan.kind == "project" else "install-environment"),
                interpreter=interpreter,
                cwd=package_root,
                work_directory=runtime_root,
                timeout_seconds=package.config.resolution.timeout_seconds,
            )
            install_stage = "install-project" if final_plan.kind == "project" else "install-environment"
            if install.plan_digest != final_plan.digest or (isinstance(install, InstallFailure) and install.stage != install_stage):
                cleanup_temporary_directory(temporary_directory)
                return failed(invariant(install_stage))
            if isinstance(install, InstallFailure):
                cleanup_temporary_directory(temporary_directory)
                return failed(install)
            if not isinstance(install, InstalledResolution):
                raise TypeError("uv install returned an unsupported outcome")
            graph = self._uv.inspect_environment(
                interpreter=interpreter,
                cwd=package_root,
                timeout_seconds=package.config.resolution.timeout_seconds,
            )
            if isinstance(graph, OperationFailureResult):
                cleanup_temporary_directory(temporary_directory)
                if graph.stage != "inspect":
                    return failed(invariant("inspect"))
                return failed(graph)

            installed = {node.name: node.version for node in graph.nodes}
            expected = {
                item.name: item.version
                for item in final_plan.packages
                if item.version is not None
            }
            expected_names = set(expected) | {package.name}
            if set(installed) != expected_names or any(
                installed.get(name) != version for name, version in expected.items()
            ):
                cleanup_temporary_directory(temporary_directory)
                return failed(
                    OperationFailureResult(
                        stage="inspect-project-plan" if final_plan.kind == "project" else "inspect-environment-plan",
                        failure=StructuredOperationFailure(fact=InstalledGraphMismatchFact(), terminal=None),
                    )
                )
            active_ids = set(cell.active_declaration_ids)
            managed_names = tuple(
                sorted(
                    {
                        declaration.name
                        for declaration in package.declarations
                        if declaration.managed
                        and declaration.declaration_id in active_ids
                    }
                )
            )
            missing = tuple(name for name in managed_names if name not in installed)
            if missing:
                cleanup_temporary_directory(temporary_directory)
                return failed(
                    OperationFailureResult(
                        stage="inspect-project-plan" if final_plan.kind == "project" else "inspect-environment-plan",
                        failure=StructuredOperationFailure(fact=InstalledGraphMismatchFact(), terminal=None),
                    )
                )
            actual_vector = tuple(
                VersionPin(name=name, version=installed[name]) for name in managed_names
            )
            if managed_vector is not None and actual_vector != managed_vector:
                cleanup_temporary_directory(temporary_directory)
                return failed(
                    OperationFailureResult(
                        stage="proposal-vector",
                        failure=StructuredOperationFailure(fact=ProposalVectorMismatchFact(), terminal=None),
                    )
                )
            policy_identity = execution_policy_identity(package.config)
            fixed_declaration_ids = tuple(
                sorted(
                    declaration.declaration_id
                    for declaration in package.declarations
                    if not declaration.managed
                    and declaration.declaration_id in active_ids
                )
            )
            environment_identity = EnvironmentIdentity.from_plans(
                attempt_id=attempt.attempt_id,
                project_plan=project_outcome,
                environment_plan=environment_outcome,
                graph=graph.nodes,
            )
            harness_baseline = (
                HarnessBaseline.from_evidence(
                    cell=cell,
                    declaration_ids=attempt.identity.harness_declaration_ids,
                    observations=(
                        environment_outcome.direct_harness if environment_outcome else ()
                    ),
                )
                if isinstance(resolution, HighestResolution)
                else resolution.harness_baseline
            )
            proposal = Proposal(
                proposal_id=environment_identity.digest,
                attempt_id=attempt.attempt_id,
                snapshot_digest=snapshot.identity.digest,
                cell=cell,
                managed_vector=actual_vector,
                fixed_declaration_ids=fixed_declaration_ids,
                resolved_graph=graph.nodes,
                project_plan_digest=project_outcome.semantic_digest,
                environment_plan_digest=environment_plan_digest,
                policy_identity=policy_identity,
                interpreter=interpreter_result.interpreter,
            )
            return PreparedEnvironment(
                attempt=attempt,
                proposal=proposal,
                source_plan=source_plan,
                proposal_root=proposal_root,
                package_root=package_root,
                environment_root=environment_root,
                interpreter=interpreter,
                project_plan=project_outcome,
                environment_plan=environment_outcome,
                environment_identity=environment_identity,
                harness_baseline=harness_baseline,
                selected_candidates=(resolution.selection if isinstance(resolution, ExactSelection) else None),
                temporary_directory=temporary_directory,
            )
        except Exception as error:
            cleanup_temporary_directory(temporary_directory)
            if isinstance(error, (ConfigurationError, InfrastructureError)):
                raise
            raise InfrastructureError("environment preparation failed", detail=str(error)) from error

    def _resolve_once(
        self,
        *,
        key: tuple[str, str],
        resolve: Callable[[], ResolutionOutcome],
    ) -> ResolutionOutcome:
        with self._plan_lock:
            existing = self._plans.get(key)
            if existing is not None:
                return existing
            outcome = resolve()
            self._plans[key] = outcome
            return outcome

    @staticmethod
    def _harness_for_resolution(
        *,
        package: PackagePlan,
        cell: Cell,
        resolution: ResolutionRequest,
        source_plan: SourcePlan,
        project_plan: ResolutionPlan,
    ) -> tuple[HarnessResolutionRequirement, ...]:
        if isinstance(resolution, HighestResolution):
            return original_harness(package.harness_requirements, cell)
        return relax_harness(
            package.harness_requirements,
            resolution.harness_baseline,
            project_plan=project_plan,
            source_plan=source_plan,
        ).requirements

    @staticmethod
    def _project_graph_is_exact(
        project: ResolutionPlan,
        environment: ResolutionPlan,
    ) -> bool:
        environment_packages = {item.name: item for item in environment.packages}
        return all(
            (resolved := environment_packages.get(item.name)) is not None
            and resolved.version == item.version
            and resolved.source == item.source
            and resolved.selected_artifact == item.selected_artifact
            for item in project.packages
        ) and all(
            (observation.satisfied_by == "PROJECT_GRAPH")
            == (observation.name in {item.name for item in project.packages})
            for observation in environment.direct_harness
        )

    @staticmethod
    def _artifact_policy_failure(
        plan: ResolutionPlan,
        *,
        policy: Literal["wheel", "sdist", "any"],
    ) -> OperationFailureResult | None:
        allowed = {"wheel", "sdist"} if policy == "any" else {policy}
        for package in plan.packages:
            if package.source.kind != "registry":
                continue
            if package.available_artifacts and all(
                artifact.kind in allowed
                for artifact in package.available_artifacts
            ):
                continue
            return OperationFailureResult(
                stage="resolve-project" if plan.kind == "project" else "resolve-environment",
                process=plan.process,
                failure=StructuredOperationFailure(
                    fact=ArtifactPolicyMismatchFact(), terminal=execution_terminal(plan.process),
                ),
            )
        return None

    @staticmethod
    def _managed_source_failure(
        *,
        package: PackagePlan,
        cell: Cell,
        source_plan: SourcePlan,
        resolution: ResolutionRequest,
        plan: ResolutionPlan,
    ) -> OperationFailureResult | None:
        active_ids = set(cell.active_declaration_ids)
        managed_names = {
            declaration.name
            for declaration in package.declarations
            if declaration.managed and declaration.declaration_id in active_ids
        }
        dual_dependencies = tuple(
            dependency
            for dependency in source_plan.registry_routed_workspace_dependencies()
            if dependency in managed_names
        )
        if not dual_dependencies:
            return None
        resolved = {item.name: item for item in plan.packages}
        selected = (
            {item.dependency: item for item in resolution.selection}
            if isinstance(resolution, ExactSelection)
            else {}
        )
        for name in dual_dependencies:
            item = resolved.get(name)
            if item is None or item.source.kind in {"path", "workspace"}:
                return OperationFailureResult(
                    stage="resolve-project",
                    process=plan.process,
                    failure=StructuredOperationFailure(
                        fact=ManagedSourceLeakageFact(), terminal=execution_terminal(plan.process),
                    ),
                )
            requested = selected.get(name)
            if requested is None and (
                not EnvironmentFactory._registry_source_matches(
                    actual=item.source,
                    expected=source_plan.source_for(name),
                )
                or not item.available_artifacts
            ):
                return OperationFailureResult(
                    stage="resolve-project",
                    process=plan.process,
                    failure=StructuredOperationFailure(
                        fact=ManagedSourceMismatchFact(), terminal=execution_terminal(plan.process),
                    ),
                )
            if requested is not None and (
                item.source.kind != "url"
                or item.version != requested.version
                or item.selected_artifact is None
                or item.selected_artifact.filename != requested.artifact.filename
                or item.selected_artifact.locator != requested.artifact.locator
                or item.selected_artifact.content_hash
                != requested.artifact.content_hash
            ):
                return OperationFailureResult(
                    stage="resolve-project",
                    process=plan.process,
                    failure=StructuredOperationFailure(
                        fact=ManagedSourceMismatchFact(), terminal=execution_terminal(plan.process),
                    ),
                )
        return None

    @staticmethod
    def _registry_source_matches(
        *,
        actual: SourceIdentity,
        expected: SourceIdentity,
    ) -> bool:
        if actual.kind != "registry" or expected.kind != "registry":
            return False

        def canonical_locator(value: str | None) -> str:
            locator = value or "https://pypi.org/simple"
            parsed = urlsplit(locator)
            return urlunsplit(
                (
                    parsed.scheme.lower(),
                    parsed.netloc.lower(),
                    parsed.path.rstrip("/"),
                    "",
                    "",
                )
            )

        return canonical_locator(actual.locator) == canonical_locator(expected.locator)

    @staticmethod
    def _selection_vector(
        selection: tuple[SelectedCandidate, ...],
    ) -> tuple[VersionPin, ...]:
        names = tuple(item.dependency for item in selection)
        if names != tuple(sorted(set(names))):
            raise ConfigurationError(
                "artifact selection dependencies must be sorted and unique"
            )
        return tuple(
            VersionPin(name=item.dependency, version=item.version) for item in selection
        )

    @staticmethod
    def _attempt(
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: ResolutionRequest,
        managed_vector: tuple[VersionPin, ...] | None,
        context: ResolutionContext,
        source_plan: SourcePlan,
    ) -> Attempt:
        requested_resolution: Literal["highest", "lowest-direct", "exact-vector"]
        if isinstance(resolution, ExactSelection):
            requested_resolution = "exact-vector"
        elif isinstance(resolution, HighestResolution):
            requested_resolution = "highest"
        else:
            requested_resolution = "lowest-direct"
        plan_identity = source_plan.identity
        harness_declaration_ids = tuple(
            sorted(
                item.declaration_id
                for item in active_harness_requirements(
                    package.harness_requirements, cell
                )
            )
        )
        if isinstance(resolution, (ExactSelection, LowestDirectResolution)):
            baseline = resolution.harness_baseline
            if baseline.cell != cell:
                raise ConfigurationError("harness baseline must match the requested cell")
            if baseline.declaration_ids != harness_declaration_ids:
                raise ConfigurationError("harness baseline must match active declarations")
            if not harness_declaration_ids and baseline.observations:
                raise ConfigurationError("empty harness baseline must have no observations")
        baseline_digest = (
            resolution.harness_baseline.digest
            if isinstance(resolution, (ExactSelection, LowestDirectResolution))
            else None
        )
        selected_digest = (
            selected_candidate_evidence_digest(resolution.selection)
            if isinstance(resolution, ExactSelection)
            else None
        )
        return Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.identity.digest,
                cell=cell,
                requested_resolution=requested_resolution,
                requested_managed_vector=managed_vector,
                active_declaration_ids=cell.active_declaration_ids,
                source_plan_identity=plan_identity,
                execution_policy_identity=execution_policy_identity(package.config),
                resolution_context_digest=context.digest,
                harness_policy_identity=(
                    "original-harness-v1"
                    if isinstance(resolution, HighestResolution)
                    else "harness-relaxation-v1"
                ),
                harness_declaration_ids=harness_declaration_ids,
                harness_baseline_digest=baseline_digest,
                selected_candidate_evidence_digest=selected_digest,
            )
        )

    @staticmethod
    def _materialize_managed_vector(
        *,
        package: PackagePlan,
        cell: Cell,
        package_root: Path,
        managed_vector: tuple[VersionPin, ...] | None,
    ) -> None:
        if managed_vector is None:
            return
        requested = {pin.name: pin.version for pin in managed_vector}
        if len(requested) != len(managed_vector):
            raise ConfigurationError("managed vector dependencies must be unique")
        active_ids = set(cell.active_declaration_ids)
        declarations = tuple(
            declaration
            for declaration in package.declarations
            if declaration.managed and declaration.declaration_id in active_ids
        )
        expected = {declaration.name for declaration in declarations}
        if set(requested) != expected:
            raise ConfigurationError(
                "managed vector must exactly cover active managed dependencies"
            )

        pyproject = package_root / "pyproject.toml"
        document = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
        for declaration in declarations:
            try:
                project = document["project"]
                if not isinstance(project, Mapping):
                    raise TypeError("project metadata is not a table")
                if declaration.location == "base":
                    value = project["dependencies"]
                else:
                    assert declaration.extra is not None
                    extras = project["optional-dependencies"]
                    if not isinstance(extras, Mapping):
                        raise TypeError("optional-dependencies metadata is not a table")
                    value = extras[declaration.extra]
            except (KeyError, TypeError) as error:
                raise ConfigurationError(
                    f"dependency location has drifted: {declaration.declaration_id}"
                ) from error
            if not isinstance(value, Array):
                raise ConfigurationError("dependency metadata is not a TOML array")
            values = tuple(str(item) for item in value)
            try:
                index = values.index(declaration.raw)
            except ValueError as error:
                raise ConfigurationError(
                    f"dependency declaration has drifted: {declaration.declaration_id}"
                ) from error
            requirement = Requirement(declaration.raw)
            retained = tuple(
                sorted(
                    str(specifier)
                    for specifier in requirement.specifier
                    if specifier.operator in {"<", "<=", "!="}
                )
            )
            extras = (
                f"[{','.join(sorted(requirement.extras))}]"
                if requirement.extras
                else ""
            )
            exact = ",".join((f"=={requested[declaration.name]}", *retained))
            marker = f"; {requirement.marker}" if requirement.marker else ""
            value[index] = f"{requirement.name}{extras}{exact}{marker}"
        pyproject.write_text(tomlkit.dumps(document), encoding="utf-8")

    @staticmethod
    def _interpreter(environment: Path) -> Path:
        if os.name == "nt":
            return environment / "Scripts" / "python.exe"
        return environment / "bin" / "python"

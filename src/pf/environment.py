from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from packaging.requirements import Requirement
import tomlkit
from tomlkit.items import Array

from pf.errors import ConfigurationError
from pf.harness import active_harness_requirements, original_harness, relax_harness
from pf.policy import evaluation_policy_identity
from pf.resolution import (
    EnvironmentIdentity,
    InstalledResolution,
    InstallFailure,
    InstallOutcome,
    ResolutionContext,
    ResolutionIndeterminate,
    ResolutionOutcome,
    ResolutionPlan,
    ResolutionRunContext,
    ResolutionUnsat,
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
    FailureDetail,
    ToolFailure,
    ToolOutcome,
)
from pf.schemas.project import (
    Cell,
    HarnessBaseline,
    HarnessResolutionRequirement,
    PackagePlan,
    Proposal,
    ResolutionSourceMode,
    SelectedCandidate,
    SourceIdentity,
    SourcePlan,
    VersionPin,
    package_source_plan,
    source_plan_identity,
    selected_candidate_evidence_digest,
)
from pf.snapshot import SourceSnapshot


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
        cell: Cell,
        resolution: ResolutionRequest,
        context: ResolutionContext,
        request_digest: str,
        work_directory: Path,
        allow_prereleases: bool,
        timeout_seconds: int | None,
        source_plan: SourcePlan,
    ) -> ResolutionOutcome: ...

    def resolve_environment(
        self,
        *,
        package: Path,
        package_name: str,
        cell: Cell,
        resolution: ResolutionRequest,
        context: ResolutionContext,
        request_digest: str,
        project_plan: ResolutionPlan,
        harness: tuple[HarnessResolutionRequirement, ...],
        work_directory: Path,
        allow_prereleases: bool,
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
    ) -> ToolOutcome: ...

    def install_resolution(
        self,
        *,
        plan: ResolutionPlan,
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
        proposal_root: Path,
        package_root: Path,
        environment_root: Path,
        interpreter: Path,
        project_plan: ResolutionPlan,
        environment_plan: ResolutionPlan,
        environment_identity: EnvironmentIdentity,
        harness_baseline: HarnessBaseline,
        temporary_directory: tempfile.TemporaryDirectory[str],
    ) -> None:
        self.attempt = attempt
        self.proposal = proposal
        self.proposal_root = proposal_root
        self.package_root = package_root
        self.environment_root = environment_root
        self.interpreter = interpreter
        self.project_plan = project_plan
        self.environment_plan = environment_plan
        self.environment_identity = environment_identity
        self.harness_baseline = harness_baseline
        self._temporary_directory = temporary_directory
        self.tested = False

    def mark_tested(self) -> None:
        self.tested = True

    def close(self) -> None:
        self._temporary_directory.cleanup()


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
        source_mode: ResolutionSourceMode,
    ) -> PreparedEnvironment | PrepareFailure:
        source_plan = package_source_plan(package, source_mode)
        run = self._uv.resolution_run_context(
            root=Path.cwd(),
            timeout_seconds=package.config.resolve_timeout,
        )
        if isinstance(run, ToolFailure):
            raise ConfigurationError("uv resolver protocol could not be established")
        context = ResolutionContext.from_inputs(
            run=run,
            cell=cell,
            source_policy_identity=self._source_policy_identity(source_plan),
            allow_prereleases=package.config.allow_prereleases,
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

        def failed(failure: ToolFailure) -> PrepareFailure:
            return PrepareFailure(
                attempt=attempt,
                failure=failure,
                project_plan_digest=project_plan_digest,
                environment_plan_digest=environment_plan_digest,
            )

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
            project_request = self._request_digest(
                kind="project",
                package=package,
                snapshot=snapshot,
                cell=cell,
                resolution=resolution,
                context=context,
                project_plan=None,
                harness=(),
                source_plan=source_plan,
            )
            emit_cell_stage(self._events, cell, "resolving project")
            project_outcome = self._resolve_once(
                key=("project", project_request),
                resolve=lambda: self._uv.resolve_project(
                    package=package_root,
                    package_name=package.name,
                    cell=cell,
                    resolution=resolution,
                    context=context,
                    request_digest=project_request,
                    work_directory=runtime_root,
                    allow_prereleases=package.config.allow_prereleases,
                    timeout_seconds=package.config.resolve_timeout,
                    source_plan=source_plan,
                ),
            )
            if not isinstance(project_outcome, ResolutionPlan):
                temporary_directory.cleanup()
                return failed(self._resolution_failure(project_outcome))
            project_plan_digest = project_outcome.semantic_digest
            source_failure = self._managed_source_failure(
                package=package,
                cell=cell,
                source_mode=source_mode,
                resolution=resolution,
                plan=project_outcome,
            )
            if source_failure is not None:
                temporary_directory.cleanup()
                return failed(source_failure)

            harness = self._harness_for_resolution(
                package=package,
                cell=cell,
                resolution=resolution,
                source_mode=source_mode,
            )
            environment_request = self._request_digest(
                kind="environment",
                package=package,
                snapshot=snapshot,
                cell=cell,
                resolution=resolution,
                context=context,
                project_plan=project_outcome,
                harness=harness,
                source_plan=source_plan,
            )
            emit_cell_stage(self._events, cell, "resolving environment")
            environment_outcome = self._resolve_once(
                key=("environment", environment_request),
                resolve=lambda: self._uv.resolve_environment(
                    package=package_root,
                    package_name=package.name,
                    cell=cell,
                    resolution=resolution,
                    context=context,
                    request_digest=environment_request,
                    project_plan=project_outcome,
                    harness=harness,
                    work_directory=runtime_root,
                    allow_prereleases=package.config.allow_prereleases,
                    timeout_seconds=package.config.resolve_timeout,
                    source_plan=source_plan,
                ),
            )
            if not isinstance(environment_outcome, ResolutionPlan):
                temporary_directory.cleanup()
                return failed(self._resolution_failure(environment_outcome))
            environment_plan_digest = environment_outcome.semantic_digest
            if not self._project_graph_is_exact(project_outcome, environment_outcome):
                temporary_directory.cleanup()
                return failed(
                    ToolFailure(
                        cause="INTERNAL_INVARIANT",
                        stage="resolve-environment",
                        process=None,
                        summary_code="managed-source-mismatch",
                        detail=FailureDetail(
                            code="managed-source-mismatch",
                            message=(
                                "the environment resolution did not preserve the "
                                "project source selection"
                            ),
                        ),
                    )
                )

            emit_cell_stage(self._events, cell, "preparing environment")
            create = self._uv.create_environment(
                environment=environment_root,
                python_minor=cell.python_minor,
                cwd=proposal_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if isinstance(create, ToolFailure):
                temporary_directory.cleanup()
                return failed(create)
            interpreter = self._interpreter(environment_root)
            interpreter_result = self._uv.inspect_interpreter(
                interpreter=interpreter,
                cwd=package_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if not isinstance(interpreter_result, InterpreterSuccess):
                temporary_directory.cleanup()
                return failed(interpreter_result)
            if (
                interpreter_result.interpreter.implementation != "cpython"
                or not interpreter_result.interpreter.version.startswith(
                    f"{cell.python_minor}."
                )
            ):
                temporary_directory.cleanup()
                return failed(
                    ToolFailure(
                        cause="ENVIRONMENT_FAILURE",
                        stage="inspect-interpreter",
                        process=interpreter_result.process,
                    )
                )
            emit_cell_stage(self._events, cell, "installing environment plan")
            install = self._uv.install_resolution(
                plan=environment_outcome,
                interpreter=interpreter,
                cwd=package_root,
                work_directory=runtime_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if isinstance(install, InstallFailure):
                temporary_directory.cleanup()
                return failed(
                    ToolFailure(
                        cause=install.cause,
                        stage=install.stage,
                        process=install.process,
                        summary_code=install.summary_code,
                    )
                )
            if not isinstance(install, InstalledResolution):
                raise TypeError("uv install returned an unsupported outcome")
            graph = self._uv.inspect_environment(
                interpreter=interpreter,
                cwd=package_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if isinstance(graph, ToolFailure):
                temporary_directory.cleanup()
                return failed(graph)

            installed = {node.name: node.version for node in graph.nodes}
            expected = {
                item.name: item.version
                for item in environment_outcome.packages
                if item.version is not None
            }
            expected_names = set(expected) | {package.name}
            if set(installed) != expected_names or any(
                installed.get(name) != version for name, version in expected.items()
            ):
                temporary_directory.cleanup()
                return failed(
                    ToolFailure(
                        cause="INTERNAL_INVARIANT",
                        stage="inspect-environment-plan",
                        process=graph.process,
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
                temporary_directory.cleanup()
                return failed(
                    ToolFailure(
                        cause="INTERNAL_INVARIANT",
                        stage="inspect",
                        process=graph.process,
                    )
                )
            actual_vector = tuple(
                VersionPin(name=name, version=installed[name]) for name in managed_names
            )
            if managed_vector is not None and actual_vector != managed_vector:
                temporary_directory.cleanup()
                return failed(
                    ToolFailure(
                        cause="INTERNAL_INVARIANT",
                        stage="proposal-vector",
                        process=graph.process,
                    )
                )
            policy_identity = evaluation_policy_identity(package.config)
            fixed_declaration_ids = tuple(
                sorted(
                    declaration.declaration_id
                    for declaration in package.declarations
                    if not declaration.managed
                    and declaration.declaration_id in active_ids
                )
            )
            environment_identity = EnvironmentIdentity.from_plans(
                project_plan=project_outcome,
                environment_plan=environment_outcome,
                graph=graph.nodes,
            )
            active_harness_ids = tuple(
                item.declaration.declaration_id for item in harness
            )
            harness_baseline = (
                HarnessBaseline.from_evidence(
                    cell=cell,
                    declaration_ids=tuple(sorted(active_harness_ids)),
                    selections=environment_outcome.direct_harness,
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
                environment_plan_digest=environment_outcome.semantic_digest,
                policy_identity=policy_identity,
                interpreter=interpreter_result.interpreter,
            )
            return PreparedEnvironment(
                attempt=attempt,
                proposal=proposal,
                proposal_root=proposal_root,
                package_root=package_root,
                environment_root=environment_root,
                interpreter=interpreter,
                project_plan=project_outcome,
                environment_plan=environment_outcome,
                environment_identity=environment_identity,
                harness_baseline=harness_baseline,
                temporary_directory=temporary_directory,
            )
        except Exception:
            temporary_directory.cleanup()
            raise

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
    def _source_policy_identity(source_plan: SourcePlan) -> str:
        payload = json.dumps(
            source_plan.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(b"pf:source-policy:v1\0" + payload).hexdigest()

    @staticmethod
    def _request_digest(
        *,
        kind: Literal["project", "environment"],
        package: PackagePlan,
        snapshot: SourceSnapshot,
        cell: Cell,
        resolution: ResolutionRequest,
        context: ResolutionContext,
        project_plan: ResolutionPlan | None,
        harness: tuple[HarnessResolutionRequirement, ...],
        source_plan: SourcePlan,
    ) -> str:
        payload = {
            "kind": kind,
            "package": package.name,
            "snapshot_digest": snapshot.identity.digest,
            "cell": cell.model_dump(mode="json"),
            "resolution": resolution.kind,
            "selection": (
                [item.model_dump(mode="json") for item in resolution.selection]
                if isinstance(resolution, ExactSelection)
                else None
            ),
            "baseline_digest": (
                resolution.harness_baseline.digest
                if isinstance(resolution, (ExactSelection, LowestDirectResolution))
                else None
            ),
            "context": context.digest,
            "project_plan": (
                project_plan.semantic_digest if project_plan is not None else None
            ),
            "harness": [item.model_dump(mode="json") for item in harness],
            "source_plan": source_plan.model_dump(mode="json"),
        }
        return hashlib.sha256(
            b"pf:resolution-request:v1\0"
            + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _harness_for_resolution(
        *,
        package: PackagePlan,
        cell: Cell,
        resolution: ResolutionRequest,
        source_mode: ResolutionSourceMode,
    ) -> tuple[HarnessResolutionRequirement, ...]:
        if isinstance(resolution, HighestResolution):
            return original_harness(package, cell, source_mode=source_mode)
        if resolution.harness_baseline.cell != cell:
            raise ConfigurationError("harness baseline must match the requested cell")
        return relax_harness(
            package,
            resolution.harness_baseline,
            source_mode=source_mode,
        ).requirements

    @staticmethod
    def _resolution_failure(
        outcome: ResolutionUnsat | ResolutionIndeterminate,
    ) -> ToolFailure:
        if isinstance(outcome, ResolutionUnsat):
            return ToolFailure(
                cause=(
                    "RESOLUTION_CONFLICT"
                    if outcome.stage == "resolve-project"
                    else "HARNESS_CONFLICT"
                ),
                stage=outcome.stage,
                process=outcome.process,
                summary_code=outcome.proof_code,
            )
        return ToolFailure(
            cause=outcome.cause,
            stage=outcome.stage,
            process=outcome.process,
            summary_code=outcome.summary_code,
        )

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
        )

    @staticmethod
    def _managed_source_failure(
        *,
        package: PackagePlan,
        cell: Cell,
        source_mode: ResolutionSourceMode,
        resolution: ResolutionRequest,
        plan: ResolutionPlan,
    ) -> ToolFailure | None:
        if source_mode != "SEARCH":
            return None
        active_ids = set(cell.active_declaration_ids)
        managed_names = {
            declaration.name
            for declaration in package.declarations
            if declaration.managed and declaration.declaration_id in active_ids
        }
        dual_routes = {
            route.dependency: route
            for route in package.source_routes
            if route.dependency in managed_names
            and route.development_source.kind == "workspace"
            and route.search_source.kind == "registry"
        }
        if not dual_routes:
            return None
        resolved = {item.name: item for item in plan.packages}
        selected = (
            {item.dependency: item for item in resolution.selection}
            if isinstance(resolution, ExactSelection)
            else {}
        )
        for name, route in dual_routes.items():
            item = resolved.get(name)
            if item is None or item.source.kind in {"path", "workspace"}:
                return ToolFailure(
                    cause="INTERNAL_INVARIANT",
                    stage="resolve-project",
                    process=None,
                    summary_code="managed-source-leakage",
                    detail=FailureDetail(
                        code="managed-source-leakage",
                        message=(
                            "a managed workspace dependency resolved from a local "
                            "source in SEARCH mode"
                        ),
                    ),
                )
            requested = selected.get(name)
            if requested is None and (
                not EnvironmentFactory._registry_source_matches(
                    actual=item.source,
                    expected=route.search_source,
                )
                or not item.available_artifacts
            ):
                return ToolFailure(
                    cause="INTERNAL_INVARIANT",
                    stage="resolve-project",
                    process=None,
                    summary_code="managed-source-mismatch",
                    detail=FailureDetail(
                        code="managed-source-mismatch",
                        message=(
                            "a managed workspace dependency did not resolve to the "
                            "expected registry artifact"
                        ),
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
                return ToolFailure(
                    cause="INTERNAL_INVARIANT",
                    stage="resolve-project",
                    process=None,
                    summary_code="managed-source-mismatch",
                    detail=FailureDetail(
                        code="managed-source-mismatch",
                        message=(
                            "a managed workspace dependency did not match the "
                            "requested registry artifact"
                        ),
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
        plan_identity = source_plan_identity(source_plan)
        harness_declaration_ids = tuple(
            sorted(
                item.declaration_id
                for item in active_harness_requirements(
                    package.harness_requirements, cell
                )
            )
        )
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
                identity_version="attempt-v2",
                source_snapshot_digest=snapshot.identity.digest,
                cell=cell,
                requested_resolution=requested_resolution,
                requested_managed_vector=managed_vector,
                active_declaration_ids=cell.active_declaration_ids,
                source_plan_identity=plan_identity,
                evaluation_policy_identity=evaluation_policy_identity(package.config),
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

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pf.baseline import HighestVersionVerifier
from pf.candidates import CandidateBuilder
from pf.coordinate_search import CoordinateSearch
from pf.environment import (
    EnvironmentFactory,
    ExactSelection,
    LowestDirectResolution,
    ResolutionRequest,
)
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
from pf.resolution import (
    InstallFailure,
    InstallOutcome,
    InstalledResolution,
    NativeResolutionPlan,
    ResolutionArtifact,
    ResolutionContext,
    ResolutionOutcome,
    ResolutionPackage,
    ResolutionPlan,
    ResolutionRunContext,
)
from pf.schemas.evaluation import (
    GraphSuccess,
    InterpreterSuccess,
    NormalExit,
    ProcessResult,
    RuntimeWitnessOutcome,
    RuntimeWitnessPlan,
    RuntimeWitnessResult,
    StageProgress,
    ToolFailure,
    ToolSuccess,
    TyCheck,
    VerifierPass,
    VerifierRequest,
    VerifierRun,
)
from pf.schemas.project import (
    AvailableArtifact,
    AvailableCandidate,
    Cell,
    HarnessResolutionRequirement,
    HarnessSelection,
    InterpreterIdentity,
    PackagePlan,
    ResolvedNode,
    SelectedCandidate,
    SourceIdentity,
    SourcePlan,
    VersionPin,
)
from pf.search import (
    SearchActivityConsumer,
    SearchCoordinator,
    SearchDiagnosticConsumer,
)
from pf.snapshot import SnapshotBuilder, SourceSnapshot


def successful_process(*, exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


@dataclass(frozen=True)
class EvaluationProject:
    package: PackagePlan
    snapshot: SourceSnapshot
    source_plan: SourcePlan


def evaluation_project(
    root: Path,
    *,
    dependency: str | None = "demo-dep",
    source: str = "",
) -> EvaluationProject:
    root.mkdir(parents=True, exist_ok=True)
    dependencies = f'dependencies = ["{dependency}"]\n' if dependency else ""
    (root / "pyproject.toml").write_text(
        f"""
[project]
name = "demo"
version = "0.1.0"
{dependencies}
[dependency-groups]
test = []

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    if source:
        (root / "demo.py").write_text(source, encoding="utf-8")
    from pf.project import ProjectLoader

    package = ProjectLoader().load(root=root).target
    snapshot = SnapshotBuilder.without_processes().build(root)
    return EvaluationProject(
        package=package,
        snapshot=snapshot,
        source_plan=SourcePlan.for_package(package, "SEARCH"),
    )


def available_artifact(dependency: str, version: str) -> AvailableArtifact:
    return AvailableArtifact(
        filename=f"{dependency}-{version}-py3-none-any.whl",
        kind="wheel",
        content_hash=f"sha256:{version[-1] * 64}",
        locator=f"https://files.example/{dependency}-{version}-py3-none-any.whl",
        python_minors=("3.10",),
        targets=("x86_64-unknown-linux-gnu",),
    )


def selected_candidate(dependency: str, version: str) -> SelectedCandidate:
    return SelectedCandidate(
        dependency=dependency,
        version=version,
        artifact=available_artifact(dependency, version),
    )


class ScriptedUv:
    def __init__(
        self,
        *,
        highest: tuple[VersionPin, ...] = (
            VersionPin(name="demo-dep", version="3"),
        ),
        lowest: tuple[VersionPin, ...] | None = None,
        install_failure: ToolFailure | None = None,
    ) -> None:
        self.highest = highest
        self.lowest = lowest if lowest is not None else highest
        self.install_failure = install_failure
        self.install_failures_by_vector: dict[
            tuple[VersionPin, ...], ToolFailure
        ] = {}
        self.resolutions: list[str] = []
        self.resolution_root_states: list[tuple[str, tuple[bool, ...]]] = []
        self.environment_roots: list[Path] = []
        self.install_vectors: list[tuple[VersionPin, ...]] = []
        self.exact_selections: list[tuple[SelectedCandidate, ...]] = []
        self._plans_by_interpreter: dict[Path, ResolutionPlan] = {}
        self._vectors_by_root: dict[Path, tuple[VersionPin, ...]] = {}

    def resolution_run_context(self, **kwargs: object) -> ResolutionRunContext:
        return ResolutionRunContext(
            uv_version="0.12.5",
            release_cutoff="2026-08-23T00:00:00+00:00",
        )

    def resolve_project(self, **kwargs: object) -> ResolutionOutcome:
        resolution = cast(ResolutionRequest, kwargs["resolution"])
        self.resolutions.append(resolution.kind)
        if isinstance(resolution, ExactSelection):
            self.exact_selections.append(resolution.selection)
        self.resolution_root_states.append(
            (resolution.kind, tuple(root.exists() for root in self.environment_roots))
        )
        return self._plan("project", resolution=resolution, kwargs=kwargs)

    def resolve_environment(self, **kwargs: object) -> ResolutionOutcome:
        resolution = cast(ResolutionRequest, kwargs["resolution"])
        project = cast(ResolutionPlan, kwargs["project_plan"])
        harness = cast(tuple[HarnessResolutionRequirement, ...], kwargs["harness"])
        source_plan = cast(SourcePlan, kwargs["source_plan"])
        packages = list(project.packages)
        selections: list[HarnessSelection] = []
        for requirement in harness:
            name = requirement.declaration.name
            package = next((item for item in packages if item.name == name), None)
            if package is None:
                version = requirement.ceiling or "8.4"
                artifact = self._resolution_package(
                    selected_candidate(name, version)
                ).selected_artifact
                assert artifact is not None
                package = ResolutionPackage(
                    name=name,
                    version=version,
                    source=source_plan.source_for(name),
                    available_artifacts=(artifact,),
                    selected_artifact=artifact,
                )
                packages.append(package)
            assert package.version is not None
            selected_artifact = package.selected_artifact
            harness_artifact = (
                None
                if selected_artifact is None
                else AvailableArtifact(
                    filename=selected_artifact.filename,
                    kind=selected_artifact.kind,
                    content_hash=selected_artifact.content_hash,
                    locator=selected_artifact.locator,
                )
            )
            selections.append(
                HarnessSelection(
                    name=name,
                    version=package.version,
                    source=package.source,
                    selected_artifact=harness_artifact,
                    ceiling_bound=requirement.ceiling is not None
                    or package.source.kind == "registry",
                )
            )
        return self._plan(
            "environment",
            resolution=resolution,
            kwargs=kwargs,
            packages=tuple(sorted(packages, key=lambda item: item.name)),
            direct_harness=tuple(sorted(selections, key=lambda item: item.name)),
        )

    def _plan(
        self,
        kind: Literal["project", "environment"],
        *,
        resolution: ResolutionRequest,
        kwargs: dict[str, object],
        packages: tuple[ResolutionPackage, ...] | None = None,
        direct_harness: tuple[HarnessSelection, ...] = (),
    ) -> ResolutionPlan:
        context = cast(ResolutionContext, kwargs["context"])
        request_digest = cast(str, kwargs["request_digest"])
        if packages is None:
            packages = self._packages(resolution)
        return ResolutionPlan.from_evidence(
            kind=kind,
            request_digest=request_digest,
            context=context,
            packages=packages,
            direct_harness=direct_harness,
            native=NativeResolutionPlan.from_content(
                'lock-version = "1.0"\ncreated-by = "uv"\npackages = []\n'
            ),
            process=successful_process(),
        )

    def _packages(
        self,
        resolution: ResolutionRequest,
    ) -> tuple[ResolutionPackage, ...]:
        if isinstance(resolution, ExactSelection):
            return tuple(self._resolution_package(item) for item in resolution.selection)
        vector = self.lowest if isinstance(resolution, LowestDirectResolution) else self.highest
        return tuple(
            self._resolution_package(selected_candidate(pin.name, pin.version))
            for pin in vector
        )

    @staticmethod
    def _resolution_package(candidate: SelectedCandidate) -> ResolutionPackage:
        artifact = candidate.artifact
        assert artifact.locator is not None
        resolved_artifact = ResolutionArtifact(
            filename=artifact.filename,
            kind=artifact.kind,
            locator=artifact.locator,
            content_hash=artifact.content_hash,
        )
        return ResolutionPackage(
            name=candidate.dependency,
            version=candidate.version,
            source=SourceIdentity(kind="registry"),
            available_artifacts=(resolved_artifact,),
            selected_artifact=resolved_artifact,
        )
    def create_environment(self, **kwargs: object) -> ToolSuccess:
        environment = cast(Path, kwargs["environment"])
        environment.mkdir(parents=True, exist_ok=True)
        self.environment_roots.append(environment)
        return ToolSuccess(stage="create-environment", process=successful_process())

    def install_resolution(self, **kwargs: object) -> InstallOutcome:
        plan = cast(ResolutionPlan, kwargs["plan"])
        interpreter = cast(Path, kwargs["interpreter"])
        cwd = cast(Path, kwargs["cwd"])
        self._plans_by_interpreter[interpreter] = plan
        vector = tuple(
            VersionPin(name=item.name, version=item.version)
            for item in plan.packages
            if item.version is not None
        )
        self._vectors_by_root[cwd] = vector
        self.install_vectors.append(vector)
        failure = self.install_failures_by_vector.get(vector, self.install_failure)
        if failure is not None:
            assert failure.process is not None
            return InstallFailure(
                cause=failure.cause,
                stage="install-environment",
                process=failure.process,
                plan_digest=plan.digest,
                summary_code=failure.summary_code,
            )
        return InstalledResolution(
            plan_digest=plan.digest,
            process=successful_process(),
        )

    def inspect_interpreter(self, **kwargs: object) -> InterpreterSuccess:
        return InterpreterSuccess(
            process=successful_process(),
            interpreter=InterpreterIdentity(
                implementation="cpython",
                version="3.10.18",
                abi="cpython-310-x86_64-linux-gnu",
            ),
        )

    def inspect_environment(self, **kwargs: object) -> GraphSuccess:
        interpreter = cast(Path, kwargs["interpreter"])
        plan = self._plans_by_interpreter[interpreter]
        nodes: tuple[ResolvedNode, ...] = (
            ResolvedNode(name="demo", version="0.1.0"),
            *(
                ResolvedNode(name=item.name, version=item.version)
                for item in plan.packages
                if item.version is not None
            ),
        )
        return GraphSuccess(
            process=successful_process(),
            nodes=tuple(sorted(nodes, key=lambda item: cast(ResolvedNode, item).name)),
        )

    def vector_for_interpreter(self, interpreter: Path) -> tuple[VersionPin, ...]:
        plan = self._plans_by_interpreter[interpreter]
        return tuple(
            VersionPin(name=item.name, version=item.version)
            for item in plan.packages
            if item.version is not None
        )

    def vector_for_root(self, root: Path) -> tuple[VersionPin, ...]:
        return self._vectors_by_root[root]


TyHandler = Callable[[tuple[VersionPin, ...], int], TyCheck | ToolFailure]
VerifierHandler = Callable[[tuple[VersionPin, ...], int], VerifierRun]
WitnessHandler = Callable[[tuple[VersionPin, ...], RuntimeWitnessPlan, int], RuntimeWitnessOutcome]


class ScriptedTy:
    def __init__(self, uv: ScriptedUv, handler: TyHandler | None = None) -> None:
        self._uv = uv
        self._handler = handler or (
            lambda vector, call: TyCheck(
                process=successful_process(),
                diagnostics=(),
            )
        )
        self.vectors: list[tuple[VersionPin, ...]] = []

    def check(self, **kwargs: object) -> TyCheck | ToolFailure:
        vector = self._uv.vector_for_interpreter(cast(Path, kwargs["interpreter"]))
        self.vectors.append(vector)
        return self._handler(vector, len(self.vectors))


class ScriptedVerifier:
    def __init__(
        self,
        uv: ScriptedUv,
        handler: VerifierHandler | None = None,
    ) -> None:
        self._uv = uv
        self._handler = handler or (
            lambda vector, call: VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
            )
        )
        self.vectors: list[tuple[VersionPin, ...]] = []

    def run(
        self,
        request: VerifierRequest,
        progress: Callable[[StageProgress | None], None] | None = None,
    ) -> VerifierRun:
        del progress
        vector = self._uv.vector_for_root(request.cwd)
        self.vectors.append(vector)
        return self._handler(vector, len(self.vectors))


class ScriptedWitnesses:
    def __init__(
        self,
        uv: ScriptedUv,
        handler: WitnessHandler | None = None,
    ) -> None:
        self._uv = uv
        self._handler = handler or (
            lambda vector, plan, call: RuntimeWitnessResult(
                status="NOT_APPLICABLE",
                plan=plan,
                process=successful_process(),
            )
        )
        self.calls: list[tuple[tuple[VersionPin, ...], RuntimeWitnessPlan]] = []

    def run(self, **kwargs: object) -> RuntimeWitnessOutcome:
        plan = cast(RuntimeWitnessPlan, kwargs["plan"])
        interpreter = cast(Path, kwargs["interpreter"])
        vector = self._uv.vector_for_interpreter(interpreter)
        self.calls.append((vector, plan))
        return self._handler(vector, plan, len(self.calls))


class ScriptedCandidates:
    def __init__(
        self,
        versions: tuple[str, ...] = ("1", "2", "3"),
        error: Exception | None = None,
    ) -> None:
        self.versions = versions
        self.error = error
        self.queries: list[tuple[str, SourceIdentity, Cell]] = []

    def query(self, **kwargs: object) -> tuple[AvailableCandidate, ...]:
        if self.error is not None:
            raise self.error
        dependency = cast(str, kwargs["dependency"])
        source = cast(SourceIdentity, kwargs["source"])
        cell = cast(Cell, kwargs["cell"])
        self.queries.append((dependency, source, cell))
        return tuple(
            AvailableCandidate(
                version=version,
                artifacts=(available_artifact(dependency, version),),
            )
            for version in self.versions
        )


@dataclass(frozen=True)
class EvaluationAssembly:
    uv: ScriptedUv
    ty: ScriptedTy
    verifier: ScriptedVerifier
    witnesses: ScriptedWitnesses
    candidates: ScriptedCandidates
    environments: EnvironmentFactory
    static: StaticEvaluator
    runtime: RuntimeEvaluator
    highest: HighestVersionVerifier
    candidate_builder: CandidateBuilder
    coordinate_search: CoordinateSearch
    coordinator: SearchCoordinator


def evaluation_assembly(
    *,
    highest: tuple[VersionPin, ...] = (
        VersionPin(name="demo-dep", version="3"),
    ),
    lowest: tuple[VersionPin, ...] | None = None,
    candidate_versions: tuple[str, ...] = ("1", "2", "3"),
    candidate_error: Exception | None = None,
    ty_handler: TyHandler | None = None,
    verifier_handler: VerifierHandler | None = None,
    witness_handler: WitnessHandler | None = None,
    install_failure: ToolFailure | None = None,
    diagnostics: SearchDiagnosticConsumer | None = None,
    events: SearchActivityConsumer | None = None,
) -> EvaluationAssembly:
    uv = ScriptedUv(
        highest=highest,
        lowest=lowest,
        install_failure=install_failure,
    )
    ty = ScriptedTy(uv, ty_handler)
    verifier = ScriptedVerifier(uv, verifier_handler)
    witnesses = ScriptedWitnesses(uv, witness_handler)
    candidates = ScriptedCandidates(candidate_versions, candidate_error)
    environments = EnvironmentFactory(uv, events=events)
    static = StaticEvaluator(ty, events=events)
    runtime = RuntimeEvaluator(
        static=static,
        verifier=verifier,
        witnesses=witnesses,
        events=events,
    )
    highest_verifier = HighestVersionVerifier(
        environments=environments,
        static=static,
        full=runtime,
    )
    candidate_builder = CandidateBuilder(candidates)
    coordinate_search = CoordinateSearch()
    coordinator = SearchCoordinator(
        environments=environments,
        candidates=candidate_builder,
        static=static,
        full=runtime,
        highest=highest_verifier,
        coordinate_search=coordinate_search,
        diagnostics=diagnostics,
        events=events,
    )
    return EvaluationAssembly(
        uv=uv,
        ty=ty,
        verifier=verifier,
        witnesses=witnesses,
        candidates=candidates,
        environments=environments,
        static=static,
        runtime=runtime,
        highest=highest_verifier,
        candidate_builder=candidate_builder,
        coordinate_search=coordinate_search,
        coordinator=coordinator,
    )

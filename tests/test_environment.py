from __future__ import annotations

import hashlib
from importlib.metadata import version as distribution_version
import json
from pathlib import Path
from typing import cast, Literal

from packaging.requirements import Requirement
import pytest
import tomli

from pf.environment import (
    EnvironmentFactory,
    ExactSelection,
    HighestResolution,
    LowestDirectResolution,
    PreparedEnvironment,
)
from pf.errors import ConfigurationError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.project import ProjectLoader
from pf.resolution import (
    InstalledResolution,
    InstallFailure,
    InstallOutcome,
    NativeResolutionPlan,
    ResolutionArtifact,
    ResolutionContext,
    ResolutionOutcome,
    ResolutionPackage,
    ResolutionPlan,
    ResolutionRunContext,
    ResolutionUnsat,
    environment_identity_digest,
)
from pf.schemas.config import EffectiveConfig, WorkspacePackage
from pf.schemas.evaluation import (
    AttemptFailureScope,
    CellStageEvent,
    FailureCause,
    GraphOutcome,
    GraphSuccess,
    InterpreterOutcome,
    InterpreterSuccess,
    PrepareFailure,
    ProcessResult,
    ToolFailure,
    ToolOutcome,
    ToolSuccess,
)
from pf.schemas.project import (
    AvailableArtifact,
    HarnessBaseline,
    HarnessSelection,
    HarnessResolutionRequirement,
    InterpreterIdentity,
    ResolvedNode,
    SelectedCandidate,
    SourceIdentity,
    SourcePlan,
    VersionPin,
)
from pf.snapshot import SnapshotBuilder


def successful_process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


def empty_harness_baseline(cell) -> HarnessBaseline:
    return HarnessBaseline.from_evidence(
        cell=cell,
        declaration_ids=(),
        selections=(),
    )


def exact_selection(cell, *pins: VersionPin) -> ExactSelection:
    return ExactSelection(
        tuple(
            SelectedCandidate(
                dependency=pin.name,
                version=pin.version,
                artifact=AvailableArtifact(
                    filename=f"{pin.name}-{pin.version}-py3-none-any.whl",
                    kind="wheel",
                    content_hash=f"sha256:{'a' * 64}",
                    locator=(
                        f"https://files.example/{pin.name}-{pin.version}"
                        "-py3-none-any.whl"
                    ),
                ),
            )
            for pin in pins
        ),
        harness_baseline=empty_harness_baseline(cell),
    )


class SuccessfulUv:
    def resolution_run_context(self, **kwargs: object) -> ResolutionRunContext:
        return ResolutionRunContext(
            uv_version="0.12.5",
            release_cutoff="2026-08-23T00:00:00+00:00",
        )

    def resolve_project(self, **kwargs: object) -> ResolutionOutcome:
        resolution = kwargs["resolution"]
        assert isinstance(
            resolution, (HighestResolution, LowestDirectResolution, ExactSelection)
        )
        packages = (
            tuple(
                ResolutionPackage(
                    name=item.dependency,
                    version=item.version,
                    source=SourceIdentity(kind="registry"),
                )
                for item in resolution.selection
            )
            if isinstance(resolution, ExactSelection)
            else (
                ResolutionPackage(
                    name="idna",
                    version="3.10",
                    source=SourceIdentity(kind="registry"),
                ),
            )
        )
        return self._plan("project", packages=packages, kwargs=kwargs)

    def resolve_environment(self, **kwargs: object) -> ResolutionOutcome:
        project = kwargs["project_plan"]
        assert isinstance(project, ResolutionPlan)
        source_plan = kwargs["source_plan"]
        assert isinstance(source_plan, SourcePlan)
        harness = cast(tuple[HarnessResolutionRequirement, ...], kwargs["harness"])
        packages = list(project.packages)
        selections: list[HarnessSelection] = []
        for requirement in harness:
            name = requirement.declaration.name
            source = source_plan.source_for(name)
            if name in {item.name for item in packages}:
                package = next(item for item in packages if item.name == name)
            else:
                package = ResolutionPackage(
                    name=name,
                    version=requirement.ceiling or "8.4",
                    source=source,
                )
                packages.append(package)
            assert package.version is not None
            if name not in {item.name for item in selections}:
                selections.append(
                    HarnessSelection(
                        name=name,
                        version=package.version,
                        source=package.source,
                        ceiling_bound=requirement.ceiling is not None
                        or source.kind == "registry",
                    )
                )
        return self._plan(
            "environment",
            packages=tuple(sorted(packages, key=lambda item: item.name)),
            direct_harness=tuple(sorted(selections, key=lambda item: item.name)),
            kwargs=kwargs,
        )

    @staticmethod
    def _plan(
        kind: Literal["project", "environment"],
        *,
        packages: tuple[ResolutionPackage, ...],
        kwargs: dict[str, object],
        direct_harness: tuple[HarnessSelection, ...] = (),
    ) -> ResolutionPlan:
        context = kwargs["context"]
        request_digest = kwargs["request_digest"]
        assert isinstance(context, ResolutionContext)
        assert isinstance(request_digest, str)
        native = NativeResolutionPlan.from_content(
            'lock-version = "1.0"\ncreated-by = "uv"\npackages = []\n'
        )
        return ResolutionPlan.from_evidence(
            kind=kind,
            request_digest=request_digest,
            context=context,
            packages=packages,
            direct_harness=direct_harness,
            native=native,
            process=successful_process(),
        )

    def create_environment(self, **kwargs: object) -> ToolOutcome:
        return ToolSuccess(stage="create-environment", process=successful_process())

    def install_resolution(self, **kwargs: object) -> InstallOutcome:
        plan = kwargs["plan"]
        assert isinstance(plan, ResolutionPlan)
        self._installed_plan = plan
        return InstalledResolution(
            plan_digest=plan.digest,
            process=successful_process(),
        )

    def inspect_interpreter(self, **kwargs: object) -> InterpreterOutcome:
        return InterpreterSuccess(
            process=successful_process(),
            interpreter=InterpreterIdentity(
                implementation="cpython",
                version="3.10.18",
                abi="cpython-310-x86_64-linux-gnu",
            ),
        )

    def inspect_environment(self, **kwargs: object) -> GraphOutcome:
        plan = getattr(self, "_installed_plan", None)
        packages = (
            plan.packages
            if isinstance(plan, ResolutionPlan)
            else (
                ResolutionPackage(
                    name="idna",
                    version="3.10",
                    source=SourceIdentity(kind="registry"),
                ),
            )
        )
        nodes: tuple[ResolvedNode, ...] = tuple(
            sorted(
                (
                    ResolvedNode(name="demo", version="0.1.0"),
                    *(
                        ResolvedNode(name=item.name, version=item.version)
                        for item in packages
                        if item.version is not None
                    ),
                ),
                key=lambda item: item.name,
            )
        )
        return GraphSuccess(
            process=successful_process(),
            nodes=nodes,
        )


def _write_demo(root: Path, *, harness: bool = False) -> Path:
    project = root / "project"
    project.mkdir()
    harness_toml = (
        """
[dependency-groups]
test = ["pytest"]
"""
        if harness
        else ""
    )
    command = '["pytest"]' if harness else '["python", "-c", "pass"]'
    (project / "pyproject.toml").write_text(
        f"""
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]
{harness_toml}
[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = {command}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return project


def _failed_tool(cause: FailureCause, stage: str) -> ToolFailure:
    return ToolFailure(
        cause=cause,
        stage=stage,
        process=ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout="",
            stderr=f"{stage} failed",
        ),
    )


def _failed_process(cause: FailureCause, stage: str) -> ProcessResult:
    process = _failed_tool(cause, stage).process
    assert isinstance(process, ProcessResult)
    return process


class TestEvaluationPolicy:
    def test_evaluation_policy_identity_ignores_scheduler_concurrency(self) -> None:
        automatic = EffectiveConfig(jobs="auto")
        serial = EffectiveConfig(jobs=1)

        assert evaluation_policy_identity(automatic) == evaluation_policy_identity(
            serial
        )


class TestEnvironmentFactory:
    @pytest.mark.parametrize(
        ("resolved_source", "summary_code", "message"),
        (
            (
                SourceIdentity(kind="workspace", locator="packages/idna"),
                "managed-source-leakage",
                "a managed workspace dependency resolved from a local source in SEARCH mode",
            ),
            (
                SourceIdentity(kind="registry", locator="https://pypi.org/simple"),
                "managed-source-mismatch",
                "a managed workspace dependency did not resolve to the expected registry artifact",
            ),
        ),
    )
    def test_managed_workspace_source_failure_is_structured_and_indeterminate(
        self,
        tmp_path: Path,
        resolved_source: SourceIdentity,
        summary_code: str,
        message: str,
    ) -> None:
        root = tmp_path / "workspace"
        member = root / "packages" / "idna"
        member.mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna>=3"]

[tool.uv.sources]
idna = { workspace = true }

[tool.uv.workspace]
members = ["packages/*"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (member / "pyproject.toml").write_text(
            '[project]\nname = "idna"\nversion = "3.10"\n',
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        class WrongSourceUv(SuccessfulUv):
            def resolve_project(self, **kwargs: object) -> ResolutionOutcome:
                return self._plan(
                    "project",
                    packages=(
                        ResolutionPackage(
                            name="idna",
                            version="3.10",
                            source=resolved_source,
                        ),
                    ),
                    kwargs=kwargs,
                )

        result = EnvironmentFactory(WrongSourceUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(result, PrepareFailure)
        assert result.failure.process is None
        assert result.failure.summary_code == summary_code
        assert result.failure.detail is not None
        assert result.failure.detail.code == summary_code
        assert result.failure.detail.message == message
        assert result.project_plan_digest is not None
        assert result.environment_plan_digest is None
        record = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=result.attempt),
            cause=result.failure.cause,
            stage=result.failure.stage,
            process=result.failure.process,
            summary_code=result.failure.summary_code,
            detail=result.failure.detail,
            project_plan_digest=result.project_plan_digest,
        )
        assert record.disposition == "INDETERMINATE"
        assert record.process is None
        assert record.detail == result.failure.detail
        snapshot.close()

    def test_managed_workspace_registry_and_exact_artifact_close_the_source_plan(
        self,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "workspace"
        member = root / "packages" / "idna"
        member.mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna>=3"]

[tool.uv.sources]
idna = { workspace = true }

[tool.uv.workspace]
members = ["packages/*"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (member / "pyproject.toml").write_text(
            '[project]\nname = "idna"\nversion = "3.10"\n',
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        selected = ResolutionArtifact(
            filename="idna-3.10-py3-none-any.whl",
            kind="archive",
            locator="https://files.example/idna-3.10-py3-none-any.whl",
            content_hash=f"sha256:{'a' * 64}",
        )

        class ClosedSourceUv(SuccessfulUv):
            def resolve_project(self, **kwargs: object) -> ResolutionOutcome:
                resolution = kwargs["resolution"]
                resolved = (
                    ResolutionPackage(
                        name="idna",
                        version="3.10",
                        source=SourceIdentity(
                            kind="url",
                            locator=selected.locator,
                            content_hash=selected.content_hash,
                        ),
                        available_artifacts=(selected,),
                        selected_artifact=selected,
                    )
                    if isinstance(resolution, ExactSelection)
                    else ResolutionPackage(
                        name="idna",
                        version="3.10",
                        source=SourceIdentity(kind="registry"),
                        available_artifacts=(selected,),
                    )
                )
                return self._plan(
                    "project",
                    packages=(resolved,),
                    kwargs=kwargs,
                )

        factory = EnvironmentFactory(ClosedSourceUv())
        highest = factory.prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(highest, PreparedEnvironment)
        highest.close()

        exact = factory.prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=ExactSelection(
                selection=(
                    SelectedCandidate(
                        dependency="idna",
                        version="3.10",
                        artifact=AvailableArtifact(
                            filename=selected.filename,
                            kind="wheel",
                            locator=selected.locator,
                            content_hash=selected.content_hash,
                        ),
                    ),
                ),
                harness_baseline=empty_harness_baseline(package.cells[0]),
            ),
        )
        assert isinstance(exact, PreparedEnvironment)
        assert exact.project_plan.packages[0].selected_artifact == selected
        exact.close()
        snapshot.close()

    def test_environment_factory_rejects_an_unqualified_uv_protocol(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = _write_demo(tmp_path)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        uv = SuccessfulUv()
        monkeypatch.setattr(
            uv,
            "resolution_run_context",
            lambda **_kwargs: _failed_tool("TOOL_FAILURE", "uv-version"),
        )

        with pytest.raises(
            ConfigurationError, match="protocol could not be established"
        ):
            EnvironmentFactory(uv).prepare(
                package=package,
                cell=package.cells[0],
                snapshot=snapshot,
                source_plan=SourcePlan.for_package(package, "SEARCH"),
                resolution=HighestResolution(),
            )

        snapshot.close()

    def test_environment_factory_materializes_an_isolated_proposal(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-m", "unittest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        prepared = EnvironmentFactory(SuccessfulUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(prepared, PreparedEnvironment)
        assert prepared.proposal.managed_vector[0].name == "idna"
        assert prepared.proposal.managed_vector[0].version == "3.10"
        assert prepared.proposal.interpreter == InterpreterIdentity(
            implementation="cpython",
            version="3.10.18",
            abi="cpython-310-x86_64-linux-gnu",
        )
        assert prepared.attempt.identity.identity_version == "attempt-v1"
        assert prepared.attempt.identity.resolution_context_digest
        assert prepared.attempt.identity.harness_policy_identity == (
            "original-harness-v1"
        )
        assert prepared.proposal.proposal_id == prepared.environment_identity.digest
        assert prepared.proposal.project_plan_digest == (
            prepared.project_plan.semantic_digest
        )
        assert prepared.proposal.environment_plan_digest == (
            prepared.environment_plan.semantic_digest
        )
        assert prepared.proposal.proposal_id == environment_identity_digest(
            project_plan_digest=prepared.proposal.project_plan_digest,
            environment_plan_digest=prepared.proposal.environment_plan_digest,
            graph=prepared.proposal.resolved_graph,
        )
        policy_document = {
            "config": package.config.model_dump(mode="json", exclude={"jobs"}),
            "tool_versions": {"ty": distribution_version("ty")},
            "verifier_outcome_policy": "configured-verifier-terminal-v1",
            "ty_diagnostic_policy": {
                "comparison": "multiset-subtraction",
                "identity_rule": (
                    "snapshot-path-line-column-code+external-namespace-path-code"
                ),
                "output_format": "gitlab",
                "policy": "static-transition-v1",
                "fingerprint": "ordered-incremental-identity-multiset",
                "region_scope": "fixed-slice-contiguous",
                "strong_classifier": "strong-classifier-v1",
                "witness_planner": "witness-planner-v1",
                "witness_harness": "witness-harness-v1",
                "boundary_rule": "runtime-evidence-only",
                "final_verification": "direct-test-command-pass",
            },
            "failure_policy": "failure-runtime-v2",
        }
        expected_policy = hashlib.sha256(
            (
                "pf:policy:v1\0"
                + json.dumps(policy_document, sort_keys=True, separators=(",", ":"))
            ).encode()
        ).hexdigest()
        assert prepared.proposal.policy_identity == expected_policy
        source = prepared.proposal_root / "pyproject.toml"
        assert source.is_file()
        source.write_text("changed\n", encoding="utf-8")
        assert (
            (root / "pyproject.toml")
            .read_text(encoding="utf-8")
            .startswith("[project]")
        )

    def test_environment_factory_resolves_identical_inputs_only_once(
        self,
        tmp_path: Path,
    ) -> None:
        class CountingUv(SuccessfulUv):
            def __init__(self) -> None:
                self.project_resolutions = 0
                self.environment_resolutions = 0

            def resolve_project(self, **kwargs: object) -> ResolutionOutcome:
                self.project_resolutions += 1
                return super().resolve_project(**kwargs)

            def resolve_environment(self, **kwargs: object) -> ResolutionOutcome:
                self.environment_resolutions += 1
                return super().resolve_environment(**kwargs)

        root = _write_demo(tmp_path, harness=True)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        uv = CountingUv()
        factory = EnvironmentFactory(uv)

        first = factory.prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(first, PreparedEnvironment)
        first.close()
        second = factory.prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(second, PreparedEnvironment)
        assert uv.project_resolutions == 1
        assert uv.environment_resolutions == 1
        assert first.project_plan is second.project_plan
        assert first.environment_plan is second.environment_plan
        second.close()
        snapshot.close()

    def test_environment_materializes_requested_vector_in_replica_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        class ReplicaInspectingUv(SuccessfulUv):
            def __init__(self) -> None:
                self.requirement: Requirement | None = None

            def resolve_project(self, **kwargs: object) -> ResolutionOutcome:
                package = kwargs["package"]
                assert isinstance(package, Path)
                replica = ProjectLoader().load(root=package)
                self.requirement = Requirement(replica.target.declarations[0].raw)
                return super().resolve_project(**kwargs)

            def inspect_environment(self, **kwargs: object) -> GraphSuccess:
                return GraphSuccess(
                    process=successful_process(),
                    nodes=(
                        ResolvedNode(name="demo", version="0.1.0"),
                        ResolvedNode(name="idna", version="3.1"),
                    ),
                )

        root = tmp_path / "project"
        root.mkdir()
        original = (
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna>=3,<4"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n"
        )
        (root / "pyproject.toml").write_text(original, encoding="utf-8")
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        uv = ReplicaInspectingUv()

        prepared = EnvironmentFactory(uv).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=exact_selection(
                package.cells[0], VersionPin(name="idna", version="3.1")
            ),
        )

        assert isinstance(prepared, PreparedEnvironment)
        assert uv.requirement is not None
        assert str(uv.requirement.specifier) == "<4,==3.1"
        assert (root / "pyproject.toml").read_text(encoding="utf-8") == original
        prepared.close()
        snapshot.close()

    def test_environment_exact_vector_installs_the_frozen_artifact_selection(
        self,
        tmp_path: Path,
    ) -> None:
        selection = (
            SelectedCandidate(
                dependency="idna",
                version="3.1",
                artifact=AvailableArtifact(
                    filename="idna-3.1-py3-none-any.whl",
                    kind="wheel",
                    content_hash=f"sha256:{'a' * 64}",
                    locator="https://files.example/idna-3.1-py3-none-any.whl",
                ),
            ),
        )

        class ExactUv(SuccessfulUv):
            def __init__(self) -> None:
                self.installed_selection: tuple[SelectedCandidate, ...] | None = None

            def resolve_project(self, **kwargs: object) -> ResolutionOutcome:
                resolution = kwargs["resolution"]
                assert isinstance(resolution, ExactSelection)
                self.installed_selection = resolution.selection
                return super().resolve_project(**kwargs)

            def inspect_environment(self, **kwargs: object) -> GraphOutcome:
                return GraphSuccess(
                    process=successful_process(),
                    nodes=(
                        ResolvedNode(name="demo", version="0.1.0"),
                        ResolvedNode(name="idna", version="3.1"),
                    ),
                )

        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna>=3,<4"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        uv = ExactUv()

        prepared = EnvironmentFactory(uv).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=ExactSelection(
                selection,
                harness_baseline=empty_harness_baseline(package.cells[0]),
            ),
        )

        assert isinstance(prepared, PreparedEnvironment)
        assert uv.installed_selection == selection
        assert prepared.attempt.identity.requested_managed_vector == (
            VersionPin(name="idna", version="3.1"),
        )
        prepared.close()
        snapshot.close()

    @pytest.mark.parametrize(
        "pins",
        (
            (
                VersionPin(name="urllib3", version="2.0"),
                VersionPin(name="idna", version="3.1"),
            ),
            (
                VersionPin(name="idna", version="3.1"),
                VersionPin(name="idna", version="3.2"),
            ),
        ),
        ids=("unsorted", "duplicate"),
    )
    def test_environment_rejects_a_noncanonical_artifact_selection(
        self,
        tmp_path: Path,
        pins: tuple[VersionPin, ...],
    ) -> None:
        root = _write_demo(tmp_path)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        with pytest.raises(ConfigurationError, match="sorted and unique"):
            EnvironmentFactory(SuccessfulUv()).prepare(
                package=package,
                cell=package.cells[0],
                snapshot=snapshot,
                source_plan=SourcePlan.for_package(package, "SEARCH"),
                resolution=exact_selection(package.cells[0], *pins),
            )
        snapshot.close()

    def test_environment_rejects_an_installed_graph_that_drifted_from_selection(
        self,
        tmp_path: Path,
    ) -> None:
        class DriftingUv(SuccessfulUv):
            def inspect_environment(self, **kwargs: object) -> GraphOutcome:
                return GraphSuccess(
                    process=successful_process(),
                    nodes=(
                        ResolvedNode(name="demo", version="0.1.0"),
                        ResolvedNode(name="idna", version="3.2"),
                    ),
                )

        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna>=3,<4"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(DriftingUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=ExactSelection(
                (
                    SelectedCandidate(
                        dependency="idna",
                        version="3.1",
                        artifact=AvailableArtifact(
                            filename="idna-3.1.tar.gz",
                            kind="sdist",
                            content_hash=f"sha256:{'a' * 64}",
                            locator="https://files.example/idna-3.1.tar.gz",
                        ),
                    ),
                ),
                harness_baseline=empty_harness_baseline(package.cells[0]),
            ),
        )

        assert isinstance(result, PrepareFailure)
        assert result.attempt.identity.requested_managed_vector == (
            VersionPin(name="idna", version="3.1"),
        )
        assert result.failure.cause == "INTERNAL_INVARIANT"
        assert result.failure.stage == "inspect-environment-plan"
        snapshot.close()

    def test_environment_prepare_failure_retains_attempt_without_a_proposal(
        self,
        tmp_path: Path,
    ) -> None:
        class ResolutionConflictUv(SuccessfulUv):
            def resolve_project(self, **kwargs: object) -> ResolutionUnsat:
                context = kwargs["context"]
                request_digest = kwargs["request_digest"]
                assert isinstance(context, ResolutionContext)
                assert isinstance(request_digest, str)
                return ResolutionUnsat(
                    stage="resolve-project",
                    request_digest=request_digest,
                    context=context,
                    proof_code="direct-version-contradiction",
                    diagnostic_digest="diagnostic",
                    process=_failed_process("RESOLUTION_CONFLICT", "resolve-project"),
                )

        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna>=3"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        requested = (VersionPin(name="idna", version="3.1"),)

        result = EnvironmentFactory(ResolutionConflictUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=exact_selection(package.cells[0], *requested),
        )

        assert isinstance(result, PrepareFailure)
        assert result.attempt.identity.requested_resolution == "exact-vector"
        assert result.attempt.identity.requested_managed_vector == requested
        assert result.failure.cause == "RESOLUTION_CONFLICT"
        assert result.project_plan_digest is None
        assert result.environment_plan_digest is None
        assert not hasattr(result, "proposal")

    def test_environment_only_materializes_declarations_active_for_cell(
        self,
        tmp_path: Path,
    ) -> None:
        class ReplicaInspectingUv(SuccessfulUv):
            def __init__(self) -> None:
                self.dependencies: tuple[str, ...] = ()
                self.optional: tuple[str, ...] = ()
                self.sibling: str = ""

            def resolve_project(self, **kwargs: object) -> ResolutionOutcome:
                package = kwargs["package"]
                assert isinstance(package, Path)
                with (package / "pyproject.toml").open("rb") as stream:
                    document = tomli.load(stream)
                self.dependencies = tuple(document["project"]["dependencies"])
                self.optional = tuple(
                    document["project"]["optional-dependencies"]["http"]
                )
                self.sibling = (
                    package.parent / "sibling" / "pyproject.toml"
                ).read_text(encoding="utf-8")
                return super().resolve_project(**kwargs)

            def inspect_environment(self, **kwargs: object) -> GraphSuccess:
                return GraphSuccess(
                    process=successful_process(),
                    nodes=(
                        ResolvedNode(name="certifi", version="2024.2"),
                        ResolvedNode(name="demo", version="0.1.0"),
                        ResolvedNode(name="idna", version="2.1"),
                    ),
                )

        root = tmp_path / "workspace"
        demo = root / "packages" / "demo"
        sibling = root / "packages" / "sibling"
        demo.mkdir(parents=True)
        sibling.mkdir(parents=True)
        root_pyproject = (
            """
    [tool.uv.workspace]
    members = ["packages/*"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    extras = "each"
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n"
        )
        demo_pyproject = (
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = [
        "idna>=3; python_version >= '3.11'",
        "idna>=2; python_version < '3.11'",
    ]

    [project.optional-dependencies]
    http = [
        "urllib3>=2; python_version >= '3.11'",
        "certifi>=2024; python_version < '3.11'",
    ]
    """.strip()
            + "\n"
        )
        sibling_pyproject = '[project]\nname = "sibling"\nversion = "0.1.0"\n'
        (root / "pyproject.toml").write_text(root_pyproject, encoding="utf-8")
        (demo / "pyproject.toml").write_text(demo_pyproject, encoding="utf-8")
        (sibling / "pyproject.toml").write_text(sibling_pyproject, encoding="utf-8")
        package = (
            ProjectLoader()
            .load(
                root=root,
                selector=WorkspacePackage(canonical_name="demo"),
            )
            .target
        )
        cell = next(cell for cell in package.cells if cell.extra_surface == ("http",))
        snapshot = SnapshotBuilder.without_processes().build(root)
        uv = ReplicaInspectingUv()

        prepared = EnvironmentFactory(uv).prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=exact_selection(
                cell,
                VersionPin(name="certifi", version="2024.2"),
                VersionPin(name="idna", version="2.1"),
            ),
        )

        assert isinstance(prepared, PreparedEnvironment)
        assert [str(Requirement(raw).specifier) for raw in uv.dependencies] == [
            ">=3",
            "==2.1",
        ]
        assert [str(Requirement(raw).specifier) for raw in uv.optional] == [
            ">=2",
            "==2024.2",
        ]
        assert uv.sibling == sibling_pyproject
        assert (root / "pyproject.toml").read_text(encoding="utf-8") == root_pyproject
        assert (demo / "pyproject.toml").read_text(encoding="utf-8") == demo_pyproject
        assert (sibling / "pyproject.toml").read_text(
            encoding="utf-8"
        ) == sibling_pyproject
        prepared.close()
        snapshot.close()

    def test_environment_maps_certified_harness_unsat_before_installation(
        self,
        tmp_path: Path,
    ) -> None:
        class HarnessConflictUv(SuccessfulUv):
            def resolve_environment(self, **kwargs: object) -> ResolutionUnsat:
                context = kwargs["context"]
                request_digest = kwargs["request_digest"]
                assert isinstance(context, ResolutionContext)
                assert isinstance(request_digest, str)
                return ResolutionUnsat(
                    stage="resolve-environment",
                    request_digest=request_digest,
                    context=context,
                    proof_code="transitive-version-contradiction",
                    diagnostic_digest="diagnostic",
                    process=_failed_process("HARNESS_CONFLICT", "resolve-environment"),
                )

        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna"]

    [dependency-groups]
    test = ["pytest"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(HarnessConflictUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(result, PrepareFailure)
        assert result.failure.cause == "HARNESS_CONFLICT"
        assert result.failure.stage == "resolve-environment"

    def test_environment_allows_harness_only_transitive_graph_to_change(
        self,
        tmp_path: Path,
    ) -> None:
        class ChangingHarnessGraphUv(SuccessfulUv):
            def resolve_environment(self, **kwargs: object) -> ResolutionOutcome:
                outcome = super().resolve_environment(**kwargs)
                assert isinstance(outcome, ResolutionPlan)
                resolution = kwargs["resolution"]
                transitive = (
                    ResolutionPackage(
                        name="pluggy",
                        version="1.6.0",
                        source=SourceIdentity(kind="registry"),
                    )
                    if isinstance(resolution, HighestResolution)
                    else ResolutionPackage(
                        name="iniconfig",
                        version="2.1.0",
                        source=SourceIdentity(kind="registry"),
                    )
                )
                return self._plan(
                    "environment",
                    packages=tuple(
                        sorted(
                            (*outcome.packages, transitive), key=lambda item: item.name
                        )
                    ),
                    direct_harness=outcome.direct_harness,
                    kwargs=kwargs,
                )

        root = _write_demo(tmp_path, harness=True)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        factory = EnvironmentFactory(ChangingHarnessGraphUv())

        original = factory.prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(original, PreparedEnvironment)
        relaxed = factory.prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=LowestDirectResolution(original.harness_baseline),
        )

        assert isinstance(relaxed, PreparedEnvironment)
        assert original.project_plan.packages == relaxed.project_plan.packages
        assert {item.name for item in original.environment_plan.packages} == {
            "idna",
            "pluggy",
            "pytest",
        }
        assert {item.name for item in relaxed.environment_plan.packages} == {
            "idna",
            "iniconfig",
            "pytest",
        }
        assert original.proposal.managed_vector == relaxed.proposal.managed_vector
        assert original.environment_identity != relaxed.environment_identity
        assert original.proposal.proposal_id != relaxed.proposal.proposal_id
        original.close()
        relaxed.close()
        snapshot.close()

    def test_environment_reports_prepare_stages(self, tmp_path: Path) -> None:
        class Events:
            def __init__(self) -> None:
                self.phases: list[str] = []

            def consume(self, event: CellStageEvent) -> None:
                self.phases.append(event.stage)

        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna"]

    [dependency-groups]
    test = ["pytest"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        events = Events()

        prepared = EnvironmentFactory(SuccessfulUv(), events=events).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(prepared, PreparedEnvironment)
        assert events.phases == [
            "resolving project",
            "resolving environment",
            "preparing environment",
            "installing environment plan",
        ]
        prepared.close()
        snapshot.close()

    def test_environment_establishes_attempt_before_any_external_operation(
        self,
        tmp_path: Path,
    ) -> None:
        class CreateFails(SuccessfulUv):
            def create_environment(self, **kwargs: object) -> ToolFailure:
                return _failed_tool("ENVIRONMENT_FAILURE", "create-environment")

        root = _write_demo(tmp_path)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(CreateFails()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(result, PrepareFailure)
        assert result.attempt.identity.requested_resolution == "highest"
        assert result.attempt.identity.requested_managed_vector is None
        assert result.failure.cause == "ENVIRONMENT_FAILURE"
        assert result.failure.stage == "create-environment"
        snapshot.close()

    def test_environment_check_prepare_failure_keeps_a_lowest_direct_attempt(
        self, tmp_path: Path
    ) -> None:
        class CreateFails(SuccessfulUv):
            def create_environment(self, **kwargs: object) -> ToolFailure:
                return _failed_tool("ENVIRONMENT_FAILURE", "create-environment")

        root = _write_demo(tmp_path)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(CreateFails()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=LowestDirectResolution(empty_harness_baseline(package.cells[0])),
        )

        assert isinstance(result, PrepareFailure)
        assert result.attempt.identity.requested_resolution == "lowest-direct"
        assert result.attempt.identity.requested_managed_vector is None
        assert result.failure.cause == "ENVIRONMENT_FAILURE"
        snapshot.close()

    @pytest.mark.parametrize(
        ("method", "cause", "stage"),
        (
            ("inspect_interpreter", "ENVIRONMENT_FAILURE", "inspect-interpreter"),
            ("install_resolution", "BUILD_FAILURE", "install-environment"),
            ("inspect_environment", "TOOL_FAILURE", "inspect"),
        ),
    )
    def test_environment_prepare_keeps_attempt_when_a_stage_fails(
        self,
        tmp_path: Path,
        method: str,
        cause: FailureCause,
        stage: str,
    ) -> None:
        class StageFails(SuccessfulUv):
            def __init__(self) -> None:
                if method != "install_resolution":
                    setattr(self, method, lambda **kwargs: _failed_tool(cause, stage))

            def install_resolution(self, **kwargs: object) -> InstallOutcome:
                if method != "install_resolution":
                    return super().install_resolution(**kwargs)
                plan = kwargs["plan"]
                assert isinstance(plan, ResolutionPlan)
                return InstallFailure(
                    plan_digest=plan.digest,
                    cause=cause,
                    process=_failed_process(cause, stage),
                )

        root = _write_demo(tmp_path)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(StageFails()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(result, PrepareFailure)
        assert result.attempt.identity.requested_resolution == "highest"
        assert result.failure.cause == cause
        assert result.failure.stage == stage
        assert result.project_plan_digest
        assert result.environment_plan_digest
        snapshot.close()

    def test_environment_rejects_an_interpreter_that_does_not_match_the_cell(
        self,
        tmp_path: Path,
    ) -> None:
        class WrongInterpreter(SuccessfulUv):
            def inspect_interpreter(self, **kwargs: object) -> InterpreterSuccess:
                return InterpreterSuccess(
                    process=successful_process(),
                    interpreter=InterpreterIdentity(
                        implementation="cpython",
                        version="3.11.13",
                        abi="cpython-311-x86_64-linux-gnu",
                    ),
                )

        root = _write_demo(tmp_path)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(WrongInterpreter()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(result, PrepareFailure)
        assert result.failure.cause == "ENVIRONMENT_FAILURE"
        assert result.failure.stage == "inspect-interpreter"
        snapshot.close()

    def test_environment_rejects_a_graph_that_omits_managed_dependencies(
        self,
        tmp_path: Path,
    ) -> None:
        class MissingManaged(SuccessfulUv):
            def inspect_environment(self, **kwargs: object) -> GraphSuccess:
                return GraphSuccess(
                    process=successful_process(),
                    nodes=(ResolvedNode(name="demo", version="0.1.0"),),
                )

        root = _write_demo(tmp_path)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(MissingManaged()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(result, PrepareFailure)
        assert result.failure.cause == "INTERNAL_INVARIANT"
        assert result.failure.stage == "inspect-environment-plan"
        snapshot.close()

    def test_environment_rejects_a_graph_with_packages_outside_the_final_plan(
        self,
        tmp_path: Path,
    ) -> None:
        class ExtraPackage(SuccessfulUv):
            def inspect_environment(self, **kwargs: object) -> GraphSuccess:
                return GraphSuccess(
                    process=successful_process(),
                    nodes=(
                        ResolvedNode(name="demo", version="0.1.0"),
                        ResolvedNode(name="idna", version="3.10"),
                        ResolvedNode(name="pluggy", version="1.6.0"),
                    ),
                )

        root = _write_demo(tmp_path)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(ExtraPackage()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(result, PrepareFailure)
        assert result.failure.cause == "INTERNAL_INVARIANT"
        assert result.failure.stage == "inspect-environment-plan"
        snapshot.close()

    def test_environment_prepare_keeps_attempt_when_harness_resolution_fails(
        self,
        tmp_path: Path,
    ) -> None:
        class HarnessFails(SuccessfulUv):
            def resolve_environment(self, **kwargs: object) -> ResolutionUnsat:
                context = kwargs["context"]
                request_digest = kwargs["request_digest"]
                assert isinstance(context, ResolutionContext)
                assert isinstance(request_digest, str)
                return ResolutionUnsat(
                    stage="resolve-environment",
                    request_digest=request_digest,
                    context=context,
                    proof_code="direct-version-contradiction",
                    diagnostic_digest="diagnostic",
                    process=_failed_process("HARNESS_CONFLICT", "resolve-environment"),
                )

        root = _write_demo(tmp_path, harness=True)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(HarnessFails()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(result, PrepareFailure)
        assert result.failure.cause == "HARNESS_CONFLICT"
        assert result.failure.stage == "resolve-environment"
        assert result.project_plan_digest
        assert result.environment_plan_digest is None
        snapshot.close()

    def test_environment_prepare_keeps_attempt_when_final_graph_inspection_fails(
        self,
        tmp_path: Path,
    ) -> None:
        class HarnessInspectFails(SuccessfulUv):
            def inspect_environment(self, **kwargs: object) -> GraphOutcome:
                return _failed_tool("TOOL_FAILURE", "inspect")

        root = _write_demo(tmp_path, harness=True)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        result = EnvironmentFactory(HarnessInspectFails()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )

        assert isinstance(result, PrepareFailure)
        assert result.failure.cause == "TOOL_FAILURE"
        assert result.failure.stage == "inspect"
        snapshot.close()

    def test_environment_rejects_a_managed_vector_that_does_not_cover_declarations(
        self,
        tmp_path: Path,
    ) -> None:
        root = _write_demo(tmp_path)
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)

        with pytest.raises(ConfigurationError, match="exactly cover"):
            EnvironmentFactory(SuccessfulUv()).prepare(
                package=package,
                cell=package.cells[0],
                snapshot=snapshot,
                source_plan=SourcePlan.for_package(package, "SEARCH"),
                resolution=ExactSelection(
                    (),
                    harness_baseline=empty_harness_baseline(package.cells[0]),
                ),
            )
        snapshot.close()

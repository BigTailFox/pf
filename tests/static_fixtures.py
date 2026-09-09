"""Prepared static inputs: one real uv/ty capture and a scripted public-seam stand-in."""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from evaluation_fixtures import evaluation_assembly, evaluation_project
from pf.adapters.process import SubprocessRunner
from pf.adapters.uv import UvAdapter
from pf.environment import EnvironmentFactory, HighestResolution, PreparedEnvironment
from pf.project import ProjectLoader
from pf.schemas.project import SourcePlan
from pf.policy import execution_policy, guidance_policy
from pf.schemas.evaluation import ProcessSpec
from pf.schemas.policy import SnapshotTyConfigMaterialized, TyToolVersionDistribution
from pf.schemas.resolution import ResolutionPlanEvidence
from pf.schemas.static import StaticSubject
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.snapshot import SnapshotBuilder
from pf.static_projection import static_subject as project_static_subject
from pf.static_request import StaticRequestFactory, StaticTyRequest


class _ScriptedStaticRequest:
    """Internal request fixture. Not a StaticEvaluator seam."""

    def capture(self, prepared, *, package, environment, cancellation=None):
        del environment
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        directory = prepared.proposal_root.parent / "static"
        directory.mkdir(parents=True, exist_ok=True)
        tool = directory / "ty"
        tool.write_bytes(b"scripted ty")
        plan = prepared.environment_plan or prepared.project_plan
        interpreter = prepared.proposal.interpreter
        assert interpreter is not None
        subject = project_static_subject(
            source_snapshot_digest=prepared.proposal.snapshot_digest,
            cell=prepared.proposal.cell,
            interpreter=interpreter,
            packages=plan.packages,
            snapshot_root=prepared.proposal_root,
        )
        assert isinstance(subject, StaticSubject), subject
        policy = guidance_policy(
            package.config,
            tool_version=TyToolVersionDistribution(version="1.0.0"),
            snapshot_ty_config=SnapshotTyConfigMaterialized(digest="a" * 64),
        ).observation
        preparation = StaticPreparationEvidence(
            attempt=prepared.attempt, proposal=prepared.proposal, subject=subject,
            project_plan=ResolutionPlanEvidence.from_plan(prepared.project_plan),
            environment_plan=ResolutionPlanEvidence.from_plan(prepared.environment_plan) if prepared.environment_plan else None,
            execution_policy=execution_policy(package.config), declarations=package.declarations,
            selected_test_group=package.selected_test_group, harness_requirements=package.harness_requirements,
            harness_baseline=prepared.harness_baseline, selected_candidates=prepared.selected_candidates,
            source_plan=prepared.source_plan,
        )
        return StaticTyRequest(
            subject, policy,
            ProcessSpec(argv=(str(tool), "check"), cwd=str(prepared.package_root), timeout_seconds=None),
            prepared.proposal_root, prepared.environment_root, prepared,
            (("snapshot", prepared.proposal_root), ("environment", prepared.environment_root)),
            str(prepared.environment_root), preparation,
        )


@pytest.fixture(scope="module")
def scripted_static_request(tmp_path_factory):
    root = tmp_path_factory.mktemp("scripted-static-inputs")
    project = evaluation_project(root / "project", dependency=None)
    assembly = evaluation_assembly(highest=())
    prepared = assembly.environments.prepare(
        package=project.package,
        cell=project.package.cells[0],
        snapshot=project.snapshot,
        source_plan=project.source_plan,
        resolution=HighestResolution(),
    )
    assert isinstance(prepared, PreparedEnvironment)
    try:
        yield _ScriptedStaticRequest().capture(
            prepared, package=project.package, environment={},
        )
    finally:
        prepared.close()
        project.snapshot.close()


@pytest.fixture(scope="module")
def static_request(tmp_path_factory):
    root = tmp_path_factory.mktemp("static-cache-inputs")
    project = root / "project"
    home = root / "home"
    home.mkdir()
    (project / "src/demo").mkdir(parents=True)
    (project / "src/demo/__init__.py").write_text("VALUE = 1\n")
    (project / "pyproject.toml").write_text('''
[project]
name = "demo"
version = "1"
[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
[tool.pf]
pythons = ["3.10"]
test-command = ["python", "-c", "import demo; assert demo.VALUE == 1"]
''')
    package = ProjectLoader().load(root=project).target
    plan = SourcePlan.for_package(package, "SEARCH")
    runner = SubprocessRunner()
    snapshot = SnapshotBuilder(runner).build(project)
    prepared = None
    try:
        prepared = EnvironmentFactory(UvAdapter(runner)).prepare(
            package=package, cell=package.cells[0], snapshot=snapshot,
            source_plan=plan, resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        executable = shutil.which("ty")
        assert executable is not None
        request = StaticRequestFactory(runner, ty_executable=Path(executable)).capture(
            prepared, package=package,  environment={
                "HOME": str(home),
                "GIT_CONFIG_SYSTEM": str(root / "no-system-config"),
            },
        )
        assert isinstance(request, StaticTyRequest), request
        yield request
    finally:
        if isinstance(prepared, PreparedEnvironment):
            prepared.close()
        snapshot.close()


@pytest.fixture
def preparation(scripted_static_request):
    return scripted_static_request.preparation


@pytest.fixture
def static_subject(preparation):
    return preparation.subject


@pytest.fixture
def policy(scripted_static_request):
    return scripted_static_request.observation_policy

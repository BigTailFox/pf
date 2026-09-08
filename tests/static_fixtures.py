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
from scripted_static import ScriptedStaticRequests
from pf.snapshot import SnapshotBuilder
from pf.static_request import StaticRequestFactory, StaticTyRequest


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
        yield ScriptedStaticRequests().capture(
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
            prepared, package=package,  environment={"HOME": str(home), "GIT_CONFIG_SYSTEM": str(root / "no-system-config")},
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

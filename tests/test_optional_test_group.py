from __future__ import annotations

import json

import pytest

from pf.environment import (
    EnvironmentFactory,
    ExactSelection,
    HighestResolution,
    LowestDirectResolution,
    PreparedEnvironment,
)
from pf.errors import ConfigurationError
from pf.project import ProjectLoader
from pf.schemas.config import WorkspacePackage
from pf.schemas.project import (
    HarnessBaseline,
    HarnessSatisfaction,
    SourceIdentity,
    SourcePlan,
    VersionPin,
)
from pf.snapshot import SnapshotBuilder
from test_environment import SuccessfulUv, exact_selection


def group_table(groups):
    return (
        "[dependency-groups]\n"
        + "\n".join(
            f"{name} = {json.dumps(requirements)}"
            for name, requirements in groups.items()
        )
        + "\n"
    )


class TestOptionalGroupPlanning:
    @pytest.mark.parametrize(
        "root_groups,member_groups,root_choice,member_choice,selected,names",
        [
            ({}, {}, None, None, None, ()),
            ({"dev": ["root-tool"]}, {}, None, None, "dev", ("root-tool",)),
            ({}, {"test": ["member-tool"]}, None, None, "test", ("member-tool",)),
            (
                {"test": ["root-tool"]},
                {"dev": ["member-tool"]},
                None,
                None,
                "dev",
                ("member-tool",),
            ),
            (
                {"dev": ["root-tool"]},
                {"dev": ["member-tool"], "test": ["unused"]},
                None,
                None,
                "dev",
                ("root-tool", "member-tool"),
            ),
            ({"dev": [], "test": ["unused"]}, {}, None, None, "dev", ()),
            (
                {"dev": ["unused"], "test": ["root-tool"]},
                {},
                "test",
                None,
                "test",
                ("root-tool",),
            ),
            (
                {"dev": ["unused"]},
                {"test": ["member-tool"]},
                "dev",
                "test",
                "test",
                ("member-tool",),
            ),
            ({"dev": ["unused"], "test": ["unused"]}, {}, "missing", None, None, ()),
            ({"dev": ["unused"]}, {}, "dev", "missing", None, ()),
            (
                {"qa": ["root-tool"]},
                {"qa": ["member-tool"]},
                "qa",
                None,
                "qa",
                ("root-tool", "member-tool"),
            ),
            (
                {"dev": ["invalid @@@"], "qa": ["root-tool"]},
                {},
                "qa",
                None,
                "qa",
                ("root-tool",),
            ),
            ({"qa": ["invalid @@@"]}, {}, None, None, None, ()),
            ({"dev": ["invalid @@@"]}, {}, "missing", None, None, ()),
        ],
    )
    def test_workspace_selection(
        self,
        tmp_path,
        root_groups,
        member_groups,
        root_choice,
        member_choice,
        selected,
        names,
    ):
        root_config = f'test-group = "{root_choice}"\n' if root_choice else ""
        member_config = (
            f'[tool.pf]\ntest-group = "{member_choice}"\n' if member_choice else ""
        )
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["member"]\n'
            '[tool.pf]\npythons = ["3.10"]\nplatforms = ["x86_64-unknown-linux-gnu"]\n'
            + root_config
            + group_table(root_groups)
        )
        member = tmp_path / "member"
        member.mkdir()
        (member / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\n'
            + member_config
            + group_table(member_groups)
        )
        package = (
            ProjectLoader()
            .load(root=tmp_path, selector=WorkspacePackage(canonical_name="demo"))
            .target
        )
        assert package.config.test.group == (member_choice or root_choice)
        assert package.selected_test_group == selected
        assert tuple(item.name for item in package.harness_requirements) == names
        assert {route.dependency for route in package.source_routes} == set(names)

    @pytest.mark.parametrize("value", ['""', "42", "[]"])
    def test_explicit_group_requires_nonempty_string(self, tmp_path, value):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\n'
            f'[tool.pf]\npythons = ["3.10"]\ntest-group = {value}\n'
        )
        with pytest.raises(ConfigurationError, match="test-group"):
            ProjectLoader().load(root=tmp_path)


class RecordingUv(SuccessfulUv):
    def __init__(self):
        self.calls = []

    def resolve_project(self, **kwargs):
        self.calls.append("project")
        return super().resolve_project(**kwargs)

    def resolve_environment(self, **kwargs):
        self.calls.append("environment")
        return super().resolve_environment(**kwargs)

    def install_resolution(self, **kwargs):
        self.calls.append("install-" + kwargs["plan"].kind)
        return super().install_resolution(**kwargs)

    def inspect_environment(self, **kwargs):
        self.calls.append("inspect")
        return super().inspect_environment(**kwargs)


class Events:
    def __init__(self):
        self.stages = []

    def consume(self, event):
        self.stages.append(event.stage)


class TestOptionalGroupPreparation:
    @pytest.mark.parametrize("kind", ["project", "environment"])
    def test_native_install_failure_identifies_actual_plan(self, tmp_path, kind):
        from pf.adapters.uv import UvAdapter
        from pf.resolution import (
            InstallFailure,
            NativeResolutionPlan,
            ResolutionContext,
            ResolutionPlan,
            ResolutionRunContext,
        )
        from pf.schemas.evaluation import ProcessResult, OperationRequestBinding
        from pf.schemas.project import Cell

        class Runner:
            def run(self, spec):
                assert spec.argv[1:3] == ("pip", "sync")
                return ProcessResult(
                    exit_code=1, duration_seconds=0.1, stderr="installation failed"
                )

        plan = ResolutionPlan.from_evidence(
            kind=kind,
            request_digest="request",
            context=ResolutionContext.from_inputs(
                run=ResolutionRunContext(
                    uv_version="0.12.5", release_cutoff="2026-09-06T00:00:00+00:00"
                ),
                cell=Cell(
                    package="demo",
                    target="x86_64-unknown-linux-gnu",
                    python_minor="3.10",
                    extra_surface=(),
                ),
                source_plan_identity="source",
                uv_project_configuration_identity="configuration",
            ),
            packages=(),
            direct_harness=(),
            native=NativeResolutionPlan.from_content(
                'lock-version = "1.0"\ncreated-by = "uv"\npackages = []\n'
            ),
            process=ProcessResult(exit_code=0, duration_seconds=0.1),
        )
        result = UvAdapter(Runner()).install_resolution(
            plan=plan,
            request_binding=OperationRequestBinding.model_validate({
                "attempt_id": "a" * 64, "stage": "install-" + kind,
                "project_plan_digest": plan.semantic_digest if kind == "project" else "b" * 64,
                "environment_plan_digest": plan.semantic_digest if kind == "environment" else None,
            }),
            interpreter=tmp_path / "python",
            cwd=tmp_path,
            work_directory=tmp_path,
            timeout_seconds=30,
        )
        assert isinstance(result, InstallFailure)
        assert result.stage == "install-" + kind
        assert result.plan_digest == plan.digest

    @pytest.mark.parametrize(
        "group",
        [
            "",
            "[dependency-groups]\ndev = []\n",
            '[dependency-groups]\ndev = [{include-group = "required"}]\nrequired = ["demo[feature]"]\n',
        ],
    )
    def test_empty_harness_all_roles_and_cache(self, tmp_path, monkeypatch, group):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\ndependencies = ["idna"]\n'
            "[project.optional-dependencies]\nfeature = []\n"
            + group
            + '[tool.pf]\npythons = ["3.10"]\nplatforms = ["x86_64-unknown-linux-gnu"]\n'
        )
        package = ProjectLoader().load(root=tmp_path).target
        cell = package.cells[0]
        assert cell.extra_surface == (("feature",) if "demo[feature]" in group else ())
        assert package.harness_requirements == ()
        assert {route.dependency for route in package.source_routes} == {"idna"}
        for name in ("original_harness", "relax_harness"):
            monkeypatch.setattr(
                "pf.environment." + name,
                lambda *a, **kw: pytest.fail("empty harness must bypass normalization"),
            )
        uv, events = RecordingUv(), Events()
        factory = EnvironmentFactory(uv, events=events)
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        try:
            baseline = None
            attempts = set()
            for role in ("highest", "lowest", "exact", "exact"):
                if role == "highest":
                    request = HighestResolution()
                else:
                    assert baseline is not None
                    if role == "lowest":
                        request = LowestDirectResolution(baseline)
                    else:
                        selection = exact_selection(
                            cell, VersionPin(name="idna", version="3.10")
                        )
                        request = ExactSelection(selection.selection, baseline)
                uv.calls.clear()
                events.stages.clear()
                prepared = factory.prepare(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                    source_plan=SourcePlan.for_package(package, "SEARCH"),
                    resolution=request,
                )
                assert isinstance(prepared, PreparedEnvironment)
                try:
                    cached = prepared.attempt.attempt_id in attempts
                    assert uv.calls == ([] if cached else ["project"]) + [
                        "install-project",
                        "inspect",
                    ]
                    assert "resolving environment" not in events.stages
                    assert "installing project plan" in events.stages
                    assert prepared.environment_plan is None
                    assert prepared.proposal.environment_plan_digest is None
                    assert prepared.environment_identity.environment_plan_digest is None
                    assert prepared.harness_baseline.declaration_ids == ()
                    assert prepared.harness_baseline.observations == ()
                    if baseline is not None:
                        assert prepared.harness_baseline == baseline
                        assert (
                            prepared.attempt.identity.harness_baseline_digest
                            == baseline.digest
                        )
                    baseline = prepared.harness_baseline
                    attempts.add(prepared.attempt.attempt_id)
                finally:
                    prepared.close()
            assert len(attempts) == 3
        finally:
            snapshot.close()

    def test_conditional_overlap_uses_cell_active_declarations(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("""
[project]
name = "demo"
version = "1"
dependencies = ["idna"]
[dependency-groups]
dev = ["idna>=3; sys_platform == 'linux'"]
[tool.pf]
pythons = ["3.10"]
platforms = ["aarch64-apple-darwin", "x86_64-unknown-linux-gnu"]
""")
        package = ProjectLoader().load(root=tmp_path).target
        uv = RecordingUv()
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        try:
            for cell in package.cells:
                uv.calls.clear()
                prepared = EnvironmentFactory(uv).prepare(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                    source_plan=SourcePlan.for_package(package, "SEARCH"),
                    resolution=HighestResolution(),
                )
                assert isinstance(prepared, PreparedEnvironment)
                try:
                    if "linux" in cell.target:
                        assert uv.calls == [
                            "project",
                            "environment",
                            "install-environment",
                            "inspect",
                        ]
                        assert (
                            prepared.harness_baseline.observations[0].satisfied_by
                            == "PROJECT_GRAPH"
                        )
                        assert prepared.environment_plan is not None
                    else:
                        assert uv.calls == ["project", "install-project", "inspect"]
                        assert prepared.environment_plan is None
                finally:
                    prepared.close()
        finally:
            snapshot.close()

    @pytest.mark.parametrize("mismatch", ["cell", "declarations", "observations"])
    def test_empty_harness_rejects_unrelated_baseline(self, tmp_path, mismatch):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\n[tool.pf]\npythons = ["3.10"]\n'
        )
        package = ProjectLoader().load(root=tmp_path).target
        cell = package.cells[0]
        baseline = HarnessBaseline.from_evidence(
            cell=cell.model_copy(update={"python_minor": "3.11"})
            if mismatch == "cell"
            else cell,
            declaration_ids=("unrelated",) if mismatch == "declarations" else (),
            observations=(
                HarnessSatisfaction(
                    name="idna",
                    version="3.10",
                    source=SourceIdentity(kind="registry"),
                    satisfied_by="EXTERNAL_HARNESS",
                    ceiling_eligible=True,
                ),
            )
            if mismatch == "observations"
            else (),
        )
        uv = RecordingUv()
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        try:
            with pytest.raises(ConfigurationError, match="harness baseline"):
                EnvironmentFactory(uv).prepare(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                    source_plan=SourcePlan.for_package(package, "SEARCH"),
                    resolution=LowestDirectResolution(baseline),
                )
            assert uv.calls == []
        finally:
            snapshot.close()

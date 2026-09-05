from __future__ import annotations

from pathlib import Path

import pytest

from pf.harness import (
    active_harness_requirements,
    harness_requirement_policy,
    original_harness,
    relax_harness,
    render_harness_requirement,
)
from pf.project import ProjectLoader
from pf.resolution import (
    NativeResolutionPlan,
    ResolutionContext,
    ResolutionPlan,
    ResolutionRunContext,
    ResolutionPackage,
)
from pf.schemas.evaluation import ProcessResult
from pf.schemas.config import WorkspacePackage
from pf.schemas.project import (
    HarnessBaseline,
    HarnessResolutionRequirement,
    HarnessSatisfaction,
    SourcePlan,
)


def _load_harness(
    tmp_path: Path,
    requirements: tuple[str, ...],
    *,
    sources: str = "",
    search_prereleases: bool = False,
    unmanaged: tuple[str, ...] = (),
):
    rendered = ",\n        ".join(repr(item) for item in requirements)
    unmanaged_line = f"unmanaged-deps = {list(unmanaged)!r}\n" if unmanaged else ""
    (tmp_path / "pyproject.toml").write_text(
        f"""
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = [
        {rendered}
]

{sources}

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
search-prereleases = {str(search_prereleases).lower()}
{unmanaged_line}test-command = ["pytest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return ProjectLoader().load(root=tmp_path).target


def _project_plan(
    package, packages: tuple[ResolutionPackage, ...] = ()
) -> ResolutionPlan:
    source_plan = SourcePlan.for_package(package, "SEARCH")
    return ResolutionPlan.from_evidence(
        kind="project",
        request_digest="project-request",
        context=ResolutionContext.from_inputs(
            run=ResolutionRunContext(
                uv_version="0.12.5", release_cutoff="2026-09-05T00:00:00Z"
            ),
            cell=package.cells[0],
            source_plan_identity=source_plan.identity,
            uv_project_configuration_identity="uv-config",
        ),
        packages=packages,
        direct_harness=(),
        native=NativeResolutionPlan.from_content(
            'lock-version = "1.0"\npackages = []\n'
        ),
        process=ProcessResult(
            exit_code=0, signal=None, duration_seconds=0.1, stdout="", stderr=""
        ),
    )


def _baseline(package, *, version: str = "8.4") -> HarnessBaseline:
    source_plan = SourcePlan.for_package(package, "SEARCH")
    active = active_harness_requirements(
        package.harness_requirements,
        package.cells[0],
    )
    names = sorted({item.name for item in active})
    observations = tuple(
        HarnessSatisfaction(
            satisfied_by="EXTERNAL_HARNESS",
            name=name,
            version=version,
            source=source_plan.source_for(name),
            ceiling_eligible=any(
                harness_requirement_policy(
                    item,
                    source=source_plan.source_for(item.name),
                ).ceiling_eligible
                for item in active
                if item.name == name
            ),
        )
        for name in names
    )
    return HarnessBaseline.from_evidence(
        cell=package.cells[0],
        declaration_ids=tuple(sorted(item.declaration_id for item in active)),
        observations=observations,
    )


def _render(
    package,
    requirement: HarnessResolutionRequirement,
) -> str:
    source_plan = SourcePlan.for_package(package, "SEARCH")
    return render_harness_requirement(
        requirement,
        source=source_plan.source_for(requirement.declaration.name),
    )


class TestHarnessPlanning:
    def test_project_loader_retains_root_package_and_include_group_provenance(
        self,
        tmp_path: Path,
    ) -> None:
        member = tmp_path / "packages" / "demo"
        member.mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text(
            """
[tool.uv.workspace]
members = ["packages/*"]

[dependency-groups]
test = [{ include-group = "common" }, "pytest>=8"]
common = ["coverage[toml]>=7; python_version >= '3.10'"]

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (member / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = ["pytest>=8", "pluggy<2"]
""".strip()
            + "\n",
            encoding="utf-8",
        )

        package = (
            ProjectLoader()
            .load(
                root=tmp_path,
                selector=WorkspacePackage(canonical_name="demo"),
            )
            .target
        )

        assert [item.name for item in package.harness_requirements] == [
            "coverage",
            "pytest",
            "pytest",
            "pluggy",
        ]
        assert [item.provenance.owner for item in package.harness_requirements] == [
            "root",
            "root",
            "package",
            "package",
        ]
        assert package.harness_requirements[0].provenance.group_path == (
            "test",
            "common",
        )
        assert package.harness_requirements[0].provenance.item_path == (0, 0)
        assert package.harness_requirements[2].provenance.pyproject_path == (
            "packages/demo/pyproject.toml"
        )
        assert len({item.declaration_id for item in package.harness_requirements}) == 4

    def test_project_loader_structures_harness_semantics_once(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(
            tmp_path,
            ("PyTest[testing]>=8,<9,!=8.2; python_version >= '3.10'",),
        )

        requirement = package.harness_requirements[0]

        assert requirement.name == "pytest"
        assert requirement.requested_extras == ("testing",)
        assert [(item.operator, item.version) for item in requirement.specifier] == [
            ("!=", "8.2"),
            ("<", "9"),
            (">=", "8"),
        ]
        assert requirement.marker == 'python_version >= "3.10"'
        assert (
            SourcePlan.for_package(package, "SEARCH").source_for(requirement.name).kind
            == "registry"
        )
        assert requirement.original_text.startswith("PyTest[testing]")

    def test_project_loader_preserves_explicit_prerelease_specifier(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(tmp_path, ("tool>=2.0a1",))

        assert [
            (clause.operator, clause.version)
            for clause in package.harness_requirements[0].specifier
        ] == [(">=", "2.0a1")]

    def test_search_prerelease_policy_is_owned_by_search_configuration(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(
            tmp_path,
            ("tool>=2",),
            search_prereleases=True,
        )

        assert package.config.search.default.prereleases is True
        assert package.harness_requirements[0].original_text == "tool>=2"


class TestHarnessRelaxation:
    @pytest.mark.parametrize("baseline_owner", ("PROJECT_GRAPH", "EXTERNAL_HARNESS"))
    @pytest.mark.parametrize("project_owned", (False, True))
    def test_ceiling_follows_current_graph_ownership(
        self, tmp_path, baseline_owner, project_owned
    ):
        package = _load_harness(tmp_path, ("pytest>=8,<10",))
        baseline = _baseline(package)
        observation = baseline.observations[0].model_copy(
            update={"satisfied_by": baseline_owner}
        )
        baseline = HarnessBaseline.from_evidence(
            cell=baseline.cell,
            declaration_ids=baseline.declaration_ids,
            observations=(observation,),
        )
        project = _project_plan(
            package,
            (
                ResolutionPackage(
                    name="pytest", version="9.0", source=observation.source
                ),
            )
            if project_owned
            else (),
        )
        relaxed = relax_harness(
            package,
            baseline,
            project_plan=project,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )
        requirement = relaxed.requirements[0]
        assert requirement.ceiling == (None if project_owned else "8.4")
        assert _render(package, requirement) == (
            "pytest<10" if project_owned else "pytest<10,<=8.4"
        )

    def test_baseline_identity_binds_satisfaction_owner(self, tmp_path):
        package = _load_harness(tmp_path, ("pytest",))
        baseline = _baseline(package)
        changed = HarnessBaseline.from_evidence(
            cell=baseline.cell,
            declaration_ids=baseline.declaration_ids,
            observations=(
                baseline.observations[0].model_copy(
                    update={"satisfied_by": "PROJECT_GRAPH"}
                ),
            ),
        )
        assert baseline.digest != changed.digest

    def test_original_harness_keeps_active_declaration_semantics(
        self, tmp_path: Path
    ) -> None:
        package = _load_harness(
            tmp_path,
            (
                "pytest>=8; python_version >= '3.10'",
                "pluggy>=1; python_version >= '3.11'",
            ),
        )

        original = original_harness(
            package,
            package.cells[0],
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert [_render(package, item) for item in original] == [
            'pytest>=8; python_version >= "3.10"'
        ]
        assert original[0].relaxed_minimum is False
        assert original[0].ceiling is None

    def test_explicit_minimum_is_removed_and_baseline_ceiling_is_added(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(tmp_path, ("pytest>=8,<9,!=8.2",))

        relaxed = relax_harness(
            package,
            _baseline(package),
            project_plan=_project_plan(package),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        requirement = relaxed.requirements[0]
        assert [(item.operator, item.version) for item in requirement.specifier] == [
            ("!=", "8.2"),
            ("<", "9"),
            ("<=", "8.4"),
        ]
        assert requirement.relaxed_minimum is True
        assert requirement.ceiling == "8.4"
        assert _render(package, requirement) == "pytest!=8.2,<9,<=8.4"

    @pytest.mark.parametrize(
        ("raw", "expected", "ceiling"),
        (
            ("pytest", "pytest<=8.4", "8.4"),
            ("pytest<9,!=8.2", "pytest!=8.2,<9,<=8.4", "8.4"),
            ("pytest~=1.4.5", "pytest<=8.4,~=1.4.5", "8.4"),
            ("pytest==1.4.*", "pytest<=8.4,==1.4.*", "8.4"),
            ("pytest==1.4.5", "pytest==1.4.5", None),
            ("pytest===vendor", "pytest===vendor", None),
        ),
    )
    def test_non_minimum_clauses_follow_independent_ceiling_policy(
        self,
        tmp_path: Path,
        raw: str,
        expected: str,
        ceiling: str | None,
    ) -> None:
        package = _load_harness(tmp_path, (raw,))

        relaxed = relax_harness(
            package,
            _baseline(package),
            project_plan=_project_plan(package),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert _render(package, relaxed.requirements[0]) == expected
        assert relaxed.requirements[0].ceiling == ceiling

    @pytest.mark.parametrize(
        ("source", "raw"),
        (
            (
                '[tool.uv.sources]\npytest = { path = "vendor/pytest" }',
                "pytest>=8",
            ),
            (
                '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
                "[tool.uv.sources]\npytest = { workspace = true }",
                "pytest>=8",
            ),
            (
                '[tool.uv.sources]\npytest = { git = "https://example.test/pytest.git", rev = "0123456789abcdef0123456789abcdef01234567" }',
                "pytest>=8",
            ),
            (
                "",
                "pytest @ https://example.test/pytest.whl#sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ),
        ),
    )
    def test_fixed_sources_are_retained_without_ceiling(
        self,
        tmp_path: Path,
        source: str,
        raw: str,
    ) -> None:
        if "path" in source:
            (tmp_path / "vendor" / "pytest").mkdir(parents=True)
        if "workspace" in source:
            member = tmp_path / "packages" / "pytest"
            member.mkdir(parents=True)
            (member / "pyproject.toml").write_text(
                '[project]\nname = "pytest"\nversion = "8.4"\n',
                encoding="utf-8",
            )
        package = _load_harness(
            tmp_path,
            (raw,),
            sources=source,
            unmanaged=(("pytest",) if "workspace" in source else ()),
        )

        relaxed = relax_harness(
            package,
            _baseline(package),
            project_plan=_project_plan(package),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        requirement = relaxed.requirements[0]
        assert requirement.specifier == requirement.declaration.specifier
        assert requirement.relaxed_minimum is False
        assert requirement.ceiling is None
        assert _render(package, requirement) == raw

    def test_relaxation_replaces_a_prerelease_minimum_with_the_ceiling(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(tmp_path, ("tool>=2.0a1",))

        relaxed = relax_harness(
            package,
            _baseline(package),
            project_plan=_project_plan(package),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert _render(package, relaxed.requirements[0]) == "tool<=8.4"

    def test_marker_inactive_declaration_is_not_part_of_cell_relaxation(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(
            tmp_path,
            (
                "pytest>=8; python_version >= '3.10'",
                "pluggy>=1; python_version >= '3.11'",
            ),
        )

        relaxed = relax_harness(
            package,
            _baseline(package),
            project_plan=_project_plan(package),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert [item.declaration.name for item in relaxed.requirements] == ["pytest"]

    def test_relaxation_rejects_a_baseline_from_different_declarations(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(tmp_path, ("pytest>=8",))
        baseline = _baseline(package).model_copy(update={"declaration_ids": ()})

        with pytest.raises(ValueError, match="does not match active declarations"):
            relax_harness(
                package,
                baseline,
                project_plan=_project_plan(package),
                source_plan=SourcePlan.for_package(package, "SEARCH"),
            )

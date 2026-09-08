import json
import sys

import packaging.markers
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
import pytest

from visible_text import visible_cli_text

from pf.cli import main
from pf.errors import ConfigurationError
from pf.markers import PortableMarker, evaluate_contextual_marker, platform_marker_facts
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder
from pf.schemas.project import ApplySelector
from pf.schemas.report import FloorProjection
from pf.search_space import bind_policy
from pf.candidates import CandidateBuilder
from pf.harness import active_harness_requirements
from pf.schemas.project import (
    AvailableArtifact,
    AvailableCandidate,
    SourcePlan,
    VersionPin,
)
from candidate_fixtures import registry_candidates


TARGETS = (
    "x86_64-unknown-linux-gnu",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
)


def write_project(
    root, dependencies=(), *, optional=None, harness=(), config="", targets=TARGETS
):
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1"\n'
        f"dependencies = {json.dumps(dependencies)}\n"
        "[project.optional-dependencies]\n"
        + "".join(
            f"{key} = {json.dumps(value)}\n" for key, value in (optional or {}).items()
        )
        + '[dependency-groups]\ntest = [{include-group = "common"}]\n'
        + f"common = {json.dumps(harness)}\n"
        + '[tool.pf]\npythons = ["3.11"]\n'
        + f"platforms = {json.dumps(sorted(targets))}\n{config}\n"
    )


class TestMarkerPlanning:
    def test_external_harness_uses_contextual_facts_and_maps_errors_with_provenance(
        self, tmp_path
    ):
        write_project(
            tmp_path,
            harness=(
                'pytest; platform_system == "Windows" and implementation_name == "cpython"',
            ),
        )
        package = ProjectLoader().load(root=tmp_path).target
        for cell in package.cells:
            active = active_harness_requirements(package.harness_requirements, cell)
            assert tuple(item.name for item in active) == (
                ("pytest",) if "windows" in cell.target else ()
            )
        write_project(tmp_path, harness=('pytest; os_name ~= "posix"',))
        package = ProjectLoader().load(root=tmp_path).target
        with pytest.raises(ConfigurationError) as caught:
            active_harness_requirements(package.harness_requirements, package.cells[0])
        assert (
            "external harness pytest: pyproject.toml: root group test/common item"
            in str(caught.value)
        )
        assert "marker comparison cannot be evaluated" in str(caught.value)

    def test_active_markers_determine_candidate_coordinates_and_lower_anchor(
        self, tmp_path
    ):
        write_project(
            tmp_path,
            (
                'idna>=1; os_name == "posix"',
                'idna>=2; platform_system == "Windows"',
                'absent>=9; os_name == "never"',
            ),
            config='search-space = "minors[declaration]"',
        )
        package = ProjectLoader().load(root=tmp_path).target

        class Index:
            def query(self, **kwargs):
                return registry_candidates(
                    tuple(
                        AvailableCandidate(
                            version=version,
                            artifacts=(
                                AvailableArtifact(
                                    filename=f"idna-{version}.tar.gz",
                                    kind="sdist",
                                    content_hash=f"sha256:{'a' * 64}",
                                    locator=f"https://files.example/idna-{version}.tar.gz",
                                ),
                            ),
                        )
                        for version in ("1", "2", "3")
                    )
                )

        builder = CandidateBuilder(Index())
        for cell in package.cells:
            bound = bind_policy(
                package.search_policy_for("idna"),
                declarations=package.declarations,
                cell=cell,
            )
            assert str(bound.declaration) == ("2" if "windows" in cell.target else "1")
            snapshots = builder.build(
                package=package,
                cell=cell,
                baseline=(VersionPin(name="idna", version="3"),),
                source_plan=SourcePlan.for_package(package, "SEARCH"),
            )
            assert tuple(item.dependency for item in snapshots) == ("idna",)
            assert tuple(item.version for item in snapshots[0].candidates) == (
                str(bound.declaration),
            )

    def test_base_optional_and_required_surfaces_use_target_aliases(
        self, tmp_path, monkeypatch
    ):
        write_project(
            tmp_path,
            (
                'colorama>=0.4; platform_system == "Windows"',
                'posix-ipc>=1; os_name == "posix"',
            ),
            optional={
                "win": ['win-extra>=1; os_name == "nt"'],
                "posix": ['posix-extra>=1; platform_system != "Windows"'],
            },
            harness=(
                'demo[win]; platform_system == "Windows"',
                'demo[posix]; os_name == "posix"',
            ),
            config='extra-policy = "none"',
        )
        default = packaging.markers.default_environment()
        plans = []
        for system in ("Linux", "Windows", "Darwin"):
            monkeypatch.setattr(
                packaging.markers,
                "default_environment",
                lambda: {**default, "platform_system": system, "os_name": "wrong"},
            )
            plans.append(ProjectLoader().load(root=tmp_path).target)
        assert plans[0] == plans[1] == plans[2]
        package = plans[0]
        for cell in package.cells:
            windows = "windows" in cell.target
            assert cell.extra_surface == (("win",) if windows else ("posix",))
            active = {
                item.name
                for item in package.declarations
                if item.declaration_id in cell.active_declaration_ids
            }
            assert active == (
                {"colorama", "win-extra"} if windows else {"posix-ipc", "posix-extra"}
            )

    @pytest.mark.parametrize(
        "left,right,target",
        [
            ('sys_platform == "win32"', 'platform_system == "Windows"', TARGETS[2]),
            ('os_name == "posix"', 'sys_platform == "linux"', TARGETS[0]),
            ('os_name == "posix"', 'platform_system == "Darwin"', TARGETS[1]),
        ],
    )
    def test_alias_overlap_is_a_configuration_error(
        self, tmp_path, left, right, target
    ):
        write_project(
            tmp_path, (f"idna>=1; {left}", f"idna>=2; {right}"), targets=(target,)
        )
        with pytest.raises(
            ConfigurationError, match="overlapping declarations for idna"
        ):
            ProjectLoader().load(root=tmp_path)

    @pytest.mark.parametrize("usage", ["base", "optional", "self-reference"])
    @pytest.mark.parametrize(
        "variable",
        [
            "python_full_version",
            "implementation_name",
            "implementation_version",
            "platform_python_implementation",
            "platform_release",
            "platform_version",
            "extra",
            "extras",
            "dependency_groups",
        ],
    )
    def test_inactive_expressions_are_qualified_before_cells(
        self, tmp_path, usage, variable
    ):
        marker = f'os_name == "never" and {variable} == "unused"'
        raw = f"idna>=1; {marker}"
        write_project(
            tmp_path,
            (raw,) if usage == "base" else (),
            optional={"off": (raw,)} if usage == "optional" else {},
            harness=(f"demo; {marker}",) if usage == "self-reference" else (),
            config='extra-policy = "none"',
            targets=(TARGETS[0],),
        )
        with pytest.raises(ConfigurationError) as caught:
            ProjectLoader().load(root=tmp_path)
        message = str(caught.value)
        assert f"unsupported marker dimension: {variable}" in message
        assert "pyproject.toml" in message
        assert (
            "target self-reference"
            if usage == "self-reference"
            else "managed dependency"
        ) in message
        assert str(tmp_path) not in message
        if usage == "self-reference":
            assert "root group test/common item" in message

    @pytest.mark.filterwarnings(
        "ignore:Cyclopts application invoked without tokens:UserWarning"
    )
    @pytest.mark.parametrize("command", ["smoke", "check", "search"])
    @pytest.mark.parametrize("usage", ["base", "self-reference", "preserved"])
    def test_cli_undefined_comparison_is_early_configuration_failure(
        self, tmp_path, monkeypatch, capsys, command, usage
    ):
        write_project(
            tmp_path,
            ('idna>=1; os_name ~= "posix"',) if usage != "self-reference" else (),
            harness=('demo; platform_system ~= "Windows"',)
            if usage == "self-reference"
            else (),
            targets=(TARGETS[0],),
            config="managed-deps = []" if usage == "preserved" else "",
        )
        with pytest.raises(
            ConfigurationError, match="marker comparison cannot be evaluated"
        ):
            ProjectLoader().load(root=tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pf", command])
        with pytest.raises(SystemExit) as caught:
            main()

        output = capsys.readouterr()
        assert caught.value.code == 3, output.err
        assert "marker comparison cannot be evaluated" in visible_cli_text(output.err)
        assert "pyproject.toml" in output.err
        assert "building snapshot" not in output.err
        assert not (tmp_path / "package-floor.json").exists()
        assert not tuple(tmp_path.glob(".pf/**/process-*.log"))
        assert not tuple(tmp_path.glob(".pf/**/snapshot*"))


class TestMarkerProjection:
    def test_posix_floors_cover_linux_and_darwin_with_original_marker(self, tmp_path):
        write_project(tmp_path, ('idna>=1; os_name == "posix"',))
        package = ProjectLoader().load(root=tmp_path).target
        floors = tuple(
            FloorProjection(cell=cell, version="2" if "linux" in cell.target else "3")
            for cell in package.cells
            if cell.active_declaration_ids
        )
        selectors = tuple(
            ApplySelector(
                sys_platform=facts.sys_platform, platform_machine=facts.platform_machine
            )
            for target in TARGETS
            for facts in (platform_marker_facts(target),)
        )
        projection = PackageReportBuilder().project(
            declarations=package.declarations,
            target_cells=package.cells,
            floors=floors,
            selected_selectors=selectors,
            platform_scoped=False,
        )
        assert projection.representable
        for raw in projection.projected_requirements:
            assert 'os_name == "posix"' in str(Requirement(raw).marker)
        for cell in package.cells:
            active = [
                Requirement(raw)
                for raw in projection.projected_requirements
                if PortableMarker.parse(str(Requirement(raw).marker)).evaluate(cell)
            ]
            assert len(active) == (1 if cell.active_declaration_ids else 0)
            if active:
                assert active[0].specifier == (
                    SpecifierSet(">=2" if "linux" in cell.target else ">=3")
                )

    def test_contextual_preserved_declaration_participates_in_group_equivalence(
        self, tmp_path, monkeypatch
    ):
        default = packaging.markers.default_environment()
        monkeypatch.setattr(
            packaging.markers,
            "default_environment",
            lambda: {**default, "python_full_version": "3.11.15"},
        )
        preserved = 'idna==1; os_name == "nt" and python_full_version >= "3.11.5"'
        write_project(tmp_path, ('idna>=1; os_name == "posix"', preserved))
        package = ProjectLoader().load(root=tmp_path).target
        assert sum(item.managed for item in package.declarations) == 1
        selectors = tuple(
            ApplySelector(
                sys_platform=facts.sys_platform, platform_machine=facts.platform_machine
            )
            for target in TARGETS
            for facts in (platform_marker_facts(target),)
        )

        def project():
            return PackageReportBuilder().project(
                declarations=package.declarations,
                target_cells=package.cells,
                floors=tuple(
                    FloorProjection(cell=cell, version="2")
                    for cell in package.cells
                    if "windows" not in cell.target
                ),
                selected_selectors=selectors,
                platform_scoped=False,
            )

        projection = project()
        assert projection.representable
        assert preserved in projection.projected_requirements
        for cell in package.cells:
            assert (
                sum(
                    evaluate_contextual_marker(str(Requirement(raw).marker), cell)
                    for raw in projection.projected_requirements
                )
                == 1
            )
        monkeypatch.setattr(
            packaging.markers,
            "default_environment",
            lambda: {**default, "python_full_version": "3.11.0"},
        )
        assert not project().representable

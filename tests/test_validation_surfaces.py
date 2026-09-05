from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from pf.errors import ConfigurationError, InfrastructureError
from pf.project import ProjectLoader
from pf.schemas.config import CheckRequest, WorkspacePackage
from pf.schemas.project import cell_id
from pf.snapshot import SnapshotBuilder
from pf.verification import CheckCellOperations, VerificationRunner
from pf.workflow import CheckCommandWorkflow


def write_project(
    root: Path,
    requirements: tuple[str, ...],
    *,
    policy: str = "each",
    custom: tuple[tuple[str, ...], ...] = (),
    version: str = 'version = "1.0"',
    optional: str = 'socks = ["PySocks>=1.5"]\nsecurity = []\nuse_chardet = ["chardet>=3"]',
    pythons: bool = True,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'''[project]
name = "demo-project"
{version}
[project.optional-dependencies]
{optional}
[dependency-groups]
test = [{{ include-group = "common" }}]
common = {json.dumps(requirements)}
[tool.pf]
{('pythons = ["3.10", "3.11"]' if pythons else "")}
platforms = ["x86_64-unknown-linux-gnu"]
extra-policy = "{policy}"
extra-surfaces = {json.dumps(custom)}
''',
        encoding="utf-8",
    )


class TestRequiredSurfaces:
    @pytest.mark.parametrize(
        ("root_config", "member_config", "artifact", "command"),
        (
            ("", "", "any", ("pytest",)),
            (
                'resolve-artifact = "wheel"\ntest-command = ["python", "-m", "pytest"]',
                "",
                "wheel",
                ("python", "-m", "pytest"),
            ),
            (
                'resolve-artifact = "wheel"\ntest-command = ["python", "-m", "pytest"]',
                'resolve-artifact = "sdist"\ntest-command = ["custom-test"]',
                "sdist",
                ("custom-test",),
            ),
            (
                'resolve-artifact = "wheel"',
                'resolve-artifact = "any"',
                "any",
                ("pytest",),
            ),
        ),
    )
    def test_loader_materializes_layered_validation_defaults(
        self, tmp_path, root_config, member_config, artifact, command
    ):
        member = tmp_path / "packages" / "demo"
        write_project(member, (), policy="none")
        path = member / "pyproject.toml"
        path.write_text(path.read_text() + member_config + "\n")
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["packages/*"]\n[tool.pf]\n'
            + root_config
            + "\n"
        )
        package = (
            ProjectLoader()
            .load(
                root=tmp_path, selector=WorkspacePackage(canonical_name="demo-project")
            )
            .target
        )
        assert package.config.resolution.artifact == artifact
        assert package.config.test.command == command
        assert package.test_group_present

    @pytest.mark.parametrize(
        ("policy", "custom", "expected"),
        (
            ("none", (), (("socks",),)),
            ("each", (), (("socks",), ("socks", "use_chardet"))),
            (
                "all",
                (),
                (
                    ("socks",),
                    ("socks", "use_chardet"),
                ),
            ),
            (
                "none",
                (("security",), ("socks", "security")),
                (("socks",), ("security", "socks")),
            ),
            *(
                (
                    policy,
                    (("security",),),
                    (("socks",), ("security", "socks"), ("socks", "use_chardet")),
                )
                for policy in ("each", "all")
            ),
        ),
    )
    def test_loader_unions_required_and_explored_surfaces(
        self, tmp_path, policy, custom, expected
    ):
        write_project(
            tmp_path,
            ("Demo.Project[socks]", "demo_project", "pytest>=8"),
            policy=policy,
            custom=custom,
        )
        package = ProjectLoader().load(root=tmp_path).target
        assert (
            tuple(
                cell.extra_surface
                for cell in package.cells
                if cell.python_minor == "3.10"
            )
            == expected
        )
        assert len({cell_id(cell) for cell in package.cells}) == 2 * len(expected)
        assert [item.name for item in package.harness_requirements] == ["pytest"]
        assert {route.dependency for route in package.source_routes} == {
            "pysocks",
            "chardet",
            "pytest",
        }
        socks = next(item for item in package.declarations if item.name == "pysocks")
        assert all(
            socks.declaration_id in cell.active_declaration_ids
            for cell in package.cells
        )

    @pytest.mark.parametrize("policy", ("none", "each", "all"))
    @pytest.mark.parametrize("requested", ("socks", "socks,security"))
    def test_loader_deduplicates_empty_and_singleton_selectable(
        self, tmp_path, policy, requested
    ):
        write_project(
            tmp_path,
            (f"demo-project[{requested}]",),
            policy=policy,
            optional="socks = []\nsecurity = []",
        )
        package = ProjectLoader().load(root=tmp_path).target
        surfaces = [
            cell.extra_surface for cell in package.cells if cell.python_minor == "3.10"
        ]
        expected: list[tuple[str, ...]] = (
            [("security", "socks")] if requested == "socks,security" else [("socks",)]
        )
        assert surfaces == expected
        assert package.harness_requirements == ()

    @pytest.mark.parametrize("policy", ("none", "each", "all"))
    def test_loader_keeps_base_when_all_declared_extra_groups_are_empty(
        self, tmp_path, policy
    ):
        write_project(tmp_path, (), policy=policy, optional="security = []")
        package = ProjectLoader().load(root=tmp_path).target
        assert [cell.extra_surface for cell in package.cells] == [(), ()]

    @pytest.mark.parametrize("policy", ("each", "all"))
    def test_loader_explores_nonempty_group_with_inactive_dependencies(
        self, tmp_path, policy
    ):
        write_project(
            tmp_path,
            (),
            policy=policy,
            optional='dormant = ["dep; python_version < \'3.10\'"]\nempty = []',
        )
        package = ProjectLoader().load(root=tmp_path).target
        assert [cell.extra_surface for cell in package.cells] == [
            (), ("dormant",), (), ("dormant",)
        ]
        assert all(cell.active_declaration_ids == () for cell in package.cells)

    def test_loader_unions_root_member_and_include_references(self, tmp_path):
        member = tmp_path / "packages" / "demo"
        write_project(member, ("demo-project[use-chardet]", "pytest>=8"), policy="none")
        (tmp_path / "pyproject.toml").write_text("""[tool.uv.workspace]
members = ["packages/*"]
[dependency-groups]
test = [{ include-group = "root-common" }]
root-common = ["Demo_Project[socks]", "demo-project"]
""")
        package = (
            ProjectLoader()
            .load(
                root=tmp_path, selector=WorkspacePackage(canonical_name="demo-project")
            )
            .target
        )
        assert {cell.extra_surface for cell in package.cells} == {
            ("socks", "use_chardet")
        }
        assert [item.name for item in package.harness_requirements] == ["pytest"]
        assert package.harness_requirements[0].provenance.owner == "package"

    @pytest.mark.parametrize(
        ("target", "marker", "active"),
        (
            ("x86_64-unknown-linux-gnu", 'sys_platform == "linux"', True),
            ("aarch64-apple-darwin", 'platform_machine == "arm64"', True),
            ("x86_64-pc-windows-msvc", 'platform_machine == "AMD64"', True),
            ("x86_64-pc-windows-msvc", 'sys_platform == "linux"', False),
        ),
    )
    def test_loader_projects_target_and_python_markers(
        self, tmp_path, target, marker, active
    ):
        write_project(
            tmp_path,
            (f'demo-project[socks]; {marker} and python_version < "3.11"',),
            policy="none",
        )
        path = tmp_path / "pyproject.toml"
        path.write_text(path.read_text().replace("x86_64-unknown-linux-gnu", target))
        package = ProjectLoader().load(root=tmp_path).target
        assert [cell.extra_surface for cell in package.cells] == [
            ("socks",) if active else (),
            (),
        ]

    @pytest.mark.parametrize(
        "marker",
        (
            'python_full_version >= "3.10.0"',
            'os_name == "posix"',
            'platform_release == "6"',
            'platform_system == "Linux"',
            'platform_version == "6"',
            'implementation_name == "cpython"',
            'implementation_version >= "3.10"',
            'platform_python_implementation == "CPython"',
            'extra == "socks"',
            '"socks" in extras',
            '"test" in dependency_groups',
        ),
    )
    def test_loader_rejects_unprojectable_self_reference_markers(
        self, tmp_path, marker
    ):
        write_project(tmp_path, (f"demo-project[socks]; {marker}",))
        with pytest.raises(
            ConfigurationError, match="unsupported self-reference marker dimension"
        ) as caught:
            ProjectLoader().load(root=tmp_path)
        assert "pyproject.toml" in str(caught.value)
        assert "test/common" in str(caught.value)

    @pytest.mark.parametrize(
        ("requirement", "version", "message"),
        (
            (
                "demo-project[missing]",
                'version = "1.0"',
                "unknown self-reference extra",
            ),
            ("demo-project[socks]>=2", 'version = "1.0"', "specifier does not match"),
            (
                "demo-project[socks]>=1",
                'dynamic = ["version"]',
                "requires a static target version",
            ),
            (
                "demo-project[socks] @ https://example.test/demo.tar.gz",
                'version = "1.0"',
                "cannot replace target source",
            ),
            (
                "demo-project @ git+https://example.test/demo.git",
                'version = "1.0"',
                "cannot replace target source",
            ),
            (
                "demo-project @ file:///tmp/demo",
                'version = "1.0"',
                "cannot replace target source",
            ),
        ),
    )
    def test_loader_rejects_invalid_surface_contract(
        self, tmp_path, requirement, version, message
    ):
        write_project(tmp_path, (requirement,), version=version)
        with pytest.raises(ConfigurationError, match=message):
            ProjectLoader().load(root=tmp_path)

    def test_loader_rejects_ambiguous_extra_declarations(self, tmp_path):
        write_project(
            tmp_path,
            ("demo-project[my-extra]",),
            optional="my_extra = []\nmy-extra = []",
        )
        with pytest.raises(ConfigurationError, match="ambiguous project extra name"):
            ProjectLoader().load(root=tmp_path)

    @pytest.mark.parametrize("version", ('version = "1.0"', 'dynamic = ["version"]'))
    def test_loader_accepts_unversioned_self_reference(self, tmp_path, version):
        write_project(
            tmp_path, ("demo-project[socks]",), version=version, policy="none"
        )
        assert {
            cell.extra_surface
            for cell in ProjectLoader().load(root=tmp_path).target.cells
        } == {("socks",)}

    def test_loader_accepts_matching_static_and_inactive_specifiers(self, tmp_path):
        write_project(
            tmp_path,
            ("demo-project[socks]>=1,<2", 'demo-project>=2; python_version < "3.10"'),
            policy="none",
        )
        assert {
            cell.extra_surface
            for cell in ProjectLoader().load(root=tmp_path).target.cells
        } == {("socks",)}

    @pytest.mark.parametrize("fails", (False, True))
    def test_workflow_qualifies_after_python_discovery_before_snapshot_and_attempt(
        self, tmp_path, fails
    ):
        write_project(
            tmp_path, ('demo-project>=2; python_version == "3.11"',), pythons=False
        )
        calls = []

        class Pythons:
            def available_cpython_minors(self, *, root):
                calls.append(root)
                if fails:
                    raise InfrastructureError("python discovery failed")
                return ("3.11",)

        class Never:
            def __getattr__(self, name):
                raise AssertionError(f"qualification must fail before {name}")

        class Events:
            def consume(self, event):
                pass

        with pytest.raises(
            InfrastructureError if fails else ConfigurationError,
            match="python discovery failed" if fails else "specifier does not match",
        ):
            CheckCommandWorkflow(
                projects=ProjectLoader(pythons=Pythons()),
                snapshots=cast(SnapshotBuilder, Never()),
                checker=cast(CheckCellOperations, Never()),
                verification=cast(VerificationRunner, Never()),
                events=Events(),
            ).run(CheckRequest(root=str(tmp_path)))
        assert calls == [tmp_path]

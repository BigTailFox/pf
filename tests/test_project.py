from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from pf.errors import ConfigurationError
from pf.project import ProjectLoader, host_target, marker_platform
from pf.project_discovery import PackageLocation, ProjectDiscovery
from pf.schemas.config import RootPackage, WorkspacePackage
from pf.schemas.project import (
    DependencySourceRoute,
    DynamicWorkspaceMemberVersion,
    ResolutionSourceMode,
    SourcePlan,
    SourceIdentity,
    StaticWorkspaceMemberVersion,
)


def write_basic_project(
    tmp_path: Path, configuration: str, *, project: str = ""
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f"""
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]
{project}

{configuration}

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


class TestProjectDiscovery:
    @pytest.mark.parametrize(
        "document",
        (
            "tool = 1\n",
            "[tool]\nuv = 1\n",
            "[tool.uv]\nworkspace = 1\n",
            "[tool]\npf = 1\n",
            "[tool.pf]\npackage = 1\n",
            "[tool.pf.package]\ndemo = 1\n",
            '[tool.uv.workspace]\nmembers = "packages/*"\n',
            "[tool.uv.workspace]\nmembers = [1]\n",
        ),
        ids=(
            "tool",
            "uv",
            "workspace",
            "pf",
            "package-map",
            "package-entry",
            "member-string",
            "member-item",
        ),
    )
    def test_project_discovery_rejects_malformed_workspace_metadata(
        self,
        tmp_path: Path,
        document: str,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(document, encoding="utf-8")

        with pytest.raises(ConfigurationError):
            ProjectDiscovery().select(root=tmp_path, selector=RootPackage())

    def test_project_discovery_rejects_an_invalid_project_name(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = ""\nversion = "1"\n',
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match="invalid project name"):
            ProjectDiscovery().select(root=tmp_path, selector=RootPackage())

    def test_single_package_loads_normalized_declarations_and_cells(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "Demo_App"
    version = "0.1.0"
    requires-python = ">=3.10"
    dependencies = [
        "NumPy>=1.24,<2",
        "click==8.1.8",
        "requests>=2; python_version >= '3.11'",
    ]

    [project.optional-dependencies]
    gpu = ["torch>=2"]

    [dependency-groups]
    test = ["pytest"]

    [tool.pf]
    python = ["3.10", "3.11"]
    platform = ["x86_64-unknown-linux-gnu"]
    extras = "each"
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        plan = ProjectLoader().load(root=tmp_path)

        package = plan.target
        assert package.name == "demo-app"
        assert [(item.name, item.kind) for item in package.declarations] == [
            ("numpy", "searchable"),
            ("click", "fixed"),
            ("requests", "searchable"),
            ("torch", "searchable"),
        ]
        assert len({item.declaration_id for item in package.declarations}) == 4
        assert [item.name for item in package.harness_requirements] == ["pytest"]
        assert [cell.extra_surface for cell in package.cells] == [
            (),
            ("gpu",),
            (),
            ("gpu",),
        ]
        assert [cell.python_minor for cell in package.cells] == [
            "3.10",
            "3.10",
            "3.11",
            "3.11",
        ]
        requests_id = next(
            item.declaration_id
            for item in package.declarations
            if item.name == "requests"
        )
        assert all(
            requests_id not in cell.active_declaration_ids for cell in package.cells[:2]
        )
        assert all(
            requests_id in cell.active_declaration_ids for cell in package.cells[2:]
        )

    def test_workspace_selects_exactly_one_named_member_and_keeps_owned_paths(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [tool.uv.workspace]
    members = ["packages/*"]

    """.strip()
            + "\n",
            encoding="utf-8",
        )
        for name in ("alpha", "beta"):
            member = tmp_path / "packages" / name
            member.mkdir(parents=True)
            (member / "pyproject.toml").write_text(
                f'[project]\nname = "{name}"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )

        plan = ProjectLoader().load(
            root=tmp_path,
            selector=WorkspacePackage(canonical_name="beta"),
        )

        assert plan.target.name == "beta"
        assert plan.target.pyproject_path == "packages/beta/pyproject.toml"
        assert plan.owned_pyproject_paths == (
            "packages/alpha/pyproject.toml",
            "packages/beta/pyproject.toml",
            "pyproject.toml",
        )

    def test_non_package_root_requires_an_explicit_member_selector(
        self,
        tmp_path: Path,
    ) -> None:
        member = tmp_path / "packages" / "demo"
        member.mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
            encoding="utf-8",
        )
        (member / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\n',
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError) as caught:
            ProjectDiscovery().select(root=tmp_path, selector=RootPackage())

        assert "workspace root has no installable [project]" in str(caught.value)
        assert caught.value.candidates == ("demo",)

    @pytest.mark.parametrize(
        ("configuration", "field"),
        (
            ('packages = ["demo"]', "packages"),
            ('exclude-packages = ["demo"]', "exclude-packages"),
            ('[tool.pf.package.demo]\npath = "packages/demo"', "path"),
        ),
    )
    def test_legacy_package_selection_configuration_is_rejected(
        self,
        tmp_path: Path,
        configuration: str,
        field: str,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "demo"\nversion = "1"\n[tool.pf]\n{configuration}\n',
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError) as caught:
            ProjectLoader().load(root=tmp_path)

        assert field in str(caught.value)
        assert "--package" in str(caught.value)

    def test_project_plan_owns_recursive_in_tree_path_package_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        first = tmp_path / "vendor" / "first"
        second = tmp_path / "vendor" / "second"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\n'
            'dependencies = ["first"]\n'
            '[tool.uv.sources]\nfirst = { path = "vendor/first" }\n'
            '[tool.pf]\npython = ["3.10"]\n'
            'platform = ["x86_64-unknown-linux-gnu"]\n'
            'test-command = ["pytest"]\n',
            encoding="utf-8",
        )
        (first / "pyproject.toml").write_text(
            '[project]\nname = "first"\nversion = "1"\n'
            '[tool.uv.sources]\nsecond = { path = "../second" }\n',
            encoding="utf-8",
        )
        (second / "pyproject.toml").write_text(
            '[project]\nname = "second"\nversion = "1"\n',
            encoding="utf-8",
        )

        plan = ProjectLoader().load(root=tmp_path)

        assert plan.owned_pyproject_paths == (
            "pyproject.toml",
            "vendor/first/pyproject.toml",
            "vendor/second/pyproject.toml",
        )

    def test_project_discovery_rejects_duplicate_canonical_names_before_selection(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "packages" / "first").mkdir(parents=True)
        (tmp_path / "packages" / "second").mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
            encoding="utf-8",
        )
        (tmp_path / "packages" / "first" / "pyproject.toml").write_text(
            '[project]\nname = "Demo_Package"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        (tmp_path / "packages" / "second" / "pyproject.toml").write_text(
            '[project]\nname = "demo-package"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError) as caught:
            ProjectDiscovery().select(
                root=tmp_path,
                selector=WorkspacePackage(canonical_name="demo-package"),
            )

        assert "duplicate canonical package name: demo-package" in str(caught.value)
        assert "packages/first" in str(caught.value)
        assert "packages/second" in str(caught.value)


class TestProjectPlanning:
    @pytest.mark.parametrize("mode", ("DEVELOPMENT", "SEARCH"))
    def test_source_plan_selects_the_effective_source_for_its_mode(
        self,
        tmp_path: Path,
        mode: ResolutionSourceMode,
    ) -> None:
        write_basic_project(tmp_path, "")
        package = ProjectLoader().load(root=tmp_path).target

        plan = SourcePlan.for_package(package, mode)

        route = package.source_routes[0]
        expected = (
            route.development_source if mode == "DEVELOPMENT" else route.search_source
        )
        assert plan.source_for("idna") == expected

    @pytest.mark.parametrize(
        ("mode", "expected"),
        (("DEVELOPMENT", "workspace"), ("SEARCH", "registry")),
    )
    def test_source_plan_selects_each_side_of_a_dual_route(
        self,
        mode: ResolutionSourceMode,
        expected: str,
    ) -> None:
        plan = SourcePlan(
            source_mode=mode,
            routes=(
                DependencySourceRoute(
                    dependency="member",
                    development_source=SourceIdentity(
                        kind="workspace", locator="packages/member"
                    ),
                    search_source=SourceIdentity(kind="registry"),
                    workspace_member_version=StaticWorkspaceMemberVersion(
                        value="1.2"
                    ),
                ),
            ),
        )

        assert plan.source_for("member").kind == expected

    def test_search_source_plan_reports_only_registry_routed_workspace_dependencies(
        self,
    ) -> None:
        registry = SourceIdentity(kind="registry", locator="https://pypi.org/simple")
        local = SourceIdentity(kind="workspace", locator="packages/local")
        managed = SourceIdentity(kind="workspace", locator="packages/managed")
        plan = SourcePlan(
            source_mode="SEARCH",
            routes=(
                DependencySourceRoute(
                    dependency="local",
                    development_source=local,
                    search_source=local,
                    workspace_member_version=DynamicWorkspaceMemberVersion(),
                ),
                DependencySourceRoute(
                    dependency="managed",
                    development_source=managed,
                    search_source=registry,
                    workspace_member_version=StaticWorkspaceMemberVersion(value="1.2"),
                ),
            ),
        )

        assert plan.registry_routed_workspace_dependencies() == ("managed",)

        development = plan.model_copy(update={"source_mode": "DEVELOPMENT"})
        assert development.registry_routed_workspace_dependencies() == ()

    @pytest.mark.parametrize(
        "member_version",
        (
            StaticWorkspaceMemberVersion(value="1.2"),
            DynamicWorkspaceMemberVersion(),
        ),
    )
    def test_source_plan_returns_frozen_workspace_member_metadata(
        self,
        member_version: StaticWorkspaceMemberVersion
        | DynamicWorkspaceMemberVersion,
    ) -> None:
        workspace = SourceIdentity(kind="workspace", locator="packages/member")
        plan = SourcePlan(
            source_mode="DEVELOPMENT",
            routes=(
                DependencySourceRoute(
                    dependency="member",
                    development_source=workspace,
                    search_source=workspace,
                    workspace_member_version=member_version,
                ),
                DependencySourceRoute(
                    dependency="registry",
                    development_source=SourceIdentity(kind="registry"),
                    search_source=SourceIdentity(kind="registry"),
                ),
            ),
        )

        assert plan.workspace_member_version_for("member") is member_version
        assert plan.workspace_member_version_for("registry") is None

    @pytest.mark.parametrize(
        "query",
        (SourcePlan.source_for, SourcePlan.workspace_member_version_for),
    )
    def test_source_plan_rejects_a_missing_dependency(
        self,
        query: Callable[[SourcePlan, str], object],
    ) -> None:
        plan = SourcePlan(source_mode="SEARCH", routes=())

        with pytest.raises(ValueError, match="source plan route is missing: missing"):
            query(plan, "missing")

    def test_source_plan_rejects_unsorted_or_duplicate_routes(self) -> None:
        registry = SourceIdentity(kind="registry")
        route = DependencySourceRoute(
            dependency="demo",
            development_source=registry,
            search_source=registry,
        )

        with pytest.raises(ValueError, match="routes must be sorted and unique"):
            SourcePlan(source_mode="SEARCH", routes=(route, route))

    def test_source_plan_identity_is_stable_across_queries_and_wire_round_trip(
        self,
    ) -> None:
        registry = SourceIdentity(kind="registry")
        plan = SourcePlan(
            source_mode="SEARCH",
            routes=(
                DependencySourceRoute(
                    dependency="demo",
                    development_source=registry,
                    search_source=registry,
                ),
            ),
        )
        original_hash = hash(plan)
        original_dump = plan.model_dump(mode="json")

        plan.source_for("demo")
        plan.workspace_member_version_for("demo")
        plan.registry_routed_workspace_dependencies()
        round_trip = SourcePlan.model_validate_json(plan.model_dump_json())

        assert plan.identity == (
            "7c3cbb244a395ac45e1f54cd5eef15d3d29847b9c52c2649b28234aa3a71b795"
        )
        assert round_trip.identity == plan.identity
        assert plan == round_trip
        assert hash(plan) == original_hash
        assert plan.model_dump(mode="json") == original_dump
        assert tuple(original_dump) == ("source_mode", "routes")

    def test_workspace_routes_only_target_direct_dependencies_by_management_policy(
        self,
        tmp_path: Path,
    ) -> None:
        member_versions = {
            "managed-lib": "1.2",
            "optional-lib": "2.0",
            "unmanaged-lib": "3.0",
            "fixed-lib": "4.0",
            "orphan-lib": "5.0",
        }
        for name, version in member_versions.items():
            member = tmp_path / "packages" / name
            member.mkdir(parents=True)
            dependency = (
                '\ndependencies = ["transitive-only>=1"]'
                if name == "orphan-lib"
                else ""
            )
            (member / "pyproject.toml").write_text(
                f'[project]\nname = "{name}"\nversion = "{version}"{dependency}\n',
                encoding="utf-8",
            )
        (tmp_path / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"
dependencies = [
  "managed-lib>=1",
  "unmanaged-lib>=1",
  "fixed-lib==4.0",
]

[project.optional-dependencies]
feature = ["optional-lib>=2"]

[tool.uv.sources]
managed-lib = { workspace = true }
optional-lib = { workspace = true }
unmanaged-lib = { workspace = true }
fixed-lib = { workspace = true }

[tool.uv.workspace]
members = ["packages/*"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
extras = "each"
unmanaged-deps = ["unmanaged-lib"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )

        package = ProjectLoader().load(root=tmp_path).target

        declarations = {item.name: item for item in package.declarations}
        routes = {item.dependency: item for item in package.source_routes}
        assert set(declarations) == {
            "fixed-lib",
            "managed-lib",
            "optional-lib",
            "unmanaged-lib",
        }
        assert "orphan-lib" not in routes
        assert "transitive-only" not in routes
        assert declarations["managed-lib"].managed is True
        assert declarations["optional-lib"].managed is True
        assert declarations["unmanaged-lib"].managed is False
        assert declarations["fixed-lib"].kind == "fixed"
        assert declarations["fixed-lib"].managed is False
        registry = SourceIdentity(kind="registry", locator="https://pypi.org/simple")
        for name in ("managed-lib", "optional-lib"):
            assert routes[name].development_source.kind == "workspace"
            assert routes[name].search_source == registry
        for name in ("unmanaged-lib", "fixed-lib"):
            assert routes[name].development_source.kind == "workspace"
            assert routes[name].search_source == routes[name].development_source
        assert routes["managed-lib"].workspace_member_version == (
            StaticWorkspaceMemberVersion(value="1.2")
        )

    def test_uv_sources_are_classified_once_and_credentials_are_not_serialized(
        self,
        tmp_path: Path,
    ) -> None:
        local = tmp_path / "packages" / "local-lib"
        local.mkdir(parents=True)
        (local / "pyproject.toml").write_text(
            '[project]\nname = "local-lib"\nversion = "1.0"\n',
            encoding="utf-8",
        )
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["local-lib", "git-lib"]

    [tool.uv.sources]
    local-lib = { workspace = true }
    git-lib = { git = "https://token@example.test/repo.git", rev = "0123456789abcdef0123456789abcdef01234567" }

    [tool.uv.workspace]
    members = ["packages/*"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        package = ProjectLoader().load(root=tmp_path).target

        assert [
            (item.name, item.kind, item.managed) for item in package.declarations
        ] == [
            ("local-lib", "searchable", True),
            ("git-lib", "fixed", False),
        ]
        routes = {item.dependency: item for item in package.source_routes}
        assert routes["git-lib"].development_source.commit == (
            "0123456789abcdef0123456789abcdef01234567"
        )
        assert routes["local-lib"].development_source == SourceIdentity(
            kind="workspace", locator="packages/local-lib"
        )
        assert routes["local-lib"].search_source == SourceIdentity(
            kind="registry", locator="https://pypi.org/simple"
        )
        assert routes["local-lib"].workspace_member_version == (
            StaticWorkspaceMemberVersion(value="1.0")
        )
        assert "token" not in package.model_dump_json()

    def test_harness_source_is_owned_by_the_canonical_source_plan(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "vendor" / "pytest").mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = ["pytest"]

[tool.uv.sources]
pytest = { path = "vendor/pytest" }

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )

        package = ProjectLoader().load(root=tmp_path).target

        assert package.source_routes[0].dependency == "pytest"
        assert package.source_routes[0].development_source == SourceIdentity(
            kind="path", locator="vendor/pytest"
        )
        assert package.source_routes[0].search_source == (
            package.source_routes[0].development_source
        )

    def test_overlapping_same_location_declarations_are_rejected(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = [
        "idna>=3; python_version >= '3.10'",
        "idna>=2; python_version < '3.12'",
    ]

    [tool.pf]
    python = ["3.11"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(
            ConfigurationError,
            match="overlapping declarations for idna in base",
        ):
            ProjectLoader().load(root=tmp_path)

    def test_explicit_extra_surfaces_must_cover_base_and_each_extra(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [project.optional-dependencies]
    cuda = ["cuda-lib"]
    arrow = ["arrow-lib"]

    [tool.pf]
    python = ["3.11"]
    platform = ["x86_64-unknown-linux-gnu"]
    extra-surfaces = [[], ["cuda", "arrow"]]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(
            ConfigurationError,
            match="extra-surfaces must include each single extra: arrow",
        ):
            ProjectLoader().load(root=tmp_path)

    def test_fixed_dependency_cannot_be_explicitly_managed(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["requests==2.32.5"]

    [tool.pf]
    managed-deps = ["requests"]
    python = ["3.11"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(
            ConfigurationError,
            match="fixed dependency cannot be managed: requests",
        ):
            ProjectLoader().load(root=tmp_path)

    def test_managed_dependency_rejects_marker_dimensions_apply_cannot_project(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna; implementation_name == 'cpython'"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(
            ConfigurationError, match="unsupported managed marker dimension"
        ):
            ProjectLoader().load(root=tmp_path)

    @pytest.mark.parametrize(
        "url",
        (
            "https://token@example.test/simple",
            "https://example.test/simple?channel=private",
            "http://example.test/simple",
        ),
    )
    def test_unsafe_named_registry_source_is_rejected_before_serialization(
        self,
        tmp_path: Path,
        url: str,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            f"""
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna"]

    [[tool.uv.index]]
    name = "private"
    url = "{url}"
    explicit = true

    [tool.uv.sources]
    idna = {{ index = "private" }}

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(
            ConfigurationError,
            match="unsafe registry URL for uv index: private",
        ):
            ProjectLoader().load(root=tmp_path)

    def test_default_uv_index_becomes_the_source_for_unpinned_registry_dependencies(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna"]

    [[tool.uv.index]]
    name = "internal"
    url = "https://packages.example/simple"
    default = true

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        source = (
            ProjectLoader().load(root=tmp_path).target.source_routes[0].search_source
        )

        assert source == SourceIdentity(
            kind="registry",
            index="internal",
            locator="https://packages.example/simple",
        )

    def test_omitted_python_matrix_uses_available_stable_minors_within_requires_python(
        self,
        tmp_path: Path,
    ) -> None:
        class Pythons:
            def available_cpython_minors(self, *, root: Path) -> tuple[str, ...]:
                return ("3.10", "3.11", "3.12")

        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    requires-python = ">=3.11,<3.13"

    [tool.pf]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        package = (
            ProjectLoader(pythons=Pythons())
            .load(
                root=tmp_path,
            )
            .target
        )

        assert [cell.python_minor for cell in package.cells] == ["3.11", "3.12"]

    def test_direct_url_requirement_requires_hash_and_records_public_source_identity(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["demo-lib @ https://example.test/demo.whl#sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        package = ProjectLoader().load(root=tmp_path).target
        declaration = package.declarations[0]

        assert declaration.kind == "fixed"
        assert package.source_routes[0].development_source == SourceIdentity(
            kind="url",
            locator="https://example.test/demo.whl",
            content_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        assert "example.test" in declaration.model_dump_json()


class TestTargetPlatform:
    def test_target_triple_uses_runtime_platform_machine_marker_values(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna; platform_machine == 'arm64'"]

    [tool.pf]
    python = ["3.10"]
    platform = ["aarch64-apple-darwin"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        package = ProjectLoader().load(root=tmp_path).target

        assert package.cells[0].active_declaration_ids == (
            package.declarations[0].declaration_id,
        )

    @pytest.mark.parametrize(
        ("target", "expected"),
        (
            (
                "x86_64-unknown-linux-musl",
                {"sys_platform": "linux", "platform_machine": "x86_64"},
            ),
            (
                "aarch64-apple-darwin",
                {"sys_platform": "darwin", "platform_machine": "arm64"},
            ),
            (
                "x86_64-pc-windows-msvc",
                {"sys_platform": "win32", "platform_machine": "AMD64"},
            ),
            (
                "aarch64-pc-windows-msvc",
                {"sys_platform": "win32", "platform_machine": "ARM64"},
            ),
        ),
    )
    def test_marker_platform_exposes_pep_508_runtime_values(
        self,
        target: str,
        expected: dict[str, str],
    ) -> None:
        assert marker_platform(target) == expected

    def test_marker_platform_rejects_unknown_target_families(self) -> None:
        with pytest.raises(ConfigurationError, match="unsupported target platform"):
            marker_platform("wasm32-unknown-unknown")

    @pytest.mark.parametrize(
        ("sys_platform", "machine", "libc", "expected"),
        (
            ("linux", "AMD64", ("musl", "1.2"), "x86_64-unknown-linux-musl"),
            ("darwin", "arm64", ("", ""), "aarch64-apple-darwin"),
            ("win32", "AMD64", ("", ""), "x86_64-pc-windows-msvc"),
        ),
    )
    def test_host_target_normalizes_supported_runtime_platforms(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sys_platform: str,
        machine: str,
        libc: tuple[str, str],
        expected: str,
    ) -> None:
        monkeypatch.setattr("sys.platform", sys_platform)
        monkeypatch.setattr("platform.machine", lambda: machine)
        monkeypatch.setattr("platform.libc_ver", lambda: libc)

        assert host_target() == expected

    def test_host_target_rejects_an_unsupported_runtime(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.platform", "emscripten")
        monkeypatch.setattr("platform.machine", lambda: "wasm32")
        with pytest.raises(ConfigurationError, match="unsupported host platform"):
            host_target()


class TestProjectLoader:
    def test_project_loader_rejects_identity_drift_during_loading(
        self,
        tmp_path: Path,
    ) -> None:
        write_basic_project(tmp_path, "")

        class DriftingDiscovery(ProjectDiscovery):
            def select(
                self,
                *,
                root: Path,
                selector: object,
            ) -> PackageLocation:
                del selector
                return PackageLocation(
                    name="other",
                    package_root=root,
                    pyproject_path=root / "pyproject.toml",
                    report_path=root / "package-floor.json",
                )

        with pytest.raises(ConfigurationError, match="identity changed"):
            ProjectLoader(discovery=DriftingDiscovery()).load(
                root=tmp_path,
            )

    @pytest.mark.parametrize(
        ("configuration", "message"),
        (
            ('[tool.uv]\nindex = ["bad"]', "invalid uv index declaration"),
            ('[[tool.uv.index]]\nname = "missing-url"', "requires name and url"),
            (
                '[[tool.uv.index]]\nname = "flat"\nurl = "https://example.test"\nformat = "flat"',
                "unsupported uv index format",
            ),
            (
                '[[tool.uv.index]]\nname = "same"\nurl = "https://one.test/simple"\n'
                '[[tool.uv.index]]\nname = "same"\nurl = "https://two.test/simple"',
                "ambiguous uv index",
            ),
            (
                '[[tool.uv.index]]\nname = "first"\nurl = "https://one.test/simple"',
                "unscoped first-index",
            ),
            (
                '[[tool.uv.index]]\nname = "one"\nurl = "https://one.test/simple"\ndefault = true\n'
                '[[tool.uv.index]]\nname = "two"\nurl = "https://two.test/simple"\ndefault = true',
                "multiple default uv indexes",
            ),
            (
                "[tool.uv.sources]\nidna = [{ workspace = true }]",
                "multiple uv sources",
            ),
            ('[tool.uv.sources]\nidna = "bad"', "invalid uv source"),
            (
                '[tool.uv.sources]\nidna = { path = "../outside" }',
                "escapes snapshot root",
            ),
            (
                '[tool.uv.sources]\nidna = { git = "https://example.test/repo", rev = "main" }',
                "exact commit",
            ),
            (
                '[tool.uv.sources]\nidna = { url = "https://example.test/idna.whl" }',
                "integrity information",
            ),
            (
                '[tool.uv.sources]\nidna = { url = "https://example.test/idna.whl", hash = "sha256:abc" }',
                "integrity information",
            ),
            ('[tool.uv.sources]\nidna = { index = "missing" }', "unknown uv index"),
            ("[tool.uv.sources]\nidna = { editable = true }", "unsupported uv source"),
            (
                '[tool.uv.sources]\nidna = { url = "relative.whl", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }',
                "source URL must be absolute",
            ),
        ),
    )
    def test_project_loader_rejects_ambiguous_uv_source_configuration(
        self,
        tmp_path: Path,
        configuration: str,
        message: str,
    ) -> None:
        write_basic_project(tmp_path, configuration)

        with pytest.raises(ConfigurationError, match=message):
            ProjectLoader().load(root=tmp_path)

    @pytest.mark.parametrize(
        ("project", "message"),
        (
            ('dynamic = ["dependencies"]', "dynamic project.dependencies"),
            ('requires-python = "not valid"', "invalid project.requires-python"),
            ('requires-python = ">=3.11"', "configured Python 3.10 violates"),
        ),
    )
    def test_project_loader_rejects_unsupported_project_metadata(
        self,
        tmp_path: Path,
        project: str,
        message: str,
    ) -> None:
        write_basic_project(tmp_path, "", project=project)

        with pytest.raises(ConfigurationError, match=message):
            ProjectLoader().load(root=tmp_path)

    @pytest.mark.parametrize(
        "dependency",
        (
            "not [valid",
            "demo @ http://example.test/demo.whl",
            "demo @ https://user:secret@example.test/demo.whl#sha256=abc",
        ),
    )
    def test_project_loader_rejects_invalid_dependency_declarations(
        self,
        tmp_path: Path,
        dependency: str,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            f"""
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = [{dependency!r}]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError):
            ProjectLoader().load(root=tmp_path)

    def test_project_loader_requires_an_available_python_minor(
        self, tmp_path: Path
    ) -> None:
        class Pythons:
            def available_cpython_minors(self, *, root: Path) -> tuple[str, ...]:
                return ("3.10",)

        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    requires-python = ">=3.12"
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match="no available stable CPython"):
            ProjectLoader(pythons=Pythons()).load(root=tmp_path)

    def test_project_loader_rejects_unknown_package_selection(
        self, tmp_path: Path
    ) -> None:
        write_basic_project(tmp_path, "")

        with pytest.raises(
            ConfigurationError, match="unknown package selection"
        ) as caught:
            ProjectLoader().load(
                root=tmp_path,
                selector=WorkspacePackage(canonical_name="other"),
            )
        assert caught.value.candidates == ("demo",)

    @pytest.mark.parametrize(
        ("surface", "message"),
        (
            ('extra-surfaces = [["gpu"]]', "include the base surface"),
            (
                'extra-surfaces = [[], ["missing"]]',
                "unknown extra in extra-surfaces",
            ),
        ),
    )
    def test_project_loader_rejects_invalid_explicit_extra_surfaces(
        self,
        tmp_path: Path,
        surface: str,
        message: str,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            f"""
    [project]
    name = "demo"
    version = "0.1.0"

    [project.optional-dependencies]
    gpu = ["gpu-lib"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    {surface}
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match=message):
            ProjectLoader().load(root=tmp_path)

    @pytest.mark.parametrize(
        ("extras", "expected"),
        (
            ("none", [()]),
            ("all", [(), ("a",), ("b",), ("a", "b")]),
        ),
    )
    def test_project_loader_builds_none_and_all_extra_surfaces(
        self,
        tmp_path: Path,
        extras: str,
        expected: list[tuple[str, ...]],
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            f"""
    [project]
    name = "demo"
    version = "0.1.0"

    [project.optional-dependencies]
    a = ["a-lib"]
    b = ["b-lib"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    extras = "{extras}"
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        package = ProjectLoader().load(root=tmp_path).target

        assert [cell.extra_surface for cell in package.cells] == expected

    @pytest.mark.parametrize(
        ("groups", "message"),
        (
            (
                'test = [{ include-group = "loop" }]\nloop = [{ include-group = "test" }]',
                "dependency group include cycle",
            ),
            ('test = [{ unsupported = "value" }]', "unsupported dependency group item"),
        ),
    )
    def test_project_loader_rejects_invalid_dependency_group_expansion(
        self,
        tmp_path: Path,
        groups: str,
        message: str,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            f"""
    [project]
    name = "demo"
    version = "0.1.0"

    [dependency-groups]
    {groups}

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match=message):
            ProjectLoader().load(root=tmp_path)

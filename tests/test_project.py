from __future__ import annotations

from pathlib import Path

import pytest

from pf.errors import ConfigurationError
from pf.project import ProjectLoader, host_target, marker_platform
from pf.project_discovery import ProjectDiscovery
from pf.schemas.project import SourceIdentity


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

        plan = ProjectLoader().load(root=tmp_path, package_selection=None)

        package = plan.packages[0]
        assert package.name == "demo-app"
        assert [(item.name, item.kind) for item in package.declarations] == [
            ("numpy", "searchable"),
            ("click", "fixed"),
            ("requests", "searchable"),
            ("torch", "searchable"),
        ]
        assert len({item.declaration_id for item in package.declarations}) == 4
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

    def test_workspace_discovers_installable_members_then_applies_root_selection(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [tool.uv.workspace]
    members = ["packages/*"]

    [tool.pf]
    packages = ["alpha", "beta"]
    exclude-packages = ["beta"]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
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

        plan = ProjectLoader().load(root=tmp_path, package_selection=None)

        assert [package.name for package in plan.packages] == ["alpha"]
        assert plan.packages[0].pyproject_path == "packages/alpha/pyproject.toml"

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
            ProjectDiscovery().discover(
                root=tmp_path,
                package_selection="packages/first",
            )

        assert "duplicate canonical package name: demo-package" in str(caught.value)
        assert "packages/first" in str(caught.value)
        assert "packages/second" in str(caught.value)


class TestProjectPlanning:
    def test_uv_sources_are_classified_once_and_credentials_are_not_serialized(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["local-lib", "git-lib"]

    [tool.uv.sources]
    local-lib = { workspace = true }
    git-lib = { git = "https://token@example.test/repo.git", rev = "0123456789abcdef0123456789abcdef01234567" }

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )

        assert [
            (item.name, item.source.kind, item.kind) for item in package.declarations
        ] == [
            ("local-lib", "workspace", "fixed"),
            ("git-lib", "git", "fixed"),
        ]
        assert package.declarations[1].source.commit == (
            "0123456789abcdef0123456789abcdef01234567"
        )
        assert "token" not in package.source_plan.model_dump_json()

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
            ProjectLoader().load(root=tmp_path, package_selection=None)

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
            ProjectLoader().load(root=tmp_path, package_selection=None)

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
            ProjectLoader().load(root=tmp_path, package_selection=None)

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
            ProjectLoader().load(root=tmp_path, package_selection=None)

    def test_named_registry_source_resolves_to_credential_free_index_url(
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
    name = "private"
    url = "https://token@example.test/simple"
    explicit = true

    [tool.uv.sources]
    idna = { index = "private" }

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        source = (
            ProjectLoader()
            .load(
                root=tmp_path,
                package_selection=None,
            )
            .packages[0]
            .declarations[0]
            .source
        )

        assert source.index == "private"
        assert source.locator == "https://example.test/simple"
        assert "token" not in source.model_dump_json()

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
            ProjectLoader()
            .load(
                root=tmp_path,
                package_selection=None,
            )
            .packages[0]
            .declarations[0]
            .source
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
                package_selection=None,
            )
            .packages[0]
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

        declaration = (
            ProjectLoader()
            .load(
                root=tmp_path,
                package_selection=None,
            )
            .packages[0]
            .declarations[0]
        )

        assert declaration.kind == "fixed"
        assert declaration.source == SourceIdentity(
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

        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )

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
            ('[tool.uv.sources]\nidna = { index = "missing" }', "unknown uv index"),
            ("[tool.uv.sources]\nidna = { editable = true }", "unsupported uv source"),
            (
                '[tool.uv.sources]\nidna = { url = "relative.whl", hash = "sha256:abc" }',
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
            ProjectLoader().load(root=tmp_path, package_selection=None)

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
            ProjectLoader().load(root=tmp_path, package_selection=None)

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
            ProjectLoader().load(root=tmp_path, package_selection=None)

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
            ProjectLoader(pythons=Pythons()).load(root=tmp_path, package_selection=None)

    def test_project_loader_rejects_unknown_package_selection(
        self, tmp_path: Path
    ) -> None:
        write_basic_project(tmp_path, "")

        with pytest.raises(
            ConfigurationError, match="unknown package selection"
        ) as caught:
            ProjectLoader().load(root=tmp_path, package_selection="other")
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
            ProjectLoader().load(root=tmp_path, package_selection=None)

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

        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )

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
            ProjectLoader().load(root=tmp_path, package_selection=None)

from __future__ import annotations

from pathlib import Path

import pytest

from pf.errors import ConfigurationError
from pf.project import ProjectLoader
from pf.schemas.project import SourceIdentity


def test_single_package_loads_normalized_declarations_and_cells(tmp_path: Path) -> None:
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
        item.declaration_id for item in package.declarations if item.name == "requests"
    )
    assert all(
        requests_id not in cell.active_declaration_ids for cell in package.cells[:2]
    )
    assert all(requests_id in cell.active_declaration_ids for cell in package.cells[2:])


def test_workspace_discovers_installable_members_then_applies_root_selection(
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


def test_uv_sources_are_classified_once_and_credentials_are_not_serialized(
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

    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]

    assert [(item.name, item.source.kind, item.kind) for item in package.declarations] == [
        ("local-lib", "workspace", "fixed"),
        ("git-lib", "git", "fixed"),
    ]
    assert package.declarations[1].source.commit == (
        "0123456789abcdef0123456789abcdef01234567"
    )
    assert "token" not in package.source_plan.model_dump_json()


def test_overlapping_same_location_declarations_are_rejected(tmp_path: Path) -> None:
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


def test_explicit_extra_surfaces_must_cover_base_and_each_extra(tmp_path: Path) -> None:
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


def test_fixed_dependency_cannot_be_explicitly_managed(tmp_path: Path) -> None:
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

    with pytest.raises(ConfigurationError, match="unsupported managed marker dimension"):
        ProjectLoader().load(root=tmp_path, package_selection=None)


def test_named_registry_source_resolves_to_credential_free_index_url(
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

    source = ProjectLoader().load(
        root=tmp_path,
        package_selection=None,
    ).packages[0].declarations[0].source

    assert source.index == "private"
    assert source.locator == "https://example.test/simple"
    assert "token" not in source.model_dump_json()


def test_default_uv_index_becomes_the_source_for_unpinned_registry_dependencies(
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

    source = ProjectLoader().load(
        root=tmp_path,
        package_selection=None,
    ).packages[0].declarations[0].source

    assert source == SourceIdentity(
        kind="registry",
        index="internal",
        locator="https://packages.example/simple",
    )


def test_omitted_python_matrix_uses_available_stable_minors_within_requires_python(
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

    package = ProjectLoader(pythons=Pythons()).load(
        root=tmp_path,
        package_selection=None,
    ).packages[0]

    assert [cell.python_minor for cell in package.cells] == ["3.11", "3.12"]


def test_direct_url_requirement_requires_hash_and_records_public_source_identity(
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

    declaration = ProjectLoader().load(
        root=tmp_path,
        package_selection=None,
    ).packages[0].declarations[0]

    assert declaration.kind == "fixed"
    assert declaration.source == SourceIdentity(
        kind="url",
        locator="https://example.test/demo.whl",
        content_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert "example.test" in declaration.model_dump_json()


def test_target_triple_uses_runtime_platform_machine_marker_values(
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

    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]

    assert package.cells[0].active_declaration_ids == (
        package.declarations[0].declaration_id,
    )

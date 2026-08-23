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
from pf.schemas.project import HarnessBaseline, HarnessSelection


def _load_harness(
    tmp_path: Path,
    requirements: tuple[str, ...],
    *,
    sources: str = "",
    allow_prereleases: bool = False,
):
    rendered = ",\n        ".join(repr(item) for item in requirements)
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
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
allow-prereleases = {str(allow_prereleases).lower()}
test-command = ["pytest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]


def _baseline(package, *, version: str = "8.4") -> HarnessBaseline:
    active = active_harness_requirements(
        package.harness_requirements,
        package.cells[0],
    )
    names = sorted({item.name for item in active})
    selections = tuple(
        HarnessSelection(
            name=name,
            version=version,
            source=next(item.source for item in active if item.name == name),
            ceiling_bound=any(
                harness_requirement_policy(item).ceiling_bound
                for item in active
                if item.name == name
            ),
        )
        for name in names
    )
    return HarnessBaseline.from_evidence(
        cell=package.cells[0],
        declaration_ids=tuple(sorted(item.declaration_id for item in active)),
        selections=selections,
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
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
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

        package = ProjectLoader().load(
            root=tmp_path,
            package_selection=None,
        ).packages[0]

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
        assert len(
            {item.declaration_id for item in package.harness_requirements}
        ) == 4

    def test_project_loader_structures_harness_semantics_once(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(
            tmp_path,
            (
                "PyTest[testing]>=8,<9,!=8.2; python_version >= '3.10'",
            ),
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
        assert requirement.source.kind == "registry"
        assert requirement.original_text.startswith("PyTest[testing]")

    def test_project_loader_preserves_explicit_prerelease_admission(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(tmp_path, ("tool>=2.0a1",))

        assert package.harness_requirements[0].prerelease_allowed is True

    def test_project_loader_preserves_configured_prerelease_admission(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(
            tmp_path,
            ("tool>=2",),
            allow_prereleases=True,
        )

        assert package.harness_requirements[0].prerelease_allowed is True


class TestHarnessRelaxation:
    def test_original_harness_keeps_active_declaration_semantics(self, tmp_path: Path) -> None:
        package = _load_harness(
            tmp_path,
            (
                "pytest>=8; python_version >= '3.10'",
                "pluggy>=1; python_version >= '3.11'",
            ),
        )

        original = original_harness(package.harness_requirements, package.cells[0])

        assert [render_harness_requirement(item) for item in original] == [
            'pytest>=8; python_version >= "3.10"'
        ]
        assert original[0].relaxed_minimum is False
        assert original[0].ceiling is None

    def test_explicit_minimum_is_removed_and_baseline_ceiling_is_added(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(tmp_path, ("pytest>=8,<9,!=8.2",))

        relaxed = relax_harness(package.harness_requirements, _baseline(package))

        requirement = relaxed.requirements[0]
        assert [(item.operator, item.version) for item in requirement.specifier] == [
            ("!=", "8.2"),
            ("<", "9"),
            ("<=", "8.4"),
        ]
        assert requirement.relaxed_minimum is True
        assert requirement.ceiling == "8.4"
        assert render_harness_requirement(requirement) == "pytest!=8.2,<9,<=8.4"

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

        relaxed = relax_harness(package.harness_requirements, _baseline(package))

        assert render_harness_requirement(relaxed.requirements[0]) == expected
        assert relaxed.requirements[0].ceiling == ceiling

    @pytest.mark.parametrize(
        ("source", "raw"),
        (
            (
                "[tool.uv.sources]\npytest = { path = \"vendor/pytest\" }",
                "pytest>=8",
            ),
            (
                "[tool.uv.workspace]\nmembers = [\"packages/*\"]\n"
                "[tool.uv.sources]\npytest = { workspace = true }",
                "pytest>=8",
            ),
            (
                "[tool.uv.sources]\npytest = { git = \"https://example.test/pytest.git\", rev = \"0123456789abcdef0123456789abcdef01234567\" }",
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
        package = _load_harness(tmp_path, (raw,), sources=source)

        relaxed = relax_harness(package.harness_requirements, _baseline(package))

        requirement = relaxed.requirements[0]
        assert requirement.specifier == requirement.declaration.specifier
        assert requirement.relaxed_minimum is False
        assert requirement.ceiling is None
        assert render_harness_requirement(requirement) == raw

    def test_removing_prerelease_minimum_keeps_admission_policy(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(tmp_path, ("tool>=2.0a1",))

        relaxed = relax_harness(package.harness_requirements, _baseline(package))

        assert relaxed.requirements[0].declaration.prerelease_allowed is True
        assert render_harness_requirement(relaxed.requirements[0]) == "tool<=8.4"

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

        relaxed = relax_harness(package.harness_requirements, _baseline(package))

        assert [item.declaration.name for item in relaxed.requirements] == ["pytest"]

    def test_relaxation_rejects_a_baseline_from_different_declarations(
        self,
        tmp_path: Path,
    ) -> None:
        package = _load_harness(tmp_path, ("pytest>=8",))
        baseline = _baseline(package).model_copy(update={"declaration_ids": ()})

        with pytest.raises(ValueError, match="does not match active declarations"):
            relax_harness(package.harness_requirements, baseline)

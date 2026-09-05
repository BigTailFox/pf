from __future__ import annotations

from pathlib import Path

import pytest
import tomli

from pf.config import (
    ConfigLoader,
    parse_max_duration,
    parse_scheduling_limit,
    resolve_run_limits,
)
from pf.errors import ConfigurationError
from pf.project_discovery import PyprojectObservation
from pf.schemas.config import (
    EffectiveConfig,
    ManagedDependencies,
    SchedulingConfig,
    UnmanagedDependencies,
)


def observation(path: Path, document: dict[str, object]) -> PyprojectObservation:
    return PyprojectObservation(path=path / "pyproject.toml", document=document)


def load_config(root: Path, package: Path) -> EffectiveConfig:
    root_observation = PyprojectObservation(
        path=root / "pyproject.toml",
        document=tomli.loads((root / "pyproject.toml").read_text(encoding="utf-8")),
    )
    target_observation = (
        root_observation
        if package.resolve() == root.resolve()
        else PyprojectObservation(
            path=package / "pyproject.toml",
            document=tomli.loads(
                (package / "pyproject.toml").read_text(encoding="utf-8")
            ),
        )
    )
    return ConfigLoader().load(
        root_observation=root_observation,
        target_observation=target_observation,
    )


class TestConfiguration:
    def test_config_loader_merges_root_defaults_and_member_local_config(
        self,
        tmp_path: Path,
    ) -> None:
        root = observation(
            tmp_path,
            {
                "project": {"name": "root"},
                "tool": {
                    "pf": {
                        "pythons": ["3.10"],
                        "platforms": ["x86_64-unknown-linux-gnu"],
                    }
                },
            },
        )
        target = observation(
            tmp_path / "missing-package",
            {
                "project": {"name": "demo"},
                "tool": {"pf": {"pythons": ["3.12"]}},
            },
        )

        config = ConfigLoader().load(
            root_observation=root,
            target_observation=target,
        )

        assert config.target.python_minors == ("3.12",)
        assert config.target.platforms == ("x86_64-unknown-linux-gnu",)

    def test_root_target_applies_root_config_once(self, tmp_path: Path) -> None:
        root = observation(
            tmp_path,
            {
                "project": {"name": "demo"},
                "tool": {"pf": {"pythons": ["3.10"], "ty-args": ["--root"]}},
            },
        )

        config = ConfigLoader().load(
            root_observation=root,
            target_observation=root,
        )

        assert config.target.python_minors == ("3.10",)
        assert config.ty.args == ("--root",)

    def test_member_replaces_lists_and_the_complete_dep_table(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "workspace-root"
    version = "0.1.0"

    [tool.pf]
    pythons = ["3.10"]
    ty-args = ["--root"]

    [[tool.pf.dep]]
    name = "numpy"
    search-step = "patch"
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = tmp_path / "packages" / "demo"
        package.mkdir(parents=True)
        (package / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    pythons = ["3.12"]
    ty-args = []
    dep = []
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        config = load_config(tmp_path, package)

        assert config.target.python_minors == ("3.12",)
        assert config.ty.args == ()
        assert config.search.overrides == ()

    def test_member_rejects_unknown_package_namespace(self, tmp_path: Path) -> None:
        root = observation(tmp_path, {"project": {"name": "root"}})
        member = observation(
            tmp_path / "packages" / "demo",
            {
                "project": {"name": "demo"},
                "tool": {"pf": {"package": {"demo": {"ty-jobs": 1}}}},
            },
        )

        with pytest.raises(
            ConfigurationError,
            match=r"unknown package \[tool\.pf\] key: package",
        ):
            ConfigLoader().load(
                root_observation=root,
                target_observation=member,
            )

    def test_member_omission_inherits_the_complete_root_dep_table(
        self,
        tmp_path: Path,
    ) -> None:
        root = observation(
            tmp_path,
            {
                "project": {"name": "root"},
                "tool": {
                    "pf": {
                        "search-step": "patch",
                        "dep": [{"name": "NumPy", "search-prereleases": True}],
                    }
                },
            },
        )
        target = observation(
            tmp_path / "demo",
            {"project": {"name": "demo"}, "tool": {"pf": {}}},
        )

        config = ConfigLoader().load(
            root_observation=root,
            target_observation=target,
        )

        assert tuple(policy.model_dump() for policy in config.search.overrides) == (
            {
                "name": "numpy",
                "space": "all",
                "step": "patch",
                "prereleases": True,
            },
        )

    def test_higher_dependency_selection_variant_replaces_the_lower_variant(
        self,
        tmp_path: Path,
    ) -> None:
        root = observation(
            tmp_path,
            {
                "project": {"name": "root"},
                "tool": {"pf": {"managed-deps": ["NumPy"]}},
            },
        )
        target = observation(
            tmp_path / "demo",
            {
                "project": {"name": "demo"},
                "tool": {"pf": {"unmanaged-deps": ["Requests"]}},
            },
        )

        config = ConfigLoader().load(
            root_observation=root,
            target_observation=target,
        )

        assert config.target.dependency_selection == UnmanagedDependencies(
            names=("requests",)
        )

    def test_config_supplies_the_v1_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        config = load_config(tmp_path, tmp_path)

        assert config.target.python_minors is None
        assert config.target.platforms is None
        assert config.target.extras.policy == "each"
        assert config.target.extras.custom_surfaces == ()
        assert config.search.default.model_dump() == {
            "space": "all",
            "step": "minor",
            "prereleases": False,
        }
        assert config.search.overrides == ()
        assert config.resolution.artifact == "any"
        assert config.resolution.timeout_seconds == 600
        assert config.ty.args == ()
        assert config.ty.timeout_seconds == 600
        assert config.test.group == "test"
        assert config.test.command == ("pytest",)
        assert config.test.cwd == "package"
        assert config.test.timeout_seconds == 1800
        assert config.scheduling.model_dump() == {
            "max_cells": "auto",
            "ty_jobs": "auto",
            "test_jobs": "auto",
        }

    def test_config_normalizes_explicit_policy_values(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    extra-policy = "all"
    extra-surfaces = [["gpu", "fast"], ["fast", "gpu"], []]
    search-space = "current-minor"
    search-step = "patch"
    search-prereleases = true
    resolve-artifact = "any"
    test-command = ["pytest", "tests"]
    test-cwd = "root"
    max-cells = 2
    ty-jobs = 1
    test-jobs = "auto"
    resolve-timeout = "2m"
    ty-timeout = "none"

    [[tool.pf.dep]]
    name = "NumPy"
    search-space = ">=1.0,<2"
    search-step = "patch"
    search-prereleases = false
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        config = load_config(tmp_path, tmp_path)

        assert config.target.platforms == ("x86_64-unknown-linux-gnu",)
        assert config.target.dependency_selection == ManagedDependencies(names=())
        assert config.target.extras.policy == "all"
        assert config.target.extras.custom_surfaces == ((), ("fast", "gpu"))
        assert config.search.default.model_dump() == {
            "space": "current-minor",
            "step": "patch",
            "prereleases": True,
        }
        assert config.search.overrides[0].model_dump() == {
            "name": "numpy",
            "space": "<2,>=1.0",
            "step": "patch",
            "prereleases": False,
        }
        assert config.resolution.artifact == "any"
        assert config.test.command == ("pytest", "tests")
        assert config.test.cwd == "root"
        assert config.scheduling.max_cells == 2
        assert config.scheduling.ty_jobs == 1
        assert config.scheduling.test_jobs == "auto"
        assert config.resolution.timeout_seconds == 120
        assert config.ty.timeout_seconds is None

    @pytest.mark.parametrize(
        ("body", "message"),
        (
            (
                'search-space = "current-minor"\nsearch-step = "minor"',
                "current-minor search-space requires patch step",
            ),
            ('test-command = ["uv", "run", "pytest"]', "cannot start with 'uv run'"),
            ("max-cells = true", "valid integer"),
            ("resolve-timeout = 10", "resolve-timeout must be a duration"),
            ('resolve-timeout = "zero"', "invalid resolve-timeout"),
            ('search-space = "future"', "invalid search-space"),
            ('managed-deps = ["NumPy", "numpy"]', "duplicate managed-deps"),
            ('pythons = ["3.12", "3.11"]', "pythons must be sorted and unique"),
            ("test-command = []", "test-command must be non-empty"),
            ("surprise = true", r"unknown \[tool.pf\] key: surprise"),
            ("package = {}", r"unknown \[tool.pf\] key: package"),
            (
                'managed-deps = ["numpy"]\nunmanaged-deps = ["torch"]',
                "managed-deps and unmanaged-deps are mutually exclusive",
            ),
            (
                'search-space = "current-major"\nsearch-step = "major"',
                "current-major search-space requires minor or patch step",
            ),
            (
                '[[tool.pf.dep]]\nname = "NumPy"\n[[tool.pf.dep]]\nname = "numpy"',
                "duplicate dep dependency: numpy",
            ),
            (
                '[[tool.pf.dep]]\nname = "numpy"\nsearch-space = "numpy>=1"',
                "invalid dep search-space",
            ),
        ),
    )
    def test_load_rejects_invalid_policy(
        self,
        tmp_path: Path,
        body: str,
        message: str,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool.pf]\n{body}\n',
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match=message):
            load_config(tmp_path, tmp_path)


class TestCliConfigParsers:
    def test_run_limits_resolve_persistent_values_and_auto_once(self) -> None:
        limits = resolve_run_limits(
            SchedulingConfig(max_cells=2, ty_jobs="auto", test_jobs=3),
            logical_cpus=8,
        )

        assert limits.model_dump() == {
            "max_cells": 2,
            "ty_jobs": 2,
            "test_jobs": 3,
            "max_duration_seconds": None,
        }

    def test_explicit_auto_overrides_persistent_limits(self) -> None:
        limits = resolve_run_limits(
            SchedulingConfig(max_cells=2, ty_jobs=1, test_jobs=1),
            max_cells="auto",
            ty_jobs="auto",
            test_jobs=4,
            max_duration_seconds=30,
            logical_cpus=8,
        )

        assert limits.model_dump() == {
            "max_cells": 8,
            "ty_jobs": 8,
            "test_jobs": 4,
            "max_duration_seconds": 30.0,
        }

    @pytest.mark.parametrize(
        ("value", "expected"),
        (("auto", "auto"), ("3", 3)),
    )
    def test_parse_scheduling_limit_returns_normalized_value(
        self,
        value: str,
        expected: str | int,
    ) -> None:
        assert parse_scheduling_limit(value, field="max-cells") == expected

    def test_parse_scheduling_limit_rejects_non_positive_value(self) -> None:
        with pytest.raises(ConfigurationError, match="max-cells must be"):
            parse_scheduling_limit("0", field="max-cells")

    @pytest.mark.parametrize(
        ("value", "expected"),
        ((None, None), ("none", None), ("2h", 7200)),
    )
    def test_parse_max_duration_returns_seconds_or_none(
        self,
        value: str | None,
        expected: int | None,
    ) -> None:
        assert parse_max_duration(value) == expected

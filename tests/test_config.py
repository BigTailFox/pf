from __future__ import annotations

from pathlib import Path

import pytest
import tomli

from pf.config import ConfigLoader, parse_jobs, parse_max_duration
from pf.errors import ConfigurationError
from pf.project_discovery import PyprojectObservation
from pf.schemas.config import EffectiveConfig


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
    def test_config_loader_merges_observations_without_reading_the_filesystem(
        self,
        tmp_path: Path,
    ) -> None:
        root = observation(
            tmp_path,
            {
                "project": {"name": "root"},
                "tool": {
                    "pf": {
                        "python": ["3.10"],
                        "package": {"demo": {"python": ["3.11"]}},
                    }
                },
            },
        )
        target = observation(
            tmp_path / "missing-package",
            {
                "project": {"name": "demo"},
                "tool": {"pf": {"python": ["3.12"]}},
            },
        )

        config = ConfigLoader().load(
            root_observation=root,
            target_observation=target,
        )

        assert config.python == ("3.12",)

    def test_root_target_applies_root_package_config_after_matching_override(
        self,
        tmp_path: Path,
    ) -> None:
        root = observation(
            tmp_path,
            {
                "project": {"name": "demo"},
                "tool": {
                    "pf": {
                        "python": ["3.10"],
                        "package": {"demo": {"python": ["3.11"]}},
                    }
                },
            },
        )

        config = ConfigLoader().load(
            root_observation=root,
            target_observation=root,
        )

        assert config.python == ("3.10",)

    def test_package_config_overrides_root_override_and_replaces_lists(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "workspace-root"
    version = "0.1.0"

    [tool.pf]
    python = ["3.10"]
    ty-args = ["--root"]

    [tool.pf.package.demo]
    python = ["3.11"]
    ty-args = ["--override"]
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
    python = ["3.12"]
    ty-args = []
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        config = load_config(tmp_path, package)

        assert config.python == ("3.12",)
        assert config.ty_args == ()

    def test_config_supplies_the_v1_evaluation_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        config = load_config(tmp_path, tmp_path)

        assert config.release_granularity == "minor"
        assert config.search_space == "all"
        assert config.distribution == "wheel"
        assert config.allow_prereleases is False
        assert config.test_group == "test"
        assert config.command_cwd == "package"
        assert config.resolve_timeout == 600
        assert config.ty_timeout == 600
        assert config.test_timeout == 1800

    def test_config_normalizes_explicit_policy_values(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    platform = ["x86_64-unknown-linux-gnu"]
    extras = "all"
    release-granularity = "patch"
    search-space = ["NumPy>=1.0,<2"]
    distribution = "any"
    allow-prereleases = true
    managed-deps = []
    test-command = ["pytest", "tests"]
    jobs = 2
    resolve-timeout = "2m"
    ty-timeout = "none"
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        config = load_config(tmp_path, tmp_path)

        assert config.platform == ("x86_64-unknown-linux-gnu",)
        assert config.extras == "all"
        assert config.release_granularity == "patch"
        assert config.search_space == ("numpy<2,>=1.0",)
        assert config.distribution == "any"
        assert config.allow_prereleases is True
        assert config.managed_deps == ()
        assert config.test_command == ("pytest", "tests")
        assert config.jobs == 2
        assert config.resolve_timeout == 120
        assert config.ty_timeout is None

    @pytest.mark.parametrize(
        ("body", "message"),
        (
            (
                'extras = "each"\nextra-surfaces = [["gpu"]]',
                "extras and extra-surfaces are mutually exclusive",
            ),
            (
                'search-space = "current-minor"\nrelease-granularity = "minor"',
                "current-minor search-space requires patch granularity",
            ),
            ('test-command = ["uv", "run", "pytest"]', "cannot start with 'uv run'"),
            ("jobs = true", "valid integer"),
            ("resolve-timeout = 10", "resolve-timeout must be a duration"),
            ('resolve-timeout = "zero"', "invalid resolve-timeout"),
            ('search-space = "future"', "invalid search-space"),
            ('search-space = ["not [valid"]', "invalid search-space entry"),
            (
                'search-space = ["demo[extra]>=1"]',
                "must contain only a name and specifier",
            ),
            (
                'search-space = ["Demo>=1", "demo<3"]',
                "duplicate search-space dependency",
            ),
            ("surprise = true", r"unknown \[tool.pf\] key: surprise"),
            (
                "test-failure-exit-codes = [1]",
                r"unknown \[tool.pf\] key: test-failure-exit-codes",
            ),
            (
                'managed-deps = ["numpy"]\nunmanaged-deps = ["torch"]',
                "managed-deps and unmanaged-deps are mutually exclusive",
            ),
            (
                'search-space = "current-major"\nrelease-granularity = "major"',
                "current-major search-space requires minor or patch granularity",
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

    def test_config_normalizes_explicit_extra_surfaces(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    extra-surfaces = [["gpu", "fast"], ["fast", "gpu"], []]
    unmanaged-deps = ["Requests", "requests"]
    test-timeout = "1s"
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        config = load_config(tmp_path, tmp_path)

        assert config.extras is None
        assert config.extra_surfaces == ((), ("fast", "gpu"))
        assert config.unmanaged_deps == ("requests",)
        assert config.test_timeout == 1


class TestCliConfigParsers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        (("auto", "auto"), ("3", 3)),
    )
    def test_parse_jobs_returns_normalized_value(
        self,
        value: str,
        expected: str | int,
    ) -> None:
        assert parse_jobs(value) == expected

    def test_parse_jobs_rejects_non_positive_value(self) -> None:
        with pytest.raises(ConfigurationError, match="jobs must be"):
            parse_jobs("0")

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

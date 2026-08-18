from __future__ import annotations

from pathlib import Path

import pytest

from pf.config import ConfigLoader
from pf.errors import ConfigurationError


def test_package_config_overrides_root_override_and_replaces_lists(
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

    config = ConfigLoader().load(root=tmp_path, package=package)

    assert config.python == ("3.12",)
    assert config.ty_args == ()


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[tool.pf]
surprise = true
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=r"unknown \[tool.pf\] key: surprise"):
        ConfigLoader().load(root=tmp_path, package=tmp_path)


def test_config_rejects_managed_and_unmanaged_lists_together(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[tool.pf]
managed-deps = ["numpy"]
unmanaged-deps = ["torch"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match="managed-deps and unmanaged-deps are mutually exclusive",
    ):
        ConfigLoader().load(root=tmp_path, package=tmp_path)


def test_config_supplies_the_v1_evaluation_defaults(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    config = ConfigLoader().load(root=tmp_path, package=tmp_path)

    assert config.release_granularity == "minor"
    assert config.search_space == "all"
    assert config.distribution == "wheel"
    assert config.allow_prereleases is False
    assert config.test_group == "test"
    assert config.test_failure_exit_codes == (1,)
    assert config.command_cwd == "package"
    assert config.resolve_timeout == 600
    assert config.ty_timeout == 600
    assert config.test_timeout == 1800


def test_config_normalizes_explicit_policy_values(tmp_path: Path) -> None:
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

    config = ConfigLoader().load(root=tmp_path, package=tmp_path)

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


def test_config_rejects_granularity_coarser_than_scalar_search_space(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[tool.pf]
search-space = "current-major"
release-granularity = "major"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match="current-major search-space requires minor or patch granularity",
    ):
        ConfigLoader().load(root=tmp_path, package=tmp_path)

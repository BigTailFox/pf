"""Resolve the host ty distribution version and parse `ty --version` output."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as distribution_version
import re

from packaging.version import InvalidVersion, Version

from pf.schemas.policy import TyToolVersion, TyToolVersionDistribution, TyToolVersionUnavailable


_TY_VERSION_OUTPUT = re.compile(
    r"^(?:ty\s+)?(?P<version>[0-9].*)$",
    re.IGNORECASE,
)


def read_ty_tool_version() -> TyToolVersion:
    """Read installed ty metadata. Does not run `ty --version`."""
    try:
        raw = distribution_version("ty")
    except PackageNotFoundError:
        return TyToolVersionUnavailable()
    try:
        version = str(Version(raw))
    except InvalidVersion:
        return TyToolVersionUnavailable()
    return TyToolVersionDistribution(version=version)


def parse_ty_version_output(stdout: str) -> Version:
    """Parse `ty --version` stdout into a PEP 440 version."""
    line = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
    match = _TY_VERSION_OUTPUT.match(line)
    if match is None:
        raise ValueError("ty --version output is not a PEP 440 version")
    try:
        return Version(match.group("version"))
    except InvalidVersion as error:
        raise ValueError("ty --version output is not a PEP 440 version") from error


def ty_version_matches_metadata(stdout: str, metadata: TyToolVersionDistribution) -> bool:
    """Compare parsed `ty --version` with distribution metadata."""
    try:
        return parse_ty_version_output(stdout) == Version(metadata.version)
    except ValueError:
        return False

"""Project marker semantics: portable Cell facts and explicit contextual evaluation.

Declaration ownership and error provenance belong to consumers, not this module.
Packaging's mutable parser objects remain private to the bounded cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from packaging._parser import Variable
from packaging.markers import InvalidMarker, Marker, UndefinedEnvironmentName

from pf.schemas.project import Cell


class MarkerError(ValueError):
    """A stable, value-free marker failure; callers add usage and provenance."""

    def __init__(
        self,
        reason: Literal[
            "syntax", "unsupported-variable", "target", "comparison", "missing-fact"
        ],
        *,
        variable: str | None = None,
    ) -> None:
        self.reason = reason
        self.variable = variable
        messages = {
            "syntax": "invalid marker syntax",
            "unsupported-variable": f"unsupported marker dimension: {variable}",
            "target": "unsupported target platform",
            "comparison": "marker comparison cannot be evaluated",
            "missing-fact": "marker evaluation requires a missing fact",
        }
        super().__init__(messages[reason])


@dataclass(frozen=True)
class PlatformMarkerFacts:
    sys_platform: str
    platform_machine: str
    platform_system: str
    os_name: str


@lru_cache(maxsize=128)
def platform_marker_facts(target: str) -> PlatformMarkerFacts:
    """Project an exact supported uv target without consulting the running host."""
    architecture, _, family = target.partition("-")
    if architecture and family in {"unknown-linux-gnu", "unknown-linux-musl"}:
        return PlatformMarkerFacts("linux", architecture, "Linux", "posix")
    if architecture and family == "apple-darwin":
        machine = "arm64" if architecture == "aarch64" else architecture
        return PlatformMarkerFacts("darwin", machine, "Darwin", "posix")
    if architecture and family == "pc-windows-msvc":
        machine = {"x86_64": "AMD64", "aarch64": "ARM64"}.get(
            architecture, architecture
        )
        return PlatformMarkerFacts("win32", machine, "Windows", "nt")
    raise MarkerError("target")


_PORTABLE_VARIABLES = frozenset(
    {"python_version", "sys_platform", "platform_machine", "platform_system", "os_name"}
)


def _variables(node: object) -> set[str]:
    if isinstance(node, Variable):
        return {node.value}
    if isinstance(node, (list, tuple)):
        return set().union(*(_variables(child) for child in node))
    return set()


@lru_cache(maxsize=2048)
def _parse(raw: str) -> Marker:
    try:
        return Marker(raw)
    except InvalidMarker as error:
        raise MarkerError("syntax") from error


@lru_cache(maxsize=2048)
def _portable(raw: str) -> Marker:
    parsed = _parse(raw)
    unsupported = sorted(_variables(parsed._markers) - _PORTABLE_VARIABLES)
    if unsupported:
        raise MarkerError("unsupported-variable", variable=unsupported[0])
    return parsed


def _environment(cell: Cell) -> dict[str, str]:
    facts = platform_marker_facts(cell.target)
    return {
        "python_version": cell.python_minor,
        "sys_platform": facts.sys_platform,
        "platform_machine": facts.platform_machine,
        "platform_system": facts.platform_system,
        "os_name": facts.os_name,
    }


def _evaluate(parsed: Marker, environment: dict[str, str]) -> bool:
    try:
        return parsed.evaluate(environment)
    except UndefinedEnvironmentName as error:
        raise MarkerError("missing-fact") from error
    except ValueError as error:
        raise MarkerError("comparison") from error


@dataclass(frozen=True)
class PortableMarker:
    """An immutable, qualified whole expression; no Cell is needed for admission."""

    raw: str | None

    def __post_init__(self) -> None:
        if self.raw is not None:
            _portable(self.raw)

    @classmethod
    def parse(cls, raw: str | None) -> PortableMarker:
        return cls(raw)

    def evaluate(self, cell: Cell) -> bool:
        if self.raw is None:
            return True
        return _evaluate(_portable(self.raw), _environment(cell))


def evaluate_contextual_marker(raw: str | None, cell: Cell) -> bool:
    """Preserved/harness semantics, not a fallback for unqualified portable input.

    Five fields are Cell-derived. Remaining fields retain packaging's host defaults;
    extra retains the existing empty-or-selected-extra activation rule.
    """
    if raw is None:
        return True
    parsed = _parse(raw)
    environment = _environment(cell)
    return any(
        _evaluate(parsed, {**environment, "extra": extra})
        for extra in ("", *cell.extra_surface)
    )

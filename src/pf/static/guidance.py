"""Bounded local static search; its endpoints carry no dynamic authority."""
from dataclasses import dataclass, replace
from typing import Literal, Protocol, runtime_checkable

from packaging.version import Version

from pf.schemas.project import VersionPin
from pf.schemas.static_comparison import (
    StaticCompared,
    StaticComparisonResult,
    StaticComparisonUnavailable,
    StaticUncompared,
)
from pf.static.guard import warn_static_failure


@dataclass(frozen=True)
class StaticPoint:
    version: str
    result: StaticComparisonResult
    comparison_identity: str | None


@dataclass(frozen=True)
class StaticHint:
    suspect: StaticPoint
    clean_neighbor: StaticPoint
    clean_is_anchor: bool


@dataclass(frozen=True)
class StaticSearchResult:
    points: tuple[StaticPoint, ...]
    hint: StaticHint | None
    reason: Literal["lower-unchanged", "context-mismatch", "static-unavailable", "anchor-unavailable", "static-inconsistent"] | None
    search_ref: str | None = None


class StaticSlice(Protocol):
    @property
    def anchor(self) -> StaticPoint: ...
    @property
    def known_points(self) -> tuple[StaticPoint, ...]: ...
    def inspect(self, version: str) -> StaticPoint: ...
    def finish(self, result: StaticSearchResult) -> str | None: ...


@runtime_checkable
class StaticGuidanceEvaluator(Protocol):
    def open_static_slice(
        self, vector: tuple[VersionPin, ...], *, dependency: str, versions: tuple[str, ...],
    ) -> StaticSlice | None: ...


def locate_static_hint(slice: StaticSlice, versions: tuple[str, ...]) -> StaticSearchResult:
    """Sample a regression/unchanged bracket without pruning oracle candidates."""
    try:
        return _locate_static_hint(slice, versions)
    except Exception as exc:
        warn_static_failure(exc)
        empty = StaticSearchResult((), None, "static-unavailable")
        try:
            return replace(empty, search_ref=slice.finish(empty))
        except Exception as finish_exc:
            warn_static_failure(finish_exc)
            return empty


def _locate_static_hint(slice: StaticSlice, versions: tuple[str, ...]) -> StaticSearchResult:
    anchor = slice.anchor
    points = [anchor]

    def finish(reason=None, hint=None):
        result = StaticSearchResult(tuple(points), hint, reason)
        try:
            return replace(result, search_ref=slice.finish(result))
        except Exception as exc:
            warn_static_failure(exc)
            return result

    if not isinstance(anchor.result, StaticCompared) or anchor.result.state != "STATIC_UNCHANGED":
        return finish("anchor-unavailable")
    ordered = sorted(set(versions) | {anchor.version}, key=Version)
    if not versions or ordered[-1] != anchor.version or len(ordered) < 2:
        return finish("static-unavailable")

    def inspect(index):
        try:
            point = slice.inspect(ordered[index])
        except Exception as exc:
            warn_static_failure(exc)
            point = StaticPoint(
                ordered[index], StaticComparisonUnavailable(reason="invalid-layout"), None,
            )
        if point.version != ordered[index]:
            point = StaticPoint(
                ordered[index], StaticComparisonUnavailable(reason="invalid-layout"), None,
            )
        points.append(point)
        return point

    def unavailable(point):
        return ("context-mismatch" if isinstance(point.result, StaticUncompared)
                and point.result.reason == "context-mismatch" else "static-unavailable")

    def inconsistent():
        states: dict[Version, str] = {}
        for point in (*slice.known_points, *points):
            if not isinstance(point.result, StaticCompared):
                continue
            version, state = Version(point.version), point.result.state
            if version in states and states[version] != state:
                return True
            states[version] = state
        clean_seen = False
        for version in sorted(states):
            if states[version] == "STATIC_UNCHANGED":
                clean_seen = True
            elif clean_seen:
                return True
        return False

    if inconsistent():
        return finish("static-inconsistent")
    low = inspect(0)
    if inconsistent():
        return finish("static-inconsistent")
    if not isinstance(low.result, StaticCompared):
        return finish(unavailable(low))
    if low.result.state == "STATIC_UNCHANGED":
        return finish("lower-unchanged")
    lo, hi, high = 0, len(ordered) - 1, anchor
    while hi - lo > 1:
        mid = (lo + hi) // 2
        point = inspect(mid)
        if inconsistent():
            return finish("static-inconsistent")
        if not isinstance(point.result, StaticCompared):
            return finish(unavailable(point))
        if point.result.state == "STATIC_REGRESSION":
            lo, low = mid, point
        else:
            hi, high = mid, point
    return finish(hint=StaticHint(low, high, high is anchor))

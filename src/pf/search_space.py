from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Iterable, Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from pf.errors import ConfigurationError, SearchSpaceResolutionError

if TYPE_CHECKING:
    from pf.schemas.project import (
        Cell,
        NamedSearchPolicy,
        PackagePlan,
        RequirementDeclaration,
    )


SEARCH_SPACE_PROFILE = "registry-series-slice-v1"
DEFAULT_WITH_LOWER_BOUND = "majors[declaration-1:]"
DEFAULT_WITHOUT_LOWER_BOUND = "majors[baseline-2:]"
AnchorName = Literal["baseline", "declaration"]
Family = Literal["majors", "minors"]
SelectionReason = Literal["explicit", "default-declaration", "default-unbounded"]
SeriesKey = tuple[int, ...]


@dataclass(frozen=True)
class Anchor:
    name: AnchorName
    offset: int = 0

    @property
    def canonical(self) -> str:
        return self.name + (f"{self.offset:+d}" if self.offset else "")


@dataclass(frozen=True)
class AllSpace:
    canonical: str = "all"


@dataclass(frozen=True)
class SpecifierSpace:
    canonical: str


@dataclass(frozen=True)
class SeriesSpace:
    family: Family
    point: bool
    start: Anchor | None
    stop: Anchor | None = None

    @property
    def anchors(self) -> tuple[Anchor, ...]:
        return tuple(item for item in (self.start, self.stop) if item is not None)

    @property
    def canonical(self) -> str:
        start = self.start.canonical if self.start else ""
        stop = self.stop.canonical if self.stop else ""
        selector = start if self.point else f"{start}:{stop}"
        return f"{self.family}[{selector}]"


ExplicitSpace = AllSpace | SpecifierSpace | SeriesSpace


@dataclass(frozen=True)
class DefaultSpace:
    with_lower_bound: AllSpace | SeriesSpace
    without_lower_bound: AllSpace | SeriesSpace


SearchSpace = ExplicitSpace | DefaultSpace


def parse(value: str, *, allow_specifier: bool = False) -> ExplicitSpace:
    """Parse the closed space grammar; never interpret arbitrary expressions."""
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"invalid search-space: {value!r}")
    compact = value.strip(" \t")
    if compact == "all":
        return AllSpace()
    match = re.fullmatch(r"(majors|minors)[ \t]*\[(.*)\]", compact)
    if match:
        family: Family = "majors" if match[1] == "majors" else "minors"
        selector = match[2].strip(" \t")
        if re.fullmatch(r"[ \t]*:[ \t]*", selector):
            return AllSpace()
        parts = selector.split(":")
        if len(parts) <= 2:

            def endpoint(raw: str) -> Anchor | None:
                raw = raw.strip(" \t")
                if not raw:
                    return None
                found = re.fullmatch(
                    r"(baseline|declaration)(?:[ \t]*([+-])[ \t]*([0-9]+))?", raw
                )
                if found is None:
                    raise ValueError("invalid anchor")
                name: AnchorName = (
                    "baseline" if found[1] == "baseline" else "declaration"
                )
                return Anchor(name, int(found[2] + found[3]) if found[2] else 0)

            try:
                start = endpoint(parts[0])
                if len(parts) == 2:
                    return SeriesSpace(family, False, start, endpoint(parts[1]))
                if start is not None:
                    return SeriesSpace(family, True, start)
            except ValueError:
                pass
    # A malformed DSL is not retried as a different language.
    if allow_specifier and not any(char in value for char in "[]"):
        try:
            specifier = str(SpecifierSet(value))
        except InvalidSpecifier:
            pass
        else:
            if specifier:
                return SpecifierSpace(specifier)
    hint = "; use ':' for a slice" if "," in value and "[" in value else ""
    raise ConfigurationError(f"invalid search-space: {value!r}{hint}")


def defaults(with_lower_bound: str, without_lower_bound: str) -> DefaultSpace:
    with_bound = parse(with_lower_bound)
    without_bound = parse(without_lower_bound)
    assert not isinstance(with_bound, SpecifierSpace)
    assert not isinstance(without_bound, SpecifierSpace)
    if isinstance(without_bound, SeriesSpace) and any(
        anchor.name == "declaration" for anchor in without_bound.anchors
    ):
        raise ConfigurationError(
            "without-lower-bound search-space cannot reference declaration"
        )
    return DefaultSpace(with_bound, without_bound)


def declaration_anchor(specifiers: Iterable[str]) -> Version | None:
    endpoints = (
        Version(clause.version)
        for expression in specifiers
        for clause in SpecifierSet(expression)
        if clause.operator in {">", ">="}
    )
    return max(endpoints, default=None)


@dataclass(frozen=True)
class BoundSpace:
    space: ExplicitSpace
    reason: SelectionReason
    declaration: Version | None


def bind(
    requested: SearchSpace,
    *,
    declaration: Version | None,
    dependency: str,
    cell: object,
) -> BoundSpace:
    reason: SelectionReason = "explicit"
    if isinstance(requested, DefaultSpace):
        if declaration is None:
            space = requested.without_lower_bound
            reason = "default-unbounded"
        else:
            space = requested.with_lower_bound
            reason = "default-declaration"
    else:
        space = requested
    if (
        declaration is None
        and isinstance(space, SeriesSpace)
        and any(anchor.name == "declaration" for anchor in space.anchors)
    ):
        raise ConfigurationError(
            f"search-space requires a declaration lower bound: {dependency}",
            detail=f"Cell: {cell}; expression: {space.canonical}; use a baseline expression or all",
        )
    return BoundSpace(space, reason, declaration)


def bind_policy(
    policy: NamedSearchPolicy,
    *,
    declarations: Iterable[RequirementDeclaration],
    cell: Cell,
) -> BoundSpace:
    """Bind one requested policy to the original active direct declarations."""
    requested = (
        parse(policy.space, allow_specifier=True)
        if policy.space is not None
        else defaults(
            policy.space_defaults.with_lower_bound,
            policy.space_defaults.without_lower_bound,
        )
    )
    active = set(cell.active_declaration_ids)
    lower = declaration_anchor(
        item.specifier
        for item in declarations
        if item.name == policy.name and item.declaration_id in active
    )
    return bind(requested, declaration=lower, dependency=policy.name, cell=cell)


def admit(package: PackagePlan) -> None:
    """Check declaration prerequisites for the whole declared matrix without I/O."""
    for cell in package.cells:
        active = set(cell.active_declaration_ids)
        names = {
            item.name
            for item in package.declarations
            if item.managed and item.declaration_id in active
        }
        for name in sorted(names):
            bind_policy(
                package.search_policy_for(name),
                declarations=package.declarations,
                cell=cell,
            )


def series_key(version: Version, family: Family) -> SeriesKey:
    release = (*version.release, 0, 0)
    return (
        (version.epoch, release[0])
        if family == "majors"
        else (version.epoch, release[0], release[1])
    )


@dataclass(frozen=True)
class SpaceSelection:
    expression: str
    reason: SelectionReason
    anchors: tuple[tuple[AnchorName, str], ...]
    family: Family | None = None
    series_keys: tuple[SeriesKey, ...] = ()
    selected_keys: tuple[SeriesKey, ...] = ()

    def contains(self, version: Version) -> bool:
        if self.family is not None:
            return series_key(version, self.family) in self.selected_keys
        return self.expression == "all" or SpecifierSet(self.expression).contains(
            version, prereleases=True
        )


def evaluate(
    bound: BoundSpace,
    *,
    baseline: Version,
    release_versions: Iterable[str],
    dependency: str,
    cell: object,
    source: object,
) -> SpaceSelection:
    space = bound.space
    keys = (
        tuple(
            sorted({series_key(Version(raw), space.family) for raw in release_versions})
        )
        if isinstance(space, SeriesSpace)
        else ()
    )
    return evaluate_series(
        bound,
        baseline=baseline,
        series_keys=keys,
        dependency=dependency,
        cell=cell,
        source=source,
    )


def evaluate_series(
    bound: BoundSpace,
    *,
    baseline: Version,
    series_keys: Iterable[SeriesKey],
    dependency: str,
    cell: object,
    source: object,
) -> SpaceSelection:
    """Evaluate using frozen series facts, shared by discovery and report reading."""
    space = bound.space
    if not isinstance(space, SeriesSpace):
        return SpaceSelection(space.canonical, bound.reason, ())
    versions = {"baseline": baseline, "declaration": bound.declaration}
    anchor_versions = {anchor.name: versions[anchor.name] for anchor in space.anchors}
    assert all(version is not None for version in anchor_versions.values())
    anchors = tuple(
        sorted((name, str(version)) for name, version in anchor_versions.items())
    )
    keys = {
        name: series_key(version, space.family)
        for name, version in anchor_versions.items()
        if version is not None
    }
    scopes = {key[:-1] for key in keys.values()}
    universe = tuple(sorted(set(series_keys)))
    relevant = tuple(key for key in universe if key[:-1] in scopes)

    def failure(
        reason: Literal["missing-anchor-series", "anchor-scope-mismatch"],
    ) -> SearchSpaceResolutionError:
        return SearchSpaceResolutionError(
            dependency=dependency,
            cell=cell,
            expression=space.canonical,
            reason=reason,
            anchors=anchors,
            series_keys=relevant,
            source=source,
        )

    if len(scopes) != 1:
        raise failure("anchor-scope-mismatch")
    if any(key not in relevant for key in keys.values()):
        raise failure("missing-anchor-series")

    def position(anchor: Anchor) -> int:
        return relevant.index(keys[anchor.name]) + anchor.offset

    if space.point:
        assert space.start is not None
        index = position(space.start)
        selected = (relevant[index],) if 0 <= index < len(relevant) else ()
    else:
        size = len(relevant)
        start = max(0, min(size, position(space.start))) if space.start else 0
        stop = max(0, min(size, position(space.stop))) if space.stop else size
        selected = relevant[start:stop]
    return SpaceSelection(
        space.canonical, bound.reason, anchors, space.family, relevant, selected
    )

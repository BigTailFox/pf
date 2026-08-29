from __future__ import annotations

from dataclasses import dataclass

from packaging.version import Version

from pf.project import marker_applies
from pf.schemas.project import (
    Cell,
    HarnessBaseline,
    HarnessRequirement,
    HarnessResolutionRequirement,
    HarnessSpecifierClause,
    PackagePlan,
    RelaxedHarness,
    ResolutionSourceMode,
    SourceIdentity,
    package_source,
)


@dataclass(frozen=True)
class HarnessRequirementPolicy:
    fixed: bool
    relaxable: bool
    ceiling_bound: bool


def harness_requirement_policy(
    requirement: HarnessRequirement,
    *,
    source: SourceIdentity,
) -> HarnessRequirementPolicy:
    exact = any(
        clause.operator == "==" and "*" not in clause.version
        or clause.operator == "==="
        for clause in requirement.specifier
    )
    fixed = source.kind != "registry" or exact
    relaxable = not fixed and any(
        clause.operator in {">", ">="} for clause in requirement.specifier
    )
    return HarnessRequirementPolicy(
        fixed=fixed,
        relaxable=relaxable,
        ceiling_bound=source.kind == "registry" and not fixed,
    )


def active_harness_requirements(
    requirements: tuple[HarnessRequirement, ...],
    cell: Cell,
) -> tuple[HarnessRequirement, ...]:
    return tuple(
        requirement
        for requirement in requirements
        if marker_applies(requirement.marker, cell)
    )


def relax_harness(
    package: PackagePlan,
    baseline: HarnessBaseline,
    *,
    source_mode: ResolutionSourceMode,
) -> RelaxedHarness:
    requirements = package.harness_requirements
    active = active_harness_requirements(requirements, baseline.cell)
    declaration_ids = tuple(sorted(item.declaration_id for item in active))
    if declaration_ids != baseline.declaration_ids:
        raise ValueError("harness baseline does not match active declarations")
    selections = {item.name: item for item in baseline.selections}
    transformed: list[HarnessResolutionRequirement] = []
    for requirement in active:
        policy = harness_requirement_policy(
            requirement,
            source=package_source(package, requirement.name, source_mode),
        )
        clauses = tuple(
            clause
            for clause in requirement.specifier
            if not (policy.relaxable and clause.operator in {">", ">="})
        )
        ceiling: str | None = None
        if policy.ceiling_bound:
            selection = selections.get(requirement.name)
            if selection is None or not selection.ceiling_bound:
                raise ValueError(
                    f"missing harness baseline ceiling: {requirement.name}"
                )
            ceiling = str(Version(selection.version))
            ceiling_clause = HarnessSpecifierClause(operator="<=", version=ceiling)
            clauses = tuple(
                sorted(
                    {*clauses, ceiling_clause},
                    key=lambda item: (item.operator, item.version),
                )
            )
        transformed.append(
            HarnessResolutionRequirement(
                declaration=requirement,
                specifier=clauses,
                relaxed_minimum=policy.relaxable,
                ceiling=ceiling,
            )
        )
    return RelaxedHarness.from_requirements(
        baseline_digest=baseline.digest,
        requirements=tuple(transformed),
    )


def original_harness(
    package: PackagePlan,
    cell: Cell,
    *,
    source_mode: ResolutionSourceMode,
) -> tuple[HarnessResolutionRequirement, ...]:
    """Project active declarations without applying the relaxation policy."""
    return tuple(
        HarnessResolutionRequirement(
            declaration=requirement,
            specifier=requirement.specifier,
            relaxed_minimum=False,
        )
        for requirement in active_harness_requirements(
            package.harness_requirements, cell
        )
    )


def render_harness_requirement(
    requirement: HarnessResolutionRequirement,
    *,
    source: SourceIdentity,
) -> str:
    declaration = requirement.declaration
    if source.kind != "registry":
        return declaration.original_text
    extras = (
        f"[{','.join(declaration.requested_extras)}]"
        if declaration.requested_extras
        else ""
    )
    specifier = ",".join(
        f"{item.operator}{item.version}" for item in requirement.specifier
    )
    marker = f"; {declaration.marker}" if declaration.marker else ""
    return f"{declaration.name}{extras}{specifier}{marker}"

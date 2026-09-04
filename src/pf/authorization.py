from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Literal

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from pf.errors import ApplyAuthorizationError, NoApplicableFloorError
from pf.policy import evaluation_policy_identity
from pf.project import marker_applies, marker_platform
from pf.report import PackageReportBuilder, ValidatedReport
from pf.schemas.apply import (
    ApplyPresentationFacts,
    AuthorizedDependencyGroupEdit,
    AuthorizedPackageApply,
    AuthorizedProjectEdit,
    AuthorizedWorkspaceApply,
)
from pf.schemas.project import (
    ApplySelector,
    Cell,
    PackagePlan,
    ProjectPlan,
    PyprojectIdentity,
    RequirementDeclaration,
    DynamicWorkspaceMemberVersion,
    StaticWorkspaceMemberVersion,
    SourcePlan,
    SourceSnapshotIdentity,
    cell_identity,
    dependency_group_key,
)
from pf.schemas.report import CellResult, CellSuccess, FloorProjection
from pf.snapshot import SourceSnapshot, uv_project_configuration_identity


GroupKey = tuple[str, str, str | None, str]
CellKey = tuple[str, str, str, tuple[str, ...]]
RequirementSemantic = tuple[object, ...]
ApplyScope = Literal["DECLARED_MATRIX", "PLATFORM_SCOPED"]


class ApplyAuthorizer:
    """Turn validated reports and current project facts into one frozen edit grant."""

    def __init__(self, *, projections: PackageReportBuilder | None = None) -> None:
        self._projections = projections or PackageReportBuilder()

    def authorize(
        self,
        *,
        report: ValidatedReport,
        project: ProjectPlan,
        current_snapshot: SourceSnapshot,
        force: bool,
    ) -> AuthorizedWorkspaceApply:
        package = project.target
        source_plan = SourcePlan.for_package(package, "SEARCH")
        if (report.package.name, report.package.pyproject_path) != (
            package.name,
            package.pyproject_path,
        ):
            raise ApplyAuthorizationError("report package identity mismatch")
        package_apply = self._authorize_package(
            report=report,
            package=package,
            source_plan=source_plan,
            current_snapshot=current_snapshot,
        )
        report_uv_configuration_identity = uv_project_configuration_identity(
            report.source_snapshot,
            package.pyproject_path,
        )
        report_target_pyproject = self._pyproject_identity(
            report.source_snapshot.pyproject_identities,
            package.pyproject_path,
        )
        current_uv_configuration_identity = uv_project_configuration_identity(
            current_snapshot.identity,
            package.pyproject_path,
            target_dependency_arrays_digest=(
                report_target_pyproject.dependency_arrays_digest
            ),
        )
        if report_uv_configuration_identity != current_uv_configuration_identity:
            raise ApplyAuthorizationError(
                "uv project configuration has drifted since search"
            )

        selected_paths = frozenset((package.pyproject_path,))
        changed_paths = self._source_layer_changes(
            expected=report.source_snapshot,
            current=current_snapshot.identity,
            selected_pyprojects=selected_paths,
            owned_pyproject_paths=project.owned_pyproject_paths,
        )
        if changed_paths and not force:
            raise ApplyAuthorizationError(
                "project source snapshot has drifted since search"
            )
        selectors = self._ordered_selectors(
            package_apply.selected_selectors
        )
        preserved = self._ordered_selectors(
            package_apply.preserved_selectors
        )
        presentation = ApplyPresentationFacts(
            observed_cells=package_apply.observed_cells,
            selected_selectors=selectors,
            preserved_selectors=preserved,
            source_drift_path_count=len(changed_paths),
            source_drift_paths=changed_paths[:8],
        )
        return AuthorizedWorkspaceApply(
            mode="FORCE" if force else "DEFAULT",
            waivers_used=("SOURCE_SNAPSHOT_DRIFT",) if changed_paths else (),
            expected_snapshot=current_snapshot.identity,
            owned_pyproject_paths=project.owned_pyproject_paths,
            package_apply=package_apply,
            presentation_facts=presentation,
        )

    def _authorize_package(
        self,
        *,
        report: ValidatedReport,
        package: PackagePlan,
        source_plan: SourcePlan,
        current_snapshot: SourceSnapshot,
    ) -> AuthorizedPackageApply:
        if report.policy_identity != evaluation_policy_identity(package.config):
            raise ApplyAuthorizationError("report evaluation policy mismatch")
        if report.source_plan != source_plan:
            raise ApplyAuthorizationError("report dependency source plan mismatch")
        if self._requires_python(
            report.package.requires_python
        ) != self._requires_python(package.requires_python):
            raise ApplyAuthorizationError("report package Python semantics mismatch")

        scope, selected, preserved, observed_cells = self._platform_authority(
            report=report,
            package=package,
        )
        report_groups = self._groups(report.requirement_declarations)
        current_groups = self._groups(package.declarations)
        intended_requirements: dict[GroupKey, tuple[str, ...]] = {}
        group_edits: list[AuthorizedDependencyGroupEdit] = []
        for key in sorted(report_groups):
            declarations = report_groups[key]
            if not any(declaration.managed for declaration in declarations):
                intended_requirements[key] = tuple(
                    declaration.raw for declaration in declarations
                )
                continue
            floors = self._group_floors(
                report=report,
                declarations=declarations,
                selected_selectors=frozenset(
                    self._selector_tuple(item) for item in selected
                ),
            )
            projection = self._projections.project(
                declarations=declarations,
                target_cells=report.target_cells,
                floors=floors,
                selected_selectors=selected,
                platform_scoped=scope == "PLATFORM_SCOPED",
            )
            if not projection.representable:
                raise ApplyAuthorizationError(
                    "dependency projection is not exactly representable"
                )
            intended_requirements[key] = projection.projected_requirements
            if projection.projected_requirements != projection.original_requirements:
                group_edits.append(
                    AuthorizedDependencyGroupEdit(
                        key=projection.key,
                        replacement_requirements=projection.projected_requirements,
                    )
                )

        original_semantics = self._declaration_group_semantics(
            report_groups,
            target_cells=report.target_cells,
        )
        self._validate_workspace_member_versions(
            source_plan=source_plan,
            intended_requirements=intended_requirements,
        )
        current_semantics = self._declaration_group_semantics(
            current_groups,
            target_cells=report.target_cells,
        )
        intended_semantics = self._raw_group_semantics(
            intended_requirements,
            report_groups=report_groups,
            target_cells=report.target_cells,
        )
        if current_semantics == original_semantics:
            dependency_state = "WRITABLE"
        elif current_semantics == intended_semantics:
            dependency_state = "NOOP"
            group_edits = []
        else:
            raise ApplyAuthorizationError(
                "project dependency declarations have drifted since search"
            )

        current_pyproject = self._pyproject_identity(
            current_snapshot.identity.pyproject_identities,
            package.pyproject_path,
        )
        authorized_edits = (
            (
                AuthorizedProjectEdit(
                    pyproject_path=package.pyproject_path,
                    expected_pyproject_identity=current_pyproject,
                    group_edits=tuple(group_edits),
                ),
            )
            if group_edits
            else ()
        )
        return AuthorizedPackageApply(
            package=report.package,
            scope=scope,
            declared_platforms=package.config.target.platforms or (),
            selected_selectors=selected,
            preserved_selectors=preserved,
            dependency_state=dependency_state,
            observed_cells=observed_cells,
            authorized_edits=authorized_edits,
        )

    @staticmethod
    def _validate_workspace_member_versions(
        *,
        source_plan: SourcePlan,
        intended_requirements: dict[GroupKey, tuple[str, ...]],
    ) -> None:
        for dependency in source_plan.registry_routed_workspace_dependencies():
            requirements = tuple(
                raw
                for key, values in intended_requirements.items()
                if key[3] == dependency
                for raw in values
            )
            if not requirements:
                continue
            intended = requirements[0]
            member_version = source_plan.workspace_member_version_for(dependency)
            if isinstance(member_version, DynamicWorkspaceMemberVersion):
                raise ApplyAuthorizationError(
                    f"Cannot apply {intended}: workspace member "
                    f"{dependency} declares its version dynamically.\n"
                    "PF cannot verify offline that the local member satisfies the "
                    "intended requirement.\n"
                    "Next: apply the requirement manually and run pf smoke, or "
                    "declare a static [project].version."
                )
            if not isinstance(member_version, StaticWorkspaceMemberVersion):
                raise ApplyAuthorizationError(
                    f"workspace member version metadata is missing: {dependency}"
                )
            version = Version(member_version.value)
            if any(version not in Requirement(raw).specifier for raw in requirements):
                raise ApplyAuthorizationError(
                    f"Cannot apply {intended}: workspace member {dependency} "
                    f"version {version} does not satisfy the intended requirement.\n"
                    "Next: update the local member version or apply the requirement "
                    "manually and run pf smoke."
                )

    def _platform_authority(
        self,
        *,
        report: ValidatedReport,
        package: PackagePlan,
    ) -> tuple[
        ApplyScope,
        tuple[ApplySelector, ...],
        tuple[ApplySelector, ...],
        int,
    ]:
        target_platforms = tuple(sorted({cell.target for cell in report.target_cells}))
        if not target_platforms:
            raise NoApplicableFloorError("report declares no target cells")
        if not any(isinstance(result, CellSuccess) for result in report.cell_results):
            raise NoApplicableFloorError("report has no successful final cell")
        declared = package.config.target.platforms
        if not declared:
            if len(target_platforms) != 1:
                raise ApplyAuthorizationError(
                    "an omitted platform declaration requires one report platform"
                )
        elif set(declared) != set(target_platforms):
            raise ApplyAuthorizationError(
                "current platform declaration does not match the report"
            )

        targets_by_platform: dict[str, set[CellKey]] = defaultdict(set)
        roots_by_platform: dict[str, list[CellResult]] = defaultdict(list)
        for cell in report.target_cells:
            targets_by_platform[cell.target].add(cell_identity(cell))
        for result in report.cell_results:
            roots_by_platform[result.cell.target].append(result)
        complete_platforms: set[str] = set()
        observed_cells = 0
        for platform, roots in roots_by_platform.items():
            root_keys = {cell_identity(result.cell) for result in roots}
            if root_keys != targets_by_platform[platform] or not all(
                isinstance(result, CellSuccess) for result in roots
            ):
                raise ApplyAuthorizationError(
                    "report has a failed or partially observed platform"
                )
            complete_platforms.add(platform)
            observed_cells += len(roots)
        if not complete_platforms:
            raise NoApplicableFloorError("report has no complete evidence platform")

        selector_platforms: dict[tuple[str, str], set[str]] = defaultdict(set)
        for platform in target_platforms:
            selector_platforms[self._target_selector(platform)].add(platform)
        selected_keys = {
            selector
            for selector, platforms in selector_platforms.items()
            if platforms & complete_platforms
        }
        preserved_keys = set(selector_platforms) - selected_keys
        if not declared or len(declared) == 1:
            if preserved_keys:
                raise ApplyAuthorizationError(
                    "single-platform report evidence is incomplete"
                )
            scope: ApplyScope = "DECLARED_MATRIX"
        else:
            scope = "PLATFORM_SCOPED" if preserved_keys else "DECLARED_MATRIX"
        return (
            scope,
            self._selector_models(selected_keys),
            self._selector_models(preserved_keys),
            observed_cells,
        )

    def _group_floors(
        self,
        *,
        report: ValidatedReport,
        declarations: tuple[RequirementDeclaration, ...],
        selected_selectors: frozenset[tuple[str, str]],
    ) -> tuple[FloorProjection, ...]:
        managed_ids = {
            declaration.declaration_id
            for declaration in declarations
            if declaration.managed
        }
        name = declarations[0].name
        floors = []
        for result in report.cell_results:
            if (
                not isinstance(result, CellSuccess)
                or self._target_selector(result.cell.target) not in selected_selectors
                or not (managed_ids & set(result.cell.active_declaration_ids))
            ):
                continue
            version = next(
                (pin.version for pin in result.final_vector if pin.name == name),
                None,
            )
            if version is None:
                raise ApplyAuthorizationError(
                    "successful Cell has no floor for an active managed dependency"
                )
            floors.append(FloorProjection(cell=result.cell, version=version))
        return tuple(sorted(floors, key=lambda floor: cell_identity(floor.cell)))

    @staticmethod
    def _groups(
        declarations: tuple[RequirementDeclaration, ...],
    ) -> dict[GroupKey, tuple[RequirementDeclaration, ...]]:
        groups: dict[GroupKey, list[RequirementDeclaration]] = defaultdict(list)
        for declaration in declarations:
            key = dependency_group_key(declaration)
            groups[
                (
                    key.pyproject_path,
                    key.location,
                    key.optional_group,
                    key.name,
                )
            ].append(declaration)
        return {
            key: tuple(sorted(value, key=lambda item: item.declaration_id))
            for key, value in groups.items()
        }

    def _declaration_group_semantics(
        self,
        groups: dict[GroupKey, tuple[RequirementDeclaration, ...]],
        *,
        target_cells: tuple[Cell, ...],
    ) -> dict[GroupKey, tuple[RequirementSemantic, ...]]:
        return {
            key: tuple(
                sorted(
                    [
                        self._requirement_semantic(
                            raw=declaration.raw,
                            kind=declaration.kind,
                            managed=declaration.managed,
                            target_cells=target_cells,
                        )
                        for declaration in declarations
                    ],
                    key=repr,
                )
            )
            for key, declarations in groups.items()
        }

    def _raw_group_semantics(
        self,
        groups: dict[GroupKey, tuple[str, ...]],
        *,
        report_groups: dict[GroupKey, tuple[RequirementDeclaration, ...]],
        target_cells: tuple[Cell, ...],
    ) -> dict[GroupKey, tuple[RequirementSemantic, ...]]:
        result = {}
        for key, requirements in groups.items():
            declarations = report_groups[key]
            preserved = {item.raw: item for item in declarations if not item.managed}
            result[key] = tuple(
                sorted(
                    [
                        self._requirement_semantic(
                            raw=raw,
                            kind=(
                                preserved[raw].kind
                                if raw in preserved
                                else "searchable"
                            ),
                            managed=(
                                preserved[raw].managed if raw in preserved else True
                            ),
                            target_cells=target_cells,
                        )
                        for raw in requirements
                    ],
                    key=repr,
                )
            )
        return result

    @staticmethod
    def _requirement_semantic(
        *,
        raw: str,
        kind: str,
        managed: bool,
        target_cells: tuple[Cell, ...],
    ) -> RequirementSemantic:
        requirement = Requirement(raw)
        marker = str(requirement.marker) if requirement.marker is not None else None
        activation = tuple(
            (cell_identity(cell), marker_applies(marker, cell)) for cell in target_cells
        )
        return (
            tuple(sorted(requirement.extras)),
            tuple(
                sorted(
                    (specifier.operator, specifier.version)
                    for specifier in requirement.specifier
                )
            ),
            marker,
            activation,
            requirement.url,
            kind,
            managed,
        )

    def _source_layer_changes(
        self,
        *,
        expected: SourceSnapshotIdentity,
        current: SourceSnapshotIdentity,
        selected_pyprojects: frozenset[str],
        owned_pyproject_paths: tuple[str, ...],
    ) -> tuple[str, ...]:
        expected_pyprojects = {
            identity.path: identity for identity in expected.pyproject_identities
        }
        current_pyprojects = {
            identity.path: identity for identity in current.pyproject_identities
        }
        owned = frozenset(owned_pyproject_paths)
        if (
            set(expected_pyprojects) != owned
            or set(current_pyprojects) != owned
            or not selected_pyprojects <= owned
        ):
            raise ApplyAuthorizationError(
                "owned pyproject membership changed since search"
            )
        changed: set[str] = set()
        expected_entries = {entry.path: entry for entry in expected.entries}
        current_entries = {entry.path: entry for entry in current.entries}
        for path in set(expected_entries) | set(current_entries):
            if expected_entries.get(path) != current_entries.get(path):
                changed.add(path)
        for path in sorted(owned):
            before = expected_pyprojects[path]
            after = current_pyprojects[path]
            if (
                path not in selected_pyprojects
                and before.dependency_arrays_digest != after.dependency_arrays_digest
            ):
                raise ApplyAuthorizationError(
                    "an unselected package dependency array has drifted"
                )
            if (
                before.mode != after.mode
                or before.remainder_digest != after.remainder_digest
            ):
                changed.add(path)
        return tuple(sorted(changed))

    @staticmethod
    def _requires_python(value: str | None) -> str:
        return str(SpecifierSet(value or ""))

    @staticmethod
    def _target_selector(target: str) -> tuple[str, str]:
        values = marker_platform(target)
        return values["sys_platform"], values["platform_machine"]

    @staticmethod
    def _selector_tuple(selector: ApplySelector) -> tuple[str, str]:
        return selector.sys_platform, selector.platform_machine

    @staticmethod
    def _selector_models(
        selectors: Iterable[tuple[str, str]],
    ) -> tuple[ApplySelector, ...]:
        return tuple(
            ApplySelector(sys_platform=sys_platform, platform_machine=machine)
            for sys_platform, machine in sorted(set(selectors))
        )

    @classmethod
    def _ordered_selectors(
        cls,
        selectors: Iterable[ApplySelector],
    ) -> tuple[ApplySelector, ...]:
        return cls._selector_models(cls._selector_tuple(item) for item in selectors)

    @staticmethod
    def _pyproject_identity(
        identities: tuple[PyprojectIdentity, ...],
        path: str,
    ) -> PyprojectIdentity:
        identity = next((item for item in identities if item.path == path), None)
        if identity is None:
            raise ApplyAuthorizationError("report lacks an owned pyproject identity")
        return identity

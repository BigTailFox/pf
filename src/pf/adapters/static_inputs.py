"""Capture installed files and interpreter facts for a clean prepared object."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from urllib.request import url2pathname

from packaging.utils import canonicalize_name
from packaging.version import Version
import tomli

from pf.adapters.process import ProcessRunner, read_process_output
from pf.schemas.evaluation import ProcessObservation, ProcessResult, ProcessSpec
from pf.schemas.project import InterpreterIdentity, PackagePlan, SourcePlan, SourceIdentity
from pf.schemas.static import (
    StaticContentPath, StaticContentUnavailable,
    StaticInstalledArtifact, StaticInstalledNode, StaticInstalledWorld,
    StaticPackageMapping, StaticSourceInput, StaticTargetInput,
)
from pf.static_subject import StaticContentCollector
from pf.static_relocation import relocate_installed_content

if TYPE_CHECKING:
    from pf.environment import PreparedEnvironment
from pf.cancellation import Cancellation


# -I -S prevents project/site hooks from running during this file inventory.
# sysconfig gets explicit venv bases because Python <3.14 handles venv prefix
# initialization in site, which is intentionally not imported here.
_INSPECT = """
import importlib.metadata as metadata
import json, os, platform, sys, sysconfig
base = sys.argv[1]
paths = sysconfig.get_paths(vars={'base': base, 'platbase': base})
sites = list(dict.fromkeys([paths['purelib'], paths['platlib']]))
nodes = []
for distribution in metadata.distributions(path=sites):
    files = distribution.files
    if files is None:
        raise ValueError('installed distribution has no file inventory')
    nodes.append({
        'name': distribution.metadata['Name'], 'version': distribution.version,
        'files': [os.path.abspath(distribution.locate_file(f)) for f in files],
        'direct_url': json.loads(distribution.read_text('direct_url.json') or 'null'),
    })
library = sysconfig.get_config_var('LDLIBRARY')
libdir = sysconfig.get_config_var('LIBDIR')
shared_library = os.path.join(libdir, library) if library and libdir else None
if shared_library and not os.path.isfile(shared_library):
    shared_library = None
print(json.dumps({
    'interpreter': {'implementation': sys.implementation.name,
                    'version': platform.python_version(),
                    'abi': sysconfig.get_config_var('SOABI') or ''},
    'executable': os.path.realpath(sys.executable),
    'stdlib': sysconfig.get_path('stdlib'), 'sites': sites,
    'shared_library': os.path.realpath(shared_library) if shared_library else None,
    'nodes': nodes,
}))
"""


@dataclass(frozen=True)
class PreparedStaticInputs:
    source: StaticSourceInput
    target: StaticTargetInput
    installed_world: StaticInstalledWorld
    roots: tuple[tuple[str, Path], ...]
    import_roots: tuple[StaticContentPath, ...]
    process: ProcessResult


@dataclass(frozen=True)
class PreparedStaticInputsUnavailable:
    failure: StaticContentUnavailable
    process: ProcessObservation | None


class StaticInputsAdapter:
    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def capture(
        self, prepared: PreparedEnvironment, *, package: PackagePlan,
        source_plan: SourcePlan, cancellation: Cancellation | None = None,
    ) -> PreparedStaticInputs | PreparedStaticInputsUnavailable:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        with prepared.static_use(cancellation=cancellation) as available:
            if not available:
                return PreparedStaticInputsUnavailable(StaticContentUnavailable(detail="installed-input-mismatch"), None)
            if (
                prepared.tested
                or package.name != prepared.proposal.cell.package
                or source_plan.identity != prepared.attempt.identity.source_plan_identity
                or prepared.package_root != prepared.proposal_root / Path(package.pyproject_path).parent
            ):
                return PreparedStaticInputsUnavailable(
                    StaticContentUnavailable(detail="installed-input-mismatch"), None,
                )
            process = self._runner.run(ProcessSpec(
                argv=(prepared.interpreter.as_posix(), "-I", "-S", "-B", "-c", _INSPECT, prepared.environment_root.as_posix()),
                cwd=prepared.package_root.as_posix(), environment_mode="explicit",
                timeout_seconds=package.config.ty.timeout_seconds,
            ), cancellation=cancellation)
            if not isinstance(process, ProcessResult) or process.exit_code != 0 or process.timed_out or not process.stdout_complete:
                return PreparedStaticInputsUnavailable(
                    StaticContentUnavailable(detail="inspection-unavailable"), process,
                )
            try:
                observed = json.loads(read_process_output(self._runner, process).stdout)
                interpreter = InterpreterIdentity.model_validate(observed["interpreter"])
                if interpreter != prepared.proposal.interpreter:
                    raise ValueError("interpreter changed after preparation")
                roots = {
                    "snapshot": prepared.proposal_root,
                    "environment": prepared.environment_root,
                    "interpreter-executable": Path(observed["executable"]),
                    "interpreter-stdlib": Path(observed["stdlib"]),
                }
                if observed["shared_library"] is not None:
                    roots["interpreter-library"] = Path(observed["shared_library"])
                manifest = StaticContentCollector().collect(roots)
                if isinstance(manifest, StaticContentUnavailable):
                    return PreparedStaticInputsUnavailable(manifest, process)
                manifest = relocate_installed_content(manifest, roots)
                source_content = manifest.for_roots(frozenset({"snapshot"}))
                target_content = manifest.for_roots(frozenset(
                    name for name in roots if name.startswith("interpreter-")
                ))
                installed_content = manifest.for_roots(frozenset({"environment", "snapshot"}))
                nodes = self._installed_nodes(prepared, package, observed["nodes"], roots)
                assigned = {location for node in nodes for location in node.files}
                support = tuple(
                    entry.location for entry in installed_content.entries
                    if entry.kind != "directory" and entry.location not in assigned
                )
                packages = self._package_mappings(prepared, package, source_plan)
                return PreparedStaticInputs(
                    source=StaticSourceInput(
                        snapshot_identity=prepared.proposal.snapshot_digest,
                        content=source_content, packages=packages, source_plan=source_plan,
                    ),
                    target=StaticTargetInput(
                        cell=prepared.proposal.cell, interpreter=interpreter,
                        content=target_content,
                        executable=StaticContentPath(root="interpreter-executable", path="."),
                        stdlib_roots=(StaticContentPath(root="interpreter-stdlib", path="."),),
                    ),
                    installed_world=StaticInstalledWorld(
                        content=installed_content, nodes=nodes, support_files=support,
                    ),
                    roots=tuple(sorted(roots.items())),
                    import_roots=tuple(self._logical_path(Path(path), roots) for path in observed["sites"]),
                    process=process,
                )
            except (OSError, ValueError, KeyError, TypeError):
                return PreparedStaticInputsUnavailable(
                    StaticContentUnavailable(detail="installed-input-mismatch"), process,
                )

    @staticmethod
    def _logical_path(path: Path, roots: dict[str, Path]) -> StaticContentPath:
        normalized = Path(os.path.abspath(path))
        matches = [
            (len(root.parts), name, normalized.relative_to(root).as_posix())
            for name, root in roots.items() if normalized.is_relative_to(root)
        ]
        if not matches:
            raise ValueError("installed path is outside registered roots")
        _, name, relative = max(matches)
        return StaticContentPath(root=name, path=relative)

    @classmethod
    def _installed_nodes(cls, prepared: PreparedEnvironment, package: PackagePlan, observations, roots) -> tuple[StaticInstalledNode, ...]:
        plan = prepared.environment_plan or prepared.project_plan
        planned = {node.name: node for node in plan.packages}
        expected = {node.name: node.version for node in prepared.proposal.resolved_graph}
        seen: set[str] = set()
        nodes: list[StaticInstalledNode] = []
        for observed in observations:
            name = canonicalize_name(observed["name"])
            version = str(Version(observed["version"]))
            if name in seen or expected.get(name) != version or (name not in planned and name != package.name):
                raise ValueError("installed inventory does not match prepared graph")
            seen.add(name)
            node = planned.get(name)
            files = tuple(sorted({
                cls._logical_path(Path(path), roots) for path in observed["files"]
            }, key=lambda item: (item.root, item.path)))
            artifact = node.selected_artifact if node else None
            direct_url = observed["direct_url"]
            mapping = None
            editable = False
            if direct_url is not None and "dir_info" in direct_url:
                parsed = urlsplit(direct_url["url"])
                if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
                    raise ValueError("installed directory mapping is not local")
                mapping = cls._logical_path(Path(url2pathname(parsed.path)), roots)
                editable = direct_url["dir_info"].get("editable", False)
            if node is None:
                expected_mapping = cls._logical_path(prepared.package_root, roots)
                if mapping != expected_mapping:
                    raise ValueError("installed project does not map to prepared source")
                source = SourceIdentity(kind="path", locator=expected_mapping.path)
                dependencies = tuple(sorted({
                    declaration.name for declaration in package.declarations
                    if declaration.declaration_id in prepared.proposal.cell.active_declaration_ids
                    and declaration.name != package.name
                }))
            else:
                source = node.source
                dependencies = node.dependencies
            nodes.append(StaticInstalledNode(
                name=name, version=version, source=source,
                artifact=(StaticInstalledArtifact.model_validate(artifact.model_dump(mode="json")) if artifact else None),
                dependencies=dependencies, install_mode="editable" if editable else "wheel",
                source_mapping=mapping, files=files,
            ))
        if seen != set(expected):
            raise ValueError("installed inventory is incomplete")
        return tuple(sorted(nodes, key=lambda node: node.name))

    @staticmethod
    def _package_mappings(prepared: PreparedEnvironment, package: PackagePlan, source_plan: SourcePlan) -> tuple[StaticPackageMapping, ...]:
        root = prepared.proposal_root
        members = {package.name: Path(package.pyproject_path).parent}
        for route in source_plan.routes:
            source = route.development_source
            if source.kind == "workspace" and source.locator is not None:
                member = Path(source.locator)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError("workspace source is outside the snapshot")
                members[route.dependency] = member
        packages: list[StaticPackageMapping] = []
        for expected, relative in sorted(members.items()):
            path = root / relative / "pyproject.toml"
            document = tomli.loads(path.read_text(encoding="utf-8"))
            name = document.get("project", {}).get("name")
            if not isinstance(name, str) or canonicalize_name(name) != expected:
                raise ValueError("workspace source does not match the package mapping")
            packages.append(StaticPackageMapping(
                package=expected,
                source=StaticContentPath(root="snapshot", path=relative.as_posix()),
            ))
        return tuple(sorted(packages, key=lambda item: item.package))

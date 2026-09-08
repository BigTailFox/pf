"""Assemble a complete ty request from a clean, verified prepared environment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
import shutil
from pathlib import Path

import tomli
import tomlkit

from pf.adapters.process import ProcessRunner, read_process_output
from pf.adapters.static_inputs import PreparedStaticInputs, StaticInputsAdapter
from pf.environment import PreparedEnvironment
from pf.cancellation import Cancellation
from pf.policy import guidance_policy, execution_policy
from pf.resolution import ResolutionPlanEvidence
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.schemas.evaluation import EnvironmentVariable, ProcessResult, ProcessSpec
from pf.schemas.policy import TyObservationPolicy
from pf.schemas.project import PackagePlan
from pf.schemas.static import (
    StaticAnalysisLayout, StaticConfigurationInput, StaticContentManifest,
    StaticContentPath, StaticContentUnavailable, StaticRootPlacement, StaticSubject,
)
from pf.static_configuration import TyConfigurationResolution, TyConfigurationResolver, TyConfigurationMaterialization, materialize_ty_configuration
from pf.static_external import FrozenSearchRoots, freeze_external_search_roots
from pf.static_ignores import (
    TyGlobalIgnoreInputs, TyGlobalIgnoreMaterialization, TyIgnoreBoundaries,
    capture_ty_global_ignores, capture_ty_ignore_boundaries,
    materialize_ty_global_ignores, materialize_ty_ignore_boundaries,
)
from pf.static_paths import TySearchPaths, resolve_ty_search_paths
from pf.static_process import StaticProcessEnvironment, bind_static_process_environment
from pf.static_relocation import relocate_installed_content
from pf.static_subject import StaticContentCollector


@dataclass(frozen=True)
class StaticTyRequest:
    subject: StaticSubject
    observation_policy: TyObservationPolicy
    spec: ProcessSpec
    snapshot_root: Path
    environment_root: Path
    prepared: PreparedEnvironment
    roots: tuple[tuple[str, Path], ...]
    captured_content: StaticContentManifest
    ignore_boundaries: TyIgnoreBoundaries | None
    preparation: StaticPreparationEvidence

    def revalidate(self) -> bool:
        if self.prepared.closed or self.prepared.tested or not self.prepared.inputs_valid:
            return False
        current = StaticContentCollector().collect(dict(self.roots))
        execution_roots = frozenset(
            entry.location.root
            for content in (self.subject.source.content, self.subject.installed_world.content,
                            self.subject.target.content)
            for entry in content.entries
        )
        if isinstance(current, StaticContentManifest):
            if current.for_roots(execution_roots) != self.captured_content.for_roots(execution_roots):
                self.prepared.invalidate_inputs()
        elif any(not path.exists() for root, path in self.roots if root in execution_roots):
            self.prepared.invalidate_inputs()
        if self.ignore_boundaries is not None and not self.ignore_boundaries.revalidate():
            return False
        return current == self.captured_content


@dataclass(frozen=True)
class StaticRequestMaterialization:
    """One prepared environment's frozen request files, never a raw fact cache."""
    key: tuple[str | None, str, tuple[tuple[str, str], ...]]
    request: StaticTyRequest


class StaticRequestFactory:
    def __init__(self, runner: ProcessRunner, *, ty_executable: Path | None = None) -> None:
        self._runner = runner
        discovered = shutil.which("ty") if ty_executable is None else None
        self._ty_executable = ty_executable or (Path(discovered) if discovered else None)

    def capture(
        self, prepared: PreparedEnvironment, *, package: PackagePlan,
        environment: Mapping[str, str],
        cancellation: Cancellation | None = None,
    ) -> StaticTyRequest | StaticContentUnavailable:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        with prepared.static_use(cancellation=cancellation) as available:
            if not available:
                return StaticContentUnavailable(detail="content-changed")
            key = (str(self._ty_executable) if self._ty_executable else None,
                   package.model_dump_json(), tuple(sorted(environment.items())))
            previous = prepared.static_materialization
            if previous is not None:
                current = previous.request.revalidate()
                if not prepared.inputs_valid:
                    return StaticContentUnavailable(detail="content-changed")
                if previous.key == key and current:
                    return previous.request
                # Only remove files owned by a prior successful materialization,
                # while the prepared input lease excludes all active borrowers.
                directory = prepared.proposal_root.parent / "static"
                try:
                    if directory.exists():
                        shutil.rmtree(directory)
                except OSError:
                    return StaticContentUnavailable(detail="unreadable-content")
                prepared.static_materialization = None
                prepared.static_consumer = None
            inputs = StaticInputsAdapter(self._runner).capture(prepared, package=package, source_plan=prepared.source_plan, cancellation=cancellation)
            if not isinstance(inputs, PreparedStaticInputs):
                return inputs.failure
            configuration = TyConfigurationResolver().resolve(
                project_directory=prepared.package_root, environment=environment,
                platform="windows" if os.name == "nt" else "posix",
            )
            if not isinstance(configuration, TyConfigurationResolution):
                return StaticContentUnavailable(detail="invalid-layout")
            paths = resolve_ty_search_paths(configuration, args=package.config.ty.args,
                                           environment=environment, cwd=prepared.package_root, package_name=package.name)
            if not isinstance(paths, TySearchPaths) or paths.ignored_pythonpath:
                return StaticContentUnavailable(detail="invalid-layout")
            try:
                request = self._assemble(prepared, package, inputs, configuration, paths, environment, cancellation)
                prepared.static_materialization = StaticRequestMaterialization(key, request)
                return request
            except (OSError, ValueError, KeyError, TypeError):
                return StaticContentUnavailable(detail="invalid-layout")

    def _assemble(self, prepared, package, inputs, configuration, paths, environment, cancellation) -> StaticTyRequest:
        directory = prepared.proposal_root.parent / "static"
        directory.mkdir(mode=0o700)
        roots = dict(inputs.roots)
        selections = (configuration.analysis_root, *paths.first_party, *paths.extra, *paths.pythonpath)
        if paths.typeshed is not None:
            selections += (paths.typeshed,)
        external = freeze_external_search_roots(selections, registered=roots, directory=directory / "external")
        if not isinstance(external, FrozenSearchRoots):
            raise ValueError("external search roots are unavailable")
        roots = dict(external.roots)
        selected = iter(external.selections)
        project_ref = next(selected)
        first_party = tuple(next(selected) for _ in paths.first_party)
        extra = tuple(next(selected) for _ in paths.extra)
        pythonpath = tuple(next(selected) for _ in paths.pythonpath)
        typeshed = next(selected) if paths.typeshed is not None else None
        project_root = _physical(project_ref, roots)

        values = dict(environment)
        expanded_path_names = []
        for name in paths.expansion_variables:
            if name in {"HOME", "PYTHONPATH"}:
                continue
            raw = Path(values[name])
            if raw.is_absolute() and raw in selections:
                values[name] = str(_physical(external.selections[selections.index(raw)], roots))
                expanded_path_names.append(name)
        permitted = {"HOME", "XDG_CONFIG_HOME", "APPDATA", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                     "TY_CONFIG_FILE", "PYTHONPATH", "LANG", "LC_ALL", "LC_CTYPE"} | set(paths.expansion_variables)
        values = {name: value for name, value in values.items() if name in permitted}
        config_roots = set(roots) - set(dict(inputs.roots))
        config_files: list[StaticContentPath] = []
        boundaries = None
        if paths.respect_ignore_files:
            ignored = capture_ty_global_ignores(environment=environment, cwd=prepared.package_root)
            if not isinstance(ignored, TyGlobalIgnoreInputs):
                raise ValueError("global ignore input is unavailable")
            frozen = materialize_ty_global_ignores(ignored, directory=directory / "global-ignores")
            if not isinstance(frozen, TyGlobalIgnoreMaterialization):
                raise ValueError("global ignore materialization is unavailable")
            roots["global-ignores"] = frozen.directory
            config_roots.add("global-ignores")
            config_files.extend(frozen.inputs)
            values.update(dict(frozen.environment))
            # Only actual analysis trees participate in file-selection walking.
            analysis_roots = {name: path for name, path in roots.items() if name == "snapshot" or name.startswith("external-")}
            boundaries = capture_ty_ignore_boundaries(roots=analysis_roots, content=external.content)
            if not isinstance(boundaries, TyIgnoreBoundaries):
                raise ValueError("outer ignore input is unavailable")
            boundary_content = materialize_ty_ignore_boundaries(boundaries, directory=directory / "ignore-boundaries")
            if not isinstance(boundary_content, StaticContentManifest):
                raise ValueError("ignore boundary changed")
            roots["ignore-boundaries"] = directory / "ignore-boundaries"
            config_roots.add("ignore-boundaries")
        elif any(name in values for name in ("HOME", "XDG_CONFIG_HOME", "APPDATA", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")):
            # These inputs have already been consumed by configuration/path
            # resolution. Replace their discovery destinations with owned empty
            # directories; their captured configuration remains in the subject.
            home = directory / "process-home"
            home.mkdir()
            roots["process-home"] = home
            config_roots.add("process-home")
            for name in ("HOME", "XDG_CONFIG_HOME", "APPDATA"):
                if name in values:
                    values[name] = str(home)
            for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
                if name in values:
                    empty = home / name.lower()
                    empty.write_bytes(b"")
                    values[name] = str(empty)

        settings = tomli.loads(configuration.effective_toml)
        retained = []
        args = package.config.ty.args
        index = 0
        while index < len(args):
            argument = args[index]
            option, equals, value = argument.partition("=")
            if option in {"--config", "-c", "--extra-search-path", "--typeshed", "--custom-typeshed-dir"}:
                if not equals:
                    index += 1
                    value = args[index]
                if option in {"--config", "-c"}:
                    settings = TyConfigurationResolver._merge(settings, tomli.loads(value))
            else:
                retained.append(argument)
                if option in {"--error", "--warn", "--ignore", "--exclude"} and not equals:
                    index += 1
                    retained.append(args[index])
            index += 1
        env_settings = settings.setdefault("environment", {})
        env_settings["root"] = [os.path.relpath(_physical(ref, roots), project_root) for ref in first_party]
        env_settings["extra-paths"] = [os.path.relpath(_physical(ref, roots), project_root) for ref in extra]
        if typeshed is not None:
            env_settings["typeshed"] = os.path.relpath(_physical(typeshed, roots), project_root)
        compiled = TyConfigurationResolution(configuration.files, tomlkit.dumps(settings), project_root, configuration.queried_paths)
        materialized = materialize_ty_configuration(compiled, directory=directory / "configuration")
        if not isinstance(materialized, TyConfigurationMaterialization):
            raise ValueError("configuration materialization is unavailable")
        roots["configuration"] = materialized.directory
        config_roots.add("configuration")
        config_files.extend(materialized.files_in_precedence_order)
        config_files.append(materialized.effective_file)
        effective_file = _physical(materialized.effective_file, roots)
        if "TY_CONFIG_FILE" in values:
            values["TY_CONFIG_FILE"] = str(effective_file)
        if "PYTHONPATH" in values:
            values["PYTHONPATH"] = os.pathsep.join(str(_physical(ref, roots)) for ref in pythonpath)

        case_probe = directory / "PfCase"
        case_probe.write_bytes(b"")
        filesystem_case = "insensitive" if (directory / "pFcASE").exists() else "sensitive"
        case_probe.unlink()
        if self._ty_executable is None:
            raise ValueError("ty executable unavailable")
        tool = self._ty_executable.resolve(strict=True)
        tool_roots = {"ty-executable": tool}
        tool_content = StaticContentCollector().collect(tool_roots)
        if not isinstance(tool_content, StaticContentManifest):
            raise ValueError("tool content unavailable")
        version = self._runner.run(ProcessSpec(argv=(str(tool), "--version"), cwd=str(directory),
                                               environment_mode="explicit", timeout_seconds=package.config.ty.timeout_seconds), cancellation=cancellation)
        if not isinstance(version, ProcessResult) or version.exit_code != 0 or version.timed_out or not version.stdout_complete:
            raise ValueError("tool version unavailable")
        policy = guidance_policy(package.config, tool_version=read_process_output(self._runner, version).stdout.strip(),
                                 tool_content=tool_content, executable=StaticContentPath(root="ty-executable", path=".")).observation
        raw_content = StaticContentCollector().collect(roots)
        if not isinstance(raw_content, StaticContentManifest):
            raise ValueError("request content unavailable")
        normalized = relocate_installed_content(raw_content, roots)
        if (
            normalized.for_roots(frozenset({"snapshot"})) != inputs.source.content
            or normalized.for_roots(frozenset(name for name in roots if name.startswith("interpreter-"))) != inputs.target.content
            or normalized.for_roots(frozenset({"environment", "snapshot"})) != inputs.installed_world.content
        ):
            raise ValueError("prepared inputs changed while assembling the request")
        path_names = tuple(name for name in ("HOME", "XDG_CONFIG_HOME", "APPDATA", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "TY_CONFIG_FILE", *expanded_path_names) if name in values)
        process = bind_static_process_environment(
            tuple(EnvironmentVariable(name=name, value=value, sensitive=name not in path_names and name != "PYTHONPATH") for name, value in values.items()),
            roots=roots, content=normalized, path_variables=path_names,
            path_list_variables=("PYTHONPATH",) if "PYTHONPATH" in values else (),
            filesystem_case=filesystem_case, environment_case="insensitive" if os.name == "nt" else "sensitive",
        )
        if not isinstance(process, StaticProcessEnvironment):
            raise ValueError("process environment unavailable")
        interpreter_roots = {name: path for name, path in roots.items() if name.startswith("interpreter-")}
        interpreter_base = Path(os.path.commonpath(tuple(str(path) for path in interpreter_roots.values())))
        placements = tuple(StaticRootPlacement(root=name, location=StaticContentPath(
            root="interpreter-installation" if name in interpreter_roots else "prepared",
            path=path.relative_to(interpreter_base if name in interpreter_roots else directory.parent).as_posix(),
        )) for name, path in sorted(roots.items()))
        target = _reference(prepared.package_root, roots)
        subject = StaticSubject(
            projection="static-subject-v1", source=inputs.source, target=inputs.target, installed_world=inputs.installed_world,
            analysis_layout=StaticAnalysisLayout(project_root=project_ref, targets=(target,), cwd=target,
                                                import_roots=(*first_party, *extra, *pythonpath, *inputs.import_roots),
                                                type_roots=(typeshed,) if typeshed else (), root_placements=placements),
            configuration=StaticConfigurationInput(content=normalized.for_roots(frozenset(config_roots)),
                                                   effective_file=materialized.effective_file,
                                                   files_in_precedence_order=tuple(config_files),
                                                   discovery_boundaries=tuple(StaticContentPath(root=name, path=".") for name in sorted(config_roots)),
                                                   external_roots=tuple(StaticContentPath(root=name, path=".") for name in sorted(roots) if name.startswith("external-"))),
            process_context=process.context,
        )
        cell = prepared.proposal.cell
        platform = "win32" if "windows" in cell.target else "darwin" if "darwin" in cell.target or "apple" in cell.target else "linux"
        spec = ProcessSpec(argv=(str(tool), "check", "--project", str(project_root), "--config-file", str(effective_file),
                                 "--python", str(prepared.interpreter), "--python-version", cell.python_minor,
                                 "--python-platform", platform, "--output-format", "gitlab", "--no-progress", "--color", "never",
                                 *retained, str(prepared.package_root)),
                           cwd=str(prepared.package_root), environment=process.variables, environment_mode="explicit",
                           timeout_seconds=package.config.ty.timeout_seconds)
        all_roots = {**roots, **tool_roots}
        all_content = StaticContentCollector().collect(all_roots)
        if not isinstance(all_content, StaticContentManifest) or all_content.for_roots(frozenset(roots)) != raw_content or all_content.for_roots(frozenset(tool_roots)) != tool_content:
            raise ValueError("request changed while capturing")
        return StaticTyRequest(subject, policy, spec, prepared.proposal_root, prepared.environment_root,
                               prepared, tuple(sorted(all_roots.items())), all_content, boundaries,
                               StaticPreparationEvidence(
                                   attempt=prepared.attempt, proposal=prepared.proposal, subject=subject,
                                   harness_requirements=package.harness_requirements,
                                   execution_policy=execution_policy(package.config),
                                   declarations=package.declarations,
                                   selected_test_group=package.selected_test_group,
                                   harness_baseline=prepared.harness_baseline,
                                   selected_candidates=prepared.selected_candidates,
                                   project_plan=ResolutionPlanEvidence.from_plan(prepared.project_plan),
                                   environment_plan=(ResolutionPlanEvidence.from_plan(prepared.environment_plan)
                                                     if prepared.environment_plan is not None else None),
                               ))


def _physical(location: StaticContentPath, roots: Mapping[str, Path]) -> Path:
    return roots[location.root] / location.path


def _reference(path: Path, roots: Mapping[str, Path]) -> StaticContentPath:
    _, name, relative = max((len(root.parts), name, path.relative_to(root).as_posix()) for name, root in roots.items() if path.is_relative_to(root))
    return StaticContentPath(root=name, path=relative)

"""Assemble a ty request from a prepared environment without hashing file trees."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import shutil

from pf.adapters.process import ProcessRunner, read_process_output
from pf.adapters.static_inputs import PreparedStaticInputs, StaticInputsAdapter
from pf.environment import PreparedEnvironment
from pf.cancellation import Cancellation
from pf.policy import guidance_policy, execution_policy
from pf.resolution import ResolutionPlanEvidence
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.schemas.evaluation import EnvironmentVariable, ProcessResult, ProcessSpec
from pf.schemas.policy import (
    SnapshotTyConfigUnavailable, TyObservationPolicy, TyToolVersion,
    TyToolVersionDistribution,
)
from pf.schemas.project import PackagePlan
from pf.schemas.static import StaticContentUnavailable, StaticSubject
from pf.static_configuration import (
    TyConfigurationResolution, TyConfigurationResolver, snapshot_ty_config_from_resolution,
)
from pf.static_paths import TySearchPaths, resolve_ty_search_paths
from pf.static_projection import static_subject
from pf.ty_version import read_ty_tool_version, ty_version_matches_metadata


def static_preparation_evidence(
    prepared: PreparedEnvironment, package: PackagePlan,
) -> StaticPreparationEvidence | StaticContentUnavailable:
    """Portable preparation from a prepared environment, before request assembly."""
    plan = prepared.environment_plan or prepared.project_plan
    interpreter = prepared.proposal.interpreter
    if interpreter is None:
        return StaticContentUnavailable(detail="installed-input-mismatch")
    subject = static_subject(
        source_snapshot_digest=prepared.proposal.snapshot_digest,
        cell=prepared.proposal.cell,
        interpreter=interpreter,
        packages=plan.packages,
        snapshot_root=prepared.proposal_root,
    )
    if isinstance(subject, StaticContentUnavailable):
        return subject
    return StaticPreparationEvidence(
        attempt=prepared.attempt, proposal=prepared.proposal, subject=subject,
        harness_requirements=package.harness_requirements,
        execution_policy=execution_policy(package.config),
        declarations=package.declarations,
        selected_test_group=package.selected_test_group,
        harness_baseline=prepared.harness_baseline,
        selected_candidates=prepared.selected_candidates,
        project_plan=ResolutionPlanEvidence.from_plan(prepared.project_plan),
        environment_plan=(
            ResolutionPlanEvidence.from_plan(prepared.environment_plan)
            if prepared.environment_plan is not None else None
        ),
        source_plan=prepared.source_plan,
    )


def may_start_ty(
    policy: TyObservationPolicy, *, tool_version_matches: bool = True,
) -> bool:
    """Recording-ty start seam: only a materialized config and matching tool start ty."""
    return (
        policy.snapshot_ty_config.kind == "materialized"
        and policy.tool_version.kind == "distribution"
        and tool_version_matches
    )


@dataclass(frozen=True)
class StaticTyRequest:
    subject: StaticSubject
    observation_policy: TyObservationPolicy
    spec: ProcessSpec
    snapshot_root: Path
    environment_root: Path
    prepared: PreparedEnvironment
    roots: tuple[tuple[str, Path], ...]
    diagnostic_prefix: str
    preparation: StaticPreparationEvidence

    def revalidate(self) -> bool:
        if self.prepared.closed or self.prepared.tested or not self.prepared.inputs_valid:
            return False
        prefix = Path(self.diagnostic_prefix)
        if not prefix.exists() or not self.snapshot_root.exists() or not self.environment_root.exists():
            self.prepared.invalidate_inputs()
            return False
        return True


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
        self._tool_version: TyToolVersion | None = None
        self._version_probe: bool | None = None

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
            key = (
                str(self._ty_executable) if self._ty_executable else None,
                package.model_dump_json(), tuple(sorted(environment.items())),
            )
            previous = prepared.static_materialization
            if previous is not None:
                current = previous.request.revalidate()
                if not prepared.inputs_valid:
                    return StaticContentUnavailable(detail="content-changed")
                if previous.key == key and current:
                    return previous.request
                directory = prepared.proposal_root.parent / "static"
                try:
                    if directory.exists():
                        shutil.rmtree(directory)
                except OSError:
                    return StaticContentUnavailable(detail="unreadable-content")
                prepared.static_materialization = None
            inputs = StaticInputsAdapter(self._runner).capture(
                prepared, package=package, source_plan=prepared.source_plan,
                cancellation=cancellation,
            )
            if not isinstance(inputs, PreparedStaticInputs):
                return inputs.failure
            try:
                request = self._assemble(
                    prepared, package, inputs, environment, cancellation,
                )
            except (OSError, ValueError, KeyError, TypeError):
                return StaticContentUnavailable(detail="invalid-layout")
            if isinstance(request, StaticContentUnavailable):
                return request
            prepared.static_materialization = StaticRequestMaterialization(key, request)
            return request

    def _tool_identity(self) -> TyToolVersion:
        if self._tool_version is None:
            self._tool_version = read_ty_tool_version()
        return self._tool_version

    def _probe_cli_version(
        self, *, directory: Path, timeout_seconds: int | None,
        cancellation: Cancellation | None,
    ) -> bool:
        if self._version_probe is not None:
            return self._version_probe
        metadata = self._tool_identity()
        if not isinstance(metadata, TyToolVersionDistribution) or self._ty_executable is None:
            self._version_probe = False
            return False
        tool = self._ty_executable.resolve(strict=True)
        version = self._runner.run(
            ProcessSpec(
                argv=(str(tool), "--version"), cwd=str(directory),
                environment_mode="explicit", timeout_seconds=timeout_seconds,
            ),
            cancellation=cancellation,
        )
        self._version_probe = (
            isinstance(version, ProcessResult)
            and version.exit_code == 0
            and not version.timed_out
            and version.stdout_complete
            and ty_version_matches_metadata(
                read_process_output(self._runner, version).stdout, metadata,
            )
        )
        return self._version_probe

    def _assemble(
        self, prepared: PreparedEnvironment, package: PackagePlan,
        inputs: PreparedStaticInputs, environment: Mapping[str, str],
        cancellation: Cancellation | None,
    ) -> StaticTyRequest | StaticContentUnavailable:
        directory = prepared.proposal_root.parent / "static"
        directory.mkdir(mode=0o700, exist_ok=True)
        snapshot_root = prepared.proposal_root
        plan = prepared.environment_plan or prepared.project_plan
        interpreter = prepared.proposal.interpreter
        if interpreter is None:
            return StaticContentUnavailable(detail="installed-input-mismatch")
        subject = static_subject(
            source_snapshot_digest=prepared.proposal.snapshot_digest,
            cell=prepared.proposal.cell,
            interpreter=interpreter,
            packages=plan.packages,
            snapshot_root=snapshot_root,
        )
        if isinstance(subject, StaticContentUnavailable):
            return subject
        platform = "windows" if os.name == "nt" else "posix"
        configuration = TyConfigurationResolver().resolve(
            project_directory=prepared.package_root, environment=environment,
            platform=platform, snapshot_root=snapshot_root,
        )
        snapshot_ty_config, materialized = snapshot_ty_config_from_resolution(
            configuration, directory=directory / "configuration",
        )
        if (
            snapshot_ty_config.kind == "materialized"
            and materialized is not None
            and isinstance(configuration, TyConfigurationResolution)
        ):
            paths = resolve_ty_search_paths(
                configuration, args=package.config.ty.args, environment=environment,
                cwd=prepared.package_root, package_name=package.name,
            )
            if (
                not isinstance(paths, TySearchPaths)
                or paths.ignored_pythonpath
                or not _search_paths_inside_snapshot(paths, snapshot_root)
            ):
                snapshot_ty_config = SnapshotTyConfigUnavailable(reason="undeclared-analysis-root")
                materialized = None
        tool_version = self._tool_identity()
        policy = guidance_policy(
            package.config, tool_version=tool_version, snapshot_ty_config=snapshot_ty_config,
        ).observation
        version_ok = True
        if isinstance(tool_version, TyToolVersionDistribution):
            version_ok = self._probe_cli_version(
                directory=directory, timeout_seconds=package.config.ty.timeout_seconds,
                cancellation=cancellation,
            )
        if not may_start_ty(policy, tool_version_matches=version_ok) or materialized is None:
            if snapshot_ty_config.kind == "unavailable":
                return StaticContentUnavailable(detail=snapshot_ty_config.reason)
            return StaticContentUnavailable(detail="invalid-layout")
        if self._ty_executable is None:
            return StaticContentUnavailable(detail="invalid-layout")
        tool = self._ty_executable.resolve(strict=True)
        cell = prepared.proposal.cell
        python_platform = (
            "win32" if "windows" in cell.target
            else "darwin" if "darwin" in cell.target or "apple" in cell.target
            else "linux"
        )
        values = {
            name: value for name, value in environment.items()
            if name in {"LANG", "LC_ALL", "LC_CTYPE"}
        }
        values["TY_CONFIG_FILE"] = str(materialized.effective_file)
        spec = ProcessSpec(
            argv=(
                str(tool), "check", "--project", str(prepared.package_root),
                "--config-file", str(materialized.effective_file),
                "--python", str(prepared.interpreter),
                "--python-version", cell.python_minor,
                "--python-platform", python_platform,
                "--output-format", "gitlab", "--no-progress", "--color", "never",
                *package.config.ty.args, str(prepared.package_root),
            ),
            cwd=str(prepared.package_root),
            environment=tuple(
                EnvironmentVariable(name=name, value=value, sensitive=False)
                for name, value in sorted(values.items())
            ),
            environment_mode="explicit",
            timeout_seconds=package.config.ty.timeout_seconds,
        )
        preparation = static_preparation_evidence(prepared, package)
        if isinstance(preparation, StaticContentUnavailable):
            return preparation
        return StaticTyRequest(
            subject, policy, spec, snapshot_root, prepared.environment_root,
            prepared,
            (("snapshot", snapshot_root), ("environment", prepared.environment_root)),
            inputs.diagnostic_prefix, preparation,
        )


def _search_paths_inside_snapshot(paths: TySearchPaths, snapshot_root: Path) -> bool:
    root = snapshot_root.resolve()
    candidates = (*paths.first_party, *paths.extra, *paths.pythonpath)
    if paths.typeshed is not None:
        candidates += (paths.typeshed,)
    for path in candidates:
        try:
            path.resolve().relative_to(root)
        except ValueError:
            return False
    return True

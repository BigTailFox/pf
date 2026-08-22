from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Literal, Protocol

from packaging.requirements import Requirement
import tomlkit
from tomlkit.items import Array

from pf.errors import ConfigurationError
from pf.policy import evaluation_policy_identity
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    GraphOutcome,
    InterpreterOutcome,
    InterpreterSuccess,
    ProgressEvent,
    PrepareFailure,
    ToolFailure,
    ToolOutcome,
)
from pf.schemas.project import Cell, PackagePlan, Proposal, VersionPin
from pf.snapshot import SourceSnapshot


class StageConsumer(Protocol):
    def consume(self, event: ProgressEvent) -> None: ...


def emit_cell_stage(events: StageConsumer | None, cell: Cell, stage: str) -> None:
    if events is None:
        return
    events.consume(
        ProgressEvent(
            package=cell.package,
            cell=cell,
            phase=stage,
            completed=0,
            total=1,
            message="running",
        )
    )


class UvOperations(Protocol):
    def create_environment(
        self,
        *,
        environment: Path,
        python_minor: str,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> ToolOutcome: ...

    def install_editable(
        self,
        *,
        interpreter: Path,
        package: Path,
        extra_surface: tuple[str, ...],
        resolution: Literal["highest", "lowest-direct"],
        timeout_seconds: int | None,
    ) -> ToolOutcome: ...

    def inspect_interpreter(
        self,
        *,
        interpreter: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> InterpreterOutcome: ...

    def inspect_environment(
        self,
        *,
        interpreter: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> GraphOutcome: ...

    def install_requirements(
        self,
        *,
        interpreter: Path,
        requirements: tuple[str, ...],
        constraints: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> ToolOutcome: ...


class PreparedEnvironment:
    """Runtime resources for one exact Proposal."""

    def __init__(
        self,
        *,
        attempt: Attempt,
        proposal: Proposal,
        proposal_root: Path,
        package_root: Path,
        environment_root: Path,
        interpreter: Path,
        temporary_directory: tempfile.TemporaryDirectory[str],
    ) -> None:
        self.attempt = attempt
        self.proposal = proposal
        self.proposal_root = proposal_root
        self.package_root = package_root
        self.environment_root = environment_root
        self.interpreter = interpreter
        self._temporary_directory = temporary_directory
        self.tested = False

    def mark_tested(self) -> None:
        self.tested = True

    def close(self) -> None:
        self._temporary_directory.cleanup()


class EnvironmentFactory:
    """Create an isolated writable source tree and environment for one Proposal."""

    def __init__(
        self, uv: UvOperations, *, events: StageConsumer | None = None
    ) -> None:
        self._uv = uv
        self._events = events

    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: Literal["highest", "lowest-direct"],
        managed_vector: tuple[VersionPin, ...] | None = None,
    ) -> PreparedEnvironment | PrepareFailure:
        attempt = self._attempt(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=resolution,
            managed_vector=managed_vector,
        )

        def failed(failure: ToolFailure) -> PrepareFailure:
            return PrepareFailure(attempt=attempt, failure=failure)

        temporary_directory = tempfile.TemporaryDirectory(prefix="pf-proposal-")
        runtime_root = Path(temporary_directory.name)
        proposal_root = runtime_root / "source"
        environment_root = runtime_root / "environment"
        try:
            snapshot.materialize(proposal_root)
            package_root = proposal_root / Path(package.pyproject_path).parent
            self._materialize_managed_vector(
                package=package,
                cell=cell,
                package_root=package_root,
                managed_vector=managed_vector,
            )
            emit_cell_stage(self._events, cell, "preparing environment")
            create = self._uv.create_environment(
                environment=environment_root,
                python_minor=cell.python_minor,
                cwd=proposal_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if isinstance(create, ToolFailure):
                temporary_directory.cleanup()
                return failed(create)
            interpreter = self._interpreter(environment_root)
            interpreter_result = self._uv.inspect_interpreter(
                interpreter=interpreter,
                cwd=package_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if not isinstance(interpreter_result, InterpreterSuccess):
                temporary_directory.cleanup()
                return failed(interpreter_result)
            if (
                interpreter_result.interpreter.implementation != "cpython"
                or not interpreter_result.interpreter.version.startswith(
                    f"{cell.python_minor}."
                )
            ):
                temporary_directory.cleanup()
                return failed(
                    ToolFailure(
                        cause="ENVIRONMENT_FAILURE",
                        stage="inspect-interpreter",
                        process=interpreter_result.process,
                    )
                )
            emit_cell_stage(self._events, cell, "installing dependencies")
            install = self._uv.install_editable(
                interpreter=interpreter,
                package=package_root,
                extra_surface=cell.extra_surface,
                resolution=resolution,
                timeout_seconds=package.config.resolve_timeout,
            )
            if isinstance(install, ToolFailure):
                temporary_directory.cleanup()
                return failed(install)
            graph = self._uv.inspect_environment(
                interpreter=interpreter,
                cwd=package_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if isinstance(graph, ToolFailure):
                temporary_directory.cleanup()
                return failed(graph)

            installed = {node.name: node.version for node in graph.nodes}
            active_ids = set(cell.active_declaration_ids)
            managed_names = tuple(
                sorted(
                    {
                        declaration.name
                        for declaration in package.declarations
                        if declaration.managed
                        and declaration.declaration_id in active_ids
                    }
                )
            )
            missing = tuple(name for name in managed_names if name not in installed)
            if missing:
                temporary_directory.cleanup()
                return failed(
                    ToolFailure(
                        cause="INTERNAL_INVARIANT",
                        stage="inspect",
                        process=graph.process,
                    )
                )
            if package.test_requirements:
                constraints = runtime_root / "target-constraints.txt"
                constraints.write_text(
                    "".join(f"{node.name}=={node.version}\n" for node in graph.nodes),
                    encoding="utf-8",
                )
                emit_cell_stage(self._events, cell, "installing harness")
                harness = self._uv.install_requirements(
                    interpreter=interpreter,
                    requirements=package.test_requirements,
                    constraints=constraints,
                    cwd=package_root,
                    timeout_seconds=package.config.resolve_timeout,
                )
                if isinstance(harness, ToolFailure):
                    temporary_directory.cleanup()
                    return failed(harness)
                harness_graph = self._uv.inspect_environment(
                    interpreter=interpreter,
                    cwd=package_root,
                    timeout_seconds=package.config.resolve_timeout,
                )
                if isinstance(harness_graph, ToolFailure):
                    temporary_directory.cleanup()
                    return failed(harness_graph)
                target_versions = {node.name: node.version for node in graph.nodes}
                after_versions = {
                    node.name: node.version for node in harness_graph.nodes
                }
                if any(
                    after_versions.get(name) != version
                    for name, version in target_versions.items()
                ):
                    temporary_directory.cleanup()
                    return failed(
                        ToolFailure(
                            cause="HARNESS_CONFLICT",
                            stage="install-harness",
                            process=harness_graph.process,
                        )
                    )
            actual_vector = tuple(
                VersionPin(name=name, version=installed[name]) for name in managed_names
            )
            policy_identity = evaluation_policy_identity(package.config)
            proposal_data = {
                "snapshot_digest": snapshot.identity.digest,
                "cell": cell.model_dump(mode="json"),
                "managed_vector": [
                    pin.model_dump(mode="json") for pin in actual_vector
                ],
                "fixed_declaration_ids": sorted(
                    declaration.declaration_id
                    for declaration in package.declarations
                    if not declaration.managed
                    and declaration.declaration_id in active_ids
                ),
                "resolved_graph": [
                    node.model_dump(mode="json") for node in graph.nodes
                ],
                "policy_identity": policy_identity,
                "interpreter": interpreter_result.interpreter.model_dump(mode="json"),
            }
            proposal_id = hashlib.sha256(
                b"pf:proposal:v1\0"
                + json.dumps(
                    proposal_data,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            proposal = Proposal(
                proposal_id=proposal_id,
                attempt_id=attempt.attempt_id,
                snapshot_digest=snapshot.identity.digest,
                cell=cell,
                managed_vector=actual_vector,
                fixed_declaration_ids=tuple(proposal_data["fixed_declaration_ids"]),
                resolved_graph=graph.nodes,
                policy_identity=policy_identity,
                interpreter=interpreter_result.interpreter,
            )
            return PreparedEnvironment(
                attempt=attempt,
                proposal=proposal,
                proposal_root=proposal_root,
                package_root=package_root,
                environment_root=environment_root,
                interpreter=interpreter,
                temporary_directory=temporary_directory,
            )
        except Exception:
            temporary_directory.cleanup()
            raise

    @staticmethod
    def _attempt(
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: Literal["highest", "lowest-direct"],
        managed_vector: tuple[VersionPin, ...] | None,
    ) -> Attempt:
        requested_resolution: Literal["highest", "lowest-direct", "exact-vector"]
        if managed_vector is not None:
            requested_resolution = "exact-vector"
        else:
            requested_resolution = resolution
        source_plan = json.dumps(
            package.source_plan.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        source_plan_identity = hashlib.sha256(
            b"pf:source-plan:v1\0" + source_plan
        ).hexdigest()
        return Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.identity.digest,
                cell=cell,
                requested_resolution=requested_resolution,
                requested_managed_vector=managed_vector,
                active_declaration_ids=cell.active_declaration_ids,
                source_plan_identity=source_plan_identity,
                evaluation_policy_identity=evaluation_policy_identity(package.config),
            )
        )

    @staticmethod
    def _materialize_managed_vector(
        *,
        package: PackagePlan,
        cell: Cell,
        package_root: Path,
        managed_vector: tuple[VersionPin, ...] | None,
    ) -> None:
        if managed_vector is None:
            return
        requested = {pin.name: pin.version for pin in managed_vector}
        if len(requested) != len(managed_vector):
            raise ConfigurationError("managed vector dependencies must be unique")
        active_ids = set(cell.active_declaration_ids)
        declarations = tuple(
            declaration
            for declaration in package.declarations
            if declaration.managed and declaration.declaration_id in active_ids
        )
        expected = {declaration.name for declaration in declarations}
        if set(requested) != expected:
            raise ConfigurationError(
                "managed vector must exactly cover active managed dependencies"
            )

        pyproject = package_root / "pyproject.toml"
        document = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
        for declaration in declarations:
            try:
                project = document["project"]
                if not isinstance(project, Mapping):
                    raise TypeError("project metadata is not a table")
                if declaration.location == "base":
                    value = project["dependencies"]
                else:
                    assert declaration.extra is not None
                    extras = project["optional-dependencies"]
                    if not isinstance(extras, Mapping):
                        raise TypeError("optional-dependencies metadata is not a table")
                    value = extras[declaration.extra]
            except (KeyError, TypeError) as error:
                raise ConfigurationError(
                    f"dependency location has drifted: {declaration.declaration_id}"
                ) from error
            if not isinstance(value, Array):
                raise ConfigurationError("dependency metadata is not a TOML array")
            values = tuple(str(item) for item in value)
            try:
                index = values.index(declaration.raw)
            except ValueError as error:
                raise ConfigurationError(
                    f"dependency declaration has drifted: {declaration.declaration_id}"
                ) from error
            requirement = Requirement(declaration.raw)
            retained = tuple(
                sorted(
                    str(specifier)
                    for specifier in requirement.specifier
                    if specifier.operator in {"<", "<=", "!="}
                )
            )
            extras = (
                f"[{','.join(sorted(requirement.extras))}]"
                if requirement.extras
                else ""
            )
            exact = ",".join((f"=={requested[declaration.name]}", *retained))
            marker = f"; {requirement.marker}" if requirement.marker else ""
            value[index] = f"{requirement.name}{extras}{exact}{marker}"
        pyproject.write_text(tomlkit.dumps(document), encoding="utf-8")

    @staticmethod
    def _interpreter(environment: Path) -> Path:
        if os.name == "nt":
            return environment / "Scripts" / "python.exe"
        return environment / "bin" / "python"

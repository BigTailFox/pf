from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Literal, Protocol

from pf.schemas.evaluation import (
    GraphOutcome,
    InterpreterOutcome,
    InterpreterSuccess,
    ToolFailure,
    ToolOutcome,
)
from pf.schemas.project import Cell, PackagePlan, Proposal, VersionPin
from pf.snapshot import SourceSnapshot


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
        requirements: tuple[str, ...] = (),
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
        proposal: Proposal,
        proposal_root: Path,
        package_root: Path,
        environment_root: Path,
        interpreter: Path,
        temporary_directory: tempfile.TemporaryDirectory[str],
    ) -> None:
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

    def __init__(self, uv: UvOperations) -> None:
        self._uv = uv

    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: Literal["highest", "lowest-direct"],
        managed_vector: tuple[VersionPin, ...] | None = None,
    ) -> PreparedEnvironment | ToolFailure:
        temporary_directory = tempfile.TemporaryDirectory(prefix="pf-proposal-")
        runtime_root = Path(temporary_directory.name)
        proposal_root = runtime_root / "source"
        environment_root = runtime_root / "environment"
        try:
            snapshot.materialize(proposal_root)
            package_root = proposal_root / Path(package.pyproject_path).parent
            create = self._uv.create_environment(
                environment=environment_root,
                python_minor=cell.python_minor,
                cwd=proposal_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if isinstance(create, ToolFailure):
                temporary_directory.cleanup()
                return create
            interpreter = self._interpreter(environment_root)
            interpreter_result = self._uv.inspect_interpreter(
                interpreter=interpreter,
                cwd=package_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if not isinstance(interpreter_result, InterpreterSuccess):
                temporary_directory.cleanup()
                return interpreter_result
            if (
                interpreter_result.interpreter.implementation != "cpython"
                or not interpreter_result.interpreter.version.startswith(
                    f"{cell.python_minor}."
                )
            ):
                temporary_directory.cleanup()
                return ToolFailure(
                    status="HARNESS_ERROR",
                    stage="inspect-interpreter",
                    process=interpreter_result.process,
                )
            requirements = tuple(
                f"{pin.name}=={pin.version}" for pin in managed_vector or ()
            )
            install = self._uv.install_editable(
                interpreter=interpreter,
                package=package_root,
                extra_surface=cell.extra_surface,
                resolution=resolution,
                timeout_seconds=package.config.resolve_timeout,
                requirements=requirements,
            )
            if isinstance(install, ToolFailure):
                temporary_directory.cleanup()
                return install
            graph = self._uv.inspect_environment(
                interpreter=interpreter,
                cwd=package_root,
                timeout_seconds=package.config.resolve_timeout,
            )
            if isinstance(graph, ToolFailure):
                temporary_directory.cleanup()
                return graph

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
                return ToolFailure(
                    status="HARNESS_ERROR",
                    stage="inspect",
                    process=graph.process,
                )
            if package.test_requirements:
                constraints = runtime_root / "target-constraints.txt"
                constraints.write_text(
                    "".join(
                        f"{node.name}=={node.version}\n" for node in graph.nodes
                    ),
                    encoding="utf-8",
                )
                harness = self._uv.install_requirements(
                    interpreter=interpreter,
                    requirements=package.test_requirements,
                    constraints=constraints,
                    cwd=package_root,
                    timeout_seconds=package.config.resolve_timeout,
                )
                if isinstance(harness, ToolFailure):
                    temporary_directory.cleanup()
                    return harness
                harness_graph = self._uv.inspect_environment(
                    interpreter=interpreter,
                    cwd=package_root,
                    timeout_seconds=package.config.resolve_timeout,
                )
                if isinstance(harness_graph, ToolFailure):
                    temporary_directory.cleanup()
                    return harness_graph
                target_versions = {node.name: node.version for node in graph.nodes}
                after_versions = {
                    node.name: node.version for node in harness_graph.nodes
                }
                if any(
                    after_versions.get(name) != version
                    for name, version in target_versions.items()
                ):
                    temporary_directory.cleanup()
                    return ToolFailure(
                        status="HARNESS_ERROR",
                        stage="install-harness",
                        process=harness_graph.process,
                    )
            actual_vector = tuple(
                VersionPin(name=name, version=installed[name]) for name in managed_names
            )
            policy_json = json.dumps(
                package.config.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            policy_identity = hashlib.sha256(
                f"pf:policy:v1\0{policy_json}".encode()
            ).hexdigest()
            proposal_data = {
                "snapshot_digest": snapshot.identity.digest,
                "cell": cell.model_dump(mode="json"),
                "managed_vector": [pin.model_dump(mode="json") for pin in actual_vector],
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
                snapshot_digest=snapshot.identity.digest,
                cell=cell,
                managed_vector=actual_vector,
                fixed_declaration_ids=tuple(proposal_data["fixed_declaration_ids"]),
                resolved_graph=graph.nodes,
                policy_identity=policy_identity,
                interpreter=interpreter_result.interpreter,
            )
            return PreparedEnvironment(
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
    def _interpreter(environment: Path) -> Path:
        if os.name == "nt":
            return environment / "Scripts" / "python.exe"
        return environment / "bin" / "python"

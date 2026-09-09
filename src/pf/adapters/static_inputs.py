"""Inspect interpreter identity and diagnostic prefix for a prepared environment."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.utils import canonicalize_name
from packaging.version import Version

from pf.adapters.process import ProcessRunner, read_process_output
from pf.schemas.evaluation import ProcessObservation, ProcessResult, ProcessSpec
from pf.schemas.project import InterpreterIdentity, PackagePlan, SourcePlan
from pf.schemas.static import StaticContentUnavailable

if TYPE_CHECKING:
    from pf.environment import PreparedEnvironment
from pf.cancellation import Cancellation


# -I -B isolates site/user hooks and skips bytecode. Inspect does not inventory distribution files.
_INSPECT = """
import importlib.metadata as metadata
import json, os, platform, sys, sysconfig
nodes = [{'name': d.metadata['Name'], 'version': d.version} for d in metadata.distributions()]
print(json.dumps({
    'interpreter': {'implementation': sys.implementation.name,
                    'version': platform.python_version(),
                    'abi': sysconfig.get_config_var('SOABI') or getattr(sys.implementation, 'cache_tag', '') or ''},
    'prefix': sys.prefix,
    'executable': os.path.realpath(sys.executable),
    'nodes': nodes,
}))
"""


@dataclass(frozen=True)
class PreparedStaticInputs:
    interpreter: InterpreterIdentity
    diagnostic_prefix: str
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
                return PreparedStaticInputsUnavailable(
                    StaticContentUnavailable(detail="installed-input-mismatch"), None,
                )
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
                argv=(prepared.interpreter.as_posix(), "-I", "-B", "-c", _INSPECT),
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
                prefix = observed["prefix"]
                if not isinstance(prefix, str) or not prefix:
                    raise ValueError("diagnostic prefix is missing")
                plan = prepared.environment_plan or prepared.project_plan
                expected = {
                    item.name: item.version
                    for item in plan.packages
                    if item.version is not None
                }
                installed = {}
                for node in observed["nodes"]:
                    name = canonicalize_name(node["name"])
                    installed[name] = str(Version(node["version"]))
                expected_names = set(expected) | {package.name}
                if set(installed) != expected_names or any(
                    installed.get(name) != version for name, version in expected.items()
                ):
                    raise ValueError("installed name/version does not match the plan")
                return PreparedStaticInputs(
                    interpreter=interpreter, diagnostic_prefix=prefix, process=process,
                )
            except (OSError, ValueError, KeyError, TypeError):
                return PreparedStaticInputsUnavailable(
                    StaticContentUnavailable(detail="installed-input-mismatch"), process,
                )

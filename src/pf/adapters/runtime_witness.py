from __future__ import annotations

import json
from pathlib import Path

from pf.adapters.process import ProcessRunner, read_process_output
from pf.schemas.evaluation import (
    ProcessSpec,
    RuntimeWitnessOutcome,
    RuntimeWitnessPlan,
    RuntimeWitnessResult,
    ToolFailure,
)


_HARNESS = r'''
import importlib
import json
import sys


def emit(status):
    sys.stdout.write(json.dumps({"status": status}, separators=(",", ":")) + "\n")


def missing_module(error, target):
    name = getattr(error, "name", None)
    return isinstance(name, str) and (name == target or target.startswith(name + "."))


def target_attribute(error, owner, name):
    return getattr(error, "obj", None) is owner and getattr(error, "name", None) == name


plan = json.loads(sys.argv[1])
module_name = plan["module"]
try:
    module = importlib.import_module(module_name)
except ModuleNotFoundError as error:
    emit("CONFIRMED_MISSING" if missing_module(error, module_name) else "NOT_APPLICABLE")
except BaseException:
    emit("NOT_APPLICABLE")
else:
    operation = plan["operation"]
    if operation == "import-module":
        emit("PRESENT")
    else:
        name = plan["symbol_or_member"]
        if operation == "import-symbol":
            try:
                owner = __import__(module_name, fromlist=[name])
            except ModuleNotFoundError as error:
                target = module_name + "." + name
                emit("CONFIRMED_MISSING" if missing_module(error, target) else "NOT_APPLICABLE")
                raise SystemExit
            except BaseException:
                emit("NOT_APPLICABLE")
                raise SystemExit
        else:
            owner = module
        if operation == "has-member" and plan["owner"] != module_name:
            emit("NOT_APPLICABLE")
        else:
            try:
                getattr(owner, name)
            except AttributeError as error:
                emit("CONFIRMED_MISSING" if target_attribute(error, owner, name) else "NOT_APPLICABLE")
            except BaseException:
                emit("NOT_APPLICABLE")
            else:
                emit("PRESENT")
'''.strip()


class RuntimeWitnessAdapter:
    """Execute the owned structured witness protocol without a shell."""

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def run(
        self,
        *,
        plan: RuntimeWitnessPlan,
        interpreter: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> RuntimeWitnessOutcome:
        payload = json.dumps(
            {
                "module": plan.module,
                "operation": plan.operation,
                "owner": plan.owner,
                "symbol_or_member": plan.symbol_or_member,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        process = self._runner.run(
            ProcessSpec(
                argv=(interpreter.as_posix(), "-I", "-c", _HARNESS, payload),
                cwd=cwd.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        if process.timed_out:
            return ToolFailure(cause="TIMEOUT", stage="witness", process=process)
        if (
            process.exit_code != 0
            or process.signal is not None
            or process.start_error is not None
            or not process.stdout_complete
            or not process.stderr_complete
        ):
            return ToolFailure(cause="TOOL_FAILURE", stage="witness", process=process)
        output = read_process_output(self._runner, process)
        if output.stderr:
            return ToolFailure(cause="TOOL_FAILURE", stage="witness", process=process)
        try:
            document = json.loads(output.stdout)
        except (json.JSONDecodeError, UnicodeError):
            return ToolFailure(cause="TOOL_FAILURE", stage="witness", process=process)
        if (
            not isinstance(document, dict)
            or set(document) != {"status"}
            or document["status"]
            not in {"PRESENT", "CONFIRMED_MISSING", "NOT_APPLICABLE"}
        ):
            return ToolFailure(cause="TOOL_FAILURE", stage="witness", process=process)
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        if output.stdout != canonical:
            return ToolFailure(cause="TOOL_FAILURE", stage="witness", process=process)
        return RuntimeWitnessResult(
            status=document["status"],
            plan=plan,
            process=process,
        )

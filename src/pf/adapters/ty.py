from __future__ import annotations

from pathlib import Path

from pf.adapters.process import ProcessRunner
from pf.schemas.evaluation import ProcessSpec, ToolFailure, TyFail, TyOutcome, TyPass


class TyAdapter:
    """Run a complete ty check and distinguish diagnostics from tool failures."""

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def check(
        self,
        *,
        interpreter: Path,
        package: Path,
        python_minor: str,
        target: str,
        args: tuple[str, ...],
        timeout_seconds: int | None,
    ) -> TyOutcome:
        result = self._runner.run(
            ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--python",
                    interpreter.as_posix(),
                    "--python-version",
                    python_minor,
                    "--python-platform",
                    self._python_platform(target),
                    "--no-progress",
                    "--color",
                    "never",
                    *args,
                    package.as_posix(),
                ),
                cwd=package.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        if result.timed_out:
            return ToolFailure(status="TIMEOUT", stage="ty", process=result)
        if result.exit_code == 0:
            return TyPass(process=result)
        if result.exit_code == 1:
            return TyFail(process=result)
        return ToolFailure(status="TOOL_ERROR", stage="ty", process=result)

    @staticmethod
    def _python_platform(target: str) -> str:
        if "-linux-" in target:
            return "linux"
        if "-apple-darwin" in target:
            return "darwin"
        if "-windows-" in target:
            return "win32"
        return "all"

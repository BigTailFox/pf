from __future__ import annotations

from pathlib import Path
import sys

from pf.adapters.process import SecretRedactor, SubprocessRunner
from pf.schemas.evaluation import ProcessSpec


def test_subprocess_runner_captures_and_redacts_external_output(tmp_path: Path) -> None:
    runner = SubprocessRunner(redactor=SecretRedactor(("top-secret",)))
    result = runner.run(
        ProcessSpec(
            argv=(
                sys.executable,
                "-c",
                "import sys; print('token=top-secret'); print('problem', file=sys.stderr)",
            ),
            cwd=tmp_path.as_posix(),
            timeout_seconds=5,
        )
    )

    assert result.exit_code == 0
    assert result.signal is None
    assert result.timed_out is False
    assert result.stdout_tail == "token=***\n"
    assert result.stderr_tail == "problem\n"
    assert "top-secret" not in result.model_dump_json()

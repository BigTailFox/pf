from __future__ import annotations

from pf.schemas.evaluation import (
    NormalExit,
    ProcessResult,
    VerifierPass,
    VerifierRejected,
)


def verifier_pass(process: ProcessResult | None = None) -> VerifierPass:
    if process is not None and process.exit_code != 0:
        raise ValueError("verifier pass fixture requires exit 0")
    return VerifierPass(terminal=NormalExit(exit_code=0))


def verifier_rejected(process: ProcessResult) -> VerifierRejected:
    if process.exit_code is None or process.exit_code == 0:
        raise ValueError("verifier rejection fixture requires a nonzero normal exit")
    return VerifierRejected(terminal=NormalExit(exit_code=process.exit_code))

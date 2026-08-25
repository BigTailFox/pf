from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import TypedDict

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import TestAdapter
from pf.environment import PreparedEnvironment
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
from pf.failure import FailurePolicy
from pf.project import host_target
from pf.resolution import (
    EnvironmentIdentity,
    NativeResolutionPlan,
    ResolutionContext,
    ResolutionPlan,
    ResolutionRunContext,
)
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    ProcessResult,
    StaticBaseline,
    StaticUnchangedEvaluation,
    TestFailEvaluation,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    Cell,
    HarnessBaseline,
    PackagePlan,
    Proposal,
    SourcePlan,
    VersionPin,
)
from pf.schemas.report import ProbeRejection


PYTHON_MINORS = ("3.10", "3.11", "3.12")


class QualificationRecord(TypedDict):
    python_minor: str
    process_exit: int | None
    test_outcome: str
    evaluation: str
    cause: str
    disposition: str
    probe_status: str
    qualified: bool


@dataclass(frozen=True)
class _PreparedEvidence:
    project_plan: ResolutionPlan
    environment_plan: ResolutionPlan
    environment_identity: EnvironmentIdentity
    harness_baseline: HarnessBaseline


class _UnusedTy:
    def check(self, **kwargs: object) -> TyCheck:
        raise AssertionError("static_result must bypass the ty adapter")


def _successful_process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.0,
    )


def _prepared_evidence(cell: Cell) -> _PreparedEvidence:
    context = ResolutionContext.from_inputs(
        run=ResolutionRunContext(
            uv_version="0.12.5",
            release_cutoff="2026-08-25T00:00:00+00:00",
        ),
        cell=cell,
        source_policy_identity="packaging-19-qualification",
        allow_prereleases=False,
    )
    native = NativeResolutionPlan.from_content(
        'lock-version = "1.0"\ncreated-by = "uv"\npackages = []\n'
    )
    project = ResolutionPlan.from_evidence(
        kind="project",
        request_digest="packaging-19-project",
        context=context,
        packages=(),
        direct_harness=(),
        native=native,
        process=_successful_process(),
    )
    environment = ResolutionPlan.from_evidence(
        kind="environment",
        request_digest="packaging-19-environment",
        context=context,
        packages=(),
        direct_harness=(),
        native=native,
        process=_successful_process(),
    )
    return _PreparedEvidence(
        project_plan=project,
        environment_plan=environment,
        environment_identity=EnvironmentIdentity.from_plans(
            project_plan=project,
            environment_plan=environment,
            graph=(),
        ),
        harness_baseline=HarnessBaseline.from_evidence(
            cell=cell,
            declaration_ids=(),
            selections=(),
        ),
    )


def _run(
    *,
    uv_binary: str,
    python_minor: str,
    root: Path,
) -> QualificationRecord:
    environment_root = root / "environment"
    project_root = root / "project"
    project_root.mkdir()
    (project_root / "test_packaging_19.py").write_text(
        "from packaging.utils import InvalidSdistFilename\n"
        "def test_import_contract():\n"
        "    assert InvalidSdistFilename is not None\n",
        encoding="utf-8",
    )
    subprocess.run(
        (uv_binary, "--no-config", "venv", "--python", python_minor, environment_root.as_posix()),
        check=True,
        capture_output=True,
        text=True,
    )
    interpreter = environment_root / "bin" / "python"
    subprocess.run(
        (
            uv_binary,
            "--no-config",
            "pip",
            "install",
            "--python",
            interpreter.as_posix(),
            "--default-index",
            "https://pypi.org/simple",
            "pytest==6.2.5",
            "packaging==19.2",
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    cell = Cell(
        package="pf",
        target="x86_64-unknown-linux-gnu",
        python_minor=python_minor,
        extra_surface=(),
    )
    vector = (VersionPin(name="packaging", version="19.2"),)
    attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="packaging-19-snapshot",
            cell=cell,
            requested_resolution="exact-vector",
            requested_managed_vector=vector,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="packaging-19-sources",
            evaluation_policy_identity="pytest-failure-witness-v1",
        )
    )
    proposal = Proposal(
        proposal_id=f"packaging-19-{python_minor}",
        attempt_id=attempt.attempt_id,
        snapshot_digest="packaging-19-snapshot",
        cell=cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="pytest-failure-witness-v1",
    )
    evidence = _prepared_evidence(cell)
    owner = tempfile.TemporaryDirectory(prefix="pf-packaging-19-owner-")
    prepared = PreparedEnvironment(
        attempt=attempt,
        proposal=proposal,
        proposal_root=project_root,
        package_root=project_root,
        environment_root=environment_root,
        interpreter=interpreter,
        project_plan=evidence.project_plan,
        environment_plan=evidence.environment_plan,
        environment_identity=evidence.environment_identity,
        harness_baseline=evidence.harness_baseline,
        temporary_directory=owner,
    )
    process = _successful_process()
    ty = TyCheck(process=process, diagnostics=())
    baseline = StaticBaseline(
        proposal=proposal,
        ty=ty,
        digest=ty_diagnostic_digest(()),
    )
    static = StaticUnchangedEvaluation(
        proposal=proposal,
        ty=ty,
        baseline_digest=baseline.digest,
    )
    package = PackagePlan(
        name="pf",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(
            test_command=(interpreter.as_posix(), "-m", "pytest", "-q"),
            test_failure_exit_codes=(1,),
            test_timeout=30,
        ),
        declarations=(),
        cells=(cell,),
        source_plan=SourcePlan(identities=()),
        test_group_present=True,
    )
    try:
        evaluation = RuntimeEvaluator(
            static=StaticEvaluator(_UnusedTy()),
            tests=TestAdapter(SubprocessRunner()),
        ).evaluate(
            prepared,
            package=package,
            baseline=baseline,
            static_result=static,
        )
    finally:
        prepared.close()
    if not isinstance(evaluation, TestFailEvaluation):
        return {
            "python_minor": python_minor,
            "process_exit": None,
            "test_outcome": "NOT_TEST_FAIL",
            "evaluation": evaluation.status,
            "cause": "TOOL_FAILURE",
            "disposition": "INDETERMINATE",
            "probe_status": "INDETERMINATE",
            "qualified": False,
        }
    failure = FailurePolicy().classify_evaluation(
        AttemptFailureScope(attempt=attempt),
        evaluation,
    )
    assert failure is not None
    if failure.disposition != "REJECTED":
        return {
            "python_minor": python_minor,
            "process_exit": evaluation.test.process.exit_code,
            "test_outcome": evaluation.test.status,
            "evaluation": evaluation.status,
            "cause": failure.cause,
            "disposition": failure.disposition,
            "probe_status": "INDETERMINATE",
            "qualified": False,
        }
    probe = ProbeRejection(
        attempt=attempt,
        proposal_id=proposal.proposal_id,
        failure_id=failure.failure_id,
        cause=failure.cause,
        evaluation=evaluation,
    )
    return {
        "python_minor": python_minor,
        "process_exit": evaluation.test.process.exit_code,
        "test_outcome": evaluation.test.status,
        "evaluation": evaluation.status,
        "cause": failure.cause,
        "disposition": failure.disposition,
        "probe_status": probe.status,
        "qualified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-minor", action="append")
    parser.add_argument("--uv-bin", default=shutil.which("uv"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.uv_bin is None:
        parser.error("uv is required")
    qualified_host = host_target()
    if qualified_host != "x86_64-unknown-linux-gnu":
        parser.error(
            "packaging 19.2 qualification is certified only on "
            "x86_64-unknown-linux-gnu"
        )
    records: list[QualificationRecord] = []
    with tempfile.TemporaryDirectory(prefix="pf-packaging-19-") as matrix_root:
        for minor in tuple(args.python_minor or PYTHON_MINORS):
            root = Path(matrix_root) / minor
            root.mkdir()
            records.append(_run(uv_binary=args.uv_bin, python_minor=minor, root=root))
    manifest = {
        "schema": "pf-packaging-19-pytest-witness-v1",
        "packaging_version": "19.2",
        "pytest_version": "6.2.5",
        "host_scope": qualified_host,
        "all_profiles_qualified": all(item["qualified"] for item in records),
        "profiles": records,
    }
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.write_text(payload, encoding="utf-8")
    return 0 if manifest["all_profiles_qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

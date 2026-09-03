from __future__ import annotations

import argparse
import json
from pathlib import Path

from pf.policy import evaluation_policy_identity
from pf.report import PackageReportBuilder
from pf.resolution import environment_identity_digest
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    NormalExit,
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticUnchangedEvaluation,
    TyCheck,
    VerifierPass,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    Cell,
    InterpreterIdentity,
    PackagePlan,
    Proposal,
    SourcePlan,
    SourceSnapshotIdentity,
    source_snapshot_digest,
)
from pf.schemas.report import (
    CellSuccess,
    CoordinateSuccess,
    PackageFloorReportV1Wire,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "schemas" / "package-floor-v1.schema.json"
COMPLETE_PATH = ROOT / "docs" / "examples" / "package-floor-v1-minimal-complete.json"
INCOMPLETE_PATH = (
    ROOT / "docs" / "examples" / "package-floor-v1-minimal-incomplete.json"
)


def _package(cell: Cell) -> PackagePlan:
    return PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(),
        cells=(cell,),
        source_routes=(),
    )


def _snapshot() -> SourceSnapshotIdentity:
    return SourceSnapshotIdentity(
        digest=source_snapshot_digest((), ()),
        entries=(),
        pyproject_identities=(),
    )


def _complete_report() -> PackageFloorReportV1Wire:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.12",
        extra_surface=(),
    )
    package = _package(cell)
    source_plan = SourcePlan.for_package(package, "SEARCH")
    snapshot = _snapshot()
    policy = evaluation_policy_identity(package.config)
    attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot.digest,
            cell=cell,
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=(),
            source_plan_identity=source_plan.identity,
            evaluation_policy_identity=policy,
            resolution_context_digest="context",
            harness_policy_identity="original-harness-v1",
        )
    )
    project_digest = "project-plan"
    environment_digest = "environment-plan"
    proposal = Proposal(
        proposal_id=environment_identity_digest(
            project_plan_digest=project_digest,
            environment_plan_digest=environment_digest,
            graph=(),
        ),
        attempt_id=attempt.attempt_id,
        snapshot_digest=snapshot.digest,
        cell=cell,
        managed_vector=(),
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity=policy,
        project_plan_digest=project_digest,
        environment_plan_digest=environment_digest,
        interpreter=InterpreterIdentity(
            implementation="cpython",
            version="3.12.11",
            abi="cpython-312-x86_64-linux-gnu",
        ),
    )
    process = ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.0,
        stdout="",
        stderr="",
    )
    digest = ty_diagnostic_digest(())
    static = StaticUnchangedEvaluation(
        proposal=proposal,
        ty=TyCheck(process=process, diagnostics=()),
        baseline_digest=digest,
    )
    evaluation = PassEvaluation(
        proposal=proposal,
        static=static,
        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
    )
    report = PackageReportBuilder().build(
        package=package,
        source_plan=source_plan,
        source_snapshot=snapshot,
        cell_results=(
            CellSuccess(
                cell=cell,
                baseline_attempt=attempt,
                static_baseline=StaticBaseline(
                    proposal=proposal,
                    ty=static.ty,
                    digest=digest,
                ),
                baseline=evaluation,
                candidate_snapshots=(),
                search=CoordinateSuccess(
                    vector=(),
                    observations=(),
                    boundaries=(),
                    regions=(),
                    sweeps=0,
                ),
                final_vector=(),
                final_evaluation=evaluation,
            ),
        ),
    )
    return report._wire


def _incomplete_report() -> PackageFloorReportV1Wire:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.12",
        extra_surface=(),
    )
    package = _package(cell)
    report = PackageReportBuilder().build(
        package=package,
        source_plan=SourcePlan.for_package(package, "SEARCH"),
        source_snapshot=_snapshot(),
        cell_results=(),
    )
    return report._wire


def generated_files() -> dict[Path, str]:
    schema = PackageFloorReportV1Wire.model_json_schema(
        mode="serialization",
        ref_template="#/$defs/{model}",
    )
    _require_const_types(schema)
    _require_serialized_defaults(schema)
    _remove_null_types(schema)
    return {
        SCHEMA_PATH: json.dumps(
            schema,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        COMPLETE_PATH: json.dumps(
            _complete_report().model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        INCOMPLETE_PATH: json.dumps(
            _incomplete_report().model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }


def _require_const_types(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _require_const_types(item)
        return
    if not isinstance(value, dict):
        return
    if "const" in value:
        constant = value["const"]
        if isinstance(constant, bool):
            value["type"] = "boolean"
        elif isinstance(constant, str):
            value["type"] = "string"
        elif isinstance(constant, int):
            value["type"] = "integer"
        elif isinstance(constant, float):
            value["type"] = "number"
        elif isinstance(constant, list):
            value["type"] = "array"
        elif isinstance(constant, dict):
            value["type"] = "object"
        elif constant is None:
            value["type"] = "null"
    for item in value.values():
        _require_const_types(item)


def _require_serialized_defaults(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _require_serialized_defaults(item)
        return
    if not isinstance(value, dict):
        return
    properties = value.get("properties")
    if isinstance(properties, dict):
        required = set(value.get("required", []))
        for name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue
            if "default" in field_schema:
                if field_schema["default"] is not None:
                    required.add(name)
                del field_schema["default"]
        if required:
            value["required"] = sorted(required)
    for item in value.values():
        _require_serialized_defaults(item)


def _remove_null_types(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _remove_null_types(item)
        return
    if not isinstance(value, dict):
        return
    for keyword in ("anyOf", "oneOf"):
        branches = value.get(keyword)
        if isinstance(branches, list):
            value[keyword] = [
                branch
                for branch in branches
                if not (isinstance(branch, dict) and branch.get("type") == "null")
            ]
    type_value = value.get("type")
    if isinstance(type_value, list):
        value["type"] = [item for item in type_value if item != "null"]
    for item in value.values():
        _remove_null_types(item)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, expected in generated_files().items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                stale.append(path.relative_to(ROOT).as_posix())
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
    if stale:
        parser.error("generated report artifacts are stale: " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

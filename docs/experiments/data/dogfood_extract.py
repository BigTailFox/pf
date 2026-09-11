"""Extract journal evidence and a search-summary from a dogfood run."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
import sys

ROOT = Path("/home/llh/pf")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def extract_journal(target: Path, package: str) -> dict[str, object]:
    sys.path.insert(0, str(ROOT / "src"))
    from pf.runlog import RunLogStore

    store = RunLogStore(root=target)
    run_id = store.latest_journal_id(package)
    if run_id is None:
        raise SystemExit(f"no latest journal for {package} under {target}")
    journal = store.read_journal(run_id)
    if journal is None:
        raise SystemExit(f"cannot read journal {run_id}")
    journal_path = target / ".pf" / "logs" / run_id / "journal.json"
    log_dir = target / ".pf" / "logs" / run_id
    process_logs = sorted(log_dir.glob("process-*.log"))
    failures = []
    for entry in journal.entries:
        failures.append(
            {
                "failure_id": entry.failure.failure_id,
                "disposition": entry.failure.disposition,
                "cause": entry.failure.cause,
                "stage": entry.failure.stage,
            }
        )
    policies = [
        {
            "package": item.package,
            "execution_policy_identity": item.execution_policy_identity,
        }
        for item in journal.package_policies
    ]
    return {
        "run_id": run_id,
        "command": journal.command,
        "journal_sha256": _sha256(journal_path) if journal_path.is_file() else None,
        "journal_schema": "verification-journal-v3",
        "journal_entries": len(journal.entries),
        "source_snapshot_digest": journal.source_snapshot_digest,
        "package_policies": policies,
        "process_log_count": len(process_logs),
        "failure_entries": failures,
    }


def extract_search_summary(
    report_path: Path,
    *,
    target: Path,
    package: str,
    phase: str,
) -> dict[str, object]:
    sys.path.insert(0, str(ROOT / "src"))
    from pf.report import ReportStore
    from pf.schemas.report import CellSuccess

    report = ReportStore().read(report_path)
    journal = extract_journal(target, package)
    cells = []
    observation_kinds: Counter[str] = Counter()
    failure_causes: Counter[str] = Counter()
    candidate_snapshots = 0
    failure_records = 0
    for result in report.cell_results:
        if not isinstance(result, CellSuccess):
            cells.append(
                {
                    "python_minor": result.cell.python_minor,
                    "extra_surface": list(result.cell.extra_surface),
                    "status": getattr(result, "status", type(result).__name__),
                }
            )
            continue
        for observation in result.search.observations:
            observation_kinds[type(observation.evidence).__name__] += 1
        for failure in result.failure_records:
            failure_causes[str(failure.cause)] += 1
            failure_records += 1
        candidate_snapshots += len(result.candidate_snapshots)
        cells.append(
            {
                "python_minor": result.cell.python_minor,
                "extra_surface": list(result.cell.extra_surface),
                "sweeps": result.search.sweeps,
                "baseline": {
                    pin.name: pin.version for pin in result.baseline.proposal.managed_vector
                },
                "final_vector": {
                    pin.name: pin.version for pin in result.final_vector
                },
                "boundaries": [
                    {
                        "dependency": boundary.dependency,
                        "floor": boundary.floor,
                        "predecessor": boundary.predecessor,
                        "predecessor_failure_id": boundary.predecessor_failure_id,
                    }
                    for boundary in result.search.boundaries
                ],
                "observation_count": len(result.search.observations),
                "failure_record_count": len(result.failure_records),
            }
        )
    declaration_by_id = {
        item.declaration_id: item for item in report.requirement_declarations
    }
    projections = []
    for item in report.projection_evidence:
        declaration = declaration_by_id[item.declaration_id]
        floors = []
        versions: list[str] = []
        for floor in item.floors:
            floors.append(
                {
                    "python_minor": floor.cell.python_minor,
                    "extra_surface": list(floor.cell.extra_surface),
                    "version": floor.version,
                }
            )
            if floor.version not in versions:
                versions.append(floor.version)
        projections.append(
            {
                "name": declaration.name,
                "raw": declaration.raw,
                "specifier": declaration.specifier,
                "representable": item.representable,
                "projected_requirements": list(item.projected_requirements),
                "floor_versions": versions,
                "floors": floors,
            }
        )
    search_policy = report.search_policy.model_dump(mode="json")
    return {
        "provenance": {
            "source": str(report_path.relative_to(ROOT)),
            "run_id": journal["run_id"],
            "sha256": _sha256(report_path),
            "bytes": report_path.stat().st_size,
            "reader_validation": {
                "status": report.result.status,
                "cells": len(report.cell_results),
            },
            "pf_head": _git_head(),
            "pf_version": report.generator.version,
            "phase": phase,
        },
        "identity": {
            "generator": report.generator.model_dump(mode="json"),
            "package": report.package.model_dump(mode="json"),
            "policy_identity": report.policy_identity,
            "guidance_policy_identity": report.guidance_policy_identity,
            "search_derivation_identity": report.search_derivation_identity,
            "report_generation_id": report.report_generation_id,
            "source_snapshot_digest": report.source_snapshot.digest,
            "verifier_outcome_policy": report.verifier_outcome_policy,
        },
        "result": {"status": report.result.status},
        "search_policy": search_policy,
        "declarations": [
            {
                "name": item.name,
                "raw": item.raw,
                "specifier": item.specifier,
                "kind": item.kind,
                "managed": item.managed,
                "location": item.location,
                "extra": item.extra,
            }
            for item in report.requirement_declarations
        ],
        "projections": projections,
        "cells": cells,
        "counts": {
            "cell_results": len(report.cell_results),
            "search_observations": sum(observation_kinds.values()),
            "observation_evidence": dict(observation_kinds),
            "failure_records_in_cells": failure_records,
            "failure_cause_counts": dict(failure_causes),
            "candidate_snapshots": candidate_snapshots,
            "search_process_logs": journal["process_log_count"],
            "journal_entries": journal["journal_entries"],
            "journal_sha256": journal["journal_sha256"],
            "source_snapshot_digest": report.source_snapshot.digest,
            "probe_pass": observation_kinds.get("ProbePass", 0),
            "probe_rejection": observation_kinds.get("ProbeRejection", 0),
        },
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "journal":
        _, _, target, package, output = sys.argv
        write_json(Path(output), extract_journal(Path(target), package))
    elif sys.argv[1] == "search":
        _, _, report, target, package, phase, output = sys.argv
        write_json(
            Path(output),
            extract_search_summary(
                Path(report),
                target=Path(target),
                package=package,
                phase=phase,
            ),
        )
    else:
        raise SystemExit("usage: dogfood_extract.py journal|search ...")

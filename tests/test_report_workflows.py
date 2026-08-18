from __future__ import annotations

from pathlib import Path

from pf.project import ProjectLoader
from pf.report import ReportStore
from pf.schemas.config import MergeRequest, ReportRequest
from pf.schemas.project import SourceSnapshotIdentity
from pf.schemas.report import (
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
)
from pf.workflow import ExplainCommandWorkflow, MergeCommandWorkflow


def report() -> PackageFloorReportV1:
    return PackageFloorReportV1(
        generator=GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1"),
        package=PackageIdentity(name="demo", pyproject_path="pyproject.toml"),
        source_snapshot=SourceSnapshotIdentity(digest="snapshot", entries=()),
        policy_identity="policy",
        requirement_declarations=(),
        candidate_snapshots=(),
        target_cells=(),
        cell_results=(),
        projection_evidence=(),
        result=IncompleteReportResult(reasons=("MISSING_CELL",)),
    )


def test_explain_reads_existing_report_and_merge_writes_requested_output(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    store = ReportStore()
    source = tmp_path / "package-floor.json"
    store.write(source, report())
    before = source.read_bytes()

    explained = ExplainCommandWorkflow(
        projects=ProjectLoader(),
        reports=store,
    ).run(ReportRequest(root=tmp_path.as_posix(), package=None))
    output = tmp_path / "merged.json"
    merged = MergeCommandWorkflow(reports=store).run(
        MergeRequest(reports=(source.as_posix(),), output=output.as_posix())
    )

    assert explained == (report(),)
    assert source.read_bytes() == before
    assert store.read(output) == merged

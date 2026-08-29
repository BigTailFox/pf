from __future__ import annotations

import json
from pathlib import Path

import pytest

from pf.errors import ConfigurationError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.report import PackageReportBuilder, ReportStore, ValidatedReport
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    CellFailureScope,
    FailureCause,
    FailureDetail,
    ProcessResult,
)
from pf.schemas.project import (
    Cell,
    PackagePlan,
    SnapshotEntry,
    SourceSnapshotIdentity,
    source_snapshot_digest,
)
from pf.schemas.report import CellIndeterminate


def package_for(cells: tuple[Cell, ...]) -> PackagePlan:
    return PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(test_timeout=1),
        declarations=(),
        cells=cells,
        source_routes=(),
    )


def snapshot_for(
    entries: tuple[SnapshotEntry, ...] = (),
) -> SourceSnapshotIdentity:
    return SourceSnapshotIdentity(
        digest=source_snapshot_digest(entries, ()),
        entries=entries,
        pyproject_identities=(),
    )


def cell_failure(
    cell: Cell,
    cause: FailureCause,
    *,
    snapshot: SourceSnapshotIdentity,
    policy_identity: str,
    stage: str = "evaluation",
    process: ProcessResult | None = None,
) -> CellIndeterminate:
    failure = FailurePolicy().classify(
        scope=CellFailureScope(
            package=cell.package,
            cell=cell,
            source_snapshot_digest=snapshot.digest,
            evaluation_policy_identity=policy_identity,
        ),
        cause=cause,
        stage=stage,
        process=process,
        detail=FailureDetail(code="test-failure", message="test failure"),
    )
    return CellIndeterminate(
        cell=cell,
        phase=stage,
        failure_id=failure.failure_id,
        failure_records=(failure,),
    )


def report_for(
    cells: tuple[Cell, ...] = (),
    results: tuple[CellIndeterminate, ...] = (),
    *,
    snapshot: SourceSnapshotIdentity | None = None,
) -> ValidatedReport:
    package = package_for(cells)
    return PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot or snapshot_for(),
        cell_results=results,
    )


class TestReportStore:
    def test_write_round_trips_canonical_schema_1_json(
        self,
        tmp_path: Path,
    ) -> None:
        store = ReportStore()
        path = tmp_path / "package-floor.json"
        report = report_for()

        store.write(path, report)

        content = path.read_text(encoding="utf-8")
        assert content.endswith("\n") and not content.endswith("\n\n")
        assert content.startswith('{"cell_results":')
        assert '"schema_version":1' in content
        assert ":null" not in content
        loaded = store.read(path)
        assert loaded == report
        rewritten = tmp_path / "rewritten.json"
        store.write(rewritten, loaded)
        assert rewritten.read_bytes() == path.read_bytes()

    @pytest.mark.parametrize(
        ("content", "message"),
        (
            (None, "cannot read report"),
            ("not-json", "invalid report JSON"),
            ('{"schema_version":2}', "unsupported report schema_version"),
            ('{"schema_version":3}', "unsupported report schema_version"),
            ("[]", "unsupported report schema_version"),
        ),
    )
    def test_read_rejects_unusable_or_non_schema_1_report(
        self,
        tmp_path: Path,
        content: str | None,
        message: str,
    ) -> None:
        path = tmp_path / "package-floor.json"
        if content is not None:
            path.write_text(content, encoding="utf-8")

        with pytest.raises(ConfigurationError, match=message):
            ReportStore().read(path)

    def test_read_rejects_oversized_report_before_loading_it(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "package-floor.json"
        with path.open("wb") as stream:
            stream.seek(64 * 1024 * 1024)
            stream.write(b"x")

        def unexpected_read(_path: Path) -> bytes:
            pytest.fail("oversized report was loaded into memory")

        monkeypatch.setattr(Path, "read_bytes", unexpected_read)

        with pytest.raises(ConfigurationError, match="64 MiB read limit"):
            ReportStore().read(path)

    def test_read_rejects_invalid_utf8_as_invalid_report_json(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "package-floor.json"
        path.write_bytes(b'{"schema_version":1,"invalid":"\xff"}')

        with pytest.raises(ConfigurationError, match="invalid report JSON"):
            ReportStore().read(path)

    def test_read_rejects_non_utf8_json_even_when_json_can_detect_it(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "package-floor.json"
        store = ReportStore()
        store.write(path, report_for())
        path.write_bytes(path.read_text(encoding="utf-8").encode("utf-16"))

        with pytest.raises(ConfigurationError, match="invalid report JSON"):
            store.read(path)

    def test_report_store_omits_captured_process_output(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        snapshot = snapshot_for()
        policy = evaluation_policy_identity(package_for((cell,)).config)
        result = cell_failure(
            cell,
            "TOOL_FAILURE",
            snapshot=snapshot,
            policy_identity=policy,
            stage="test",
            process=ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=11.12,
                stdout="484 passed in 11.12s",
                stderr="secret-noise",
                stdout_complete=False,
            ),
        )
        report = report_for((cell,), (result,), snapshot=snapshot)
        path = tmp_path / "package-floor.json"

        ReportStore().write(path, report)

        content = path.read_text(encoding="utf-8")
        loaded = ReportStore().read(path)
        process = loaded.failure_records[0].process
        assert '"stdout":' not in content
        assert '"stderr":' not in content
        assert "484 passed" not in content
        assert "secret-noise" not in content
        assert process is not None
        assert process.exit_code == 0
        assert process.stdout_complete is False
        assert process.stdout == ""
        assert process.stderr == ""

    def test_update_path_rejects_bad_existing_without_overwrite(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "package-floor.json"
        original = b'{"schema_version":2}\n'
        path.write_bytes(original)

        with pytest.raises(
            ConfigurationError,
            match="unsupported report schema_version",
        ):
            ReportStore().update_path(path, report_for())

        assert path.read_bytes() == original

    def test_reader_rejects_cross_cell_failure_reference(
        self,
        tmp_path: Path,
    ) -> None:
        cells = tuple(
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor=minor,
                extra_surface=(),
            )
            for minor in ("3.10", "3.11")
        )
        snapshot = snapshot_for()
        policy = evaluation_policy_identity(package_for(cells).config)
        results = tuple(
            cell_failure(
                cell,
                "TIMEOUT",
                snapshot=snapshot,
                policy_identity=policy,
            )
            for cell in cells
        )
        path = tmp_path / "package-floor.json"
        ReportStore().write(
            path,
            report_for(cells, results, snapshot=snapshot),
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        foreign_ref = document["cell_results"][1]["failure_ref"]
        document["cell_results"][0]["failure_ref"] = foreign_ref
        document["cell_results"][0]["failure_refs"] = [foreign_ref]
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(
            ConfigurationError,
            match="CellResult evidence mismatch",
        ):
            ReportStore().read(path)


class TestReportMergeAndUpdate:
    def test_merge_is_deterministic_and_rejects_conflicting_cells(self) -> None:
        cells = tuple(
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor=minor,
                extra_surface=(),
            )
            for minor in ("3.10", "3.11")
        )
        snapshot = snapshot_for()
        policy = evaluation_policy_identity(package_for(cells).config)

        def partial(cell: Cell, cause: FailureCause) -> ValidatedReport:
            result = cell_failure(
                cell,
                cause,
                snapshot=snapshot,
                policy_identity=policy,
            )
            return report_for(cells, (result,), snapshot=snapshot)

        first = partial(cells[1], "TIMEOUT")
        second = partial(cells[0], "TOOL_FAILURE")
        merged = ReportStore().merge((first, second))

        assert [result.cell.python_minor for result in merged.cell_results] == [
            "3.10",
            "3.11",
        ]
        assert merged.result.status == "incomplete"
        assert merged.result.reasons == ("INDETERMINATE",)

        with pytest.raises(ConfigurationError, match="conflicting result for cell"):
            ReportStore().merge((first, partial(cells[1], "SOURCE_FAILURE")))

    def test_update_replaces_local_cells_and_retains_other_hosts(self) -> None:
        cells = (
            Cell(
                package="demo",
                target="aarch64-apple-darwin",
                python_minor="3.10",
                extra_surface=(),
            ),
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
        )
        snapshot = snapshot_for()
        policy = evaluation_policy_identity(package_for(cells).config)
        existing_results = tuple(
            cell_failure(
                cell,
                "TIMEOUT",
                snapshot=snapshot,
                policy_identity=policy,
                stage="old",
            )
            for cell in cells
        )
        replacement_result = cell_failure(
            cells[1],
            "TOOL_FAILURE",
            snapshot=snapshot,
            policy_identity=policy,
            stage="new",
        )
        existing = report_for(cells, existing_results, snapshot=snapshot)
        replacement = report_for(
            cells,
            (replacement_result,),
            snapshot=snapshot,
        )

        updated = ReportStore().update(existing, replacement)

        indeterminate = tuple(
            result
            for result in updated.cell_results
            if isinstance(result, CellIndeterminate)
        )
        assert len(indeterminate) == len(updated.cell_results)
        assert [(result.cell.target, result.phase) for result in indeterminate] == [
            ("aarch64-apple-darwin", "old"),
            ("x86_64-unknown-linux-gnu", "new"),
        ]
        assert (
            ReportStore().update(
                updated,
                report_for(cells, (), snapshot=snapshot),
            )
            is updated
        )

    def test_merge_and_update_reject_generation_drift(self) -> None:
        original = report_for()
        changed = report_for(
            snapshot=snapshot_for(
                (
                    SnapshotEntry(
                        path="README.md",
                        kind="file",
                        mode=0o644,
                        content_digest="a" * 64,
                    ),
                )
            )
        )

        with pytest.raises(ConfigurationError, match="generation identity"):
            ReportStore().merge((original, changed))
        with pytest.raises(ConfigurationError, match="generation identity"):
            ReportStore().update(original, changed)

from __future__ import annotations

import copy
import json
from io import StringIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from rich.console import Console

from evaluation_fixtures import evaluation_assembly
from pf.errors import ConfigurationError
from pf.authorization import ApplyAuthorizer
from pf.editor import ProjectEditor
from pf.errors import ApplyAuthorizationError
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.schemas.project import SourcePlan
from pf.schemas.project import SeriesInventory, candidate_snapshot_digest
from pf.schemas.report import CellSuccess
from pf.snapshot import SnapshotBuilder
from pf.terminal import TerminalPresenter, PF_THEME
from pf.workflow import ExplainCommandResult


@pytest.fixture
def search_case(tmp_path: Path, request: pytest.FixtureRequest):
    (tmp_path / "pyproject.toml").write_text("""[project]
name = "demo"
version = "0.1"
dependencies = ["demo-dep>=2"]
[project.optional-dependencies]
extra = ["demo-dep>=2"]
[dependency-groups]
test = []
[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""")
    if getattr(request, "param", None) == "unbounded":
        path = tmp_path / "pyproject.toml"
        path.write_text(path.read_text().replace("demo-dep>=2", "demo-dep<4"))
    elif getattr(request, "param", None) is not None:
        path = tmp_path / "pyproject.toml"
        path.write_text(path.read_text() + f'search-space = "{request.param}"\n')
    project = ProjectLoader().load(root=tmp_path)
    snapshot = SnapshotBuilder.without_processes().build(
        tmp_path, owned_pyproject_paths=project.owned_pyproject_paths
    )
    source = SourcePlan.for_package(project.target, "SEARCH")

    def report_for(cell, versions=("1", "2", "3")):
        assembly = evaluation_assembly(candidate_versions=versions)
        result = assembly.coordinator.search(
            package=project.target, cell=cell, snapshot=snapshot, source_plan=source
        )
        assert isinstance(result, CellSuccess)
        return PackageReportBuilder().build(
            package=project.target,
            source_plan=source,
            source_snapshot=snapshot.identity,
            cell_results=(result,),
        )

    try:
        yield project, snapshot, source, report_for
    finally:
        snapshot.close()


def document_for(report, path: Path) -> dict:
    ReportStore().write(path, report)
    return json.loads(path.read_text())


class TestSearchSpaceReport:
    @pytest.mark.parametrize("search_case", ["unbounded"], indirect=True)
    def test_unbounded_default_is_derived_from_the_reports_real_baseline(
        self, search_case, tmp_path: Path
    ) -> None:
        project, _, _, report_for = search_case
        document_for(report_for(project.target.cells[0]), tmp_path / "report.json")
        report = ReportStore().read(tmp_path / "report.json")
        selection = report.search_spaces()[0].selection
        assert selection is not None
        assert selection.reason == "default-unbounded"
        assert selection.expression == "majors[baseline-2:]"
        assert selection.anchors == (("baseline", "3"),)
        assert selection.selected_keys == ((0, 1), (0, 2), (0, 3))

    def test_opaque_policy_cannot_be_authorized_by_rehashing_its_snapshot(
        self, search_case, tmp_path: Path
    ) -> None:
        project, _, _, report_for = search_case
        report = report_for(project.target.cells[0])
        document = document_for(report, tmp_path / "report.json")
        result = report.cell_results[0]
        assert isinstance(result, CellSuccess)
        snapshot = result.candidate_snapshots[0]
        forged_policy = "0" * 64
        forged_id = candidate_snapshot_digest(
            dependency=snapshot.dependency,
            cell=snapshot.cell,
            policy_identity=forged_policy,
            source_plan_identity=snapshot.source_plan_identity,
            source=snapshot.source,
            candidates=snapshot.candidates,
            series_representatives=snapshot.series_representatives,
            selection=snapshot.selection,
            series_inventory=snapshot.series_inventory,
        )
        document["inputs"]["candidate_snapshots"][0]["policy_identity"] = forged_policy
        (tmp_path / "report.json").write_text(
            json.dumps(document).replace(snapshot.digest, forged_id)
        )
        with pytest.raises(
            ConfigurationError, match="policy/selection/identity"
        ) as caught:
            ReportStore().read(tmp_path / "report.json")
        assert "candidate policy identity mismatch" in str(caught.value.__cause__)

    def test_valid_inventory_hash_with_wrong_anchor_scope_is_rejected(
        self, search_case, tmp_path: Path
    ) -> None:
        project, _, _, report_for = search_case
        document = document_for(
            report_for(project.target.cells[0]), tmp_path / "report.json"
        )
        record = document["inputs"]["series_inventories"][0]
        wrong = SeriesInventory(
            dependency=record["dependency"],
            source=record["source"],
            family="majors",
            series_keys=((1, 1), (1, 2), (1, 3)),
        )
        document["inputs"]["series_inventories"] = [
            {
                "series_inventory_id": wrong.inventory_id,
                **wrong.model_dump(mode="json", exclude_none=True),
            }
        ]
        document["inputs"]["candidate_snapshots"][0]["series_inventory_ref"] = (
            wrong.inventory_id
        )
        (tmp_path / "report.json").write_text(json.dumps(document))
        with pytest.raises(ConfigurationError, match="policy/selection/identity"):
            ReportStore().read(tmp_path / "report.json")

    def test_same_policy_for_distinct_names_is_one_canonical_binding(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text("""[project]
name = "demo"
version = "0.1"
dependencies = ["z-dep>=2", "a-dep", "fixed~=2.0"]
[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
""")
        project = ProjectLoader().load(root=tmp_path)
        snapshot = SnapshotBuilder.without_processes().build(
            tmp_path, owned_pyproject_paths=project.owned_pyproject_paths
        )
        try:
            report = PackageReportBuilder().build(
                package=project.target,
                source_plan=SourcePlan.for_package(project.target, "SEARCH"),
                source_snapshot=snapshot.identity,
                cell_results=(),
            )
            document = document_for(report, tmp_path / "partial.json")
            bindings = document["inputs"]["search_policy"]["bindings"]
            assert len(bindings) == 1
            assert bindings[0]["dependencies"] == ["a-dep", "z-dep"]
            assert (
                len(
                    ReportStore().read(tmp_path / "partial.json").search_policy.bindings
                )
                == 1
            )
            duplicate = copy.deepcopy(bindings[0])
            bindings[0]["dependencies"] = ["a-dep"]
            duplicate["dependencies"] = ["z-dep"]
            bindings.append(duplicate)
            (tmp_path / "partial.json").write_text(json.dumps(document))
            with pytest.raises(ConfigurationError):
                ReportStore().read(tmp_path / "partial.json")
        finally:
            snapshot.close()

    def test_default_selection_round_trip_and_shared_inventory(
        self, search_case, tmp_path: Path
    ) -> None:
        project, _, _, report_for = search_case
        reports = tuple(report_for(cell) for cell in project.target.cells)
        assert len(reports) == 2
        store = ReportStore()
        single = document_for(reports[0], tmp_path / "single.json")
        combined = store.merge(reports)
        document = document_for(combined, tmp_path / "combined.json")
        loaded = store.read(tmp_path / "combined.json")
        schema = json.loads(
            (
                Path(__file__).parents[1] / "docs/schemas/package-floor-v1.schema.json"
            ).read_text()
        )
        Draft202012Validator(schema).validate(document)
        print(
            json.dumps(
                {
                    "single_report_bytes": (tmp_path / "single.json").stat().st_size,
                    "two_cell_report_bytes": (tmp_path / "combined.json")
                    .stat()
                    .st_size,
                    "policy_bindings": len(
                        document["inputs"]["search_policy"]["bindings"]
                    ),
                    "series_inventories": len(document["inputs"]["series_inventories"]),
                }
            )
        )
        assert len(document["inputs"]["search_policy"]["bindings"]) == 1
        assert (
            len(single["inputs"]["series_inventories"])
            == len(document["inputs"]["series_inventories"])
            == 1
        )
        assert len(document["inputs"]["candidate_snapshots"]) == 2
        binding = document["inputs"]["search_policy"]["bindings"][0]
        assert binding["requested_space"] is None
        assert binding["dependencies"] == ["demo-dep"]
        assert binding["space_defaults"] == {
            "with_lower_bound": "majors[declaration-1:]",
            "without_lower_bound": "majors[baseline-2:]",
        }
        inventory = document["inputs"]["series_inventories"][0]
        assert inventory["series_keys"] == [[0, 1], [0, 2], [0, 3]]
        assert all(
            record["series_inventory_ref"] == inventory["series_inventory_id"]
            for record in document["inputs"]["candidate_snapshots"]
        )
        for projection in loaded.search_spaces():
            assert projection.selection is not None
            assert projection.selection.reason == "default-declaration"
            assert projection.selection.anchors == (("declaration", "2"),)
            assert projection.selection.selected_keys == ((0, 1), (0, 2), (0, 3))
            assert projection.representatives == ("1", "2", "3")

    def test_merge_keeps_distinct_histories_and_update_removes_unreachable_inventory(
        self, search_case, tmp_path: Path
    ) -> None:
        project, _, _, report_for = search_case
        first, second = project.target.cells
        original = report_for(first)
        different = report_for(second, versions=("0", "1", "2", "3"))
        assert original.report_generation_id == different.report_generation_id
        assert (
            original.search_spaces()[0].representatives
            == different.search_spaces()[1].representatives
        )
        merged = ReportStore().merge((original, different))
        assert (
            len(
                document_for(merged, tmp_path / "merged.json")["inputs"][
                    "series_inventories"
                ]
            )
            == 2
        )
        replaced = ReportStore().update(merged, report_for(second))
        assert (
            len(
                document_for(replaced, tmp_path / "replaced.json")["inputs"][
                    "series_inventories"
                ]
            )
            == 1
        )

    @pytest.mark.parametrize(
        "mutation",
        [
            "policy-digest",
            "artifact-policy",
            "missing-name",
            "duplicate-group",
            "missing-ref",
            "extra-inventory",
            "noncanonical-space",
            "wrong-profile",
            "null-series-ref",
        ],
    )
    def test_reader_rejects_inconsistent_policy_and_observation_graph(
        self, search_case, tmp_path: Path, mutation: str
    ) -> None:
        project, _, _, report_for = search_case
        document = document_for(
            report_for(project.target.cells[0]), tmp_path / "report.json"
        )
        inputs = document["inputs"]
        if mutation == "policy-digest":
            inputs["candidate_snapshots"][0]["policy_identity"] = "0" * 64
        elif mutation == "artifact-policy":
            inputs["search_policy"]["artifact"] = "wheel"
        elif mutation == "missing-name":
            inputs["search_policy"]["bindings"] = []
        elif mutation == "duplicate-group":
            inputs["search_policy"]["bindings"] *= 2
        elif mutation == "missing-ref":
            inputs["candidate_snapshots"][0]["series_inventory_ref"] = "0" * 64
        elif mutation == "noncanonical-space":
            inputs["search_policy"]["bindings"][0]["requested_space"] = (
                " majors [ declaration - 1 : ] "
            )
        elif mutation == "wrong-profile":
            inputs["search_policy"]["profile"] = "unrecognized"
        elif mutation == "null-series-ref":
            inputs["candidate_snapshots"][0]["series_inventory_ref"] = None
        else:
            other = document_for(
                report_for(project.target.cells[1], versions=("0", "1", "2", "3")),
                tmp_path / "other.json",
            )
            inputs["series_inventories"].extend(other["inputs"]["series_inventories"])
            inputs["series_inventories"].sort(
                key=lambda item: item["series_inventory_id"]
            )
        (tmp_path / "report.json").write_text(json.dumps(document))
        with pytest.raises(ConfigurationError):
            ReportStore().read(tmp_path / "report.json")

    def test_host_partial_binds_unselected_defaults_without_inventory(
        self, search_case, tmp_path: Path
    ) -> None:
        project, snapshot, source, _ = search_case
        partial = PackageReportBuilder().build(
            package=project.target,
            source_plan=source,
            source_snapshot=snapshot.identity,
            cell_results=(),
        )
        document = document_for(partial, tmp_path / "report.json")
        assert document["inputs"]["series_inventories"] == []
        assert (
            document["inputs"]["search_policy"]["bindings"][0]["requested_space"]
            is None
        )
        assert all(p.selection is None for p in partial.search_spaces())
        tampered = copy.deepcopy(document)
        tampered["inputs"]["search_policy"]["bindings"][0]["space_defaults"][
            "without_lower_bound"
        ] = "all"
        (tmp_path / "report.json").write_text(json.dumps(tampered))
        with pytest.raises(ConfigurationError, match="generation identity"):
            ReportStore().read(tmp_path / "report.json")

    @pytest.mark.parametrize("force", [False, True])
    def test_default_apply_is_idempotent_and_binds_unselected_branch(
        self, search_case, tmp_path: Path, force: bool
    ) -> None:
        project, snapshot, _, report_for = search_case
        report = ReportStore().merge(
            tuple(report_for(cell) for cell in project.target.cells)
        )
        authorizer = ApplyAuthorizer()
        initial = authorizer.authorize(
            report=report, project=project, current_snapshot=snapshot, force=force
        )
        assert initial.package_apply.dependency_state == "WRITABLE"
        ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
            authorization=initial, root=tmp_path
        )
        updated = ProjectLoader().load(root=tmp_path)
        current = SnapshotBuilder.without_processes().build(
            tmp_path, owned_pyproject_paths=updated.owned_pyproject_paths
        )
        try:
            repeated = authorizer.authorize(
                report=report, project=updated, current_snapshot=current, force=force
            )
            assert repeated.package_apply.dependency_state == "NOOP"
            assert repeated.package_apply.authorized_edits == ()
            assert all(
                p.selection.anchors == (("declaration", "2"),)
                for p in report.search_spaces()
                if p.selection
            )
        finally:
            current.close()
        path = tmp_path / "pyproject.toml"
        path.write_text(
            path.read_text()
            + '\n[tool.pf.search-space-defaults]\nwith-lower-bound = "majors[declaration-1:]"\nwithout-lower-bound = "all"\n'
        )
        changed = ProjectLoader().load(root=tmp_path)
        current = SnapshotBuilder.without_processes().build(
            tmp_path, owned_pyproject_paths=changed.owned_pyproject_paths
        )
        try:
            with pytest.raises(ApplyAuthorizationError, match="search policy mismatch"):
                authorizer.authorize(
                    report=report,
                    project=changed,
                    current_snapshot=current,
                    force=force,
                )
        finally:
            current.close()

    def test_explain_consumes_validated_projection_without_parser_or_project(
        self, search_case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project, _, _, report_for = search_case
        report = report_for(project.target.cells[0])

        def forbidden(*args, **kwargs):
            raise AssertionError("Explain must consume only validated facts")

        monkeypatch.setattr("pf.search_space.parse", forbidden)
        monkeypatch.setattr("pf.project.ProjectLoader.load", forbidden)
        monkeypatch.setattr("pf.adapters.uv.UvAdapter.query", forbidden)
        output = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=output, width=220, theme=PF_THEME), root=tmp_path
        )
        assert (
            terminal.render_explain(
                ExplainCommandResult(report=report, report_path="package-floor.json")
            )
            == 0
        )
        rendered = output.getvalue()
        assert "conditional default" in rendered
        assert "majors[declaration-1:]" in rendered
        assert "declaration=2" in rendered
        assert "exact representatives: 1, 2, 3" in rendered
        assert "evidence unavailable" in rendered

    @pytest.mark.parametrize("search_case", ["all"], indirect=True)
    def test_explicit_all_has_required_null_inventory_reference(
        self, search_case, tmp_path: Path
    ) -> None:
        project, _, _, report_for = search_case
        report = report_for(project.target.cells[0])
        document = document_for(report, tmp_path / "all.json")
        assert document["inputs"]["series_inventories"] == []
        assert (
            document["inputs"]["candidate_snapshots"][0]["series_inventory_ref"] is None
        )
        schema = json.loads(
            (
                Path(__file__).parents[1] / "docs/schemas/package-floor-v1.schema.json"
            ).read_text()
        )
        Draft202012Validator(schema).validate(document)
        loaded = ReportStore().read(tmp_path / "all.json")
        selection = loaded.search_spaces()[0].selection
        assert selection is not None
        assert selection.expression == "all"
        assert selection.reason == "explicit"
        assert selection.anchors == ()

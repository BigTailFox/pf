from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import Literal

from rich.console import Console

from pf.failure import FailurePolicy
from pf.report import PackageReportBuilder, ReportStore, ValidatedReport
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineRejection,
    CellFailureScope,
    PassEvaluation,
    ProcessResult,
    PytestFailureCase,
    PytestFailureDetail,
    StaticBaseline,
    StaticUnchangedEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    Cell,
    PackagePlan,
    Proposal,
    RequirementDeclaration,
    SourceIdentity,
    SourcePlan,
    SourceSnapshotIdentity,
    source_snapshot_digest,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    IncompleteReportResult,
    ProjectionEvidence,
    failure_records_for_result,
)
from pf.terminal import PF_THEME, TerminalPresenter


def _process_result(*, exit_code: int = 1) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=1,
        stdout="",
        stderr="",
    )


def _attempt(
    cell: Cell,
    *,
    resolution: Literal["highest", "lowest-direct", "exact-vector"] = "exact-vector",
) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=cell,
            requested_resolution=resolution,
            requested_managed_vector=() if resolution == "exact-vector" else None,
            active_declaration_ids=(),
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )


def _report(
    *,
    target_cells: tuple[Cell, ...],
    cell_results: tuple[CellResult, ...],
    declarations: tuple[RequirementDeclaration, ...] = (),
    projections: tuple[ProjectionEvidence, ...] = (),
) -> ValidatedReport:
    package = PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(),
        cells=(),
        source_plan=SourcePlan(identities=()),
    )
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest(()),
        entries=(),
    )
    base = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(),
    )
    return replace(
        base,
        target_cells=target_cells,
        cell_results=cell_results,
        requirement_declarations=declarations,
        projection_evidence=projections,
        result=IncompleteReportResult(
            status="incomplete",
            reasons=("INDETERMINATE",),
        ),
        failure_records=tuple(
            failure
            for result in cell_results
            for failure in failure_records_for_result(result)
        ),
    )


class TestExplainCellCards:
    def test_explain_terminal_cell_renders_only_terminal_status_and_reason(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        historical = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=_attempt(cell)),
            cause="TEST_FAILURE",
            stage="test",
            process=_process_result(),
        )
        terminal = FailurePolicy().classify(
            scope=CellFailureScope(
                package=cell.package,
                cell=cell,
                source_snapshot_digest="snapshot",
                evaluation_policy_identity="policy",
            ),
            cause="TOOL_FAILURE",
            stage="test",
            process=_process_result(),
        )
        result = CellIndeterminate(
            cell=cell,
            phase="runtime-search",
            failure_id=terminal.failure_id,
            failure_records=(historical, terminal),
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                color_system=None,
                width=80,
            ),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        assert presenter.render_explain(
            (_report(target_cells=(cell,), cell_results=(result,)),)
        ) == 0

        rendered = stdout.getvalue()
        normalized = " ".join(rendered.replace("│", "").split())
        assert "╭" in rendered
        assert rendered.count("[py3.10][x86_64-unknown-linux-gnu][no-extra]") == 1
        assert "search stopped at [testing]" in normalized
        assert "Search stopped before the configured search" in normalized
        assert "space was fully evaluated." in normalized
        assert "PF could not complete a verification tool" in normalized
        assert "operation reliably." in normalized
        assert terminal.failure_id in normalized
        assert "pf diagnose demo --failure" in normalized
        assert historical.failure_id not in rendered
        assert "The full test command failed" not in rendered
        assert "What happened:" not in rendered
        assert "Impact:" not in rendered

    def test_explain_report_summary_and_requirements_share_one_card(self) -> None:
        declaration = RequirementDeclaration(
            declaration_id="demo:dependencies:rich",
            package="demo",
            location="base",
            name="rich",
            source=SourceIdentity(kind="registry"),
            pyproject_path="pyproject.toml",
            raw="rich>=14",
            kind="searchable",
            managed=True,
        )
        projection = ProjectionEvidence(
            declaration_id=declaration.declaration_id,
            floors=(),
            projected_requirements=(),
            representable=False,
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                color_system=None,
                width=80,
            ),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(
            (
                _report(
                    target_cells=(),
                    cell_results=(),
                    declarations=(declaration,),
                    projections=(projection,),
                ),
            )
        )

        rendered = stdout.getvalue()
        assert rendered.count("╭") == 1
        card = rendered[rendered.index("╭") : rendered.index("╰")]
        assert "demo · package-floor.json" in card
        assert "Status: incomplete" in card
        assert "Apply: not authorized by this report" in card
        assert "Cells: 0/0 covered" in card
        assert "Requirements" in card
        assert "rich>=14" in card
        assert "projection blocked" in card

    def test_explain_terminal_cell_hides_static_baseline_evidence(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = _attempt(cell, resolution="highest")
        proposal = Proposal(
            proposal_id="highest",
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        diagnostic = TyDiagnostic(
            identity="snapshot|demo.py|1|1|existing-error",
            origin="snapshot",
            path="demo.py",
            line=1,
            column=1,
            code="existing-error",
            severity="major",
            message="existing project error",
        )
        ty = TyCheck(process=_process_result(), diagnostics=(diagnostic,))
        baseline = StaticBaseline(
            proposal=proposal,
            ty=ty,
            digest=ty_diagnostic_digest(ty.diagnostics),
        )
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=ty,
            baseline_digest=baseline.digest,
        )
        baseline_pass = PassEvaluation(
            proposal=proposal,
            static=static,
            test=TestPass(process=_process_result(exit_code=0)),
        )
        terminal = FailurePolicy().classify(
            scope=CellFailureScope(
                package=cell.package,
                cell=cell,
                source_snapshot_digest="snapshot",
                evaluation_policy_identity="policy",
            ),
            cause="TOOL_FAILURE",
            stage="test",
            process=_process_result(),
        )
        result = CellIndeterminate(
            cell=cell,
            phase="runtime-search",
            failure_id=terminal.failure_id,
            failure_records=(terminal,),
            baseline_attempt=attempt,
            static_baseline=baseline,
            baseline=baseline_pass,
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(
            (_report(target_cells=(cell,), cell_results=(result,)),)
        )

        rendered = stdout.getvalue()
        assert "ty baseline" not in rendered
        assert "demo.py" not in rendered
        assert "existing-error" not in rendered
        assert "existing project error" not in rendered

    def test_explain_missing_target_cell_has_an_explicit_warning_card(self) -> None:
        report = ReportStore().read(
            Path(__file__).parents[1]
            / "docs/examples/package-floor-v2-minimal-incomplete.json"
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain((report,))

        rendered = stdout.getvalue()
        assert "⚠ [py3.12][x86_64-unknown-linux-gnu][no-extra]" in rendered
        assert "search stopped" in rendered
        assert "This target cell has no result in this report." in rendered
        assert "pf diagnose" not in rendered

    def test_explain_success_cell_shows_only_its_final_status(self) -> None:
        report = ReportStore().read(
            Path(__file__).parents[1]
            / "docs/examples/package-floor-v2-minimal-complete.json"
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                color_system=None,
                width=80,
            ),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain((report,))

        rendered = stdout.getvalue()
        assert rendered.count("╭") == 2
        assert "✓ [py3.12][x86_64-unknown-linux-gnu][no-extra]" in rendered
        assert "search completed" in rendered
        assert "ty baseline" not in rendered
        assert "What happened:" not in rendered
        assert "pf diagnose" not in rendered
        assert "Next: pf apply demo" in rendered

    def test_explain_baseline_rejection_hides_pytest_detail(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = _attempt(cell, resolution="highest")
        proposal = Proposal(
            proposal_id="highest",
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        ty = TyCheck(process=_process_result(exit_code=0), diagnostics=())
        baseline = StaticBaseline(
            proposal=proposal,
            ty=ty,
            digest=ty_diagnostic_digest(ty.diagnostics),
        )
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=ty,
            baseline_digest=baseline.digest,
        )
        test_process = _process_result()
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(
                process=test_process,
                detail=PytestFailureDetail(
                    first=PytestFailureCase(
                        nodeid="tests/test_widget.py::test_old_dependency",
                        phase="call",
                    ),
                    total=2,
                ),
            ),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=test_process,
        )
        result = BaselineRejection(
            attempt=attempt,
            failure=failure,
            static_baseline=baseline,
            evaluation=evaluation,
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(
            (_report(target_cells=(cell,), cell_results=(result,)),)
        )

        rendered = stdout.getvalue()
        assert "The full test command failed for this version combination." in rendered
        assert failure.failure_id in rendered
        assert "tests/test_widget.py" not in rendered
        assert "... and 1 more" not in rendered
        assert "ty baseline" not in rendered

    def test_explain_tty_colors_report_and_cell_outcomes(self) -> None:
        report = ReportStore().read(
            Path(__file__).parents[1]
            / "docs/examples/package-floor-v2-minimal-incomplete.json"
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
                width=80,
            ),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain((report,))

        rendered = stdout.getvalue()
        assert "\x1b[2;33m╭" in rendered
        assert "\x1b[1;36mdemo" in rendered
        assert "\x1b[36mpackage-floor.json" in rendered
        assert "Status: \x1b[33mincomplete" in rendered
        assert "Apply: \x1b[33mnot authorized by this report" in rendered
        assert "\x1b[33m⚠ " in rendered

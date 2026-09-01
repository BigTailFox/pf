from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import Literal

import pytest
from rich.console import Console

from pf.failure import FailurePolicy
from pf.report import PackageReportBuilder, ReportStore, ValidatedReport
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellFailureScope,
    FailureRecord,
    NormalExit,
    PassEvaluation,
    ProcessResult,
    PytestFailureCase,
    PytestFailureDetail,
    RuntimeEvaluationRun,
    StaticBaseline,
    StaticUnchangedEvaluation,
    TyCheck,
    TyDiagnostic,
    VerifierDiagnostics,
    VerifierPass,
    VerifierRejected,
    VerifierRejectedEvaluation,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    Cell,
    PackagePlan,
    Proposal,
    RequirementDeclaration,
    SourceSnapshotIdentity,
    source_snapshot_digest,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    FloorProjection,
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
        source_routes=(),
    )
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest((), ()),
        entries=(),
        pyproject_identities=(),
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


def _declaration(
    name: str,
    *,
    specifier: str,
) -> RequirementDeclaration:
    return RequirementDeclaration(
        declaration_id=f"demo:dependencies:{name}",
        package="demo",
        location="base",
        name=name,
        specifier=specifier,
        pyproject_path="pyproject.toml",
        raw=f"{name}{specifier}",
        kind="searchable",
        managed=True,
    )


def _search_failure(
    cell: Cell,
    *,
    reason: Literal[
        "NO_PASS_IN_SEARCH_SPACE", "NON_MONOTONIC", "NONDETERMINISTIC"
    ],
) -> CellSearchFailure:
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
    process = _process_result(exit_code=0)
    ty = TyCheck(process=process, diagnostics=())
    baseline = StaticBaseline(
        proposal=proposal,
        ty=ty,
        digest=ty_diagnostic_digest(()),
    )
    evaluation = PassEvaluation(
        proposal=proposal,
        static=StaticUnchangedEvaluation(
            proposal=proposal,
            ty=ty,
            baseline_digest=baseline.digest,
        ),
        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
    )
    return CellSearchFailure(
        reason=reason,
        cell=cell,
        phase="runtime-search",
        baseline_attempt=attempt,
        static_baseline=baseline,
        baseline=evaluation,
    )


class TestExplainCellCards:
    @pytest.mark.parametrize(
        ("reason", "bucket", "conclusion"),
        (
            (
                "NO_PASS_IN_SEARCH_SPACE",
                "1 no floor · 1 total",
                "no compatible version combination was found",
            ),
            (
                "NON_MONOTONIC",
                "1 search failed · 1 total",
                "Search evidence was non-monotonic",
            ),
            (
                "NONDETERMINISTIC",
                "1 search failed · 1 total",
                "Repeated checks disagreed",
            ),
        ),
    )
    def test_explain_distinguishes_no_floor_from_search_failure(
        self,
        reason: Literal[
            "NO_PASS_IN_SEARCH_SPACE", "NON_MONOTONIC", "NONDETERMINISTIC"
        ],
        bucket: str,
        conclusion: str,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        result = _search_failure(cell, reason=reason)
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        assert presenter.render_explain(
            _report(target_cells=(cell,), cell_results=(result,))
        ) == 0

        rendered = " ".join(stdout.getvalue().split())
        assert bucket in rendered
        assert conclusion in rendered
        assert "pf diagnose" not in rendered

    def test_explain_shows_fixed_declarations_and_blocked_projection_floors(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        managed = _declaration("rich", specifier=">=14")
        fixed = RequirementDeclaration(
            declaration_id="demo:dependencies:local",
            package="demo",
            location="base",
            name="local",
            pyproject_path="pyproject.toml",
            raw="local @ file:///workspace/local",
            kind="fixed",
            managed=False,
        )
        projection = ProjectionEvidence(
            declaration_id=managed.declaration_id,
            floors=(FloorProjection(cell=cell, version="14.2.0"),),
            projected_requirements=(),
            representable=False,
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(
            _report(
                target_cells=(),
                cell_results=(),
                declarations=(managed, fixed),
                projections=(projection,),
            )
        )

        rendered = " ".join(stdout.getvalue().split())
        assert "rich>=14" in rendered
        assert "projection blocked" in rendered
        assert (
            "[py3.11][x86_64-unknown-linux-gnu][no-extra]·14.2.0"
            in rendered.replace(" ", "")
        )
        assert "local @ file:///workspace/local" in rendered
        assert "fixed · not managed" in rendered
    def test_explain_terminal_cell_renders_only_terminal_status_and_reason(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        historical = FailureRecord.from_verifier(
            scope=AttemptFailureScope(attempt=_attempt(cell)),
            disposition="REJECTED",
            cause="VERIFIER_EXITED_NONZERO",
            stage="test",
            terminal=NormalExit(exit_code=1),
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

        assert (
            presenter.render_explain(
                _report(target_cells=(cell,), cell_results=(result,))
            )
            == 0
        )

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
        assert f"pf diagnose {terminal.failure_id} --package demo" in normalized
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
            _report(
                target_cells=(),
                cell_results=(),
                declarations=(declaration,),
                projections=(projection,),
            )
        )

        rendered = stdout.getvalue()
        assert rendered.count("╭") == 1
        card = rendered[rendered.index("╭") : rendered.index("╰")]
        assert "demo · package-floor.json" in card
        assert "incomplete · blocked by report evidence" in card
        assert "current project was not inspected" in card
        assert "0 total" in card
        assert "Requirements" in card
        assert "rich>=14" in card
        assert "projection blocked" in card

    def test_explain_requirements_align_projection_details(self) -> None:
        rich = _declaration("rich", specifier=">=14.0")
        packaging = _declaration("packaging", specifier=">=25.0")
        projections = tuple(
            ProjectionEvidence(
                declaration_id=declaration.declaration_id,
                floors=(),
                projected_requirements=(),
                representable=False,
            )
            for declaration in (rich, packaging)
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(
            _report(
                target_cells=(),
                cell_results=(),
                declarations=(rich, packaging),
                projections=projections,
            )
        )

        requirement_lines = [
            line.rstrip()
            for line in stdout.getvalue().splitlines()
            if "projection blocked" in line
        ]
        assert len(requirement_lines) == 2
        assert (
            len({line.index("projection blocked") for line in requirement_lines}) == 1
        )

    def test_explain_requirements_style_original_specifier_and_version(
        self,
    ) -> None:
        declaration = _declaration("rich", specifier=">=14.0")
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
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
                width=80,
            ),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(
            _report(
                target_cells=(),
                cell_results=(),
                declarations=(declaration,),
                projections=(projection,),
            )
        )

        assert "rich\x1b[36m>=\x1b[0m\x1b[1;36m14.0\x1b[0m" in stdout.getvalue()

    def test_explain_requirements_style_projected_version_without_coloring_marker(
        self,
    ) -> None:
        declaration = _declaration("rich", specifier=">=14.0")
        projection = ProjectionEvidence(
            declaration_id=declaration.declaration_id,
            floors=(),
            projected_requirements=('rich>=15.0; python_version >= "3.11"',),
            representable=True,
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

        presenter.render_explain(
            _report(
                target_cells=(),
                cell_results=(),
                declarations=(declaration,),
                projections=(projection,),
            )
        )

        rendered = stdout.getvalue()
        assert "-> rich\x1b[32m>=\x1b[0m\x1b[1;32m15.0\x1b[0m" in rendered
        assert "python_version" in rendered
        assert '"3.11"' in rendered

    def test_explain_requirements_style_entire_multi_clause_specifier(
        self,
    ) -> None:
        declaration = _declaration("demo", specifier=" (>=1, <2)")
        projection = ProjectionEvidence(
            declaration_id=declaration.declaration_id,
            floors=(),
            projected_requirements=("demo (>=1.5, <2)",),
            representable=True,
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

        presenter.render_explain(
            _report(
                target_cells=(),
                cell_results=(),
                declarations=(declaration,),
                projections=(projection,),
            )
        )

        rendered = stdout.getvalue()
        assert (
            "demo \x1b[36m(>=\x1b[0m\x1b[1;36m1\x1b[0m"
            "\x1b[36m, <\x1b[0m\x1b[1;36m2\x1b[0m\x1b[36m)\x1b[0m" in rendered
        )
        assert (
            "-> demo \x1b[32m(>=\x1b[0m\x1b[1;32m1.5\x1b[0m"
            "\x1b[32m, <\x1b[0m\x1b[1;32m2\x1b[0m\x1b[32m)\x1b[0m" in rendered
        )

    def test_explain_multiple_marker_requirements_are_indented_under_declaration(
        self,
    ) -> None:
        declaration = RequirementDeclaration(
            declaration_id="demo:dependencies:foo",
            package="demo",
            location="base",
            name="foo",
            pyproject_path="pyproject.toml",
            raw="foo>=1",
            kind="searchable",
            managed=True,
        )
        projection = ProjectionEvidence(
            declaration_id=declaration.declaration_id,
            floors=(),
            projected_requirements=(
                'foo>=2; python_version < "3.12"',
                'foo>=3; python_version >= "3.12"',
            ),
            representable=True,
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(
            _report(
                target_cells=(),
                cell_results=(),
                declarations=(declaration,),
                projections=(projection,),
            )
        )

        rendered = " ".join(stdout.getvalue().split())
        assert "foo>=1" in rendered
        assert '-> foo>=2; python_version < "3.12"' in rendered
        assert '-> foo>=3; python_version >= "3.12"' in rendered

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
            verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
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

        presenter.render_explain(_report(target_cells=(cell,), cell_results=(result,)))

        rendered = stdout.getvalue()
        assert "ty baseline" not in rendered
        assert "demo.py" not in rendered
        assert "existing-error" not in rendered
        assert "existing project error" not in rendered

    def test_explain_missing_target_cell_has_an_explicit_warning_card(self) -> None:
        report = ReportStore().read(
            Path(__file__).parents[1]
            / "docs/examples/package-floor-v1-minimal-incomplete.json"
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(report)

        rendered = stdout.getvalue()
        assert "⚠  [py3.12][x86_64-unknown-linux-gnu][no-extra]" in rendered
        assert "search stopped" in rendered
        assert "This target cell has no result in this report." in rendered
        assert "pf diagnose" not in rendered

    def test_explain_success_cell_shows_only_its_final_status(self) -> None:
        report = ReportStore().read(
            Path(__file__).parents[1]
            / "docs/examples/package-floor-v1-minimal-complete.json"
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

        presenter.render_explain(report)

        rendered = stdout.getvalue()
        assert rendered.count("╭") == 1
        assert "✓  [py3.12][x86_64-unknown-linux-gnu][no-extra]" in rendered
        assert rendered.count("[py3.12][x86_64-unknown-linux-gnu][no-extra]") == 1
        assert "ty baseline" not in rendered
        assert "What happened:" not in rendered
        assert "pf diagnose" not in rendered
        assert "-> pf apply --package demo" in rendered

    def test_explain_mixed_report_renders_each_target_once_and_one_next_action(
        self,
    ) -> None:
        complete = ReportStore().read(
            Path(__file__).parents[1]
            / "docs/examples/package-floor-v1-minimal-complete.json"
        )
        passed = complete.cell_results[0]
        rejected_cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        rejected_attempt = _attempt(rejected_cell, resolution="highest")
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=rejected_attempt),
            cause="HARNESS_CONFLICT",
            stage="resolve-environment",
            process=_process_result(),
        )
        rejected = BaselineRejection(
            attempt=rejected_attempt,
            failure=failure,
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
            _report(
                target_cells=(passed.cell, rejected_cell),
                cell_results=(passed, rejected),
            )
        )

        rendered = stdout.getvalue()
        passed_identity = "[py3.12][x86_64-unknown-linux-gnu][no-extra]"
        rejected_identity = "[py3.10][x86_64-unknown-linux-gnu][no-extra]"
        assert rendered.count("╭") == 2
        assert rendered.count(passed_identity) == 1
        assert rendered.count(rejected_identity) == 1
        assert rendered.count("pf diagnose") == 1
        assert f"pf diagnose {failure.failure_id} --package demo" in " ".join(
            rendered.split()
        )
        assert "pf apply --package demo" not in rendered

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
        evaluation = VerifierRejectedEvaluation(
            proposal=proposal,
            static=static,
            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        runtime = RuntimeEvaluationRun(
            evaluation=evaluation,
            diagnostics=VerifierDiagnostics(
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
        failure = FailureRecord.from_verifier(
            scope=AttemptFailureScope(attempt=attempt),
            disposition="REJECTED",
            cause="VERIFIER_EXITED_NONZERO",
            stage="test",
            terminal=NormalExit(exit_code=1),
        )
        result = BaselineRejection(
            attempt=attempt,
            failure=failure,
            static_baseline=baseline,
            evaluation=evaluation,
            runtime=runtime,
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(_report(target_cells=(cell,), cell_results=(result,)))

        rendered = stdout.getvalue()
        assert "The configured verifier rejected this version combination." in rendered
        assert failure.failure_id in rendered
        assert "tests/test_widget.py" not in rendered
        assert "... and 1 more" not in rendered
        assert "ty baseline" not in rendered

    def test_explain_baseline_indeterminate_renders_terminal_reason(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = _attempt(cell, resolution="highest")
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TOOL_FAILURE",
            stage="install",
            process=_process_result(),
        )
        result = BaselineIndeterminate(
            attempt=attempt,
            failure=failure,
        )
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False),
        )

        presenter.render_explain(_report(target_cells=(cell,), cell_results=(result,)))

        rendered = stdout.getvalue()
        normalized = " ".join(rendered.split())
        assert "!  [py3.10][x86_64-unknown-linux-gnu][no-extra]" in rendered
        assert (
            "search stopped at [baseline][highest][installing dependencies]"
            in normalized
        )
        assert (
            "PF could not complete a verification tool operation reliably."
            in normalized
        )
        assert failure.failure_id in normalized

    def test_explain_tty_colors_report_and_cell_outcomes(self) -> None:
        report = ReportStore().read(
            Path(__file__).parents[1]
            / "docs/examples/package-floor-v1-minimal-incomplete.json"
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

        presenter.render_explain(report)

        rendered = stdout.getvalue()
        assert "\x1b[2;33m╭" in rendered
        assert "\x1b[1;36mdemo" in rendered
        assert "package-floor.json" in rendered
        assert "\x1b[4;36m" in rendered
        assert "\x1b]8;" in rendered
        assert "incomplete" in rendered
        assert "blocked; no applicable final floor" in rendered
        assert "\x1b[33m⚠" in rendered

    def test_explain_summary_uses_red_when_any_cell_is_red(self) -> None:
        rejected_cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        rejected_attempt = _attempt(rejected_cell, resolution="highest")
        rejection = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=rejected_attempt),
            cause="HARNESS_CONFLICT",
            stage="resolve-environment",
            process=_process_result(),
        )
        rejected = BaselineRejection(
            attempt=rejected_attempt,
            failure=rejection,
        )
        indeterminate_cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        indeterminate_failure = FailurePolicy().classify(
            scope=CellFailureScope(
                package=indeterminate_cell.package,
                cell=indeterminate_cell,
                source_snapshot_digest="snapshot",
                evaluation_policy_identity="policy",
            ),
            cause="TOOL_FAILURE",
            stage="test",
            process=_process_result(),
        )
        indeterminate = CellIndeterminate(
            cell=indeterminate_cell,
            phase="runtime-search",
            failure_id=indeterminate_failure.failure_id,
            failure_records=(indeterminate_failure,),
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

        presenter.render_explain(
            _report(
                target_cells=(rejected_cell, indeterminate_cell),
                cell_results=(rejected, indeterminate),
            )
        )

        assert "\x1b[1;31mReport incomplete" in stdout.getvalue()
        assert "1 rejected cell" in stdout.getvalue()
        assert "1 unknown cell" in stdout.getvalue()

    def test_explain_summary_uses_yellow_when_report_has_only_yellow_cells(
        self,
    ) -> None:
        report = ReportStore().read(
            Path(__file__).parents[1]
            / "docs/examples/package-floor-v1-minimal-incomplete.json"
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

        presenter.render_explain(report)

        assert "\x1b[1;33mReport incomplete" in stdout.getvalue()
        assert "1 missing cell" in stdout.getvalue()

    def test_explain_summary_uses_green_when_report_authorizes_apply(
        self,
    ) -> None:
        report = ReportStore().read(
            Path(__file__).parents[1]
            / "docs/examples/package-floor-v1-minimal-complete.json"
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

        presenter.render_explain(report)

        assert (
            "\x1b[1;32mNo managed dependencies require floor changes."
            "\x1b[0m" in stdout.getvalue()
        )

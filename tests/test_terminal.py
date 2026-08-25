from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import Literal

import pytest

from conftest import empty_harness_baseline
from rich.console import Console

from pf.errors import ConfigurationError, InfrastructureError, NoApplicableFloorError
from pf.failure import FailurePolicy
from pf.runlog import RunLogStore
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellCompletedEvent,
    CellContextEvent,
    BaselineDetailIdentity,
    CellFailed,
    CellFailureScope,
    CellMatrixEvent,
    CellStageEvent,
    CellSucceeded,
    CheckCompatibilityFailure,
    CheckIndeterminate,
    CheckPass,
    DiagnosticClassification,
    PassEvaluation,
    ProcessEvent,
    ProcessResult,
    ProcessSpec,
    FailureDetail,
    FailureCause,
    FailureRecord,
    HighestVersionPass,
    SearchFailureEvent,
    SearchProbeDetailIdentity,
    SmokeBaselineRejection,
    SmokePass,
    SmokeIndeterminate,
    StaticBaseline,
    StaticRegressionEvaluation,
    StaticUnchangedEvaluation,
    StatusEvent,
    StageProgress,
    TestFail,
    TestFailEvaluation,
    TestPass,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    VerificationRole,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    Cell,
    Proposal,
    RequirementDeclaration,
    SourceIdentity,
    SourceSnapshotIdentity,
    VersionPin,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CoordinateFailure,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProjectionEvidence,
    ProbeObservation,
    ProbeRejection,
    report_generation_id,
)
from pf.terminal import PF_THEME, TerminalPresenter
from pf.static_transition import static_fingerprint

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def visible(text: str) -> str:
    return _ANSI.sub("", text)


class TTYBuffer(StringIO):
    def isatty(self) -> bool:
        return True


def process_result(
    *,
    exit_code: int | None = 1,
    stderr: str = "",
    stdout: str = "",
    timed_out: bool = False,
    start_error: str | None = None,
) -> ProcessResult:
    if start_error is not None:
        exit_code = None
        signal = None
    elif exit_code is None:
        signal = 9
    else:
        signal = None
    return ProcessResult(
        exit_code=exit_code,
        signal=signal,
        duration_seconds=1,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        start_error=start_error,
    )


def general_classifications(
    *diagnostics: TyDiagnostic,
) -> tuple[DiagnosticClassification, ...]:
    return tuple(
        DiagnosticClassification(
            diagnostic_identity=diagnostic.identity,
            classification="general",
            reason_code="test-fixture",
        )
        for diagnostic in diagnostics
    )


def completed_event(
    cell: Cell,
    *,
    status: str,
    completed: int = 1,
    total: int = 1,
    phase: str = "complete",
    diagnostics: tuple[TyDiagnostic, ...] = (),
    process: ProcessResult | None = None,
    failure: FailureRecord | None = None,
    role: VerificationRole | None = None,
    stage: str | None = None,
    diagnose_available: bool = True,
) -> CellCompletedEvent:
    if status in {"PASS", "SUCCESS"}:
        outcome = CellSucceeded(
            status=status,
            phase=phase,
            diagnostics=diagnostics,
            process=process,
        )
    else:
        outcome = CellFailed(
            status=status,
            phase=stage or phase,
            diagnostics=diagnostics,
            process=process,
            failures=() if failure is None else (failure,),
            verification_role=role,
        )
    return CellCompletedEvent(
        cell=cell,
        completed=completed,
        total=total,
        outcome=outcome,
        diagnose_available=diagnose_available,
    )


def recorded_failure(
    *,
    cause: FailureCause,
    stage: str,
    process: ProcessResult,
) -> FailureRecord:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    return FailurePolicy().classify(
        scope=CellFailureScope(
            package=cell.package,
            cell=cell,
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="policy",
        ),
        cause=cause,
        stage=stage,
        process=process,
    )


def presenter() -> tuple[TerminalPresenter, StringIO, StringIO]:
    stdout = StringIO()
    stderr = StringIO()
    return (
        TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
        ),
        stdout,
        stderr,
    )


def incomplete_report(
    *reasons: str,
    projections: tuple[ProjectionEvidence, ...] = (),
    cell_results: tuple[CellResult, ...] = (),
    declarations: tuple[RequirementDeclaration, ...] = (),
) -> PackageFloorReportV1:
    generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
    package = PackageIdentity(name="demo", pyproject_path="pyproject.toml")
    snapshot = SourceSnapshotIdentity(digest="snapshot", entries=())
    target_cells = tuple(result.cell for result in cell_results)
    return PackageFloorReportV1(
        report_generation_id=report_generation_id(
            generator=generator,
            package=package,
            source_snapshot=snapshot,
            policy_identity="policy",
            requirement_declarations=declarations,
            target_cells=target_cells,
        ),
        generator=generator,
        package=package,
        source_snapshot=snapshot,
        policy_identity="policy",
        requirement_declarations=declarations,
        candidate_snapshots=(),
        target_cells=target_cells,
        cell_results=cell_results,
        projection_evidence=projections,
        result=IncompleteReportResult(reasons=reasons),
    )


def requirement_declaration(
    declaration_id: str,
    *,
    name: str,
    raw: str,
) -> RequirementDeclaration:
    return RequirementDeclaration(
        declaration_id=declaration_id,
        package="demo",
        location="base",
        name=name,
        source=SourceIdentity(kind="registry"),
        pyproject_path="pyproject.toml",
        raw=raw,
        kind="searchable",
        managed=True,
    )


def attempt_for(
    cell: Cell,
    *,
    resolution: Literal["highest", "lowest-direct", "exact-vector"] = "highest",
    vector: tuple[VersionPin, ...] | None = None,
) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=cell,
            requested_resolution=resolution,
            requested_managed_vector=vector,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )


def cell_indeterminate(
    cell: Cell,
    *,
    cause: FailureCause,
    stage: str,
    process: ProcessResult | None = None,
) -> CellIndeterminate:
    failure = FailurePolicy().classify(
        scope=CellFailureScope(
            package=cell.package,
            cell=cell,
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="policy",
        ),
        cause=cause,
        stage=stage,
        process=process,
        detail=(
            None
            if process is not None
            else FailureDetail(code="terminal", message="index unavailable")
        ),
    )
    return CellIndeterminate(
        cell=cell,
        phase=stage,
        failure_id=failure.failure_id,
        failure_records=(failure,),
    )



def tty_presenter() -> TerminalPresenter:
    return TerminalPresenter(
        stdout=Console(file=StringIO(), force_terminal=True),
        stderr=Console(file=StringIO(), force_terminal=True),
    )


class TestErrorRendering:
    def test_configuration_error_uses_stderr_without_terminal_escape_codes(
        self,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
        )

        exit_code = presenter.render_error(ConfigurationError("unknown key: surprise"))

        assert exit_code == 3
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == "✗ configuration: unknown key: surprise\n"
        assert "\x1b[" not in stderr.getvalue()

    def test_render_error_lists_known_package_candidates(self) -> None:
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_error(
            ConfigurationError(
                "unknown package selection: other",
                candidates=("alpha", "beta", "demo"),
            )
        )

        assert exit_code == 3
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "Error: unknown package selection: other\n"
            "Known packages: alpha, beta, demo\n"
            "Usage: pf COMMAND\n"
            "Try 'pf --help' for more information.\n"
        )

    def test_render_error_truncates_known_package_candidates_after_ten(self) -> None:
        terminal, _, stderr = presenter()
        names = tuple(f"pkg{index:02d}" for index in range(12))

        terminal.render_error(
            ConfigurationError("unknown package selection: other", candidates=names)
        )

        assert stderr.getvalue() == (
            "Error: unknown package selection: other\n"
            "Known packages: pkg00, pkg01, pkg02, pkg03, pkg04, pkg05, pkg06, "
            "pkg07, pkg08, pkg09, ... and 2 more\n"
            "Usage: pf COMMAND\n"
            "Try 'pf --help' for more information.\n"
        )

    def test_infrastructure_error_prints_the_captured_detail(self) -> None:
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_error(
            InfrastructureError(
                "uv could not list available Python versions",
                detail="uv: failed to execute 'uv python list'",
            )
        )

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ infrastructure: uv could not list available Python versions\n"
            "uv: failed to execute 'uv python list'\n"
        )

    def test_render_error_does_not_claim_in_progress_apply_succeeded(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=terminal, force_terminal=True),
        )

        presenter.consume(StatusEvent(message="applying floors"))
        exit_code = presenter.render_error(
            NoApplicableFloorError("cannot apply an incomplete floor report")
        )

        output = visible(terminal.getvalue())
        assert "applied floors" not in output
        assert "✓" not in output
        assert exit_code == 2
        assert (
            "✗ no-applicable-floor: cannot apply an incomplete floor report" in output
        )

    def test_render_error_keeps_completed_steps_and_drops_in_progress(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=terminal, force_terminal=True),
        )

        presenter.consume(StatusEvent(message="loading project"))
        presenter.consume(StatusEvent(message="building snapshot"))
        presenter.render_error(
            ConfigurationError("project source snapshot has drifted since search")
        )

        output = visible(terminal.getvalue())
        assert "✓ loaded project" in output
        assert "built snapshot" not in output
        assert (
            "✗ configuration: project source snapshot has drifted since search"
            in output
        )


class TestProgressRendering:
    def test_progress_is_stable_lines_off_tty_and_dynamic_on_tty(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        first = completed_event(
            cell,
            completed=1,
            total=2,
            status="SUCCESS",
        )
        last = first.model_copy(update={"completed": 2})
        plain = StringIO()
        plain_presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=False, color_system=None),
            stderr=Console(file=plain, force_terminal=False, color_system=None),
        )
        terminal = StringIO()
        tty_presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=terminal, force_terminal=True),
        )

        plain_presenter.consume(first)
        tty_presenter.consume(first)
        tty_presenter.consume(last)
        tty_presenter.close()

        assert plain.getvalue() == "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
        assert "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00" in visible(
            terminal.getvalue()
        )
        assert "╭" in visible(terminal.getvalue())

    def test_completed_cell_log_includes_indented_status_and_diagnostic(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(
            CellStageEvent(cell=cell, stage="static check")
        )
        terminal.consume(
            completed_event(
                cell,
                status="STATIC_REGRESSION",
                process=process_result(
                    exit_code=1,
                    stderr="error: Unresolved import 'missing'",
                ),
                stage="ty",
            )
        )

        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra] failed at static checking\n"
            "error: Unresolved import 'missing'\n"
        )

    def test_completed_cell_with_failure_record_prints_diagnose_and_role_impact(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="lowest-direct")
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(
                exit_code=1, stderr="error: Unresolved import 'missing'"
            ),
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(
            completed_event(
                cell,
                status="REJECTED",
                failure=failure,
                role="declaration",
                stage="test",
            )
        )

        output = stderr.getvalue()
        assert stdout.getvalue() == ""
        assert "failed at testing" in output
        assert "The declared lower bounds did not pass the required checks." in output
        assert f"pf diagnose demo --failure {failure.failure_id}" in output
        assert "STATIC_REGRESSION" not in output
        assert "REJECTED" not in output

    def test_completed_cell_omits_diagnose_when_journal_is_unavailable(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="highest")
        process = process_result(
            exit_code=1,
            stderr="Failed to build `numpy==1.24.0`\nBecause cmake is missing",
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="BUILD_FAILURE",
            stage="install-project",
            process=process,
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(
            completed_event(
                cell,
                status="REJECTED",
                failure=failure,
                process=process,
                role="declaration-capture",
                stage="install-project",
                diagnose_available=False,
            )
        )

        output = stderr.getvalue()
        assert stdout.getvalue() == ""
        assert "failed at installing dependencies" in output
        assert "This version combination could not be built." in output
        assert "pf diagnose" not in output
        assert failure.failure_id not in output
        assert "Failed to build `numpy==1.24.0`" in output

    def test_successful_cell_does_not_print_probe_diagnose(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="exact-vector", vector=())
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(exit_code=1, stderr="1 failed"),
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(SearchFailureEvent(cell=cell, failure=failure))
        terminal.consume(
            completed_event(cell, status="SUCCESS")
        )

        output = stderr.getvalue()
        assert stdout.getvalue() == ""
        assert "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in output
        assert "failed at" not in output
        assert "pf diagnose" not in output
        assert failure.failure_id not in output

    def test_completed_cell_log_collapses_multiline_diagnostics_to_a_summary(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.12",
            extra_surface=(),
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(
            completed_event(
                cell,
                status="BUILD_UNAVAILABLE",
                process=process_result(
                    exit_code=1,
                    stderr=(
                        "Failed to build `numpy==1.24.0`\nBecause cmake is missing"
                    ),
                ),
                stage="install-project",
            )
        )

        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.12][x86_64-unknown-linux-gnu][no-extra] failed at installing dependencies\n"
            "Failed to build `numpy==1.24.0`\n"
            "Because cmake is missing\n"
        )

    def test_cell_matrix_summary_lists_count_and_axes(self) -> None:
        terminal, stdout, stderr = presenter()

        terminal.consume(
            CellMatrixEvent(
                cells=(
                    Cell(
                        package="demo",
                        target="x86_64-unknown-linux-gnu",
                        python_minor="3.12",
                        extra_surface=("cuda",),
                    ),
                    Cell(
                        package="demo",
                        target="x86_64-unknown-linux-gnu",
                        python_minor="3.10",
                        extra_surface=(),
                    ),
                    Cell(
                        package="demo",
                        target="aarch64-apple-darwin",
                        python_minor="3.12",
                        extra_surface=("arrow", "cuda"),
                    ),
                )
            )
        )

        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✓ selected 3 cells\n"
            "  python: 3.10, 3.12\n"
            "  platform: aarch64-apple-darwin, x86_64-unknown-linux-gnu\n"
            "  extra surfaces: no-extra, cuda, arrow+cuda\n"
        )

    def test_tty_setup_facts_render_in_one_rounded_card(self) -> None:
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=stderr, force_terminal=True),
        )
        cells = (
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.11",
                extra_surface=(),
            ),
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.12",
                extra_surface=(),
            ),
        )

        terminal.consume(StatusEvent(message="loading project"))
        terminal.consume(StatusEvent(message="building snapshot"))
        terminal.consume(StatusEvent(message="smoke testing"))
        terminal.consume(CellMatrixEvent(cells=cells))

        output = visible(stderr.getvalue())
        assert output.count("╭") == 1
        assert "✓ loaded project" in output
        assert "✓ built snapshot" in output
        assert "✓ selected 3 cells" in output
        assert "python: 3.10, 3.11, 3.12" in output
        assert "platform: x86_64-unknown-linux-gnu" in output
        assert "extra surfaces: no-extra" in output
        terminal.close()

    def test_tty_live_lifecycle_renders_through_public_events(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM", "xterm-256color")
        cell_a = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        cell_b = cell_a.model_copy(update={"python_minor": "3.11"})
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=stderr, force_terminal=True),
        )

        terminal.consume(StatusEvent(message="searching cells"))
        terminal.consume(CellMatrixEvent(cells=(cell_a, cell_b)))
        terminal.consume(CellStageEvent(cell=cell_a, stage="installing dependencies"))
        terminal.consume(
            completed_event(cell_a, status="SUCCESS", completed=1, total=2)
        )
        terminal.close()

        output = visible(stderr.getvalue())
        assert "✓ selected 2 cells" in output
        assert "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00" in output
        assert "[py3.11][x86_64-unknown-linux-gnu][no-extra]" in output
        assert "1/2" in output
        assert "╭" in output

    def test_tty_stage_and_known_total_render_without_private_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM", "xterm-256color")
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                color_system="standard",
                theme=PF_THEME,
            ),
        )

        terminal.consume(StatusEvent(message="applying floors", completed=1, total=2))
        terminal.consume(CellStageEvent(cell=cell, stage="installing dependencies"))
        terminal.close()

        output = stderr.getvalue()
        plain = visible(output)
        assert "applying floors" in plain
        assert "1/2" in plain
        assert "installing dependencies" in plain
        assert "━" in plain
        stage_at = output.index("installing dependencies")
        assert "\x1b[2m" in output[: stage_at + 1]

    def test_tty_live_cell_renders_structured_baseline_identity(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=stderr, force_terminal=True, theme=PF_THEME),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellContextEvent(
                cell=cell,
                detail=BaselineDetailIdentity(),
            )
        )
        terminal.consume(CellStageEvent(cell=cell, stage="resolving project"))
        terminal.close()

        output = visible(stderr.getvalue())
        assert "[baseline][highest]" in output
        assert output.index("[baseline][highest]") < output.index("resolving project")

    def test_tty_live_cell_renders_search_probe_identity_above_stage(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=stderr, force_terminal=True, theme=PF_THEME),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellContextEvent(
                cell=cell,
                detail=SearchProbeDetailIdentity(
                    dependency="pydantic",
                    version="1.5",
                    lower_version="1.0",
                    upper_version="2.0",
                    candidate_count=7,
                ),
            )
        )
        terminal.consume(CellStageEvent(cell=cell, stage="static check"))
        terminal.consume(CellStageEvent(cell=cell, stage="dynamic tests"))
        terminal.close()

        output = visible(stderr.getvalue())
        identity = "[pydantic==1.5][1.0…2.0 · 7 candidates]"
        assert identity in output
        assert output.rindex(identity) < output.rindex("dynamic tests")

    def test_tty_live_stage_renders_determinate_dot_progress(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=3, total=8, unit="tests"),
            )
        )
        terminal.close()

        output = visible(stderr.getvalue())
        assert "dynamic tests" in output
        assert "3/8 tests" in output
        assert "●" in output
        assert "·" in output

    def test_narrow_tty_keeps_exact_stage_count_when_dots_do_not_fit(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=56),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=56,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=3, total=8, unit="tests"),
            )
        )
        terminal.close()

        output = visible(stderr.getvalue())
        assert "3/8 tests" in output
        assert "●" not in output
        assert "·" not in output

    def test_invalid_stage_progress_can_restore_the_spinner_stage(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=3, total=8, unit="tests"),
            )
        )
        terminal.consume(CellStageEvent(cell=cell, stage="dynamic tests"))
        terminal.close()

        output = visible(stderr.getvalue())
        latest_stage = output[output.rindex("dynamic tests") :]
        assert "3/8 tests" not in latest_stage
        assert "●" not in latest_stage
        assert "·" not in latest_stage



    def test_tty_frozen_failure_card_leads_with_diagnose_details_and_process_summary(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
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
        ty_process = process_result(exit_code=0, stdout="[]")
        check = TyCheck(process=ty_process, diagnostics=())
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=ty_diagnostic_digest(()),
        )
        test_process = process_result(
            stderr=(
                "==================== test session starts ====================\n"
                "collected 3 items\n"
                "FAILED tests/test_cli.py::test_example\n"
                "FAILED tests/test_project.py::test_load\n"
                "=== 2 failed, 1 passed in 0.51s ==="
            )
        )
        baseline = StaticBaseline(
            proposal=proposal,
            ty=check,
            digest=ty_diagnostic_digest(check.diagnostics),
        )
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=test_process),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=test_process,
        )
        logs = RunLogStore(root=tmp_path, run_id="tty-run")
        logs.record(
            2,
            ProcessSpec(
                argv=("pytest",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            test_process,
        )
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
                width=120,
            ),
            logs=logs,
            root=tmp_path,
        )

        exit_code = terminal.render_smoke(
            SmokeBaselineRejection(
                outcomes=(
                    BaselineRejection(
                        attempt=attempt,
                        failure=failure,
                        static_baseline=baseline,
                        evaluation=evaluation,
                    ),
                )
            )
        )

        output = stderr.getvalue()
        plain = visible(output)
        stripped = "".join(" " if ch in "│╭╮╰╯─" else ch for ch in plain)
        collapsed = " ".join(stripped.split())
        diagnose = f"`pf diagnose demo --failure {failure.failure_id}`"
        assert exit_code == 1
        assert "╭" in plain
        assert "failed at testing" in collapsed
        assert diagnose in collapsed
        assert "-> run" in collapsed
        assert "-> see" in collapsed
        assert "for more information" in collapsed
        assert "The full test command failed for this version combination." in collapsed
        assert (
            "The highest-version resolution did not pass the required checks."
            in collapsed
        )
        assert ".pf/logs/tty-run/process-0002.log" in collapsed
        assert "details." in collapsed
        assert "test session starts" not in collapsed
        assert "collected 3 items" not in collapsed
        assert "FAILED tests/test_cli.py::test_example" in collapsed
        assert "FAILED tests/test_project.py::test_load" in collapsed
        assert "=== 2 failed, 1 passed in 0.51s ===" in collapsed
        assert collapsed.index("failed at testing") < collapsed.index(
            "The full test command failed for this version combination."
        )
        assert collapsed.index("The full test command failed") < collapsed.index(
            "for more information"
        )
        assert collapsed.index("for more information") < collapsed.index(
            "FAILED tests/test_cli.py"
        )
        assert collapsed.index("=== 2 failed, 1 passed in 0.51s ===") < collapsed.index(
            "details."
        )
        assert "31" in output
        path_at = output.index(".pf/logs/tty-run/process-0002.log")
        assert "34" in output[:path_at]








    def test_non_tty_hides_process_activity_behind_run_logs(self) -> None:
        terminal, stdout, stderr = presenter()

        terminal.consume(StatusEvent(message="loading project"))
        terminal.consume(
            ProcessEvent(
                process_id=1,
                argv=("uv", "python", "list", "--output-format", "json"),
                state="started",
            )
        )
        terminal.consume(
            ProcessEvent(
                process_id=1,
                argv=("uv", "python", "list", "--output-format", "json"),
                state="finished",
                duration_seconds=0.4,
            )
        )

        terminal.consume(
            ProcessEvent(
                process_id=2,
                argv=("mystery",),
                state="finished",
            )
        )

        assert stdout.getvalue() == ""
        assert stderr.getvalue() == "loading project\n"




    def test_tty_completed_status_checkmark_is_green(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=StringIO(),
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )

        presenter.consume(StatusEvent(message="loading project"))
        presenter.consume(StatusEvent(message="building snapshot"))
        presenter.consume(
            CellMatrixEvent(
                cells=(
                    Cell(
                        package="demo",
                        target="x86_64-unknown-linux-gnu",
                        python_minor="3.10",
                        extra_surface=(),
                    ),
                )
            )
        )

        output = terminal.getvalue()
        assert "✓ loaded project" in visible(output)
        check_at = output.index("✓")
        assert "\x1b[32m" in output[: check_at + 1]

    def test_tty_matrix_axis_lines_are_dim(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )
        presenter.consume(
            CellMatrixEvent(
                cells=(
                    Cell(
                        package="demo",
                        target="x86_64-unknown-linux-gnu",
                        python_minor="3.10",
                        extra_surface=(),
                    ),
                )
            )
        )

        output = terminal.getvalue()
        python_at = output.index("python:")
        assert "\x1b[2m" in output[: python_at + 1]
        plain = visible(output)
        assert "✓ selected 1 cell" in plain
        assert "python: 3.10" in plain
        assert "platform: x86_64-unknown-linux-gnu" in plain
        assert "extra surfaces: no-extra" in plain
        assert "╭" in plain

    def test_tty_completed_cell_log_keeps_dim_status_and_diagnostic(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )

        presenter.consume(StatusEvent(message="checking declarations"))
        presenter.consume(
            CellStageEvent(cell=cell, stage="static check")
        )
        presenter.consume(
            completed_event(
                cell,
                status="STATIC_REGRESSION",
                process=process_result(
                    exit_code=1,
                    stderr="error: Unresolved import 'missing'",
                ),
                stage="ty",
            )
        )

        output = terminal.getvalue()
        plain = visible(output)
        assert "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in plain
        assert "failed at static checking" in plain
        assert "0:00:00" in plain
        assert "error: Unresolved import 'missing'" in plain
        assert "  error: Unresolved import 'missing'" not in plain
        assert "STATIC_REGRESSION" not in plain
        assert "╭" in plain
        title_at = output.index("[py3.10]")
        assert "31" in output[:title_at]

    def test_tty_failed_progress_uses_a_red_cross(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=StringIO(),
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )

        presenter.consume(StatusEvent(message="checking declarations"))
        presenter.consume(
            completed_event(
                cell,
                status="STATIC_REGRESSION",
                stage="ty",
            )
        )

        output = terminal.getvalue()
        plain = visible(output)
        assert "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in plain
        assert "STATIC_REGRESSION" not in plain
        assert "checked declarations" not in plain
        cross_at = output.index("✗")
        assert "31" in output[: cross_at + 1]

    def test_tty_warning_progress_uses_a_warning_icon(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=StringIO(),
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )

        presenter.consume(StatusEvent(message="searching cells"))
        presenter.consume(
            completed_event(cell, status="NO_PASS_IN_SEARCH_SPACE")
        )

        output = terminal.getvalue()
        plain = visible(output)
        assert "⚠ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in plain
        assert "NO_PASS_IN_SEARCH_SPACE" not in plain
        assert "searched cells" not in plain
        warn_at = output.index("⚠")
        assert "33" in output[: warn_at + 1]

    def test_tty_process_log_path_is_a_local_file_hyperlink(
        self, tmp_path: Path
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()
        logs = RunLogStore(root=tmp_path, run_id="linked-run")
        process = process_result(stderr="test process failed")
        path = logs.record(
            1,
            ProcessSpec(
                argv=("pytest",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            process,
        )
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=True, color_system="truecolor"),
            stderr=Console(file=stderr, force_terminal=True, color_system="truecolor"),
            logs=logs,
            root=tmp_path,
        )

        terminal.render_check(
            CheckIndeterminate(
                failure=recorded_failure(
                    cause="TOOL_FAILURE",
                    stage="test",
                    process=process,
                )
            )
        )

        assert "\x1b]8;" in stderr.getvalue()
        assert path.resolve().as_uri() in stderr.getvalue()
        assert ".pf/logs/linked-run/process-0001.log" in visible(stderr.getvalue())


class TestVerificationRendering:
    @pytest.mark.parametrize(
        ("result", "expected_exit", "fragments"),
        (
            (
                CheckCompatibilityFailure(evaluations=()),
                1,
                ("Check failed", "lower bounds are incompatible", "0 cells"),
            ),
            (
                CheckIndeterminate(
                    failure=recorded_failure(
                        cause="TIMEOUT",
                        stage="test",
                        process=process_result(
                            stderr="timeout",
                            timed_out=True,
                            start_error="timeout",
                        ),
                    )
                ),
                4,
                ("Check indeterminate", "timed out", "compatibility is unknown"),
            ),
        ),
    )
    def test_check_failures_have_stable_exit_codes(
        self,
        result: CheckCompatibilityFailure | CheckIndeterminate,
        expected_exit: int,
        fragments: tuple[str, ...],
    ) -> None:
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_check(result)

        assert exit_code == expected_exit
        assert stdout.getvalue() == ""
        assert all(fragment in stderr.getvalue() for fragment in fragments)

    def test_check_indeterminate_prints_a_short_reason_and_log_link(
        self,
        tmp_path: Path,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()
        logs = RunLogStore(root=tmp_path, run_id="test-run")
        process = process_result(
            stderr=(
                "No solution found when resolving dependencies:\nbecause tomli==2.0.0"
            ),
        )
        logs.record(
            1,
            ProcessSpec(
                argv=("uv", "pip", "install"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            process,
        )
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
            logs=logs,
            root=tmp_path,
        )

        exit_code = terminal.render_check(
            CheckIndeterminate(
                failure=recorded_failure(
                    cause="RESOLUTION_CONFLICT",
                    stage="install-harness",
                    process=process,
                )
            )
        )

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "! Check indeterminate · This version combination has conflicting dependency requirements and cannot be installed. · 0 cells\n"
            "  No solution found when resolving dependencies: because tomli==2.0.0\n"
            "  details: .pf/logs/test-run/process-0001.log\n"
        )

    def test_smoke_test_failure_prints_dynamic_summary_and_log_link(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
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
        ty_process = process_result(exit_code=0, stdout="[]")
        check = TyCheck(process=ty_process, diagnostics=())
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=ty_diagnostic_digest(()),
        )
        test_process = process_result(stderr="1 failed\n2 passed")
        baseline = StaticBaseline(
            proposal=proposal,
            ty=check,
            digest=ty_diagnostic_digest(check.diagnostics),
        )
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=test_process),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=test_process,
        )
        logs = RunLogStore(root=tmp_path, run_id="smoke-run")
        logs.record(
            2,
            ProcessSpec(
                argv=("pytest",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            test_process,
        )
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
            logs=logs,
            root=tmp_path,
        )

        exit_code = terminal.render_smoke(
            SmokeBaselineRejection(
                outcomes=(
                    BaselineRejection(
                        attempt=attempt,
                        failure=failure,
                        static_baseline=baseline,
                        evaluation=evaluation,
                    ),
                )
            )
        )

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.11][x86_64-unknown-linux-gnu][no-extra] failed at testing\n"
            "The full test command failed for this version combination.\n"
            "The highest-version resolution did not pass the required checks.\n"
            f"-> run `pf diagnose demo --failure {failure.failure_id}` for more information.\n"
            "1 failed\n"
            "2 passed\n"
            "-> see .pf/logs/smoke-run/process-0002.log for details.\n"
            "✗ Smoke failed · highest-version resolution did not pass · 1 cell\n"
        )

    @pytest.mark.parametrize(
        ("adapter_stage", "failed_at"),
        (
            ("install", "installing dependencies"),
            ("install-harness", "installing harness"),
            ("ty", "static checking"),
            ("test", "testing"),
        ),
    )
    def test_smoke_tool_failures_use_stable_user_stage_names(
        self,
        adapter_stage: str,
        failed_at: str,
    ) -> None:
        terminal, stdout, stderr = presenter()
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TOOL_FAILURE",
            stage=adapter_stage,
            process=process_result(stderr="tool failed"),
        )

        exit_code = terminal.render_smoke(
            SmokeIndeterminate(
                outcomes=(BaselineIndeterminate(attempt=attempt, failure=failure),)
            )
        )

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert f"failed at {failed_at}" in stderr.getvalue()
        assert (
            "PF could not complete a verification tool operation reliably."
            in stderr.getvalue()
        )
        assert "this candidate" not in stderr.getvalue()
        assert "TOOL_FAILURE" not in stderr.getvalue()
        assert "BASELINE_INDETERMINATE" not in stderr.getvalue()

    def test_smoke_ty_diagnostics_are_warnings_with_one_line_summaries(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
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
            identity="snapshot|src/demo.py|4|7|invalid-type",
            origin="snapshot",
            path="src/demo.py",
            line=4,
            column=7,
            code="invalid-type",
            severity="major",
            message="Expected str,\n  found int",
        )
        process = process_result(exit_code=1, stdout="[]")
        logs = RunLogStore(root=tmp_path, run_id="ty-run")
        logs.record(
            3,
            ProcessSpec(
                argv=("ty", "check"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            process,
        )
        check = TyCheck(process=process, diagnostics=(diagnostic,))
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=ty_diagnostic_digest(check.diagnostics),
        )
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
            logs=logs,
            root=tmp_path,
        )

        exit_code = terminal.render_smoke(
            SmokePass(
                outcomes=(
                    HighestVersionPass(
                        attempt=attempt,
                        harness_baseline=empty_harness_baseline(
                            attempt.identity.cell
                        ),
                        baseline=StaticBaseline(
                            proposal=proposal,
                            ty=check,
                            digest=ty_diagnostic_digest(check.diagnostics),
                        ),
                        evaluation=PassEvaluation(
                            proposal=proposal,
                            static=static,
                            test=TestPass(
                                process=process.model_copy(update={"exit_code": 0})
                            ),
                        ),
                    ),
                )
            )
        )

        assert exit_code == 0
        assert stdout.getvalue() == "✓ Smoke passed · 1 cell\n"
        assert stderr.getvalue() == (
            "⚠ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
            "src/demo.py:4:7 [invalid-type] Expected str, found int\n"
            "-> see .pf/logs/ty-run/process-0003.log for details.\n"
        )

    def test_check_reuses_ty_warning_summaries(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        proposal = Proposal(
            proposal_id="lowest",
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        diagnostic = TyDiagnostic(
            identity="external|site-packages/demo.pyi|invalid-return-type",
            origin="external",
            path="site-packages/demo.pyi",
            line=None,
            column=None,
            code="invalid-return-type",
            severity="major",
            message="Returned int instead of str",
        )
        process = process_result(exit_code=1, stdout="[]")
        check = TyCheck(process=process, diagnostics=(diagnostic,))
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=ty_diagnostic_digest(check.diagnostics),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_check(
            CheckPass(
                evaluations=(
                    PassEvaluation(
                        proposal=proposal,
                        static=static,
                        test=TestPass(
                            process=process.model_copy(update={"exit_code": 0})
                        ),
                    ),
                )
            )
        )

        assert exit_code == 0
        assert stdout.getvalue() == "✓ Check passed · 1 cell\n"
        assert stderr.getvalue() == (
            "⚠ [py3.11][x86_64-unknown-linux-gnu][no-extra]\n"
            "site-packages/demo.pyi [invalid-return-type] Returned int instead of str\n"
        )

    def test_check_runtime_failure_summarizes_static_increment(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        proposal = Proposal(
            proposal_id="lowest",
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        existing = TyDiagnostic(
            identity="snapshot|demo.py|1|1|existing",
            origin="snapshot",
            path="demo.py",
            line=1,
            column=1,
            code="existing",
            severity="major",
            message="existing diagnostic",
        )
        increment = TyDiagnostic(
            identity="snapshot|demo.py|9|2|dependency-regression",
            origin="snapshot",
            path="demo.py",
            line=9,
            column=2,
            code="dependency-regression",
            severity="major",
            message="new dependency regression",
        )
        process = process_result(exit_code=1, stdout="[]")
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=(existing, increment)),
            baseline_digest=ty_diagnostic_digest((existing,)),
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=general_classifications(increment),
        )
        terminal, stdout, stderr = presenter()
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=process),
        )

        exit_code = terminal.render_check(
            CheckCompatibilityFailure(evaluations=(evaluation,))
        )

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.11][x86_64-unknown-linux-gnu][no-extra] failed at testing\n"
            "demo.py:9:2 [dependency-regression] new dependency regression\n"
            "✗ Check failed · declared lower bounds are incompatible · 1 cell\n"
        )

    def test_check_does_not_repeat_diagnostics_already_frozen_from_progress(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        proposal = Proposal(
            proposal_id="lowest",
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        increment = TyDiagnostic(
            identity="snapshot|demo.py|9|2|dependency-regression",
            origin="snapshot",
            path="demo.py",
            line=9,
            column=2,
            code="dependency-regression",
            severity="major",
            message="new dependency regression",
        )
        process = process_result(exit_code=1, stdout="[]")
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=(increment,)),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=general_classifications(increment),
        )
        terminal, stdout, stderr = presenter()
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=process),
        )
        terminal.consume(
            completed_event(
                cell,
                status="TEST_FAIL",
                diagnostics=(increment,),
                process=process,
                stage="test",
            )
        )

        exit_code = terminal.render_check(
            CheckCompatibilityFailure(evaluations=(evaluation,))
        )

        assert exit_code == 1
        assert stdout.getvalue() == ""
        output = stderr.getvalue()
        assert output.count("demo.py:9:2 [dependency-regression]") == 1
        assert "STATIC_REGRESSION" not in output
        assert "ty: 1 new diagnostic" not in output
        assert output.endswith(
            "✗ Check failed · declared lower bounds are incompatible · 1 cell\n"
        )

    def test_smoke_live_completion_uses_smoke_baseline_impact(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(exit_code=1, stderr="1 failed"),
        )
        terminal, _, stderr = presenter()
        terminal.bind_command("smoke")

        terminal.consume(
            completed_event(
                cell,
                status="BASELINE_REJECTION",
                failure=failure,
                role="baseline",
                stage="test",
            )
        )

        output = stderr.getvalue()
        assert (
            "The highest-version resolution did not pass the required checks." in output
        )
        assert "did not start the floor search" not in output


class TestSearchRendering:
    def test_search_candidate_diagnostics_use_stage_summaries_and_log_links(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="exact-vector", vector=())
        proposal = Proposal(
            proposal_id="candidate",
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        static_process = process_result(exit_code=1, stdout="[]")
        increment = TyDiagnostic(
            identity="snapshot|demo.py|4|2|bad-argument-type",
            origin="snapshot",
            path="demo.py",
            line=4,
            column=2,
            code="bad-argument-type",
            severity="error",
            message="argument has the wrong type",
        )
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=static_process, diagnostics=(increment,)),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=general_classifications(increment),
        )
        dynamic_process = process_result(stderr="1 failed\n2 passed")
        dynamic = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=dynamic_process),
        )
        install_process = process_result(stderr="No solution found\nconflicting pins")
        install = ToolFailure(
            cause="HARNESS_CONFLICT",
            stage="install-harness",
            process=install_process,
        )
        logs = RunLogStore(root=tmp_path, run_id="search-run")
        for process_id, process in enumerate(
            (static_process, dynamic_process, install_process), start=1
        ):
            logs.record(
                process_id,
                ProcessSpec(
                    argv=("tool",),
                    cwd=tmp_path.as_posix(),
                    timeout_seconds=10,
                ),
                process,
            )
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
            logs=logs,
            root=tmp_path,
        )
        dynamic_failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=dynamic_process,
        )
        install_failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause=install.cause,
            stage=install.stage,
            process=install.process,
        )
        terminal.consume(
            SearchFailureEvent(cell=cell, failure=dynamic_failure, evaluation=dynamic)
        )
        terminal.consume(SearchFailureEvent(cell=cell, failure=install_failure))

        exit_code = terminal.render_search(
            (incomplete_report("NO_PASS_IN_SEARCH_SPACE"),)
        )

        output = stderr.getvalue()
        assert exit_code == 2
        assert "demo.py:4:2 [bad-argument-type] argument has the wrong type" in output
        assert "The full test command failed for this version combination." in output
        assert "test dependencies cannot be installed" in output
        assert "RESOLUTION_CONFLICT" not in output
        assert output.count(".pf/logs/search-run/") == 2
        assert output.count("for details.") == 2

    @pytest.mark.parametrize(
        ("reasons", "expected_exit"),
        (
            ((), 0),
            (("BASELINE_REJECTION",), 1),
            (("INDETERMINATE",), 4),
            (("BASELINE_REJECTION", "INDETERMINATE"), 1),
            (("NO_PASS_IN_SEARCH_SPACE",), 2),
        ),
    )
    def test_search_reasons_determine_the_exit_code(
        self,
        reasons: tuple[str, ...],
        expected_exit: int,
    ) -> None:
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((incomplete_report(*reasons),))

        assert exit_code == expected_exit
        expected_stderr: dict[tuple[str, ...], str] = {
            (): "",
            ("BASELINE_REJECTION",): (
                "✗ Search stopped · highest-version baseline did not pass · 1 report written\n"
            ),
            ("INDETERMINATE",): (
                "! Search stopped · compatibility is unknown · 1 report written\n"
            ),
            ("BASELINE_REJECTION", "INDETERMINATE"): (
                "✗ Search stopped · highest-version baseline did not pass · 1 report written\n"
            ),
            ("NO_PASS_IN_SEARCH_SPACE",): (
                "⚠ Search incomplete · 1 report written · no applicable floor\n"
            ),
        }
        if not reasons:
            assert stderr.getvalue() == ""
            assert (
                stdout.getvalue()
                == "✓ Search complete · 1 report · package-floor.json\n"
            )
        else:
            assert stdout.getvalue() == ""
            assert stderr.getvalue() == expected_stderr[reasons]

    def test_search_baseline_rejection_prints_user_guidance(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="HARNESS_CONFLICT",
            stage="resolve-environment",
            process=process_result(
                stderr="No solution found when resolving dependencies",
            ),
        )
        report = incomplete_report(
            "BASELINE_REJECTION",
            cell_results=(BaselineRejection(attempt=attempt, failure=failure),),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((report,))

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra] failed at resolving the test environment\n"
            "The test dependencies cannot be installed without changing the versions being checked.\n"
            "The highest-version baseline did not pass, so PF did not start the floor search for this cell.\n"
            f"-> run `pf diagnose demo --failure {failure.failure_id}` for more information.\n"
            "No solution found when resolving dependencies\n"
            "✗ Search stopped · highest-version baseline did not pass · 1 report written\n"
        )

    def test_search_infra_failure_prints_message_detail_without_a_process(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        terminal_result = cell_indeterminate(
            cell,
            cause="SOURCE_FAILURE",
            stage="candidate-discovery",
        )
        report = incomplete_report(
            "INDETERMINATE",
            cell_results=(terminal_result,),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((report,))

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "! [py3.10][x86_64-unknown-linux-gnu][no-extra] failed at candidate discovery\n"
            "PF could not reach or read a configured package source.\n"
            "PF could not obtain the information needed to start or continue this cell.\n"
            f"-> run `pf diagnose demo --failure {terminal_result.failure_id}` for more information.\n"
            "! Search stopped · compatibility is unknown · 1 report written\n"
        )

    def test_search_probe_indeterminate_prints_candidate_unknown_impact(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(
            cell,
            resolution="exact-vector",
            vector=(VersionPin(name="idna", version="2.0"),),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TIMEOUT",
            stage="test",
            process=process_result(timed_out=True),
        )
        report = incomplete_report(
            "INDETERMINATE",
            cell_results=(
                CellIndeterminate(
                    cell=cell,
                    phase="test",
                    failure_id=failure.failure_id,
                    failure_records=(failure,),
                ),
            ),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((report,))

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "! [py3.10][x86_64-unknown-linux-gnu][no-extra] failed at testing\n"
            "The operation timed out, so compatibility is unknown.\n"
            "PF could not determine whether this candidate works, so it stopped this cell.\n"
            f"-> run `pf diagnose demo --failure {failure.failure_id}` for more information.\n"
            "process timed out\n"
            "! Search stopped · compatibility is unknown · 1 report written\n"
        )

    def test_search_reuses_highest_baseline_ty_warning_summaries(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
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
            identity="snapshot|demo.py|3|unresolved-reference",
            origin="snapshot",
            path="demo.py",
            line=3,
            column=None,
            code="unresolved-reference",
            severity="major",
            message="Name is not defined",
        )
        process = process_result(exit_code=1, stdout="[]")
        check = TyCheck(process=process, diagnostics=(diagnostic,))
        baseline = StaticBaseline(
            proposal=proposal,
            ty=check,
            digest=ty_diagnostic_digest(check.diagnostics),
        )
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=baseline.digest,
        )
        test_process = process_result(stderr="1 failed, 2 passed")
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=test_process),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=test_process,
        )
        report = incomplete_report(
            "BASELINE_REJECTION",
            cell_results=(
                BaselineRejection(
                    attempt=attempt,
                    failure=failure,
                    static_baseline=baseline,
                    evaluation=evaluation,
                ),
            ),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((report,))

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra] failed at testing\n"
            "The full test command failed for this version combination.\n"
            "The highest-version baseline did not pass, so PF did not start the floor search for this cell.\n"
            f"-> run `pf diagnose demo --failure {failure.failure_id}` for more information.\n"
            "1 failed, 2 passed\n"
            "demo.py:3 [unresolved-reference] Name is not defined\n"
            "✗ Search stopped · highest-version baseline did not pass · 1 report written\n"
        )


class TestExplainRendering:
    def test_explain_renders_incomplete_reasons_and_projection_requirements(
        self,
    ) -> None:
        report = incomplete_report(
            "MISSING_CELL",
            declarations=(
                requirement_declaration(
                    "demo:dependencies:foo",
                    name="foo",
                    raw="foo>=1",
                ),
                requirement_declaration(
                    "demo:dependencies:bar",
                    name="bar",
                    raw="bar>=2",
                ),
            ),
            projections=(
                ProjectionEvidence(
                    declaration_id="demo:dependencies:foo",
                    floors=(),
                    projected_requirements=("foo>=1",),
                    representable=True,
                ),
                ProjectionEvidence(
                    declaration_id="demo:dependencies:bar",
                    floors=(),
                    projected_requirements=(),
                    representable=False,
                ),
            ),
        )
        terminal, stdout, _ = presenter()

        exit_code = terminal.render_explain((report,))

        assert exit_code == 0
        rendered = stdout.getvalue()
        assert "demo · package-floor.json" in rendered
        assert "Status: incomplete" in rendered
        assert "Apply: not authorized by this report" in rendered
        assert "foo>=1" in rendered
        assert "projection blocked" in rendered
        assert "Summary: report is incomplete and cannot be applied." in rendered
        assert "Apply: ready" not in rendered
        assert "reasons: MISSING_CELL" not in rendered
        assert "demo:dependencies:foo" not in rendered
        assert "demo:dependencies:bar" not in rendered

    def test_explain_does_not_silently_use_declaration_digest(self) -> None:
        digest = "a" * 64
        report = incomplete_report(
            "MISSING_CELL",
            projections=(
                ProjectionEvidence(
                    declaration_id=digest,
                    floors=(),
                    projected_requirements=("foo>=1",),
                    representable=True,
                ),
            ),
        )
        terminal, stdout, _ = presenter()

        with pytest.raises(
            ConfigurationError,
            match="report projection is missing its requirement declaration",
        ):
            terminal.render_explain((report,))

        assert digest not in stdout.getvalue()

    def test_explain_distinguishes_baseline_diagnostics_from_static_increments(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        baseline_attempt = attempt_for(cell)
        baseline_proposal = Proposal(
            proposal_id="highest",
            attempt_id=baseline_attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        candidate_vector = (VersionPin(name="demo-dep", version="1"),)
        candidate_attempt = attempt_for(
            cell,
            resolution="exact-vector",
            vector=candidate_vector,
        )
        proposal = Proposal(
            proposal_id="candidate",
            attempt_id=candidate_attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=candidate_vector,
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        existing = TyDiagnostic(
            identity="snapshot|demo.py|1|1|existing-error",
            origin="snapshot",
            path="demo.py",
            line=1,
            column=1,
            code="existing-error",
            severity="major",
            message="existing project error",
        )
        increment = TyDiagnostic(
            identity="snapshot|demo.py|2|1|dependency-regression",
            origin="snapshot",
            path="demo.py",
            line=2,
            column=1,
            code="dependency-regression",
            severity="major",
            message="dependency API is unavailable",
        )
        process = process_result(stdout="[]")
        baseline = StaticBaseline(
            proposal=baseline_proposal,
            ty=TyCheck(process=process, diagnostics=(existing,)),
            digest=ty_diagnostic_digest((existing,)),
        )
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=(existing, increment)),
            baseline_digest=baseline.digest,
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=general_classifications(increment),
        )
        runtime_failure = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=process),
        )
        rejection = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=candidate_attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=runtime_failure.test.process,
        )
        baseline_static = StaticUnchangedEvaluation(
            proposal=baseline_proposal,
            ty=baseline.ty,
            baseline_digest=baseline.digest,
        )
        failure = CellSearchFailure(
            reason="NO_PASS_IN_SEARCH_SPACE",
            cell=cell,
            phase="runtime-search",
            baseline_attempt=baseline_attempt,
            static_baseline=baseline,
            baseline=PassEvaluation(
                proposal=baseline_proposal,
                static=baseline_static,
                test=TestPass(process=process.model_copy(update={"exit_code": 0})),
            ),
            failure_records=(rejection,),
            coordinate_failure=CoordinateFailure(
                status="NO_PASS_IN_SEARCH_SPACE",
                observations=(
                    ProbeObservation(
                        dependency="demo-dep",
                        candidate_version="1",
                        vector=candidate_vector,
                        evidence=ProbeRejection(
                            attempt=candidate_attempt,
                            proposal_id=proposal.proposal_id,
                            failure_id=rejection.failure_id,
                            cause="TEST_FAILURE",
                            evaluation=runtime_failure,
                        ),
                    ),
                ),
            ),
        )
        report = incomplete_report(
            "NO_PASS_IN_SEARCH_SPACE",
            cell_results=(failure,),
        )
        terminal, stdout, _ = presenter()

        exit_code = terminal.render_explain((report,))

        assert exit_code == 0
        rendered = stdout.getvalue()
        assert "Apply: not authorized by this report" in rendered
        assert "What happened:" in rendered
        assert (
            f"Diagnose: pf diagnose demo --failure {rejection.failure_id}" in rendered
        )
        assert "ty baseline: 1 diagnostic" in rendered
        assert (
            "+ demo.py:2:1 [dependency-regression] dependency API is unavailable"
            in rendered
        )
        assert "NO_PASS_IN_SEARCH_SPACE" not in rendered
        assert "Apply: ready" not in rendered

    def test_explain_folds_repeated_diagnostics_and_caps_unique_lines(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        baseline_attempt = attempt_for(cell)
        baseline_proposal = Proposal(
            proposal_id="highest",
            attempt_id=baseline_attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        candidate_vector = (VersionPin(name="demo-dep", version="1"),)
        candidate_attempt = attempt_for(
            cell,
            resolution="exact-vector",
            vector=candidate_vector,
        )
        proposal = Proposal(
            proposal_id="candidate",
            attempt_id=candidate_attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=candidate_vector,
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        existing = TyDiagnostic(
            identity="snapshot|demo.py|1|1|existing-error",
            origin="snapshot",
            path="demo.py",
            line=1,
            column=1,
            code="existing-error",
            severity="major",
            message="existing project error",
        )
        process = process_result(stdout="[]")
        baseline = StaticBaseline(
            proposal=baseline_proposal,
            ty=TyCheck(process=process, diagnostics=(existing,)),
            digest=ty_diagnostic_digest((existing,)),
        )
        repeated = TyDiagnostic(
            identity="snapshot|demo.py|2|1|dependency-regression",
            origin="snapshot",
            path="demo.py",
            line=2,
            column=1,
            code="dependency-regression",
            severity="major",
            message="dependency API is unavailable",
        )
        extras = tuple(
            TyDiagnostic(
                identity=f"snapshot|demo.py|{index}|1|extra-{index}",
                origin="snapshot",
                path="demo.py",
                line=index,
                column=1,
                code=f"extra-{index}",
                severity="major",
                message=f"extra diagnostic {index}",
            )
            for index in range(3, 14)
        )
        incremental: tuple[TyDiagnostic, ...] = tuple(
            sorted(
                (repeated, repeated, repeated, *extras),
                key=lambda item: item.identity,
            )
        )
        diagnostics: tuple[TyDiagnostic, ...] = tuple(
            sorted((existing, *incremental), key=lambda item: item.identity)
        )
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=diagnostics),
            baseline_digest=baseline.digest,
            incremental=incremental,
            static_fingerprint=static_fingerprint(
                tuple(item.identity for item in incremental)
            ),
            classifications=general_classifications(*incremental),
        )
        runtime_failure = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=process),
        )
        rejection = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=candidate_attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=runtime_failure.test.process,
        )
        baseline_static = StaticUnchangedEvaluation(
            proposal=baseline_proposal,
            ty=baseline.ty,
            baseline_digest=baseline.digest,
        )
        failure = CellSearchFailure(
            reason="NO_PASS_IN_SEARCH_SPACE",
            cell=cell,
            phase="runtime-search",
            baseline_attempt=baseline_attempt,
            static_baseline=baseline,
            baseline=PassEvaluation(
                proposal=baseline_proposal,
                static=baseline_static,
                test=TestPass(process=process.model_copy(update={"exit_code": 0})),
            ),
            failure_records=(rejection,),
            coordinate_failure=CoordinateFailure(
                status="NO_PASS_IN_SEARCH_SPACE",
                observations=(
                    ProbeObservation(
                        dependency="demo-dep",
                        candidate_version="1",
                        vector=candidate_vector,
                        evidence=ProbeRejection(
                            attempt=candidate_attempt,
                            proposal_id=proposal.proposal_id,
                            failure_id=rejection.failure_id,
                            cause="TEST_FAILURE",
                            evaluation=runtime_failure,
                        ),
                    ),
                ),
            ),
        )
        terminal, stdout, _ = presenter()

        terminal.render_explain(
            (incomplete_report("NO_PASS_IN_SEARCH_SPACE", cell_results=(failure,)),)
        )

        rendered = stdout.getvalue()
        assert "×3" in rendered
        assert "extra diagnostic 3" in rendered
        assert "... and 2 more unique diagnostics" in rendered
        assert "extra diagnostic 9" not in rendered
        assert "Diagnose: pf diagnose demo" in rendered

    @pytest.mark.parametrize("width", (56, 80, 120))
    def test_explain_keeps_required_fields_readable_at_common_widths(
        self, width: int
    ) -> None:
        report = incomplete_report("MISSING_CELL")
        stdout = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                color_system=None,
                width=width,
            ),
            stderr=Console(
                file=StringIO(),
                force_terminal=True,
                color_system=None,
                width=width,
            ),
        )

        terminal.render_explain((report,))

        rendered = stdout.getvalue()
        assert "demo · package-floor.json" in rendered
        assert "Status: incomplete" in rendered
        assert "Apply: not authorized by this report" in rendered
        assert "Summary:" in rendered
        for line in rendered.splitlines():
            assert len(line) <= width

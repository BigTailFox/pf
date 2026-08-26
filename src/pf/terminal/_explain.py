from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.text import Text

from pf.errors import ConfigurationError
from pf.schemas.evaluation import (
    AttemptFailureScope,
    FailureRecord,
    StaticRegressionEvaluation,
    TyDiagnostic,
)
from pf.schemas.project import Cell
from pf.schemas.report import (
    CellIndeterminate,
    CellSearchFailure,
    CellSuccess,
)
from pf.report import ValidatedReport

_UNIQUE_DIAGNOSTIC_LIMIT = 10


class FailureView(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def impact(self) -> str: ...


class ExplainPresenter(Protocol):
    stdout: Console

    def close(self, *, abandon_pending: bool = False) -> None: ...

    def failure_presentation(self, failure: FailureRecord) -> FailureView: ...


def render(
    presenter: ExplainPresenter,
    reports: tuple[ValidatedReport, ...],
) -> int:
    presenter.close()
    if not reports:
        presenter.stdout.print("explained 0 reports")
        return 0
    for index, report in enumerate(reports):
        if index:
            presenter.stdout.print()
        _render_report(presenter, report)
    return 0


def _render_report(
    presenter: ExplainPresenter,
    report: ValidatedReport,
) -> None:
    declarations = {
        item.declaration_id: item for item in report.requirement_declarations
    }
    if any(
        projection.declaration_id not in declarations
        for projection in report.projection_evidence
    ):
        raise ConfigurationError(
            "report projection is missing its requirement declaration"
        )
    complete = report.result.status == "complete"
    covered = sum(
        1 for result in report.cell_results if isinstance(result, CellSuccess)
    )
    targets = len(report.target_cells) or len(report.cell_results)
    presenter.stdout.print(Text(f"{report.package.name} · {_report_path(report)}"))
    presenter.stdout.print(Text(f"Status: {report.result.status}"))
    presenter.stdout.print(
        "Apply: authorized by this report"
        if complete
        else "Apply: not authorized by this report"
    )
    presenter.stdout.print(Text(f"Cells: {covered}/{targets} covered"))
    if report.requirement_declarations or report.projection_evidence:
        presenter.stdout.print()
        presenter.stdout.print("Requirements")
        projected_ids = {
            projection.declaration_id for projection in report.projection_evidence
        }
        for projection in report.projection_evidence:
            declaration = declarations[projection.declaration_id]
            label = declaration.raw or declaration.name
            if not projection.representable:
                detail = "projection blocked"
            elif not projection.projected_requirements:
                detail = "no applicable floor"
            else:
                detail = f"-> {'; '.join(projection.projected_requirements)}"
            presenter.stdout.print(Text(f"  {label}   {detail}"))
        for declaration in report.requirement_declarations:
            if declaration.declaration_id not in projected_ids:
                presenter.stdout.print(Text(f"  {declaration.raw or declaration.name}"))
    failures = report.failure_records
    if failures or (
        report.result.status == "incomplete" and "MISSING_CELL" in report.result.reasons
    ):
        presenter.stdout.print()
        presenter.stdout.print("Blockers")
        if failures:
            grouped: dict[tuple[str, str], list[FailureRecord]] = {}
            for failure in failures:
                presentation = presenter.failure_presentation(failure)
                grouped.setdefault(
                    (presentation.title, presentation.impact), []
                ).append(failure)
            for records in grouped.values():
                failure = records[0]
                presentation = presenter.failure_presentation(failure)
                cells = {
                    (
                        record.scope.attempt.identity.cell
                        if isinstance(record.scope, AttemptFailureScope)
                        else record.scope.cell
                    )
                    for record in records
                }
                if len(cells) > 1:
                    pythons = ", ".join(f"Python {cell.python_minor}" for cell in cells)
                    presenter.stdout.print(Text(f"  {len(cells)} cells · {pythons}"))
                presenter.stdout.print(Text(f"  What happened: {presentation.title}"))
                presenter.stdout.print(Text(f"  Impact: {presentation.impact}"))
                diagnose = f"pf diagnose {report.package.name}"
                unique_ids = {record.failure_id for record in records}
                if len(unique_ids) == 1:
                    diagnose += f" --failure {failure.failure_id}"
                presenter.stdout.print(Text(f"  Diagnose: {diagnose}"))
        elif (
            report.result.status == "incomplete"
            and "MISSING_CELL" in report.result.reasons
        ):
            presenter.stdout.print("  target cells are missing from this host run")
    _render_static_diagnostics(presenter, report)
    presenter.stdout.print()
    if complete:
        managed = tuple(
            item for item in report.requirement_declarations if item.managed
        )
        presenter.stdout.print(
            Text(
                "Summary: "
                f"{_counted(len(managed) or len(report.projection_evidence), 'dependency declaration')} "
                "have verified floors."
            )
        )
        presenter.stdout.print(Text(f"Next: pf apply {report.package.name}"))
    else:
        presenter.stdout.print("Summary: report is incomplete and cannot be applied.")


def _render_static_diagnostics(
    presenter: ExplainPresenter,
    report: ValidatedReport,
) -> None:
    incrementals: list[tuple[Cell, TyDiagnostic]] = []
    for result in report.cell_results:
        baseline = result.static_baseline
        if baseline is None:
            continue
        count = len(baseline.diagnostics)
        noun = "diagnostic" if count == 1 else "diagnostics"
        presenter.stdout.print(
            Text(f"  {_cell_title(result.cell)} ty baseline: {count} {noun}")
        )
        if isinstance(result, CellSuccess):
            searches = (result.search,)
        elif isinstance(result, (CellIndeterminate, CellSearchFailure)) and (
            result.coordinate_failure is not None
        ):
            searches = (result.coordinate_failure,)
        else:
            searches = ()
        seen_proposals: set[str] = set()
        for search in searches:
            for observation in search.observations:
                evidence = observation.evidence
                static = evidence.static_evaluation
                if not isinstance(static, StaticRegressionEvaluation):
                    continue
                if evidence.proposal_id is None:
                    raise ValueError("static evidence requires a Proposal")
                if evidence.proposal_id in seen_proposals:
                    continue
                seen_proposals.add(evidence.proposal_id)
                incrementals.extend(
                    (result.cell, diagnostic) for diagnostic in static.incremental
                )
    _print_folded_diagnostics(
        presenter,
        incrementals,
        package=report.package.name,
        failures=report.failure_records,
    )


def _print_folded_diagnostics(
    presenter: ExplainPresenter,
    incrementals: list[tuple[Cell, TyDiagnostic]],
    *,
    package: str,
    failures: tuple[FailureRecord, ...],
) -> None:
    if not incrementals:
        return
    groups: OrderedDict[str, list[Cell]] = OrderedDict()
    for cell, diagnostic in incrementals:
        groups.setdefault(_diagnostic_summary(diagnostic), []).append(cell)
    unique = tuple(groups.items())
    shown = unique[:_UNIQUE_DIAGNOSTIC_LIMIT]
    for summary, cells in shown:
        line = f"    + {summary}"
        if len(cells) > 1:
            line += f"  ×{len(cells)}"
        cell_count = len({_cell_key(cell) for cell in cells})
        if cell_count > 1:
            line += f" · {cell_count} cells"
        presenter.stdout.print(Text(line))
    omitted = len(unique) - len(shown)
    if omitted:
        diagnose = f"pf diagnose {package}"
        if len(failures) == 1:
            diagnose += f" --failure {failures[0].failure_id}"
        presenter.stdout.print(Text(f"    ... and {omitted} more unique diagnostics"))
        presenter.stdout.print(Text(f"    Diagnose: {diagnose}"))


def _format_extra_surface(surface: tuple[str, ...]) -> str:
    return "default" if not surface else ",".join(surface)


def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (cell.package, cell.python_minor, cell.target, cell.extra_surface)


def _cell_title(cell: Cell) -> str:
    return (
        f"[py{cell.python_minor}][{cell.target}]"
        f"[{_format_extra_surface(cell.extra_surface)}]"
    )


def _single_line_summary(value: str) -> str:
    return " ".join(value.split())


def _diagnostic_summary(diagnostic: TyDiagnostic) -> str:
    location = diagnostic.path
    if diagnostic.line is not None:
        location += f":{diagnostic.line}"
    if diagnostic.column is not None:
        location += f":{diagnostic.column}"
    return f"{location} [{diagnostic.code}] {_single_line_summary(diagnostic.message)}"


def _counted(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def _report_path(report: ValidatedReport) -> str:
    parent = Path(report.package.pyproject_path).parent
    relative = (
        Path("package-floor.json")
        if parent == Path(".")
        else parent / "package-floor.json"
    )
    return relative.as_posix()

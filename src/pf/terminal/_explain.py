from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.text import Text

from pf.errors import ConfigurationError
from pf.report import ValidatedReport
from pf.schemas.evaluation import BaselineIndeterminate, BaselineRejection
from pf.schemas.project import Cell
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CellSuccess,
)
from pf.terminal._presentation import CellPresentation, OutcomeKind


class ExplainPresenter(Protocol):
    stdout: Console

    def close(self, *, abandon_pending: bool = False) -> None: ...

    def _render_explain_cell(self, presentation: CellPresentation) -> None: ...

    def _render_explain_overview(
        self,
        lines: tuple[Text, ...],
        *,
        kind: OutcomeKind,
    ) -> None: ...


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
    kind = _report_kind(report)
    covered = sum(
        1 for result in report.cell_results if isinstance(result, CellSuccess)
    )
    targets = len(report.target_cells) or len(report.cell_results)
    overview: list[Text] = [
        Text.assemble(
            (report.package.name, "cell"),
            " · ",
            (_report_path(report), "path"),
        ),
        Text.assemble(
            "Status: ",
            (report.result.status, f"reason.{kind}"),
        ),
        Text.assemble(
            "Apply: ",
            (
                "authorized by this report"
                if complete
                else "not authorized by this report",
                f"reason.{kind}",
            ),
        ),
        Text(f"Cells: {covered}/{targets} covered"),
    ]
    if report.requirement_declarations or report.projection_evidence:
        overview.extend((Text(), Text("Requirements", style="bold")))
        projected_ids = {
            projection.declaration_id for projection in report.projection_evidence
        }
        for projection in report.projection_evidence:
            declaration = declarations[projection.declaration_id]
            label = declaration.raw or declaration.name
            if not projection.representable:
                detail = "projection blocked"
                detail_style = "reason.warning"
            elif not projection.projected_requirements:
                detail = "no applicable floor"
                detail_style = "reason.warning"
            else:
                detail = f"-> {'; '.join(projection.projected_requirements)}"
                detail_style = "version"
            line = Text("  ")
            line.append(label)
            line.append("   ")
            line.append(detail, style=detail_style)
            overview.append(line)
        for declaration in report.requirement_declarations:
            if declaration.declaration_id not in projected_ids:
                overview.append(Text(f"  {declaration.raw or declaration.name}"))
    presenter._render_explain_overview(
        tuple(overview),
        kind=kind,
    )

    cells = report.target_cells or tuple(result.cell for result in report.cell_results)
    if cells:
        presenter.stdout.print()
        results = {_cell_key(result.cell): result for result in report.cell_results}
        for cell in cells:
            presenter._render_explain_cell(
                _cell_presentation(results.get(_cell_key(cell)), cell=cell)
            )

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


def _cell_presentation(
    result: CellResult | None,
    *,
    cell: Cell,
) -> CellPresentation:
    if result is None:
        return CellPresentation(
            cell=cell,
            identity=None,
            kind="warning",
            status="MISSING_CELL",
            elapsed=None,
            failures=(),
            detail=None,
            primary_failure_id=None,
            process=None,
            stage="",
            role=None,
            command="search",
            diagnose_available=False,
        )

    presentation = CellPresentation.from_result(
        result,
        cell=cell,
        command="search",
    )
    if isinstance(result, CellIndeterminate):
        terminal = next(
            failure
            for failure in result.failure_records
            if failure.failure_id == result.failure_id
        )
        return replace(
            presentation,
            failures=(terminal,),
            detail=None,
            primary_failure_id=terminal.failure_id,
            process=terminal.process,
            stage=terminal.stage,
        )
    if isinstance(result, (CellSuccess, CellSearchFailure)):
        return replace(
            presentation,
            failures=(),
            detail=None,
            primary_failure_id=None,
            process=None,
        )
    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        return replace(presentation, detail=None)
    raise AssertionError(f"unsupported CellResult: {type(result).__name__}")


def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (cell.package, cell.python_minor, cell.target, cell.extra_surface)


def _report_kind(report: ValidatedReport) -> OutcomeKind:
    if report.result.status == "complete":
        return "success"
    reasons = set(report.result.reasons)
    if "BASELINE_REJECTION" in reasons:
        return "failure"
    if "INDETERMINATE" in reasons:
        return "indeterminate"
    return "warning"


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

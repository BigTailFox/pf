from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Protocol

from packaging.requirements import InvalidRequirement, Requirement
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


_SPECIFIER_TOKEN = re.compile(
    r"(?P<operator>===|~=|==|!=|<=|>=|<|>)(?P<space>\s*)(?P<version>[^,;)\s]+)"
)


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
        requirement_width = max(
            (
                Text(declaration.raw or declaration.name).cell_len
                for declaration in report.requirement_declarations
            ),
            default=0,
        )
        projected_ids = {
            projection.declaration_id for projection in report.projection_evidence
        }
        for projection in report.projection_evidence:
            declaration = declarations[projection.declaration_id]
            label = declaration.raw or declaration.name
            label_text = _requirement_text(label, color="cyan")
            if not projection.representable:
                detail = Text("projection blocked", style="reason.warning")
            elif not projection.projected_requirements:
                detail = Text("no applicable floor", style="reason.warning")
            else:
                if len(projection.projected_requirements) > 1:
                    line = Text("  ")
                    line.append_text(label_text)
                    overview.append(line)
                    for requirement in projection.projected_requirements:
                        line = Text("    -> ")
                        line.append_text(_requirement_text(requirement, color="green"))
                        overview.append(line)
                    continue
                detail = Text("-> ")
                detail.append_text(
                    _requirement_text(
                        projection.projected_requirements[0],
                        color="green",
                    )
                )
            line = Text("  ")
            line.append_text(label_text)
            line.append(" " * (requirement_width - label_text.cell_len + 3))
            line.append_text(detail)
            overview.append(line)
        for declaration in report.requirement_declarations:
            if declaration.declaration_id not in projected_ids:
                line = Text("  ")
                line.append_text(
                    _requirement_text(
                        declaration.raw or declaration.name,
                        color="cyan",
                    )
                )
                overview.append(line)
    presenter._render_explain_overview(
        tuple(overview),
        kind=kind,
    )

    cells = report.target_cells or tuple(result.cell for result in report.cell_results)
    results = {_cell_key(result.cell): result for result in report.cell_results}
    presentations = tuple(
        _cell_presentation(results.get(_cell_key(cell)), cell=cell) for cell in cells
    )
    if presentations:
        presenter.stdout.print()
        for presentation in presentations:
            presenter._render_explain_cell(presentation)

    presenter.stdout.print()
    summary_kind = _summary_kind(complete=complete, cells=presentations)
    if complete:
        managed = tuple(
            item for item in report.requirement_declarations if item.managed
        )
        presenter.stdout.print(
            Text(
                "Summary: "
                f"{_counted(len(managed) or len(report.projection_evidence), 'dependency declaration')} "
                "have verified floors.",
                style=f"summary.{summary_kind}",
            )
        )
        presenter.stdout.print(Text(f"Next: pf apply {report.package.name}"))
    else:
        presenter.stdout.print(
            Text(
                "Summary: report is incomplete and cannot be applied.",
                style=f"summary.{summary_kind}",
            )
        )


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


def _summary_kind(
    *,
    complete: bool,
    cells: tuple[CellPresentation, ...],
) -> OutcomeKind:
    if complete:
        return "success"
    if any(cell.kind == "failure" for cell in cells):
        return "failure"
    if any(cell.kind == "indeterminate" for cell in cells):
        return "indeterminate"
    return "warning"


def _requirement_text(requirement: str, *, color: str) -> Text:
    text = Text(requirement)
    try:
        parsed = Requirement(requirement)
    except InvalidRequirement:
        return text
    if not parsed.specifier:
        return text
    declaration = requirement.partition(";")[0]
    matches = tuple(_SPECIFIER_TOKEN.finditer(declaration))
    if not matches:
        return text
    start = matches[0].start("operator")
    prefix = declaration[:start].rstrip()
    if prefix.endswith("("):
        start = len(prefix) - 1
    end = matches[-1].end("version")
    suffix = declaration[end:]
    closing = suffix.lstrip()
    if closing.startswith(")"):
        end += len(suffix) - len(closing) + 1
    text.stylize(color, start, end)
    for match in matches:
        text.stylize(
            f"bold {color}",
            match.start("version"),
            match.end("version"),
        )
    return text


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

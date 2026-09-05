from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Protocol

from packaging.requirements import InvalidRequirement, Requirement
from rich.console import Console, RenderableType
from rich.table import Column, Table
from rich.text import Text

from pf.errors import ConfigurationError
from pf.report import ValidatedReport
from pf.schemas.evaluation import BaselineIndeterminate, BaselineRejection
from pf.project import marker_platform
from pf.schemas.project import Cell, cell_identity
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CellSuccess,
)
from pf.terminal import (
    _path_text,
    _plain_result_card,
    _report_cell_counts,
    _report_distribution_text,
    _result_card,
)
from pf.terminal._presentation import (
    CellPresentation,
    OutcomeKind,
    cell_title_text,
    marker_group,
)


_SPECIFIER_TOKEN = re.compile(
    r"(?P<operator>===|~=|==|!=|<=|>=|<|>)(?P<space>\s*)(?P<version>[^,;)\s]+)"
)


class ExplainPresenter(Protocol):
    stdout: Console

    def close(self, *, abandon_pending: bool = False) -> None: ...

    def _render_explain_cell(
        self,
        presentation: CellPresentation,
        *,
        show_diagnose: bool,
    ) -> None: ...

    def _print_outcome(
        self,
        kind: OutcomeKind,
        message: str,
        *,
        console: Console | None = None,
    ) -> None: ...


def render(
    presenter: ExplainPresenter,
    report: ValidatedReport,
    *,
    report_path: str,
    root: Path,
) -> int:
    presenter.close()
    _render_report(presenter, report, report_path=report_path, root=root)
    return 0


def _render_report(
    presenter: ExplainPresenter,
    report: ValidatedReport,
    *,
    report_path: str,
    root: Path,
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
    apply_message, _, scoped_eligible = _apply_status(report)
    cells = report.target_cells or tuple(result.cell for result in report.cell_results)
    results = {_cell_key(result.cell): result for result in report.cell_results}
    presentations = tuple(
        _cell_presentation(results.get(_cell_key(cell)), cell=cell) for cell in cells
    )
    summary_kind = _summary_kind(complete=complete, cells=presentations)
    diagnose_index = next(
        (
            index
            for index, presentation in enumerate(presentations)
            if presentation.failures
            and presentation.primary_failure_id is not None
            and presentation.diagnose_available
        ),
        None,
    )

    title = Text.assemble(
        (report.package.name, "bold cyan"),
        " · ",
    )
    title.append_text(
        _path_text(
            report_path,
            base=root,
            terminal=presenter.stdout.is_terminal,
        )
    )
    if complete:
        evidence = Text.assemble(
            ("complete", "reason.success bold"),
            " · report evidence is eligible for apply",
        )
    elif scoped_eligible:
        evidence = Text.assemble(
            ("incomplete", f"reason.{summary_kind} bold"),
            " · platform-scoped apply evidence is available",
        )
    else:
        evidence = Text.assemble(
            ("incomplete", f"reason.{summary_kind} bold"),
            " · ",
            (apply_message, f"reason.{summary_kind}"),
        )
    rows: list[tuple[RenderableType | None, RenderableType]] = [
        (
            Text(
                {"success": "✓", "failure": "✗", "warning": "⚠", "indeterminate": "!"}[
                    summary_kind
                ],
                style=summary_kind,
            ),
            title,
        ),
        (None, evidence),
        (None, Text("current project was not inspected", style="dim")),
        (None, Text()),
        (None, Text("Cells", style="bold")),
        (None, _report_distribution_text(report)),
    ]
    for presentation in presentations:
        if presentation.kind != "success":
            continue
        rows.append(
            (
                None,
                marker_group(
                    ((Text("✓", style="success"), cell_title_text(presentation.cell)),),
                    expand=True,
                ),
            )
        )
    if report.requirement_declarations:
        rows.extend(
            (
                (None, Text()),
                (None, Text("Requirements", style="bold")),
                (
                    None,
                    _requirements_grid(
                        report,
                    ),
                ),
            )
        )
    spaces = report.search_spaces()
    if spaces:
        rows.extend(((None, Text()), (None, Text("Search spaces", style="bold"))))
        rows.append((None, Text(f"Artifact policy: {report.search_policy.artifact}")))
        for binding in report.search_policy.bindings:
            requested = binding.requested_space or "conditional default"
            rows.append((None, Text(
                f"{', '.join(binding.dependencies)}: requested {requested}; step {binding.step}; "
                f"prereleases {'included' if binding.prereleases else 'excluded'}"
            )))
            rows.append((None, Text(
                f"  with lower bound: {binding.space_defaults.with_lower_bound}; "
                f"without lower bound: {binding.space_defaults.without_lower_bound}", style="dim"
            )))
        for projection in spaces:
            selection = projection.selection
            surface = ",".join(projection.cell.extra_surface) or "base"
            label = f"{projection.cell.target} py{projection.cell.python_minor} {surface} · {projection.dependency}"
            if selection is None:
                rows.append((None, Text(f"{label}: series selection evidence unavailable", style="dim")))
                continue
            rows.append((None, Text(f"{label}: {selection.expression} ({selection.reason})")))
            if selection.anchors:
                rows.append((None, Text("  anchors: " + ", ".join(f"{name}={version}" for name, version in selection.anchors))))
                series = ", ".join(
                    (f"{key[0]}!" if key[0] else "") + ".".join(str(part) for part in key[1:])
                    for key in selection.selected_keys
                )
                rows.append((None, Text(f"  selected series: {series}")))
            rows.append((None, Text("  exact representatives: " + ", ".join(projection.representatives))))
    if diagnose_index is None and (complete or scoped_eligible):
        action = Text("-> ", style="hint")
        action.append(
            f"pf apply --package {report.package.name}",
            style="hint not dim",
        )
        rows.extend(((None, Text()), (None, action)))
    presenter.stdout.print(
        _result_card(tuple(rows), kind=summary_kind)
        if presenter.stdout.is_terminal
        else _plain_result_card(tuple(rows))
    )

    for index, presentation in enumerate(presentations):
        if presentation.kind == "success":
            continue
        presenter.stdout.print()
        presenter._render_explain_cell(
            presentation,
            show_diagnose=index == diagnose_index,
        )

    if complete:
        managed = sum(
            1
            for projection in report.projection_evidence
            if projection.projected_requirements
        )
        if managed == 0:
            summary = "No managed dependencies require floor changes."
        elif managed == 1:
            summary = "1 managed dependency has a verified floor."
        else:
            summary = f"{managed} managed dependencies have verified floors."
    elif scoped_eligible:
        summary = "Report incomplete · platform-scoped apply may be authorized"
    else:
        incomplete = [
            f"{count} {label} cell{'s' if count != 1 else ''}"
            for label, count in _report_cell_counts(report)
            if label != "passed"
        ]
        summary = "Report incomplete"
        if incomplete:
            summary += " · " + " · ".join(incomplete)
        summary += " · apply blocked"
    presenter._print_outcome(summary_kind, summary, console=presenter.stdout)


def _requirements_grid(
    report: ValidatedReport,
) -> Table:
    grid = Table.grid(
        Column(ratio=1, overflow="fold", no_wrap=False),
        Column(ratio=1, overflow="fold", no_wrap=False),
        padding=(0, 2),
        expand=True,
    )
    projections = {
        projection.declaration_id: projection for projection in report.projection_evidence
    }
    for declaration in report.requirement_declarations:
        label = _requirement_text(
            declaration.raw or declaration.name,
            color="cyan",
        )
        projection = projections.get(declaration.declaration_id)
        details: list[Text] = []
        if not declaration.managed:
            details.append(Text("fixed · not managed", style="dim"))
        elif projection is None or (
            projection.representable and not projection.projected_requirements
        ):
            details.append(Text("no applicable floor", style="reason.warning"))
        elif not projection.representable:
            details.append(Text("projection blocked", style="reason.warning"))
            for floor in projection.floors:
                floor_text = cell_title_text(floor.cell)
                floor_text.append(f" · {floor.version}", style="reason.success")
                details.append(floor_text)
        else:
            for requirement in projection.projected_requirements:
                detail = Text("-> ")
                detail.append_text(_requirement_text(requirement, color="green"))
                details.append(detail)
        for index, detail in enumerate(details):
            grid.add_row(label if index == 0 else Text(), detail)
    return grid


def _apply_status(report: ValidatedReport) -> tuple[str, OutcomeKind, bool]:
    if report.result.status == "complete":
        return "eligible; current project will be rechecked", "success", False
    reasons = set(report.result.reasons)
    if "MISSING_CELL" not in reasons or not reasons <= {
        "MISSING_CELL",
        "UNREPRESENTABLE_PROJECTION",
    }:
        return "blocked by report evidence", "warning", False

    targets_by_platform: dict[str, set[tuple[str, str, str, tuple[str, ...]]]] = {}
    roots_by_platform: dict[str, list[CellResult]] = {}
    for cell in report.target_cells:
        targets_by_platform.setdefault(cell.target, set()).add(cell_identity(cell))
    for result in report.cell_results:
        roots_by_platform.setdefault(result.cell.target, []).append(result)
    if not roots_by_platform:
        return "blocked; no applicable final floor", "warning", False

    complete_platforms: set[str] = set()
    for platform, roots in roots_by_platform.items():
        if {cell_identity(result.cell) for result in roots} != targets_by_platform.get(
            platform, set()
        ) or not all(isinstance(result, CellSuccess) for result in roots):
            return "blocked by partial or non-success evidence", "warning", False
        complete_platforms.add(platform)

    selectors = {
        tuple(marker_platform(platform).values()) for platform in targets_by_platform
    }
    complete_selectors = {
        tuple(marker_platform(platform).values()) for platform in complete_platforms
    }
    if len(selectors) > 1 and complete_selectors and selectors - complete_selectors:
        return (
            "eligible for platform-scoped apply if the current declaration still "
            "matches",
            "warning",
            True,
        )
    return "blocked by report evidence", "warning", False


def _cell_presentation(
    result: CellResult | None,
    *,
    cell: Cell,
) -> CellPresentation:
    if result is None:
        return CellPresentation(
            cell=cell,
            identity=None,
            completed_packages=None,
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

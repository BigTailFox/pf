from __future__ import annotations

from pathlib import Path

import pytest

from evaluation_fixtures import (
    evaluation_assembly,
    evaluation_project,
    selected_candidate,
    successful_process,
)

from pf.environment import ExactSelection, HighestResolution, PreparedEnvironment
from pf.schemas.evaluation import (
    StaticBaselineCapture,
    StaticRegressionEvaluation,
    TyCheck,
    TyDiagnostic,
)
from pf.schemas.project import VersionPin


def _diagnostic(
    *,
    line: int,
    column: int,
    code: str,
    message: str = "wording is not evidence",
) -> TyDiagnostic:
    identity = f"snapshot|demo.py|{line}|{column}|{code}"
    return TyDiagnostic(
        identity=identity,
        origin="snapshot",
        path="demo.py",
        line=line,
        column=column,
        code=code,
        severity="major",
        message=message,
    )


class TestStaticEvaluatorClassification:
    @pytest.mark.parametrize(
        ("source", "diagnostic", "operation", "module", "owner", "name"),
        (
            (
                "import requests.missing\n",
                _diagnostic(line=1, column=8, code="unresolved-import"),
                "import-module",
                "requests.missing",
                None,
                None,
            ),
            (
                "from requests import Missing\n",
                _diagnostic(line=1, column=6, code="unresolved-import"),
                "import-symbol",
                "requests",
                None,
                "Missing",
            ),
            (
                "import requests as req\nreq.missing\n",
                _diagnostic(line=2, column=5, code="unresolved-attribute"),
                "has-member",
                "requests",
                "requests",
                "missing",
            ),
        ),
        ids=("module", "symbol", "member"),
    )
    def test_static_evaluator_builds_structurally_recoverable_witnesses(
        self,
        tmp_path: Path,
        source: str,
        diagnostic: TyDiagnostic,
        operation: str,
        module: str,
        owner: str | None,
        name: str | None,
    ) -> None:
        project = evaluation_project(
            tmp_path / "project",
            dependency="requests",
            source=source,
        )
        assembly = evaluation_assembly(
            highest=(VersionPin(name="requests", version="3"),),
            ty_handler=lambda vector, call: TyCheck(
                process=successful_process(exit_code=0 if call == 1 else 1),
                diagnostics=() if call == 1 else (diagnostic,),
            ),
        )
        highest = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(highest, PreparedEnvironment)
        capture = assembly.static.capture(highest, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)
        candidate = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=ExactSelection(
                selection=(selected_candidate("requests", "2"),),
                harness_baseline=highest.harness_baseline,
            ),
            source_plan=project.source_plan,
        )
        assert isinstance(candidate, PreparedEnvironment)

        evaluation = assembly.static.evaluate(
            candidate,
            package=project.package,
            baseline=capture.baseline,
        )

        assert isinstance(evaluation, StaticRegressionEvaluation)
        result = evaluation.classifications[0]
        assert result.classification == "strong"
        assert result.reason_code == "witness-planned"
        assert result.witness_plan is not None
        assert result.witness_plan.managed_dependency == "requests"
        assert result.witness_plan.operation == operation
        assert result.witness_plan.module == module
        assert result.witness_plan.owner == owner
        assert result.witness_plan.symbol_or_member == name
        highest.close()
        candidate.close()

    @pytest.mark.parametrize(
        ("source", "diagnostic", "reason"),
        (
            (
                "import requests\n",
                _diagnostic(line=1, column=8, code="invalid-type"),
                "code-not-allowlisted",
            ),
            (
                "value.missing\n",
                _diagnostic(line=1, column=7, code="unresolved-attribute"),
                "target-not-unique",
            ),
            (
                "import flask\n",
                _diagnostic(line=1, column=8, code="unresolved-import"),
                "managed-dependency-not-unique",
            ),
            (
                "import requests\n",
                TyDiagnostic(
                    identity="external|site-packages/requests.py|unresolved-import",
                    origin="external",
                    path="site-packages/requests.py",
                    line=None,
                    column=None,
                    code="unresolved-import",
                    severity="major",
                    message="wording is not evidence",
                ),
                "diagnostic-not-in-snapshot",
            ),
            (
                "import requests\n",
                TyDiagnostic(
                    identity="snapshot|../demo.py|1|8|unresolved-import",
                    origin="snapshot",
                    path="../demo.py",
                    line=1,
                    column=8,
                    code="unresolved-import",
                    severity="major",
                    message="wording is not evidence",
                ),
                "source-path-invalid",
            ),
            (
                "def broken(:\n",
                _diagnostic(line=1, column=1, code="unresolved-import"),
                "source-ast-unavailable",
            ),
            (
                "import requests, flask\n",
                _diagnostic(line=1, column=8, code="unresolved-import"),
                "target-not-unique",
            ),
            (
                "from .requests import thing\n",
                _diagnostic(line=1, column=6, code="unresolved-import"),
                "target-not-unique",
            ),
            (
                "from requests import *\n",
                _diagnostic(line=1, column=6, code="unresolved-import"),
                "target-not-unique",
            ),
            (
                "import requests\n",
                _diagnostic(line=2, column=1, code="unresolved-import"),
                "target-not-unique",
            ),
        ),
        ids=(
            "unsupported-code",
            "ambiguous-target",
            "unmanaged-module",
            "external-diagnostic",
            "unsafe-source",
            "invalid-source",
            "multiple-imports",
            "relative-import",
            "star-import",
            "outside-node",
        ),
    )
    def test_static_evaluator_downgrades_without_message_guessing(
        self,
        tmp_path: Path,
        source: str,
        diagnostic: TyDiagnostic,
        reason: str,
    ) -> None:
        project = evaluation_project(
            tmp_path / "project",
            dependency="requests",
            source=source,
        )
        assembly = evaluation_assembly(
            highest=(VersionPin(name="requests", version="3"),),
            ty_handler=lambda vector, call: TyCheck(
                process=successful_process(exit_code=0 if call == 1 else 1),
                diagnostics=(
                    ()
                    if call == 1
                    else (
                        diagnostic.model_copy(
                            update={
                                "message": (
                                    "first wording" if call == 2 else "second wording"
                                )
                            }
                        ),
                    )
                ),
            ),
        )
        highest = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(highest, PreparedEnvironment)
        capture = assembly.static.capture(highest, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)
        candidate = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=ExactSelection(
                selection=(selected_candidate("requests", "2"),),
                harness_baseline=highest.harness_baseline,
            ),
            source_plan=project.source_plan,
        )
        assert isinstance(candidate, PreparedEnvironment)

        first = assembly.static.evaluate(
            candidate,
            package=project.package,
            baseline=capture.baseline,
        )
        second = assembly.static.evaluate(
            candidate,
            package=project.package,
            baseline=capture.baseline,
        )

        assert isinstance(first, StaticRegressionEvaluation)
        assert isinstance(second, StaticRegressionEvaluation)
        assert first.classifications == second.classifications
        result = first.classifications[0]
        assert result.classification == "general"
        assert result.reason_code == reason
        assert result.witness_plan is None
        highest.close()
        candidate.close()

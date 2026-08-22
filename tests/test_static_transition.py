from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from pf.environment import PreparedEnvironment
from pf.project import ProjectLoader
from pf.schemas.evaluation import Attempt, AttemptIdentity, TyDiagnostic
from pf.schemas.project import Proposal, VersionPin
from pf.static_transition import StaticTransitionClassifier


def _context(tmp_path: Path, source: str) -> tuple[PreparedEnvironment, object]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests>=1"]

[dependency-groups]
test = []

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=project_root, package_selection=None).packages[0]
    temporary = tempfile.TemporaryDirectory(prefix="pf-transition-", dir=tmp_path)
    proposal_root = Path(temporary.name) / "source"
    proposal_root.mkdir()
    (proposal_root / "demo.py").write_text(source, encoding="utf-8")
    cell = package.cells[0]
    vector = (VersionPin(name="requests", version="2"),)
    attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=cell,
            requested_resolution="exact-vector",
            requested_managed_vector=vector,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )
    prepared = PreparedEnvironment(
        attempt=attempt,
        proposal=Proposal(
            proposal_id="proposal",
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=vector,
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        ),
        proposal_root=proposal_root,
        package_root=proposal_root,
        environment_root=Path(temporary.name) / "environment",
        interpreter=Path(temporary.name) / "environment" / "bin" / "python",
        temporary_directory=temporary,
    )
    return prepared, package


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
)
def test_classifier_builds_only_structurally_recoverable_witnesses(
    tmp_path: Path,
    source: str,
    diagnostic: TyDiagnostic,
    operation: str,
    module: str,
    owner: str | None,
    name: str | None,
) -> None:
    prepared, package = _context(tmp_path, source)

    result = StaticTransitionClassifier().classify(
        prepared,
        package=package,  # type: ignore[arg-type]
        incremental=(diagnostic,),
    )[0]

    assert result.classification == "strong"
    assert result.reason_code == "witness-planned"
    assert result.witness_plan is not None
    assert result.witness_plan.managed_dependency == "requests"
    assert result.witness_plan.operation == operation
    assert result.witness_plan.module == module
    assert result.witness_plan.owner == owner
    assert result.witness_plan.symbol_or_member == name
    prepared.close()


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
    ),
)
def test_classifier_downgrades_without_guessing_from_message(
    tmp_path: Path,
    source: str,
    diagnostic: TyDiagnostic,
    reason: str,
) -> None:
    prepared, package = _context(tmp_path, source)
    classifier = StaticTransitionClassifier()

    first = classifier.classify(
        prepared,
        package=package,  # type: ignore[arg-type]
        incremental=(diagnostic,),
    )[0]
    second = classifier.classify(
        prepared,
        package=package,  # type: ignore[arg-type]
        incremental=(diagnostic.model_copy(update={"message": "different wording"}),),
    )[0]

    assert first == second.model_copy(
        update={"diagnostic_identity": first.diagnostic_identity}
    )
    assert first.classification == "general"
    assert first.reason_code == reason
    assert first.witness_plan is None
    prepared.close()

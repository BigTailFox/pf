from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from packaging.utils import canonicalize_name

if TYPE_CHECKING:
    from pf.environment import PreparedEnvironment
    from pf.schemas.evaluation import DiagnosticClassification, TyDiagnostic
    from pf.schemas.project import PackagePlan


STATIC_POLICY_VERSION = "static-transition-v1"
STRONG_CLASSIFIER_VERSION = "strong-classifier-v1"
WITNESS_PLANNER_VERSION = "witness-planner-v1"

_STRONG_CODES = frozenset({"unresolved-import", "unresolved-attribute"})
WitnessOperation = Literal["import-module", "import-symbol", "has-member"]


def static_fingerprint(identities: tuple[str, ...]) -> str:
    """Hash one ordered incremental DiagnosticIdentity multiset."""
    canonical = json.dumps(identities, separators=(",", ":")).encode()
    return hashlib.sha256(
        f"pf:ty-static-state:{STATIC_POLICY_VERSION}\0".encode() + canonical
    ).hexdigest()


class StaticTransitionClassifier:
    """Classify increments using only diagnostic structure and the source AST."""

    def classify(
        self,
        prepared: "PreparedEnvironment",
        *,
        package: "PackagePlan",
        incremental: tuple["TyDiagnostic", ...],
    ) -> tuple["DiagnosticClassification", ...]:
        return tuple(
            self._classify_one(
                prepared,
                package=package,
                diagnostic=diagnostic,
            )
            for diagnostic in incremental
        )

    def _classify_one(
        self,
        prepared: "PreparedEnvironment",
        *,
        package: "PackagePlan",
        diagnostic: "TyDiagnostic",
    ) -> "DiagnosticClassification":
        from pf.schemas.evaluation import DiagnosticClassification

        if diagnostic.code not in _STRONG_CODES:
            return DiagnosticClassification(
                diagnostic_identity=diagnostic.identity,
                classification="general",
                reason_code="code-not-allowlisted",
            )
        if diagnostic.origin != "snapshot" or diagnostic.line is None:
            return DiagnosticClassification(
                diagnostic_identity=diagnostic.identity,
                classification="general",
                reason_code="diagnostic-not-in-snapshot",
            )
        source = self._source_path(prepared.proposal_root, diagnostic.path)
        if source is None:
            return DiagnosticClassification(
                diagnostic_identity=diagnostic.identity,
                classification="general",
                reason_code="source-path-invalid",
            )
        try:
            tree = ast.parse(
                source.read_text(encoding="utf-8"), filename=diagnostic.path
            )
        except (OSError, SyntaxError, UnicodeError):
            return DiagnosticClassification(
                diagnostic_identity=diagnostic.identity,
                classification="general",
                reason_code="source-ast-unavailable",
            )
        target = (
            self._import_target(tree, diagnostic)
            if diagnostic.code == "unresolved-import"
            else self._member_target(tree, diagnostic)
        )
        if target is None:
            return DiagnosticClassification(
                diagnostic_identity=diagnostic.identity,
                classification="general",
                reason_code="target-not-unique",
            )
        operation, module, owner, symbol_or_member = target
        dependency = self._managed_dependency(
            module,
            prepared=prepared,
            package=package,
        )
        if dependency is None:
            return DiagnosticClassification(
                diagnostic_identity=diagnostic.identity,
                classification="general",
                reason_code="managed-dependency-not-unique",
            )
        from pf.schemas.evaluation import RuntimeWitnessPlan

        plan = RuntimeWitnessPlan(
            diagnostic_identities=(diagnostic.identity,),
            managed_dependency=dependency,
            operation=operation,
            module=module,
            owner=owner,
            symbol_or_member=symbol_or_member,
        )
        return DiagnosticClassification(
            diagnostic_identity=diagnostic.identity,
            classification="strong",
            reason_code="witness-planned",
            witness_plan=plan,
        )

    @staticmethod
    def _source_path(root: Path, relative: str) -> Path | None:
        candidate = (root / relative).resolve()
        resolved_root = root.resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            return None
        if not candidate.is_file():
            return None
        return candidate

    @classmethod
    def _import_target(
        cls,
        tree: ast.AST,
        diagnostic: "TyDiagnostic",
    ) -> tuple[WitnessOperation, str, str | None, str | None] | None:
        matches = tuple(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and cls._contains(node, diagnostic)
        )
        if len(matches) != 1:
            return None
        node = matches[0]
        if isinstance(node, ast.Import):
            if len(node.names) != 1:
                return None
            return "import-module", node.names[0].name, None, None
        if node.level != 0 or node.module is None or len(node.names) != 1:
            return None
        imported = node.names[0].name
        if imported == "*":
            return None
        return "import-symbol", node.module, None, imported

    @classmethod
    def _member_target(
        cls,
        tree: ast.AST,
        diagnostic: "TyDiagnostic",
    ) -> tuple[WitnessOperation, str, str | None, str | None] | None:
        imports: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import) or len(node.names) != 1:
                continue
            alias = node.names[0]
            local = alias.asname or alias.name.split(".", 1)[0]
            imports[local] = alias.name
        matches = tuple(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in imports
            and cls._contains(node, diagnostic)
        )
        if len(matches) != 1:
            return None
        attribute = matches[0]
        assert isinstance(attribute.value, ast.Name)
        module = imports[attribute.value.id]
        return "has-member", module, module, attribute.attr

    @staticmethod
    def _contains(node: ast.AST, diagnostic: "TyDiagnostic") -> bool:
        line = diagnostic.line
        start_line = getattr(node, "lineno", None)
        if line is None or not isinstance(start_line, int):
            return False
        end_line = getattr(node, "end_lineno", start_line)
        if not isinstance(end_line, int):
            end_line = start_line
        if not start_line <= line <= end_line:
            return False
        if diagnostic.column is None or line not in {start_line, end_line}:
            return True
        column = diagnostic.column - 1
        node_column = getattr(node, "col_offset", 0)
        start_column = node_column if isinstance(node_column, int) else 0
        if line != start_line:
            start_column = 0
        end_column = getattr(node, "end_col_offset", column + 1)
        if not isinstance(end_column, int):
            end_column = column + 1
        return start_column <= column <= end_column

    @staticmethod
    def _managed_dependency(
        module: str,
        *,
        prepared: "PreparedEnvironment",
        package: "PackagePlan",
    ) -> str | None:
        root = module.split(".", 1)[0]
        canonical_root = canonicalize_name(root)
        active_ids = set(prepared.proposal.cell.active_declaration_ids)
        installed = {pin.name for pin in prepared.proposal.managed_vector}
        matches = {
            declaration.name
            for declaration in package.declarations
            if declaration.managed
            and declaration.declaration_id in active_ids
            and declaration.name in installed
            and canonicalize_name(declaration.name) == canonical_root
        }
        if len(matches) != 1:
            return None
        return next(iter(matches))

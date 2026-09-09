from __future__ import annotations

import ast
from pathlib import Path

from pf.static.audit import _admit_saved_static_audit
from pf.static.comparison import derive_static_comparison
from pf.static.guidance import locate_static_hint


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "src" / "pf" / "schemas"
STATIC_PKG = ROOT / "src" / "pf" / "static"


_FORBIDDEN_SCHEMA_IMPORTS = {
    "pf.static",
    "pf.harness",
    "pf.ty_options",
    "pf.resolution",
}
_FORBIDDEN_SCHEMA_PREFIXES = ("pf.static_",)
_ALLOWED_SCHEMA_EXCEPTIONS = {
    ("config.py", "pf.errors"),
    ("config.py", "pf.search_space"),
    ("project.py", "pf.search_space"),
}
_REPLAY_NAMES = {
    "derive_static_comparison",
    "admit_static_consumer_context",
    "locate_static_hint",
    "original_harness",
    "relax_harness",
    "active_harness_requirements",
    "validate_ty_args",
    "resolution_projection",
    "subject_interpreter",
}


def _module_name(path: Path) -> str:
    return "pf.schemas." + ".".join(path.relative_to(SCHEMAS).with_suffix("").parts)


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_schemas_obey_static_import_allowlist() -> None:
    violations: list[str] = []
    for path in SCHEMAS.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for name in _imported_modules(tree):
            forbidden = name in _FORBIDDEN_SCHEMA_IMPORTS or name.startswith(_FORBIDDEN_SCHEMA_PREFIXES)
            if name.startswith("pf.static.") or name == "pf.static":
                forbidden = True
            if not forbidden:
                continue
            if (path.name, name) in _ALLOWED_SCHEMA_EXCEPTIONS:
                continue
            violations.append(f"{path.relative_to(ROOT)} imports {name}")
    assert violations == []


def test_schema_validators_do_not_replay_compare_hint_or_harness() -> None:
    hits: list[str] = []
    for path in SCHEMAS.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in _REPLAY_NAMES:
                    hits.append(f"{path.relative_to(ROOT)}:{node.lineno} calls {name}")
    assert hits == []


def test_admit_uses_shared_derive_and_hint() -> None:
    source = Path(_admit_saved_static_audit.__code__.co_filename).read_text()
    tree = ast.parse(source)
    imported: dict[str, str] = {}
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    assert imported.get("derive_static_comparison") == "pf.static.comparison.derive_static_comparison"
    assert imported.get("locate_static_hint") == "pf.static.guidance.locate_static_hint"
    assert "derive_static_comparison" in called
    assert "locate_static_hint" in called
    assert derive_static_comparison is not None
    assert locate_static_hint is not None
    second_derive = [
        path for path in (ROOT / "src" / "pf").rglob("*.py")
        if path != STATIC_PKG / "comparison.py"
        and "def derive_static_comparison" in path.read_text()
    ]
    second_hint = [
        path for path in (ROOT / "src" / "pf").rglob("*.py")
        if path != STATIC_PKG / "guidance.py"
        and "def locate_static_hint" in path.read_text()
    ]
    assert second_derive == []
    assert second_hint == []


SRC = ROOT / "src" / "pf"
PRODUCT_CALLERS = (
    "cli.py",
    "check.py",
    "baseline.py",
    "search.py",
    "verification.py",
    "workflow.py",
    "coordinate_search.py",
    "report.py",
    "runlog.py",
)
PUBLIC_STATIC_NAMES = {
    "CollectedStaticSubject",
    "StaticEvaluator",
    "StaticGuidanceEvaluator",
    "StaticHint",
    "StaticPoint",
    "StaticSearchResult",
    "StaticSlice",
    "TyCheckCache",
    "locate_static_hint",
}
SEARCH_EXTRA_NAMES = {"StaticSliceCollector"}
TY_ADAPTER_EXTRA = {"StaticTyRequest"}
CACHE_DOMAIN_METHODS = {
    "lookup",
    "collect",
    "compare",
    "compare_document",
    "record_pass",
    "record_direct_pass",
    "set_highest",
    "set_highest_uncollected",
    "consumer",
    "snapshot",
    "register_prepared",
    "bind_direct_pass_consumer",
    "find_direct_pass",
    "find_pass",
    "find_consumer",
    "local_comparisons",
    "record_search",
    "record_omission",
    "record_skip",
    "record_selection",
}
RUNNER_CACHE_METHODS = {"admitted_membership", "documents", "stop", "close"}
FORBIDDEN_PRODUCT_MODULES = {
    "pf.static_cache",
    "pf.static_request",
    "pf.static_paths",
    "pf.static_configuration",
    "pf.static_subject",
    "pf.static_admission",
    "pf.static_guidance",
    "pf.static_projection",
    "pf.ty_fact",
    "pf.ty_options",
    "pf.static.evaluator",
    "pf.static.comparison",
    "pf.static.audit",
    "pf.static.guidance",
}


def _imported_from(tree: ast.AST, module: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def test_product_callers_import_only_public_static_names() -> None:
    violations: list[str] = []
    for name in PRODUCT_CALLERS:
        path = SRC / name
        tree = ast.parse(path.read_text(), filename=str(path))
        imported = _imported_modules(tree)
        for module in imported:
            if module in FORBIDDEN_PRODUCT_MODULES or module.startswith("pf.static_"):
                violations.append(f"{name} imports {module}")
            if module.startswith("pf.static.") and module != "pf.static":
                violations.append(f"{name} imports {module}")
        allowed = set(PUBLIC_STATIC_NAMES)
        if name == "search.py":
            allowed |= SEARCH_EXTRA_NAMES
        for symbol in _imported_from(tree, "pf.static"):
            if symbol not in allowed:
                violations.append(f"{name} imports pf.static.{symbol}")
    evaluation = ast.parse((SRC / "evaluation.py").read_text())
    for module in _imported_modules(evaluation):
        if module == "pf.static" or module.startswith("pf.static") or module in FORBIDDEN_PRODUCT_MODULES:
            violations.append(f"evaluation.py imports {module}")
    environment = ast.parse((SRC / "environment.py").read_text())
    forbidden_env = {
        "TyCheckCache", "RunStaticConsumerRef", "RunStaticPassRef",
        "StaticEvaluator", "CollectedStaticSubject",
    }
    for symbol in _imported_from(environment, "pf.static") | _imported_from(environment, "pf.static_cache"):
        if symbol in forbidden_env:
            violations.append(f"environment.py imports {symbol}")
    adapter = ast.parse((SRC / "adapters" / "ty.py").read_text())
    for symbol in _imported_from(adapter, "pf.static"):
        if symbol not in TY_ADAPTER_EXTRA:
            violations.append(f"adapters/ty.py imports pf.static.{symbol}")
    for module in _imported_modules(adapter):
        if module in FORBIDDEN_PRODUCT_MODULES or module.startswith("pf.static."):
            violations.append(f"adapters/ty.py imports {module}")
    assert violations == []


def test_search_has_no_handwritten_slice() -> None:
    source = (SRC / "search.py").read_text()
    tree = ast.parse(source)
    classes = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    assert "StaticSliceCollector" in _imported_from(tree, "pf.static")
    assert "_RunnerStaticSlice" not in classes
    assert "StaticPoint" not in _imported_from(tree, "pf.static")
    assert "RunStaticConsumerRef" not in source
    assert "RunStaticPassRef" not in source
    assert "static.open_slice(" in source or "self._static.open_slice(" in source


def test_derive_and_hint_have_one_implementation() -> None:
    evaluator = ast.parse((STATIC_PKG / "evaluator.py").read_text())
    called: set[str] = set()
    for node in ast.walk(evaluator):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    methods = {
        node.name for node in ast.walk(evaluator) if isinstance(node, ast.FunctionDef)
    }
    assert "compare_global" in methods
    assert called.issuperset({"compare_global", "compare_document"})
    assert "locate_static_hint" in (STATIC_PKG / "guidance.py").read_text()
    audit = (STATIC_PKG / "audit.py").read_text()
    assert "derive_static_comparison" in audit or "compare_static_document" in audit
    assert "locate_static_hint" in audit
    second_derive = [
        path for path in SRC.rglob("*.py")
        if path != STATIC_PKG / "comparison.py"
        and "def derive_static_comparison" in path.read_text()
    ]
    second_hint = [
        path for path in SRC.rglob("*.py")
        if path != STATIC_PKG / "guidance.py"
        and "def locate_static_hint" in path.read_text()
    ]
    assert second_derive == []
    assert second_hint == []


def test_no_test_only_public_static_exports() -> None:
    from pf.static import __all__ as exported

    assert set(exported) == {
        "CollectedStaticSubject",
        "StaticEvaluator",
        "StaticGuidanceEvaluator",
        "StaticHint",
        "StaticPoint",
        "StaticSearchResult",
        "StaticSlice",
        "StaticSliceCollector",
        "StaticTyRequest",
        "TyCheckCache",
        "TyOperations",
        "locate_static_hint",
    }


def test_product_callers_do_not_invoke_cache_domain_methods() -> None:
    violations: list[str] = []
    allowed_by_file = {
        "verification.py": RUNNER_CACHE_METHODS,
    }
    for name in ("check.py", "baseline.py", "search.py", "cli.py", "verification.py"):
        path = SRC / name
        tree = ast.parse(path.read_text(), filename=str(path))
        allowed = allowed_by_file.get(name, set())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                method = node.func.attr
                if method in CACHE_DOMAIN_METHODS and method not in allowed:
                    violations.append(f"{name}:{node.lineno} calls cache domain {method}")
    assert violations == []


def test_orchestrators_do_not_accept_failures() -> None:
    from pf.baseline import HighestVersionVerifier
    from pf.check import CompatibilityChecker
    from pf.search import SearchCoordinator, _ProposalRunner
    import inspect

    for cls in (CompatibilityChecker, HighestVersionVerifier, SearchCoordinator, _ProposalRunner):
        parameters = inspect.signature(cls.__init__).parameters
        assert "failures" not in parameters, cls.__name__


def test_static_facts_do_not_associate_process_logs() -> None:
    hits: list[str] = []
    roots = [STATIC_PKG, SRC / "ty_fact.py", SRC / "static_cache.py"]
    for path in roots:
        files = path.rglob("*.py") if path.is_dir() else [path]
        for file in files:
            tree = ast.parse(file.read_text(), filename=str(file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "associate":
                        hits.append(f"{file.relative_to(ROOT)}:{node.lineno}")
    assert hits == []

"""Independent public-wire examples for D005's closed structured fact contract."""

FACT_CASES = [
    ("request-invariant", "create-environment", None, "INTERNAL_INVARIANT"),
    ("evidence-conflict", "resolve-project", {"kind": "normal-exit", "exit_code": 0}, "INTERNAL_INVARIANT"),
    ("source-access-failed", "resolve-project", None, "SOURCE_FAILURE"),
    ("environment-access-failed", "install-project", None, "ENVIRONMENT_FAILURE"),
    ("artifact-invalid", "install-environment", None, "SOURCE_FAILURE"),
    ("resolution-output-incomplete", "resolve-project", {"kind": "normal-exit", "exit_code": 0}, "TOOL_FAILURE"),
    ("resolution-plan-invalid", "resolve-environment", {"kind": "normal-exit", "exit_code": 0}, "TOOL_FAILURE"),
    ("artifact-policy-mismatch", "resolve-project", {"kind": "normal-exit", "exit_code": 0}, "INTERNAL_INVARIANT"),
    ("managed-source-leakage", "resolve-project", {"kind": "normal-exit", "exit_code": 0}, "INTERNAL_INVARIANT"),
    ("managed-source-mismatch", "resolve-environment", {"kind": "normal-exit", "exit_code": 0}, "INTERNAL_INVARIANT"),
    ("interpreter-observation-invalid", "inspect-interpreter", {"kind": "normal-exit", "exit_code": 0}, "TOOL_FAILURE"),
    ("interpreter-mismatch", "inspect-interpreter", {"kind": "normal-exit", "exit_code": 0}, "ENVIRONMENT_FAILURE"),
    ("graph-observation-invalid", "inspect", {"kind": "normal-exit", "exit_code": 0}, "TOOL_FAILURE"),
    ("installed-graph-mismatch", "inspect-project-plan", None, "INTERNAL_INVARIANT"),
    ("proposal-vector-mismatch", "proposal-vector", None, "INTERNAL_INVARIANT"),
]

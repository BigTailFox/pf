from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from runpy import run_path
from typing import Callable, cast

from jsonschema import Draft202012Validator
import pytest

from pf.report import ReportStore
from pf.schemas.report import CompleteReportResult, IncompleteReportResult


ROOT = Path(__file__).resolve().parents[1]
_validate_recorded_performance = cast(
    Callable[[object], None],
    run_path(str(ROOT / "scripts" / "qualify_report_schema.py"))[
        "_validate_recorded_performance"
    ],
)
SCHEMA_PATH = ROOT / "docs" / "schemas" / "package-floor-v2.schema.json"
EXAMPLE_PATHS = (
    ROOT / "docs" / "examples" / "package-floor-v2-minimal-complete.json",
    ROOT / "docs" / "examples" / "package-floor-v2-minimal-incomplete.json",
)


def test_report_schema_and_examples_are_generated_and_valid() -> None:
    subprocess.run(
        [sys.executable, "scripts/generate_report_schema.py", "--check"],
        cwd=ROOT,
        check=True,
    )

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert "schema_version" in schema["required"]
    assert all(
        definition.get("additionalProperties") is False
        for definition in schema["$defs"].values()
        if definition.get("type") == "object"
    )

    def contains_default(value: object) -> bool:
        if isinstance(value, dict):
            return "default" in value or any(
                contains_default(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_default(item) for item in value)
        return False

    assert not contains_default(schema)

    def contains_null_type(value: object) -> bool:
        if isinstance(value, dict):
            return value.get("type") == "null" or any(
                contains_null_type(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_null_type(item) for item in value)
        return False

    assert not contains_null_type(schema)
    validator = Draft202012Validator(schema)
    reports = []
    for path in EXAMPLE_PATHS:
        document = json.loads(path.read_text(encoding="utf-8"))
        validator.validate(document)
        reports.append(ReportStore().read(path))

    assert isinstance(reports[0].result, CompleteReportResult)
    assert isinstance(reports[1].result, IncompleteReportResult)


@pytest.mark.parametrize(
    "recorded",
    (
        {},
        {"read_validate": {}, "merge_complementary": {}},
        {
            "read_validate": {"peak_bytes": True, "seconds": 0.1},
            "merge_complementary": {"peak_bytes": 1, "seconds": 0.1},
        },
        {
            "read_validate": {"peak_bytes": 1, "seconds": -0.1},
            "merge_complementary": {"peak_bytes": 1, "seconds": 0.1},
        },
        {
            "read_validate": {"peak_bytes": 1, "seconds": float("nan")},
            "merge_complementary": {"peak_bytes": 1, "seconds": 0.1},
        },
        {
            "read_validate": {"peak_bytes": 1, "seconds": float("inf")},
            "merge_complementary": {"peak_bytes": 1, "seconds": 0.1},
        },
    ),
)
def test_qualification_record_requires_valid_performance_sections(
    recorded: object,
) -> None:
    with pytest.raises(RuntimeError, match="qualification record"):
        _validate_recorded_performance(recorded)

    _validate_recorded_performance(
        {
            "read_validate": {"peak_bytes": 0, "seconds": 0},
            "merge_complementary": {"peak_bytes": 1, "seconds": 0.1},
        }
    )

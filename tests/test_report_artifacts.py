from __future__ import annotations

import json
from pathlib import Path
from runpy import run_path
import sys
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
_generate_schema_main = cast(
    Callable[[], int],
    run_path(str(ROOT / "scripts" / "generate_report_schema.py"))["main"],
)
SCHEMA_PATH = ROOT / "docs" / "schemas" / "package-floor-v2.schema.json"
EXAMPLE_PATHS = (
    ROOT / "docs" / "examples" / "package-floor-v2-minimal-complete.json",
    ROOT / "docs" / "examples" / "package-floor-v2-minimal-incomplete.json",
)


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _contains_type(value: object, expected: str) -> bool:
    if isinstance(value, dict):
        return value.get("type") == expected or any(
            _contains_type(item, expected) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_type(item, expected) for item in value)
    return False


class TestReportArtifacts:
    def test_generate_schema_check_accepts_committed_artifacts(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            ["generate_report_schema.py", "--check"],
        )

        assert _generate_schema_main() == 0

    def test_json_schema_is_strict_and_canonical(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(schema)

        assert schema["additionalProperties"] is False
        assert "schema_version" in schema["required"]
        assert all(
            definition.get("additionalProperties") is False
            for definition in schema["$defs"].values()
            if definition.get("type") == "object"
        )
        assert not _contains_key(schema, "default")
        assert not _contains_type(schema, "null")

    @pytest.mark.parametrize(
        ("path", "result_type"),
        (
            (EXAMPLE_PATHS[0], CompleteReportResult),
            (EXAMPLE_PATHS[1], IncompleteReportResult),
        ),
        ids=("complete", "incomplete"),
    )
    def test_example_validates_and_loads_expected_result(
        self,
        path: Path,
        result_type: type[CompleteReportResult] | type[IncompleteReportResult],
    ) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)

        validator.validate(json.loads(path.read_text(encoding="utf-8")))
        report = ReportStore().read(path)

        assert isinstance(report.result, result_type)


class TestReportSchemaQualificationRecord:
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
    def test_validate_rejects_invalid_performance_sections(
        self,
        recorded: object,
    ) -> None:
        with pytest.raises(RuntimeError):
            _validate_recorded_performance(recorded)

    def test_validate_accepts_complete_non_negative_performance(self) -> None:
        _validate_recorded_performance(
            {
                "read_validate": {"peak_bytes": 0, "seconds": 0},
                "merge_complementary": {"peak_bytes": 1, "seconds": 0.1},
            }
        )

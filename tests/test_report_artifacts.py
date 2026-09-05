from __future__ import annotations

import json
from pathlib import Path
from runpy import run_path
import sys
from typing import Callable, cast

from jsonschema import Draft202012Validator
import pytest

from pf.report import ReportStore
from pf.schemas.report import (
    CompleteReportResult,
    IncompleteReportResult,
    PackageFloorReportV1Wire,
)


ROOT = Path(__file__).resolve().parents[1]
_generator = run_path(str(ROOT / "scripts" / "generate_report_schema.py"))
_generate_schema_main = cast(
    Callable[[], int],
    _generator["main"],
)
_generated_files = cast(
    Callable[[], dict[Path, str]],
    _generator["generated_files"],
)
SCHEMA_PATH = ROOT / "docs" / "schemas" / "package-floor-v1.schema.json"
EXAMPLE_PATHS = (
    ROOT / "docs" / "examples" / "package-floor-v1-minimal-complete.json",
    ROOT / "docs" / "examples" / "package-floor-v1-minimal-incomplete.json",
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
    def test_generate_schema_canonicalizes_const_types_across_pydantic_versions(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def schema_without_const_types(**_: object) -> dict[str, object]:
            return {
                "properties": {
                    "enabled": {"const": True},
                    "kind": {"const": "demo"},
                    "ratio": {"const": 1.5},
                    "schema_version": {"const": 1},
                },
                "type": "object",
            }

        monkeypatch.setattr(
            PackageFloorReportV1Wire,
            "model_json_schema",
            schema_without_const_types,
        )

        schema = json.loads(_generated_files()[SCHEMA_PATH])

        assert schema["properties"] == {
            "enabled": {"const": True, "type": "boolean"},
            "kind": {"const": "demo", "type": "string"},
            "ratio": {"const": 1.5, "type": "number"},
            "schema_version": {"const": 1, "type": "integer"},
        }

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
        assert schema["title"] == "PackageFloorReportV1Wire"
        assert "schema_version" in schema["required"]
        assert schema["properties"]["schema_version"]["const"] == 1
        assert all(
            definition.get("additionalProperties") is False
            for definition in schema["$defs"].values()
            if definition.get("type") == "object"
        )
        assert not _contains_key(schema, "default")
        nullable_fields = {
            (name, field)
            for name, definition in schema["$defs"].items()
            for field, field_schema in definition.get("properties", {}).items()
            if _contains_type(field_schema, "null")
        }
        assert nullable_fields == {
            ("SearchPolicyBinding", "requested_space"),
            ("CandidateSnapshotV1", "series_inventory_ref"),
        }
        for name, field in nullable_fields:
            assert field in schema["$defs"][name]["required"]

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

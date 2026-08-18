from __future__ import annotations

import pytest
from pydantic import ValidationError

from pf.schemas.base import FrozenSchema
from pf.schemas.config import EffectiveConfig, MergeRequest, SearchRequest
from pf.schemas.evaluation import ProcessResult, ProcessSpec
from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    SourceIdentity,
)


class ExampleRecord(FrozenSchema):
    name: str


def test_cross_module_records_are_strict_and_immutable() -> None:
    with pytest.raises(ValidationError):
        ExampleRecord.model_validate({"name": "pf", "unexpected": True})

    record = ExampleRecord(name="pf")
    with pytest.raises(ValidationError):
        record.name = "changed"


@pytest.mark.parametrize(
    "config",
    (
        {"python": ("python3",)},
        {"python": ("3.11", "3.10")},
        {"jobs": True},
        {"jobs": 0},
        {"test_failure_exit_codes": (1, 1)},
        {"test_failure_exit_codes": (0,)},
    ),
)
def test_effective_config_rejects_ambiguous_runtime_policy(
    config: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        EffectiveConfig.model_validate(config)


@pytest.mark.parametrize(
    "payload",
    (
        {"root": ".", "jobs": True},
        {"root": ".", "jobs": 0},
        {"root": ".", "max_duration_seconds": 0},
    ),
)
def test_search_request_rejects_invalid_scheduling(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(payload)


def test_merge_request_requires_an_input_report() -> None:
    with pytest.raises(ValidationError):
        MergeRequest(reports=(), output="merged.json")


@pytest.mark.parametrize(
    "spec",
    (
        {"argv": (), "cwd": ".", "timeout_seconds": None},
        {"argv": ("python",), "cwd": ".", "timeout_seconds": 0},
    ),
)
def test_process_spec_rejects_an_unexecutable_contract(
    spec: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProcessSpec.model_validate(spec)


@pytest.mark.parametrize(
    "facts",
    (
        {"exit_code": None, "signal": None, "start_error": None},
        {"exit_code": 0, "signal": 9, "start_error": None},
    ),
)
def test_process_result_requires_exactly_one_terminal_fact(
    facts: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProcessResult.model_validate(
            {
                "duration_seconds": 0,
                "stdout_summary": "",
                "stderr_summary": "",
                "stdout_tail": "",
                "stderr_tail": "",
                **facts,
            }
        )


@pytest.mark.parametrize(
    "cell",
    (
        {
            "package": "demo",
            "target": "linux",
            "python_minor": "3.10",
            "extra_surface": (),
        },
        {
            "package": "demo",
            "target": "x86_64-unknown-linux-gnu",
            "python_minor": "3.10",
            "extra_surface": ("gpu", "gpu"),
        },
        {
            "package": "demo",
            "target": "x86_64-unknown-linux-gnu",
            "python_minor": "3.10",
            "extra_surface": (),
            "active_declaration_ids": ("b", "a"),
        },
    ),
)
def test_cell_requires_exact_normalized_coordinates(
    cell: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Cell.model_validate(cell)


def test_candidate_snapshot_requires_unique_nonempty_versions() -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    artifact = AvailableArtifact(
        filename="demo.whl",
        kind="wheel",
        content_hash="sha256:abc",
    )
    base = {
        "dependency": "demo",
        "cell": cell,
        "policy_identity": "policy",
        "source": SourceIdentity(kind="registry"),
        "series_representatives": (),
        "digest": "digest",
    }

    with pytest.raises(ValidationError):
        CandidateSnapshot(candidates=(), **base)
    duplicate = Candidate(version="1.0", series_key="1", artifact=artifact)
    with pytest.raises(ValidationError):
        CandidateSnapshot(candidates=(duplicate, duplicate), **base)

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pf.schemas.base import FrozenSchema


class ExampleRecord(FrozenSchema):
    name: str


def test_cross_module_records_are_strict_and_immutable() -> None:
    with pytest.raises(ValidationError):
        ExampleRecord.model_validate({"name": "pf", "unexpected": True})

    record = ExampleRecord(name="pf")
    with pytest.raises(ValidationError):
        record.name = "changed"

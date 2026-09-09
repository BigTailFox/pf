from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pf import _pytest_pruning as pruning

pytestmark = pytest.mark.infra


@pytest.fixture
def pruning_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    request = tmp_path / "prune-request.json"
    request.write_text(json.dumps(["tests/test_example.py::test_failed"]))
    monkeypatch.setenv("PF_PYTEST_PRUNE_REQUEST", str(request))
    monkeypatch.setenv("PF_PYTEST_PRUNE_NONCE", "current-run")
    monkeypatch.setenv("PF_PYTEST_OBSERVER_NONCE", "current-run")
    return request


@contextmanager
def collection(config: object) -> Iterator[None]:
    """Observe the config at the hook's handoff to pytest core collection."""
    hook = pruning.pytest_cmdline_main(config)
    next(hook)
    try:
        yield
    finally:
        with pytest.raises(StopIteration):
            hook.send(None)


class TestPytestPruningSelection:
    @pytest.mark.parametrize(
        "nodeids",
        [
            ["tests/test_example.py::test_failed"],
            [
                "tests/test_example.py::test_failed[z value]",
                "tests/test_example.py::test_failed[a-中文]",
            ],
        ],
        ids=["single-case", "ordered-parameterized-cases"],
    )
    def test_pytest_cmdline_main_selects_requested_cases_before_collection(
        self, pruning_request: Path, nodeids: list[str]
    ) -> None:
        pruning_request.write_text(json.dumps(nodeids), encoding="utf-8")
        config = SimpleNamespace(args=["tests/original"], option=object())
        original_option = config.option

        with collection(config):
            assert config.args == nodeids
            assert config.option is original_option

    @pytest.mark.parametrize(
        "variable,value",
        [
            ("PF_PYTEST_PRUNE_REQUEST", None),
            ("PF_PYTEST_PRUNE_REQUEST", ""),
            ("PF_PYTEST_PRUNE_NONCE", None),
            ("PF_PYTEST_PRUNE_NONCE", ""),
            ("PF_PYTEST_OBSERVER_NONCE", None),
            ("PF_PYTEST_OBSERVER_NONCE", ""),
            ("PF_PYTEST_PRUNE_NONCE", "another-run"),
        ],
        ids=[
            "missing-request",
            "empty-request",
            "missing-prune-nonce",
            "empty-prune-nonce",
            "missing-observer-nonce",
            "empty-observer-nonce",
            "nonce-mismatch",
        ],
    )
    def test_pytest_cmdline_main_preserves_selection_without_matching_invocation(
        self,
        pruning_request: Path,
        monkeypatch: pytest.MonkeyPatch,
        variable: str,
        value: str | None,
    ) -> None:
        if value is None:
            monkeypatch.delenv(variable)
        else:
            monkeypatch.setenv(variable, value)
        config = SimpleNamespace(args=["tests/original"])

        with collection(config):
            assert config.args == ["tests/original"]

    @pytest.mark.parametrize(
        "payload",
        [None, b"\xff", b"{", b"null", b"{}", b'"test_bad.py"', b'["test_bad.py", 1]'],
        ids=[
            "missing-file",
            "invalid-utf8",
            "malformed-json",
            "null-document",
            "object-document",
            "string-document",
            "non-string-member",
        ],
    )
    def test_pytest_cmdline_main_preserves_selection_when_request_is_unusable(
        self, pruning_request: Path, payload: bytes | None
    ) -> None:
        if payload is None:
            pruning_request.unlink()
        else:
            pruning_request.write_bytes(payload)
        config = SimpleNamespace(args=["tests/original"])

        with collection(config):
            assert config.args == ["tests/original"]

    @pytest.mark.parametrize(
        "attributes",
        [{}, {"args": None}, {"args": ("tests/original",)}],
        ids=["missing-args", "null-args", "non-list-args"],
    )
    def test_pytest_cmdline_main_preserves_unsupported_collection_config(
        self, pruning_request: Path, attributes: dict[str, object]
    ) -> None:
        config = SimpleNamespace(**attributes)

        with collection(config):
            assert vars(config) == attributes

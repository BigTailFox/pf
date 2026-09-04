"""Standalone pytest plugin that replaces Config.args before core collection."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_REQUEST_VARIABLE = "PF_PYTEST_PRUNE_REQUEST"
_NONCE_VARIABLE = "PF_PYTEST_PRUNE_NONCE"
_OBSERVER_NONCE_VARIABLE = "PF_PYTEST_OBSERVER_NONCE"


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_cmdline_main(config: object):
    try:
        _replace_args(config)
    except Exception:
        pass
    yield


def _replace_args(config: object) -> None:
    request_path = os.environ.get(_REQUEST_VARIABLE)
    nonce = os.environ.get(_NONCE_VARIABLE)
    observer_nonce = os.environ.get(_OBSERVER_NONCE_VARIABLE)
    if (
        not request_path
        or not nonce
        or not observer_nonce
        or nonce != observer_nonce
    ):
        return
    payload = Path(request_path).read_bytes()
    document = json.loads(payload.decode("utf-8"))
    if type(document) is not list or any(type(item) is not str for item in document):
        return
    args = getattr(config, "args", None)
    if not isinstance(args, list):
        return
    args[:] = list(document)

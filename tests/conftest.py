from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest

from pf.schemas.project import Cell, HarnessBaseline
from process_lane import (
    assert_e2e_implies_process,
    clear_collected_items,
    collected_items,
    install_subprocess_runner_guard,
    record_collected_item,
    using_item,
)
from static_fixtures import scripted_static_request as scripted_static_request


testmon_datafile = os.environ.get("TESTMON_DATAFILE")
if testmon_datafile:
    Path(testmon_datafile).parent.mkdir(parents=True, exist_ok=True)


def empty_harness_baseline(cell: Cell) -> HarnessBaseline:
    return HarnessBaseline.from_evidence(
        cell=cell,
        declaration_ids=(),
        observations=(),
    )


@pytest.fixture
def run_cache():
    from pf.static import TyCheckCache
    with TyCheckCache() as cache:
        yield cache


def pytest_configure(config: pytest.Config) -> None:
    install_subprocess_runner_guard()


def pytest_sessionstart(session: pytest.Session) -> None:
    clear_collected_items()


def pytest_itemcollected(item: pytest.Item) -> None:
    record_collected_item(item)


def pytest_collection_finish(session: pytest.Session) -> None:
    assert_e2e_implies_process(collected_items())


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, None, None]:
    with using_item(item):
        yield

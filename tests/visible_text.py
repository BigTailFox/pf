from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import subprocess
import sys

from process_lane import require_external_process_lane


def run_pf_cli(
    *args: str,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Capture `python -m pf` as UTF-8 text, matching CLI stdio policy."""

    require_external_process_lane("run_pf_cli")
    return subprocess.run(
        [sys.executable, "-m", "pf", *args],
        cwd=cwd,
        env=None if env is None else dict(env),
        timeout=timeout,
        check=check,
        capture_output=True,
        encoding="utf-8",
    )


def run_installed_pf(
    *args: str,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Capture the installed `pf` entry via `uv run --no-sync pf`."""

    require_external_process_lane("run_installed_pf")
    return subprocess.run(
        ["uv", "run", "--no-sync", "pf", *args],
        env=None if env is None else dict(env),
        timeout=timeout,
        check=False,
        capture_output=True,
        encoding="utf-8",
    )


def run_ty_executable(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a real `ty` executable captured as text."""

    require_external_process_lane("run_ty_executable")
    return subprocess.run(
        argv,
        cwd=cwd,
        env=None if env is None else dict(env),
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
    )


def _without_box_drawing(text: str) -> str:
    return "".join(
        " " if 0x2500 <= ord(character) <= 0x257F else character
        for character in text
    )


def visible_cli_text(text: str) -> str:
    """Flatten Rich canvas chrome so semantic substrings survive wrapping.

    Panel and table rendering insert box-drawing characters and newlines
    between words. Public-copy assertions should compare this flattening
    instead of a particular terminal width.
    """

    return " ".join(_without_box_drawing(text).split())


def compact_cli_text(text: str) -> str:
    """Remove whitespace and box-drawing so identifiers survive wrapping."""

    return "".join(
        character
        for character in _without_box_drawing(text)
        if not character.isspace()
    )

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_installed_module_cli_completes_check_search_explain_apply(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "demo").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "__init__.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"

[dependency-groups]
test = []

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
managed-deps = []
test-command = ["python", "-c", "import demo; assert demo.VALUE == 1"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    for command in ("check", "search", "explain", "apply"):
        result = subprocess.run(
            [sys.executable, "-m", "pf", command],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            command,
            result.stdout,
            result.stderr,
        )

    assert (tmp_path / "package-floor.json").is_file()
    assert "demo: complete" in subprocess.run(
        [sys.executable, "-m", "pf", "explain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

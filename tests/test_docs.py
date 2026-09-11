from __future__ import annotations

from pathlib import Path
from runpy import run_path
import subprocess
from typing import Callable, cast

import pytest


ROOT = Path(__file__).resolve().parents[1]
_checker = run_path(str(ROOT / "scripts" / "check_docs.py"))
_slugify = cast(Callable[[str], str], _checker["slugify"])
_main = cast(Callable[..., int], _checker["main"])

pytestmark = pytest.mark.infra


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git", "-c", "user.name=PF tests", "-c", "user.email=pf-tests@example.invalid",
            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={root / '.no-hooks'}", "-C", str(root), *args,
        ],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def documentation_root(tmp_path: Path) -> Path:
    current = "- **状态：** 现行\n- **最后核对：** 2026-09-10\n"
    example = (
        "```toml\n[tool.pf]\n"
        'test-command = ["pytest"]\nsearch-resolution = "patch"\nmax-cells = 4\n'
        '[tool.pf.search-space-defaults]\nwith-lower-bound = "majors[declaration-1:]"\n'
        'without-lower-bound = "majors[baseline-2:]"\n```\n'
    )
    files = {
        "AGENTS.md": "[owners](docs/README.md) [vocabulary](CONTEXT.md) [tests](tests/README.md)\n",
        "CONTEXT.md": current,
        "README.md": example,
        "README.zh.md": example,
        "tests/README.md": "# Tests\n",
        "docs/README.md": current + "[D001](designs/D001-owner.md)\n",
        "docs/designs/D001-owner.md": current,
        "docs/archived/designs/D000-historical.md": "# Historical Design\n",
        "tracked.txt": "original\n",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


@pytest.fixture
def documentation_git_root(documentation_root: Path) -> Path:
    _git(documentation_root, "init", "-q")
    _git(documentation_root, "add", ".")
    _git(documentation_root, "commit", "-qm", "Initial documentation")
    return documentation_root


class TestEngineeringDocs:
    def test_heading_slugs_match_published_anchors(self) -> None:
        assert _slugify("7. 配置") == "7-配置"
        assert _slugify("2.1 P2 NO PASS 文案夸大已验证范围") == (
            "21-p2-no-pass-文案夸大已验证范围"
        )
        assert _slugify("4. R007 开放项交接") == "4-r007-开放项交接"

    def test_repository_documentation_invariants(self) -> None:
        assert _main([]) == 0

    @pytest.mark.parametrize(
        ("status", "design"),
        [
            ("进行中", "../designs/D001-owner.md"),
            ("已完成", "../archived/designs/D000-historical.md"),
        ],
        ids=["ongoing-live-design", "complete-archived-design"],
    )
    def test_plan_accepts_status_and_existing_design(
        self, documentation_root: Path, status: str, design: str,
    ) -> None:
        plan = documentation_root / "docs/plans/P001-plan.md"
        plan.parent.mkdir()
        plan.write_text(
            f"- **状态：** {status}\n- **对应 Design：** [Design]({design})\n",
            encoding="utf-8",
        )
        assert _main(["--root", str(documentation_root)]) == 0

    @pytest.mark.parametrize(
        ("metadata", "diagnostic"),
        [
            ("- **状态：** 进行中\n", "missing 对应 Design"),
            (
                "- **状态：** 草案\n- **对应 Design：** [D001](../designs/D001-owner.md)\n",
                "状态 '草案' is not allowed",
            ),
            (
                "- **状态：** 进行中\n- **对应 Design：** [missing](../designs/D002-missing.md)\n",
                "对应 Design must link to an existing Design",
            ),
            (
                "- **状态：** 进行中\n- **对应 Design：** [index](../README.md)\n",
                "对应 Design must link to an existing Design",
            ),
        ],
        ids=["missing-design", "invalid-status", "missing-target", "non-design-target"],
    )
    def test_plan_rejects_invalid_metadata(
        self, documentation_root: Path, capsys: pytest.CaptureFixture[str],
        metadata: str, diagnostic: str,
    ) -> None:
        plan = documentation_root / "docs/plans/P001-plan.md"
        plan.parent.mkdir()
        plan.write_text(metadata, encoding="utf-8")
        assert _main(["--root", str(documentation_root)]) == 1
        assert diagnostic in capsys.readouterr().err

    def test_experiment_requires_evidence_location(
        self, documentation_root: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        experiment = documentation_root / "docs/experiments/E001-experiment.md"
        experiment.parent.mkdir()
        metadata = "- **状态：** 已完成\n- **日期：** 2026-09-10\n- **性质：** Experiment\n"
        experiment.write_text(metadata, encoding="utf-8")
        assert _main(["--root", str(documentation_root)]) == 1
        assert "missing 证据位置" in capsys.readouterr().err

        experiment.write_text(
            metadata + "- **证据位置：** [saved evidence](evidence.txt)\n", encoding="utf-8",
        )
        (experiment.parent / "evidence.txt").write_text("recorded result\n", encoding="utf-8")
        assert _main(["--root", str(documentation_root)]) == 0


class TestEngineeringDocsGitComparison:
    def test_base_accepts_clean_committed_documentation(
        self, documentation_git_root: Path,
    ) -> None:
        root = documentation_git_root
        base = _git(root, "rev-parse", "HEAD")
        (root / "tracked.txt").write_text("clean change\n", encoding="utf-8")
        _git(root, "commit", "-qam", "Clean change")
        assert _main(["--root", str(root), "--base", base]) == 0

    def test_base_reports_committed_archive_deletion_and_whitespace(
        self, documentation_git_root: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        root = documentation_git_root
        base = _git(root, "rev-parse", "HEAD")
        (root / "docs/archived/designs/D000-historical.md").unlink()
        (root / "tracked.txt").write_text("trailing whitespace \n", encoding="utf-8")
        _git(root, "commit", "-qam", "Committed documentation defects")
        assert _git(root, "status", "--porcelain") == ""
        assert _main(["--root", str(root)]) == 0

        assert _main(["--root", str(root), "--base", base]) == 1
        output = capsys.readouterr().err
        assert "archived records were deleted: docs/archived/designs/D000-historical.md" in output
        assert "tracked.txt:1: trailing whitespace" in output

    def test_whitespace_ignores_experiment_evidence(
        self, documentation_git_root: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        root = documentation_git_root
        evidence = root / "docs/experiments/data/E999/apply.diff"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("+ captured trailing whitespace \n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "Captured experiment evidence")
        assert _main(["--root", str(root), "--base", "HEAD"]) == 0

        (root / "tracked.txt").write_text("trailing whitespace \n", encoding="utf-8")
        assert _main(["--root", str(root), "--base", "HEAD"]) == 1
        output = capsys.readouterr().err
        assert "tracked.txt:1: trailing whitespace" in output
        assert "apply.diff" not in output

    @pytest.mark.parametrize("staged", [False, True], ids=["worktree", "index"])
    def test_base_keeps_local_archive_and_whitespace_checks(
        self, documentation_git_root: Path, capsys: pytest.CaptureFixture[str], staged: bool,
    ) -> None:
        root = documentation_git_root
        (root / "docs/archived/designs/D000-historical.md").unlink()
        (root / "tracked.txt").write_text("trailing whitespace \n", encoding="utf-8")
        if staged:
            _git(root, "add", "-A")
        assert _main(["--root", str(root), "--base", "HEAD"]) == 1
        output = capsys.readouterr().err
        assert "archived records were deleted: docs/archived/designs/D000-historical.md" in output
        assert "tracked.txt:1: trailing whitespace" in output

    def test_base_rejects_invalid_revision(
        self, documentation_git_root: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert _main(["--root", str(documentation_git_root), "--base", "missing-revision"]) == 1
        assert "invalid --base 'missing-revision'" in capsys.readouterr().err

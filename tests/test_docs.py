from __future__ import annotations

from pathlib import Path
from runpy import run_path
from typing import Callable, cast


ROOT = Path(__file__).resolve().parents[1]
_checker = run_path(str(ROOT / "scripts" / "check_docs.py"))
_slugify = cast(Callable[[str], str], _checker["slugify"])
_main = cast(Callable[..., int], _checker["main"])


class TestEngineeringDocs:
    def test_heading_slugs_match_published_anchors(self) -> None:
        assert _slugify("7. 配置") == "7-配置"
        assert _slugify("2.1 P2 NO PASS 文案夸大已验证范围") == (
            "21-p2-no-pass-文案夸大已验证范围"
        )
        assert _slugify("4. R007 开放项交接") == "4-r007-开放项交接"

    def test_repository_documentation_invariants(self) -> None:
        assert _main([]) == 0

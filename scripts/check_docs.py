"""Check PF engineering-document invariants. Not a user CLI."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlparse

import tomli

from pf.config import ConfigLoader
from pf.project_discovery import PyprojectObservation
from pf.schemas.config import EffectiveConfig


ROOT = Path(__file__).resolve().parents[1]
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#*)?$")
HTML_ID = re.compile(r"<a\s+(?:id|name)=\"([^\"]+)\"", re.I)
MARKDOWN_LINK = re.compile(r"(?<!!)\[(?:[^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FIELD = re.compile(r"^- \*\*([^*]+)：\*\* (.+)$", re.M)
OWNER_LINK = re.compile(r"\[D\d+\]\((designs/D\d+[^)]+\.md)\)")
STATUS_BY_KIND = {
    "review": {"开放", "已解决或已移交", "已归档"},
    "concept": {"开放", "转入 Design", "关闭"},
    "experiment": {"进行中", "已完成"},
    "investigation": {"进行中", "已完成"},
}
TEMPORARY_DESIGN_STATUSES = {"草案", "已接受待实施", "实施中"}
TEMPORARY_DESIGN_FIELDS = {"状态", "目标 owner", "验收标准"}

SKIP_SCHEMES = {"http", "https", "mailto"}
LIVE_MARKDOWN_ROOTS = (
    ROOT / "AGENTS.md",
    ROOT / "CONTEXT.md",
    ROOT / "README.md",
    ROOT / "README.zh.md",
    ROOT / "tests" / "README.md",
    ROOT / "tests" / "history.md",
)


def slugify(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "").replace("*", "")
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "-", text).strip("-")


def iter_markdown(root: Path) -> list[Path]:
    files = [path for path in LIVE_MARKDOWN_ROOTS if path.is_file()]
    files.extend(sorted((root / "docs").rglob("*.md")))
    extra = root / "tests" / "execution_qualification" / "README.md"
    if extra.is_file():
        files.append(extra)
    return files


def strip_fences(text: str) -> str:
    lines: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return "\n".join(lines)


def collect_anchors(text: str) -> set[str]:
    body = strip_fences(text)
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for raw in HTML_ID.findall(body):
        anchors.add(raw)
    for line in body.splitlines():
        match = HEADING.match(line.strip())
        if match is None:
            continue
        base = slugify(match.group(2))
        if not base:
            continue
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def frontmatter(text: str) -> dict[str, str]:
    header = "\n".join(text.splitlines()[:24])
    return {name: value.strip() for name, value in FIELD.findall(header)}


def status_token(value: str) -> str:
    return re.split(r"[（；，]", value, maxsplit=1)[0].strip()


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def check_pointers(root: Path) -> list[str]:
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    errors: list[str] = []
    if "docs/README.md" not in agents:
        errors.append("AGENTS.md missing owner-map pointer to docs/README.md")
    if "CONTEXT.md" not in agents:
        errors.append("AGENTS.md missing vocabulary pointer to CONTEXT.md")
    if "tests/README.md" not in agents:
        errors.append("AGENTS.md missing repository-tests pointer to tests/README.md")
    return errors


def check_owners(root: Path) -> list[str]:
    index = (root / "docs" / "README.md").read_text(encoding="utf-8")
    listed = {
        (root / "docs" / path).resolve()
        for path in OWNER_LINK.findall(index)
        if "/appendices/" not in path
    }
    files = {
        path.resolve()
        for path in (root / "docs" / "designs").glob("D*.md")
    }
    errors: list[str] = []
    for path in sorted(listed - files):
        errors.append(f"index owner missing file: {rel(path, root)}")
    for path in sorted(files - listed):
        token = status_token(frontmatter(path.read_text(encoding="utf-8")).get("状态", ""))
        if token in TEMPORARY_DESIGN_STATUSES:
            errors.append(f"temporary Design not listed in index: {rel(path, root)}")
        else:
            errors.append(f"live Design not listed as owner: {rel(path, root)}")
    return errors


def check_frontmatter(root: Path) -> list[str]:
    errors: list[str] = []
    required_current = [
        root / "docs" / "README.md",
        root / "CONTEXT.md",
        *sorted((root / "docs" / "designs" / "appendices").glob("*.md")),
    ]
    for path in required_current:
        fields = frontmatter(path.read_text(encoding="utf-8"))
        location = rel(path, root)
        if status_token(fields.get("状态", "")) != "现行":
            errors.append(f"{location}: 状态 must be 现行")
        checked = fields.get("最后核对", "")
        if DATE.match(checked) is None:
            errors.append(f"{location}: 最后核对 must be YYYY-MM-DD")
    for path in sorted((root / "docs" / "designs").glob("D*.md")):
        fields = frontmatter(path.read_text(encoding="utf-8"))
        location = rel(path, root)
        token = status_token(fields.get("状态", ""))
        if token == "现行":
            checked = fields.get("最后核对", "")
            if DATE.match(checked) is None:
                errors.append(f"{location}: 最后核对 must be YYYY-MM-DD")
            continue
        if token not in TEMPORARY_DESIGN_STATUSES:
            errors.append(f"{location}: 状态 {token!r} is not allowed")
            continue
        missing = TEMPORARY_DESIGN_FIELDS - fields.keys()
        if missing:
            errors.append(f"{location}: missing {', '.join(sorted(missing))}")
    kinds = (
        ("review", root / "docs" / "reviews", {"状态", "日期", "性质"}),
        ("concept", root / "docs" / "concepts", {"状态", "日期", "性质"}),
        ("experiment", root / "docs" / "experiments", {"状态", "日期", "性质"}),
        ("investigation", root / "docs" / "investigations", {"状态", "日期", "性质", "证据位置"}),
    )
    for kind, directory, required in kinds:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            fields = frontmatter(path.read_text(encoding="utf-8"))
            location = rel(path, root)
            missing = required - fields.keys()
            if missing:
                errors.append(f"{location}: missing {', '.join(sorted(missing))}")
                continue
            token = status_token(fields["状态"])
            if token not in STATUS_BY_KIND[kind]:
                errors.append(f"{location}: 状态 {token!r} is not allowed")
            if DATE.match(fields["日期"]) is None:
                errors.append(f"{location}: 日期 must start with YYYY-MM-DD")
    return errors


def resolve_link(source: Path, target: str, root: Path) -> tuple[Path | None, str, str | None]:
    parsed = urlparse(target)
    if parsed.scheme in SKIP_SCHEMES:
        return None, "external", None
    if parsed.scheme and parsed.scheme not in {"", "file"}:
        return None, f"unsupported scheme {parsed.scheme}", None
    fragment = unquote(parsed.fragment) if parsed.fragment else None
    raw_path = unquote(parsed.path)
    if not raw_path:
        return source, "ok", fragment
    linked = (source.parent / raw_path).resolve()
    try:
        linked.relative_to(root.resolve())
    except ValueError:
        return linked, "outside repository", fragment
    if not linked.exists():
        return linked, "missing", fragment
    return linked, "ok", fragment


def check_links(root: Path) -> list[str]:
    errors: list[str] = []
    anchors: dict[Path, set[str]] = {}
    for path in iter_markdown(root):
        text = path.read_text(encoding="utf-8")
        anchors[path.resolve()] = collect_anchors(text)
        body = strip_fences(text)
        body = re.sub(r"`[^`]*`", "", body)
        for target in MARKDOWN_LINK.findall(body):
            linked, status, fragment = resolve_link(path, target, root)
            location = rel(path, root)
            if status == "external":
                continue
            if status != "ok" or linked is None:
                if (
                    status == "missing"
                    and linked is not None
                    and is_local_artifact_link(linked, root)
                ):
                    continue
                errors.append(f"{location}: {status} {target}")
                continue
            if fragment is None or linked.suffix.lower() != ".md":
                continue
            dest_anchors = anchors.get(linked)
            if dest_anchors is None:
                dest_anchors = collect_anchors(linked.read_text(encoding="utf-8"))
                anchors[linked] = dest_anchors
            if fragment not in dest_anchors:
                errors.append(f"{location}: missing anchor {target}")
    return errors


def example_config(text: str) -> EffectiveConfig:
    blocks = re.findall(r"```toml\n(.*?)```", text, flags=re.S)
    for block in blocks:
        if "[tool.pf]" in block and "search-resolution" in block:
            parsed = tomli.loads(block)
            document = {"project": {"name": "demo"}, **parsed}
            observation = PyprojectObservation(
                path=ROOT / "pyproject.toml",
                document=document,
            )
            return ConfigLoader().load(
                root_observation=observation,
                target_observation=observation,
            )
    raise AssertionError("README is missing the [tool.pf] configuration example")


def check_readme_examples(root: Path) -> list[str]:
    english = example_config((root / "README.md").read_text(encoding="utf-8"))
    chinese = example_config((root / "README.zh.md").read_text(encoding="utf-8"))
    errors: list[str] = []
    for label, config in (("README.md", english), ("README.zh.md", chinese)):
        if config.search.default.resolution != "patch":
            errors.append(f"{label}: search-resolution must load as patch")
        if config.scheduling.max_cells != 4:
            errors.append(f"{label}: max-cells must load as 4")
        if config.test.command != ("pytest",):
            errors.append(f"{label}: test-command must load as pytest")
        defaults = config.search.default.space_defaults
        if defaults.with_lower_bound != "majors[declaration-1:]":
            errors.append(f"{label}: with-lower-bound example drifted")
        if defaults.without_lower_bound != "majors[baseline-2:]":
            errors.append(f"{label}: without-lower-bound example drifted")
    if english != chinese:
        errors.append("README.md and README.zh.md configuration examples are not equivalent")
    return errors


def run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def is_git_work_tree(root: Path) -> bool:
    result = run_git(root, ["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def is_local_artifact_link(linked: Path, root: Path) -> bool:
    try:
        relative = linked.relative_to(root.resolve())
    except ValueError:
        return False
    if relative.name in {"package-floor.json", "uv.lock"}:
        return True
    return bool(relative.parts) and relative.parts[0] == "experiments"


def check_archive_freeze(root: Path) -> list[str]:
    if not is_git_work_tree(root):
        return []
    result = run_git(
        root,
        ["diff", "--diff-filter=D", "--name-only", "HEAD", "--", "docs/archived"],
    )
    if result.returncode != 0:
        return [f"git diff archive freeze failed: {result.stderr.strip()}"]
    deleted = [
        line
        for line in result.stdout.splitlines()
        if line and line != "docs/archived/README.md"
    ]
    if not deleted:
        return []
    return ["archived records were deleted: " + ", ".join(deleted)]


def check_whitespace(root: Path) -> list[str]:
    if not is_git_work_tree(root):
        return []
    errors: list[str] = []
    for args in (["diff", "--check"], ["diff", "--check", "--cached"]):
        result = run_git(root, args)
        if result.returncode == 0 and not result.stdout.strip() and not result.stderr.strip():
            continue
        output = (result.stdout + result.stderr).strip()
        errors.append(output or "git diff --check failed")
    return errors


def check_docs(root: Path) -> list[str]:
    errors: list[str] = []
    for checker in (
        check_pointers,
        check_owners,
        check_frontmatter,
        check_links,
        check_readme_examples,
        check_archive_freeze,
        check_whitespace,
    ):
        errors.extend(checker(root))
    return errors


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(list(argv) if argv is not None else None)
    errors = check_docs(args.root.resolve())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"{len(errors)} documentation check(s) failed", file=sys.stderr)
        return 1
    print("documentation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

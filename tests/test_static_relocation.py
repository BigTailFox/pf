from __future__ import annotations

import json
import base64
import hashlib
from pathlib import Path

import pytest

from pf.schemas.static import StaticContentManifest
from pf.static_relocation import relocate_installed_content
from pf.static_subject import StaticContentCollector


def capture(root: Path, *, suffix: str = ""):
    roots = {"snapshot": root / "source", "environment": root / "environment"}
    for path in roots.values():
        path.mkdir(parents=True)
    environment = roots["environment"]
    (environment / "bin").mkdir()
    (environment / "site" / "demo.dist-info").mkdir(parents=True)
    (environment / "bin" / "activate").write_text(f"VIRTUAL_ENV='{environment}'\n")
    (environment / "bin" / "demo").write_text(f"#!{environment}/bin/python\nprint('demo{suffix}')\n")
    (environment / "site" / "demo.pth").write_text(str(roots["snapshot"]) + "\n")
    (environment / "site" / "demo.dist-info" / "direct_url.json").write_text(json.dumps({
        "url": roots["snapshot"].as_uri(), "dir_info": {"editable": True},
    }))
    (environment / "site" / "demo.dist-info" / "uv_cache.json").write_text('{"timestamp": 17}')
    rows = []
    for relative in ("demo.pth", "demo.dist-info/direct_url.json"):
        payload = (environment / "site" / relative).read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
        rows.append(f"{relative},sha256={digest},{len(payload)}\n")
    (environment / "site" / "demo.dist-info" / "RECORD").write_text("".join(rows))
    (roots["snapshot"] / "demo.py").write_text("VALUE = 1\n")
    raw = StaticContentCollector().collect(roots)
    assert isinstance(raw, StaticContentManifest)
    return roots, raw, relocate_installed_content(raw, roots)


class TestInstalledContentRelocation:
    def test_registered_paths_relocate_without_losing_file_content(self, tmp_path: Path) -> None:
        _, raw_a, a = capture(tmp_path / "first")
        _, raw_b, b = capture(tmp_path / "second-longer-name")
        assert raw_a.identity != raw_b.identity
        assert a.identity == b.identity
        assert len([entry for entry in a.entries if entry.relocation is not None]) == 5
        assert str(tmp_path) not in a.model_dump_json()
        assert StaticContentManifest.model_validate_json(a.model_dump_json()) == a
        assert a.for_roots(frozenset({"environment"})) == a
        _, _, changed = capture(tmp_path / "changed", suffix="-changed")
        assert changed.identity != a.identity

    def test_unknown_metadata_keeps_its_actual_bytes(self, tmp_path: Path) -> None:
        roots, _, before = capture(tmp_path / "first")
        (roots["environment"] / "site" / "demo.dist-info" / "uv_cache.json").write_text('{"timestamp": 18}')
        raw = StaticContentCollector().collect(roots)
        assert isinstance(raw, StaticContentManifest)
        after = relocate_installed_content(raw, roots)
        assert after.identity != before.identity

    def test_source_literals_and_executable_pth_are_not_relocated(self, tmp_path: Path) -> None:
        roots, _, _ = capture(tmp_path / "first")
        (roots["snapshot"] / "demo.py").write_text(repr(str(roots["snapshot"])))
        (roots["environment"] / "site" / "demo.pth").write_text("import sys; sys.path.append(" + repr(str(roots["snapshot"])) + ")\n")
        raw = StaticContentCollector().collect(roots)
        assert isinstance(raw, StaticContentManifest)
        relocated = relocate_installed_content(raw, roots)
        for entry in raw.entries:
            if entry.location.path in {"demo.py", "site/demo.pth"}:
                assert relocated.entry_at(entry.location) == entry

    def test_direct_url_without_dir_info_keeps_actual_bytes(self, tmp_path: Path) -> None:
        roots, _, _ = capture(tmp_path / "first")
        (roots["environment"] / "site" / "demo.dist-info" / "direct_url.json").write_text(
            json.dumps({"url": "https://example.test/demo"}),
        )
        raw = StaticContentCollector().collect(roots)
        assert isinstance(raw, StaticContentManifest)
        relocated = relocate_installed_content(raw, roots)
        entry = next(
            item for item in relocated.entries
            if item.location.path.endswith("direct_url.json")
        )
        assert entry.relocation is None

    def test_changed_file_invalidates_captured_content(self, tmp_path: Path) -> None:
        roots, raw, _ = capture(tmp_path / "first")
        (roots["environment"] / "site" / "demo.pth").write_text("changed\n")
        with pytest.raises(ValueError, match="changed during relocation"):
            relocate_installed_content(raw, roots)

    def test_unverified_record_retains_exact_bytes(self, tmp_path: Path) -> None:
        roots, _, _ = capture(tmp_path / "first")
        record = roots["environment"] / "site/demo.dist-info/RECORD"
        record.write_text(record.read_text().replace("sha256=", "sha512="))
        raw = StaticContentCollector().collect(roots)
        assert isinstance(raw, StaticContentManifest)
        result = relocate_installed_content(raw, roots)
        entry = next(item for item in result.entries if item.location.path.endswith("/RECORD"))
        assert entry == raw.entry_at(entry.location)
        assert entry.relocation is None

    def test_reader_rejects_record_reference_to_different_file(self, tmp_path: Path) -> None:
        from pf.schemas.static import StaticTextProjection

        _, _, content = capture(tmp_path / "first")
        document = content.model_dump(mode="json")
        entry = next(item for item in document["entries"] if item["location"]["path"].endswith("/RECORD"))
        for part in entry["relocation"]["parts"]:
            if part["kind"] == "file-value":
                part["location"]["path"] = "site/different.pth"
        entry["content_digest"] = StaticTextProjection.model_validate(entry["relocation"]).identity
        with pytest.raises(ValueError, match="named file"):
            StaticContentManifest.model_validate(document)

    @pytest.mark.parametrize("fault", ["digest", "root", "source", "profile"])
    def test_reader_rejects_forged_relocation(self, tmp_path: Path, fault: str) -> None:
        _, _, content = capture(tmp_path / "first")
        document = content.model_dump(mode="json")
        entry = next(item for item in document["entries"] if item["location"]["path"] == "site/demo.pth")
        if fault == "digest":
            entry["content_digest"] = "0" * 64
        elif fault == "root":
            part = next(part for part in entry["relocation"]["parts"] if part["kind"] == "logical-root")
            part["root"] = "unregistered"
            from pf.schemas.static import StaticTextProjection
            entry["content_digest"] = StaticTextProjection.model_validate(entry["relocation"]).identity
        elif fault == "source":
            entry["location"]["root"] = "snapshot"
        else:
            entry["relocation"]["profile"] = "future"
        with pytest.raises(ValueError):
            StaticContentManifest.model_validate(document)

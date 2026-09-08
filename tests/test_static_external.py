from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil

import pytest

from pf.schemas.static import StaticContentManifest, StaticContentUnavailable
from pf.static_external import FrozenSearchRoots, freeze_external_search_roots


class TestExternalSearchRootSnapshots:
    def test_destination_cannot_modify_or_recurse_into_input_roots(self, tmp_path: Path) -> None:
        external = tmp_path / "external"
        external.mkdir()
        destination = external / "owned"
        result = freeze_external_search_roots((external,), registered={}, directory=destination)
        assert result == StaticContentUnavailable(detail="invalid-layout")
        assert not destination.exists()

    def test_overlapping_roots_preserve_layout_order_and_offline_content(self, tmp_path: Path) -> None:
        source, external = tmp_path / "source", tmp_path / "external"
        source.mkdir()
        (external / "nested").mkdir(parents=True)
        (external / "nested" / "sample.pyi").write_bytes(b"VALUE: int\n")
        result = freeze_external_search_roots(
            (external / "nested", source, external), registered={"snapshot": source},
            directory=tmp_path / "owned",
        )
        assert isinstance(result, FrozenSearchRoots)
        assert [(ref.root, ref.path) for ref in result.selections] == [
            ("external-000", "nested"), ("snapshot", "."), ("external-000", "."),
        ]
        owned = dict(result.roots)["external-000"]
        assert owned != external
        shutil.rmtree(external)
        assert (owned / "nested" / "sample.pyi").read_bytes() == b"VALUE: int\n"
        encoded = result.content.model_dump_json()
        assert str(tmp_path) not in encoded
        assert StaticContentManifest.model_validate_json(encoded) == result.content
        entry = next(entry for entry in result.content.entries if entry.location.path == "nested/sample.pyi")
        assert entry.content_digest == hashlib.sha256(b"VALUE: int\n").hexdigest()

    @pytest.mark.skipif(os.name == "nt", reason="test requires symlinks")
    def test_closed_links_point_to_owned_snapshots(self, tmp_path: Path) -> None:
        external = tmp_path / "external"
        external.mkdir()
        (external / "sample.pyi").write_bytes(b"VALUE: int\n")
        (external / "alias.pyi").symlink_to(external / "sample.pyi")
        result = freeze_external_search_roots((external,), registered={}, directory=tmp_path / "owned")
        assert isinstance(result, FrozenSearchRoots)
        owned = dict(result.roots)["external-000"]
        shutil.rmtree(external)
        assert (owned / "alias.pyi").is_symlink()
        assert (owned / "alias.pyi").resolve() == owned / "sample.pyi"
        assert (owned / "alias.pyi").read_bytes() == b"VALUE: int\n"

    @pytest.mark.skipif(os.name == "nt", reason="test requires symlinks")
    def test_unknown_link_does_not_import_an_undeclared_root(self, tmp_path: Path) -> None:
        external = tmp_path / "external"
        external.mkdir()
        (tmp_path / "unregistered.pyi").write_text("VALUE: int\n")
        (external / "alias.pyi").symlink_to(tmp_path / "unregistered.pyi")
        result = freeze_external_search_roots((external,), registered={}, directory=tmp_path / "owned")
        assert result == StaticContentUnavailable(detail="unclosed-symlink")
        assert not (tmp_path / "owned").exists()

    def test_snapshot_copy_keeps_dotfiles_and_generated_content(self, tmp_path: Path) -> None:
        external = tmp_path / "external"
        (external / ".cache").mkdir(parents=True)
        (external / ".cache" / "sample.pyi").write_text("VALUE: int\n")
        result = freeze_external_search_roots((external,), registered={}, directory=tmp_path / "owned")
        assert isinstance(result, FrozenSearchRoots)
        assert any(entry.location.path == ".cache/sample.pyi" for entry in result.content.entries)

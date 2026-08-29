from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import tomli

from pf.editor import ProjectEditor
from pf.errors import ConfigurationError
from pf.project import ProjectLoader
from pf.schemas.config import RootPackage, TargetSelector, WorkspacePackage
from pf.schemas.apply import (
    ApplyPresentationFacts,
    AuthorizedDependencyGroupEdit,
    AuthorizedPackageApply,
    AuthorizedProjectEdit,
    AuthorizedWorkspaceApply,
)
from pf.schemas.project import ApplySelector, dependency_group_key
from pf.schemas.report import PackageIdentity
from pf.snapshot import SnapshotBuilder


def _authorization(
    root: Path,
    *,
    replacements: dict[str, tuple[str, ...]] | None = None,
    selector: TargetSelector = RootPackage(),
    noop: bool = False,
) -> AuthorizedWorkspaceApply:
    project = ProjectLoader().load(root=root, selector=selector)
    snapshot = SnapshotBuilder.without_processes().build(
        root,
        owned_pyproject_paths=project.owned_pyproject_paths,
    )
    try:
        identity_by_path = {
            identity.path: identity
            for identity in snapshot.identity.pyproject_identities
        }
        package = project.target
        declarations = tuple(
            declaration
            for declaration in package.declarations
            if declaration.name == "idna" and declaration.location == "base"
        )
        if not declarations:
            raise AssertionError("editor fixture requires a base idna group")
        replacement = (
            replacements[package.pyproject_path]
            if replacements is not None
            else ("idna<4,>=3.0",)
        )
        edits = (
            ()
            if noop
            else (
                AuthorizedProjectEdit(
                    pyproject_path=package.pyproject_path,
                    expected_pyproject_identity=identity_by_path[
                        package.pyproject_path
                    ],
                    group_edits=(
                        AuthorizedDependencyGroupEdit(
                            key=dependency_group_key(declarations[0]),
                            replacement_requirements=replacement,
                        ),
                    ),
                ),
            )
        )
        package_apply = AuthorizedPackageApply(
            package=PackageIdentity(
                name=package.name,
                pyproject_path=package.pyproject_path,
                requires_python=package.requires_python,
            ),
            scope="DECLARED_MATRIX",
            declared_platforms=package.config.platform,
            selected_selectors=(
                ApplySelector(
                    sys_platform="linux",
                    platform_machine="x86_64",
                ),
            ),
            preserved_selectors=(),
            dependency_state="NOOP" if noop else "WRITABLE",
            observed_cells=1,
            authorized_edits=edits,
        )
        facts = ApplyPresentationFacts(
            observed_cells=1,
            selected_selectors=(
                ApplySelector(
                    sys_platform="linux",
                    platform_machine="x86_64",
                ),
            ),
            preserved_selectors=(),
        )
        return AuthorizedWorkspaceApply(
            mode="DEFAULT",
            expected_snapshot=snapshot.identity,
            owned_pyproject_paths=project.owned_pyproject_paths,
            package_apply=package_apply,
            presentation_facts=facts,
        )
    finally:
        snapshot.close()


def _empty_authorization(root: Path) -> AuthorizedWorkspaceApply:
    snapshot = SnapshotBuilder.without_processes().build(root)
    try:
        return AuthorizedWorkspaceApply(
            mode="DEFAULT",
            expected_snapshot=snapshot.identity,
            owned_pyproject_paths=(),
            package_apply=AuthorizedPackageApply(
                package=PackageIdentity(
                    name="demo",
                    pyproject_path="pyproject.toml",
                ),
                scope="DECLARED_MATRIX",
                declared_platforms=(),
                selected_selectors=(),
                preserved_selectors=(),
                dependency_state="NOOP",
                observed_cells=0,
                authorized_edits=(),
            ),
            presentation_facts=ApplyPresentationFacts(
                observed_cells=0,
                selected_selectors=(),
                preserved_selectors=(),
            ),
        )
    finally:
        snapshot.close()


def _write_single_project(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        """[project]
name = "demo"
version = "1" # keep version comment
dependencies = [
    "idna>2,!=2.5,<4", # keep dependency comment
    "click==8.1.8",
]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""",
        encoding="utf-8",
    )


class TestProjectEditor:
    def test_authorized_group_edit_preserves_toml_and_reauthorization_is_noop(
        self,
        tmp_path: Path,
    ) -> None:
        _write_single_project(tmp_path)
        authorization = _authorization(
            tmp_path,
            replacements={
                "pyproject.toml": ("idna!=2.5,<4,>=3.0",),
            },
        )
        editor = ProjectEditor(snapshots=SnapshotBuilder.without_processes())

        first = editor.apply(authorization=authorization, root=tmp_path)
        after_first = (tmp_path / "pyproject.toml").read_bytes()
        repeated = editor.apply(
            authorization=_authorization(tmp_path, noop=True),
            root=tmp_path,
        )

        with (tmp_path / "pyproject.toml").open("rb") as stream:
            document = tomli.load(stream)
        assert first.changed is True
        assert repeated.changed is False
        assert document["project"]["dependencies"] == [
            "idna!=2.5,<4,>=3.0",
            "click==8.1.8",
        ]
        content = after_first.decode()
        assert "# keep version comment" in content
        assert "# keep dependency comment" in content

    def test_editor_rechecks_the_authorized_workspace_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        _write_single_project(tmp_path)
        (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        authorization = _authorization(
            tmp_path,
            replacements={"pyproject.toml": ("idna!=2.5,<4,>=3.0",)},
        )
        (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        before = (tmp_path / "pyproject.toml").read_bytes()

        with pytest.raises(ConfigurationError, match="after apply authorization"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                authorization=authorization, root=tmp_path
            )

        assert (tmp_path / "pyproject.toml").read_bytes() == before

    def test_editor_rejects_pyproject_semantic_drift_before_prepare(
        self,
        tmp_path: Path,
    ) -> None:
        _write_single_project(tmp_path)
        authorization = _authorization(
            tmp_path,
            replacements={"pyproject.toml": ("idna!=2.5,<4,>=3.0",)},
        )
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text(encoding="utf-8").replace(
                "click==8.1.8",
                "click==8.1.7",
            ),
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match="pyproject identity"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                authorization=authorization, root=tmp_path
            )

    def test_editor_raw_compare_and_swap_preserves_a_concurrent_format_edit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_single_project(tmp_path)
        authorization = _authorization(
            tmp_path,
            replacements={"pyproject.toml": ("idna!=2.5,<4,>=3.0",)},
        )
        editor = ProjectEditor(snapshots=SnapshotBuilder.without_processes())
        real_prepare = editor._prepare_edit
        pyproject = tmp_path / "pyproject.toml"

        def prepare_then_format(*args, **kwargs):  # type: ignore[no-untyped-def]
            prepared = real_prepare(*args, **kwargs)
            pyproject.write_text(
                pyproject.read_text(encoding="utf-8") + "# concurrent comment\n",
                encoding="utf-8",
            )
            return prepared

        monkeypatch.setattr(editor, "_prepare_edit", prepare_then_format)

        with pytest.raises(ConfigurationError, match="after apply prepare"):
            editor.apply(authorization=authorization, root=tmp_path)

        assert "# concurrent comment" in pyproject.read_text(encoding="utf-8")
        assert ">=3.0" not in pyproject.read_text(encoding="utf-8")

    def test_editor_replaces_an_entire_existing_scoped_group(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """[project]
name = "demo"
version = "1"
dependencies = [
    "idna>=2,<4; sys_platform == 'linux' and platform_machine == 'x86_64'",
    "idna<4; sys_platform != 'linux' or platform_machine != 'x86_64'",
]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""",
            encoding="utf-8",
        )
        replacement = (
            'idna>=2,<4; sys_platform == "linux" and platform_machine == "x86_64"',
            'idna>=3,<4; sys_platform == "win32" and platform_machine == "AMD64"',
        )
        authorization = _authorization(
            tmp_path,
            replacements={"pyproject.toml": replacement},
        )

        ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
            authorization=authorization,
            root=tmp_path,
        )

        with (tmp_path / "pyproject.toml").open("rb") as stream:
            dependencies = tomli.load(stream)["project"]["dependencies"]
        assert dependencies == list(replacement)
        assert not any("!=" in raw for raw in dependencies)

    def test_editor_applies_only_the_selected_workspace_member(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
            '[tool.pf]\npython = ["3.10"]\n'
            'platform = ["x86_64-unknown-linux-gnu"]\n'
            'test-command = ["pytest"]\n',
            encoding="utf-8",
        )
        for name in ("alpha", "beta"):
            package_root = tmp_path / "packages" / name
            package_root.mkdir(parents=True)
            (package_root / "pyproject.toml").write_text(
                f'[project]\nname = "{name}"\nversion = "1"\n'
                'dependencies = ["idna<4"]\n',
                encoding="utf-8",
            )
        authorization = _authorization(
            tmp_path,
            selector=WorkspacePackage(canonical_name="alpha"),
        )

        edit = ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
            authorization=authorization, root=tmp_path
        )

        assert edit.changed is True
        with (tmp_path / "packages" / "alpha" / "pyproject.toml").open(
            "rb"
        ) as stream:
            assert tomli.load(stream)["project"]["dependencies"] == ["idna<4,>=3.0"]
        with (tmp_path / "packages" / "beta" / "pyproject.toml").open(
            "rb"
        ) as stream:
            assert tomli.load(stream)["project"]["dependencies"] == ["idna<4"]

    def test_unselected_workspace_member_drift_blocks_the_selected_edit(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
            '[tool.pf]\npython = ["3.10"]\n'
            'platform = ["x86_64-unknown-linux-gnu"]\n'
            'test-command = ["pytest"]\n',
            encoding="utf-8",
        )
        for name in ("alpha", "beta"):
            package_root = tmp_path / "packages" / name
            package_root.mkdir(parents=True)
            content = (
                f'[project]\nname = "{name}"\nversion = "1"\n'
                'dependencies = ["idna<4"]\n'
            ).encode()
            (package_root / "pyproject.toml").write_bytes(content)
        selector = WorkspacePackage(canonical_name="alpha")
        authorization = _authorization(tmp_path, selector=selector)
        alpha = tmp_path / "packages" / "alpha" / "pyproject.toml"
        beta = tmp_path / "packages" / "beta" / "pyproject.toml"
        alpha_before = alpha.read_bytes()
        beta.write_bytes(beta.read_bytes().replace(b"idna<4", b"idna<3"))

        with pytest.raises(ConfigurationError, match="after apply authorization"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                authorization=authorization, root=tmp_path
            )

        assert alpha.read_bytes() == alpha_before

    def test_editor_recovers_a_prepared_target_before_rechecking_authorization(
        self,
        tmp_path: Path,
    ) -> None:
        _write_single_project(tmp_path)
        authorization = _authorization(
            tmp_path,
            replacements={"pyproject.toml": ("idna!=2.5,<4,>=3.0",)},
        )
        pyproject = tmp_path / "pyproject.toml"
        original = pyproject.read_bytes()
        target = original.replace(b"idna>2,!=2.5,<4", b"idna!=2.5,<4,>=3.0")
        pyproject.write_bytes(target)
        state = tmp_path / ".pf"
        state.mkdir()
        backup = state / "apply-target.backup"
        backup.write_bytes(original)
        journal = state / "apply-recovery.json"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "state": "PROJECTS_REPLACED",
                    "files": [
                        {
                            "pyproject_path": "pyproject.toml",
                            "original_digest": hashlib.sha256(original).hexdigest(),
                            "target_digest": hashlib.sha256(target).hexdigest(),
                            "backup_path": ".pf/apply-target.backup",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        class StopAfterRecover(SnapshotBuilder):
            def build(
                self,
                root: Path,
                *,
                owned_pyproject_paths: tuple[str, ...] = (),
            ):
                del root, owned_pyproject_paths
                raise ConfigurationError("stop after recover")

        with pytest.raises(ConfigurationError, match="stop after recover"):
            ProjectEditor(snapshots=StopAfterRecover.without_processes()).apply(
                authorization=authorization, root=tmp_path
            )

        assert pyproject.read_bytes() == original

    def test_invalid_recovery_log_fails_before_an_empty_transaction(
        self,
        tmp_path: Path,
    ) -> None:
        journal = tmp_path / ".pf" / "apply-recovery.json"
        journal.parent.mkdir()
        journal.write_text("not JSON\n", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="invalid apply recovery log"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                authorization=_empty_authorization(tmp_path),
                root=tmp_path,
            )

    def test_editor_does_not_reload_the_project_after_authorization(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_single_project(tmp_path)
        authorization = _authorization(
            tmp_path,
            replacements={"pyproject.toml": ("idna!=2.5,<4,>=3.0",)},
        )

        def forbidden_load(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("editor must not load project semantics")

        monkeypatch.setattr(ProjectLoader, "load", forbidden_load)
        ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
            authorization=authorization,
            root=tmp_path,
        )

        assert ">=3.0" in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")

from __future__ import annotations

from pathlib import Path

import pytest

from pf.adapters.uv_lock import (
    UvLockError,
    normalize_uv_pylock_paths,
    parse_uv_pylock,
)


REGISTRY_LOCK = """
lock-version = "1.0"
created-by = "uv"
requires-python = ">=3.11"

[[packages]]
name = "Requests"
version = "2.32.5"
index = "https://user:secret@example.test/simple?token=secret"
dependencies = [{ name = "urllib3" }]

[[packages.wheels]]
name = "requests-2.32.5-py3-none-any.whl"
url = "https://user:secret@example.test/files/requests.whl?token=secret"
hashes = { sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }

[packages.sdist]
name = "requests-2.32.5.tar.gz"
url = "https://example.test/files/requests.tar.gz"
hashes = { sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }

[[packages]]
name = "urllib3"
version = "2.5.0"
index = "https://example.test/simple"

[[packages.wheels]]
name = "urllib3-2.5.0-py3-none-any.whl"
url = "https://example.test/files/urllib3.whl"
hashes = { sha256 = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc" }
"""


class TestUvPylockParser:
    @pytest.mark.parametrize(
        "package",
        (
            "1",
            '{ name = 1, directory = { path = "." } }',
            '{ name = "demo", version = "invalid version", directory = { path = "." } }',
            '{ name = "demo", version = 1, directory = { path = "." } }',
            '{ name = "demo", marker = "not a marker", directory = { path = "." } }',
            '{ name = "demo", marker = 1, directory = { path = "." } }',
            '{ name = "demo", dependencies = 1, directory = { path = "." } }',
            '{ name = "demo", dependencies = [1], directory = { path = "." } }',
            '{ name = "demo", dependencies = [{ name = 1 }], directory = { path = "." } }',
            '{ name = "demo", directory = "." }',
            '{ name = "demo", directory = {} }',
            '{ name = "demo", directory = { path = "../outside" } }',
            '{ name = "demo", vcs = 1 }',
            '{ name = "demo", vcs = { type = "hg", url = "https://example.test/x", commit-id = "abc" } }',
            '{ name = "demo", vcs = { type = "git", url = 1, commit-id = "abc" } }',
            '{ name = "demo", archive = 1 }',
            '{ name = "demo", version = "1", index = 1, wheels = [{ path = "demo.whl", hashes = { sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } }] }',
            '{ name = "demo", version = "1", wheels = 1 }',
            '{ name = "demo", version = "1", wheels = [1] }',
            '{ name = "demo", version = "1", wheels = [{ path = "../demo.whl", hashes = { sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } }] }',
            '{ name = "demo", version = "1", wheels = [{}] }',
            '{ name = "demo", version = "1", wheels = [{ path = "demo.whl" }] }',
            '{ name = "demo", version = "1", wheels = [{ path = "demo.whl", hashes = { sha256 = "short" } }] }',
            '{ name = "demo", version = "1", directory = { path = "." }, archive = { url = "https://example.test/demo.whl", hashes = { sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } } }',
        ),
    )
    def test_parser_rejects_malformed_package_evidence(self, package: str) -> None:
        content = (
            'lock-version = "1.0"\ncreated-by = "uv"\npackages = [' + package + "]\n"
        )

        with pytest.raises(UvLockError):
            parse_uv_pylock(
                content, python_version="3.11.0", target="x86_64-unknown-linux-gnu"
            )

    @pytest.mark.parametrize(
        "requires_python",
        ("1", '"not a specifier"'),
        ids=("non-string", "invalid"),
    )
    def test_parser_rejects_invalid_python_compatibility(
        self,
        requires_python: str,
    ) -> None:
        content = (
            'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = '
            + requires_python
            + "\npackages = []\n"
        )

        with pytest.raises(UvLockError):
            parse_uv_pylock(
                content, python_version="3.11.0", target="x86_64-unknown-linux-gnu"
            )

    def test_parser_rejects_duplicate_package_selections(self) -> None:
        package = (
            '{ name = "demo", version = "1", wheels = ['
            '{ path = "demo.whl", hashes = { sha256 = '
            '"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } }] }'
        )
        content = (
            'lock-version = "1.0"\ncreated-by = "uv"\npackages = ['
            + package
            + ", "
            + package
            + "]\n"
        )

        with pytest.raises(UvLockError, match="one entry per package"):
            parse_uv_pylock(
                content, python_version="3.11.0", target="x86_64-unknown-linux-gnu"
            )

    def test_parser_projects_vcs_and_local_artifact_sources(self) -> None:
        sha256 = "a" * 64
        content = f'''\
lock-version = "1.0"
created-by = "uv"
packages = [
  {{ name = "demo", vcs = {{ type = "git", url = "https://user:secret@example.test/demo.git?token=x", commit-id = "abc" }} }},
  {{ name = "tool", version = "1", wheels = [{{ path = "artifacts/tool.whl", hashes = {{ sha256 = "{sha256}" }} }}] }}
]
'''

        packages = parse_uv_pylock(
            content, python_version="3.11.0", target="x86_64-unknown-linux-gnu"
        )

        assert packages[0].source.locator == "https://example.test/demo.git"
        assert packages[1].available_artifacts[0].locator == "artifacts/tool.whl"

    @pytest.mark.parametrize(
        "content",
        (
            "not = [toml",
            'lock-version = "1.0"\ncreated-by = "uv"\n',
            'lock-version = "1.0"\ncreated-by = "uv"\npackages = [1]\n',
            'lock-version = "1.0"\ncreated-by = "uv"\npackages = [{ name = "demo", directory = "." }]\n',
            'lock-version = "1.0"\ncreated-by = "uv"\npackages = [{ name = "demo", directory = {} }]\n',
        ),
    )
    def test_normalizer_rejects_malformed_directory_evidence(
        self,
        tmp_path: Path,
        content: str,
    ) -> None:
        with pytest.raises(UvLockError):
            normalize_uv_pylock_paths(
                content,
                source_root=tmp_path,
                lock_root=tmp_path,
            )

    def test_normalizer_rejects_paths_outside_the_lock_root(
        self,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        outside = tmp_path.parent / "outside"
        content = (
            'lock-version = "1.0"\ncreated-by = "uv"\npackages = '
            f'[{{ name = "demo", directory = {{ path = "{outside}" }} }}]\n'
        )

        with pytest.raises(UvLockError):
            normalize_uv_pylock_paths(
                content,
                source_root=source,
                lock_root=tmp_path,
            )

    def test_parser_projects_registry_graph_and_secret_free_artifacts(self) -> None:
        packages = parse_uv_pylock(
            REGISTRY_LOCK, python_version="3.11.0", target="x86_64-unknown-linux-gnu"
        )

        requests = packages[0]
        assert requests.name == "requests"
        assert requests.version == "2.32.5"
        assert requests.dependencies == ("urllib3",)
        assert requests.source.locator == "https://example.test/simple"
        assert [item.kind for item in requests.available_artifacts] == [
            "sdist",
            "wheel",
        ]
        assert requests.available_artifacts[1].locator == (
            "https://example.test/files/requests.whl"
        )
        assert requests.selected_artifact is None

    def test_parser_projects_editable_and_direct_archive_sources(self) -> None:
        content = """
lock-version = "1.0"
created-by = "uv"
requires-python = ">=3.11"

[[packages]]
name = "demo"
directory = { path = ".", editable = true }

[[packages]]
name = "tool"
version = "1.0"
archive = { url = "https://example.test/tool.whl", hashes = { sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } }
"""

        packages = parse_uv_pylock(
            content, python_version="3.11.0", target="x86_64-unknown-linux-gnu"
        )

        assert packages[0].version is None
        assert packages[0].source.kind == "path"
        assert packages[1].source.kind == "url"
        assert packages[1].selected_artifact == packages[1].available_artifacts[0]

    def test_parser_normalizes_an_absolute_directory_within_the_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "source"
        dependency = source / "vendor" / "tool"
        dependency.mkdir(parents=True)
        (dependency / "pyproject.toml").write_text(
            '[project]\nname = "tool"\nversion = "1.0"\n',
            encoding="utf-8",
        )
        content = f'''\
lock-version = "1.0"
created-by = "uv"
packages = [{{ name = "tool", directory = {{ path = "{dependency.as_posix()}" }} }}]
'''

        packages = parse_uv_pylock(
            content,
            python_version="3.11.0",
            target="x86_64-unknown-linux-gnu",
            source_root=source,
        )

        assert packages[0].source.locator == "vendor/tool"
        assert packages[0].version == "1.0"

    def test_parser_resolves_relative_directories_from_the_lock_location(
        self,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        content = """\
lock-version = "1.0"
created-by = "uv"
packages = [{ name = "demo", directory = { path = "source", editable = true } }]
"""

        packages = parse_uv_pylock(
            content,
            python_version="3.11.0",
            target="x86_64-unknown-linux-gnu",
            source_root=source,
            lock_root=tmp_path,
        )

        assert packages[0].source.locator == "."
        assert packages[0].version == "0.1.0"

    def test_native_plan_normalizes_snapshot_directories_for_replica_reuse(
        self,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "source"
        dependency = source / "vendor" / "tool"
        dependency.mkdir(parents=True)
        content = f'''\
lock-version = "1.0"
created-by = "uv"
packages = [{{ name = "tool", directory = {{ path = "{dependency}" }} }}]
'''

        normalized = normalize_uv_pylock_paths(
            content,
            source_root=source,
            lock_root=tmp_path,
        )

        assert dependency.as_posix() not in normalized
        assert 'path = "source/vendor/tool"' in normalized

    def test_parser_retains_a_package_marker_from_a_single_cell_plan(self) -> None:
        content = REGISTRY_LOCK.replace(
            'requires-python = ">=3.11"',
            'requires-python = ">=3.10"',
            1,
        ).replace(
            'name = "Requests"',
            'name = "Requests"\nmarker = "python_full_version < \'3.11\'"',
            1,
        )

        packages = parse_uv_pylock(
            content, python_version="3.10.0", target="x86_64-unknown-linux-gnu"
        )

        assert packages[0].marker == 'python_full_version < "3.11"'

    @pytest.mark.parametrize(
        ("replacement", "message"),
        (
            ('lock-version = "2.0"', "unsupported pylock version"),
            ('created-by = "other"', "not created by uv"),
            ('requires-python = ">=3.12"', "does not cover"),
            ("environments = [\"sys_platform == 'linux'\"]", "environment forks"),
        ),
    )
    def test_parser_rejects_plan_identity_or_cell_drift(
        self,
        replacement: str,
        message: str,
    ) -> None:
        if replacement.startswith("lock-version"):
            content = REGISTRY_LOCK.replace('lock-version = "1.0"', replacement)
        elif replacement.startswith("created-by"):
            content = REGISTRY_LOCK.replace('created-by = "uv"', replacement)
        elif replacement.startswith("requires-python"):
            content = REGISTRY_LOCK.replace('requires-python = ">=3.11"', replacement)
        elif replacement.startswith("environments"):
            content = REGISTRY_LOCK.replace(
                'requires-python = ">=3.11"',
                f'requires-python = ">=3.11"\n{replacement}',
                1,
            )
        else:
            content = REGISTRY_LOCK.replace(
                'name = "Requests"',
                f'name = "Requests"\n{replacement}',
                1,
            )

        with pytest.raises(UvLockError, match=message):
            parse_uv_pylock(
                content, python_version="3.11.0", target="x86_64-unknown-linux-gnu"
            )

    @pytest.mark.parametrize(
        ("content", "message"),
        (
            ("not = [toml", "not valid TOML"),
            (
                'lock-version = "1.0"\ncreated-by = "uv"\n',
                "packages must be an array",
            ),
            (
                'lock-version = "1.0"\ncreated-by = "uv"\npackages = [{name="demo", version="1"}]\n',
                "one install source",
            ),
            (
                REGISTRY_LOCK.replace(
                    'dependencies = [{ name = "urllib3" }]',
                    'dependencies = [{ name = "missing" }]',
                ),
                "graph is incomplete",
            ),
        ),
    )
    def test_parser_rejects_incomplete_native_evidence(
        self,
        content: str,
        message: str,
    ) -> None:
        with pytest.raises(UvLockError, match=message):
            parse_uv_pylock(
                content, python_version="3.11.0", target="x86_64-unknown-linux-gnu"
            )


class TestActivePylockProjection:
    @pytest.mark.parametrize(
        ("python", "names", "dependencies"),
        (("3.11.0", ("demo", "tomli"), ("tomli",)), ("3.11.15", ("demo",), ())),
    )
    def test_actual_patch_selects_nodes_and_dependency_references(
        self, python, names, dependencies
    ):
        content = """
lock-version = "1.0"
created-by = "uv"
[[packages]]
name = "demo"
directory = {path = "."}
dependencies = [{name = "tomli", version = "2.4.1"}]
[[packages]]
name = "tomli"
version = "2.4.1"
marker = "python_full_version <= '3.11'"
directory = {path = "tomli"}
"""
        packages = parse_uv_pylock(
            content, python_version=python, target="x86_64-unknown-linux-gnu"
        )
        assert tuple(p.name for p in packages) == names
        assert packages[0].dependencies == dependencies

    @pytest.mark.parametrize(
        ("target", "marker"),
        (
            (
                "x86_64-unknown-linux-gnu",
                "sys_platform == 'linux' and platform_machine == 'x86_64' and os_name == 'posix' and platform_system == 'Linux'",
            ),
            ("aarch64-unknown-linux-gnu", "platform_machine == 'aarch64'"),
            (
                "aarch64-apple-darwin",
                "sys_platform == 'darwin' and platform_machine == 'arm64' and platform_system == 'Darwin'",
            ),
            (
                "x86_64-pc-windows-msvc",
                "sys_platform == 'win32' and platform_machine == 'AMD64' and os_name == 'nt' and platform_system == 'Windows'",
            ),
            ("aarch64-pc-windows-msvc", "platform_machine == 'ARM64'"),
            (
                "x86_64-unknown-linux-gnu",
                "python_version == '3.11' and implementation_name == 'cpython' and implementation_version == '3.11.15' and platform_python_implementation == 'CPython'",
            ),
        ),
    )
    def test_target_and_interpreter_select_one_mutually_exclusive_entry(
        self, target, marker
    ):
        content = f'''lock-version = "1.0"
created-by = "uv"
[[packages]]
name = "demo"
version = "1"
marker = "{marker}"
directory = {{path = "."}}
[[packages]]
name = "demo"
version = "2"
marker = "not-a-variable == 'x'"
'''.replace("not-a-variable == 'x'", "python_full_version < '3.11.1'")
        packages = parse_uv_pylock(content, python_version="3.11.15", target=target)
        assert [(p.name, p.version) for p in packages] == [("demo", "1")]

    @pytest.mark.parametrize(
        "marker",
        (
            "python_version < '0' and platform_release == 'host'",
            "python_version > '0' or platform_version == 'host'",
            "extra == 'test'",
            "'test' in extras",
            "'test' in dependency_groups",
            "os_name ~= 'posix'",
        ),
    )
    def test_unsupported_dimensions_and_comparisons_fail_closed(self, marker):
        content = f'''lock-version = "1.0"
created-by = "uv"
[[packages]]
name = "demo"
marker = "{marker}"
directory = {{path = "."}}
'''
        with pytest.raises(UvLockError):
            parse_uv_pylock(
                content, python_version="3.11.15", target="x86_64-unknown-linux-gnu"
            )

    @pytest.mark.parametrize("version", ("3.11", "invalid"))
    def test_projection_requires_actual_patch(self, version):
        with pytest.raises(UvLockError, match="actual Python"):
            parse_uv_pylock(
                'lock-version = "1.0"\ncreated-by = "uv"\npackages = []',
                python_version=version,
                target="x86_64-unknown-linux-gnu",
            )

    @pytest.mark.parametrize(
        "compatibility",
        (
            'requires-python = "<3.11.1"',
            'requires-python = "invalid"',
            "requires-python = 1",
        ),
    )
    def test_active_package_python_compatibility_is_required(self, compatibility):
        content = f"""lock-version = "1.0"
created-by = "uv"
[[packages]]
name = "demo"
{compatibility}
directory = {{path = "."}}
"""
        with pytest.raises(UvLockError, match="Python compatibility|actual Python"):
            parse_uv_pylock(
                content, python_version="3.11.15", target="x86_64-unknown-linux-gnu"
            )

    @pytest.mark.parametrize("key", ("extras", "dependency-groups", "default-groups"))
    def test_multi_use_groups_are_not_silently_selected(self, key):
        with pytest.raises(UvLockError, match="selectable groups"):
            parse_uv_pylock(
                f'lock-version = "1.0"\ncreated-by = "uv"\n{key} = ["test"]\npackages = []',
                python_version="3.11.15",
                target="x86_64-unknown-linux-gnu",
            )

    @pytest.mark.parametrize(
        "reference",
        ('{name = "missing"}', '{name = "tool", version = "3"}', '{name = "tool"}'),
    )
    def test_unknown_and_ambiguous_active_references_fail(self, reference):
        content = f"""lock-version = "1.0"
created-by = "uv"
[[packages]]
name = "demo"
directory = {{path = "."}}
dependencies = [{reference}]
[[packages]]
name = "tool"
version = "1"
directory = {{path = "one"}}
[[packages]]
name = "tool"
version = "2"
directory = {{path = "two"}}
"""
        with pytest.raises(UvLockError, match="incomplete|ambiguous"):
            parse_uv_pylock(
                content, python_version="3.11.15", target="x86_64-unknown-linux-gnu"
            )

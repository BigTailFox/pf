from __future__ import annotations

from candidate_fixtures import registry_candidates
from pf.schemas.project import RegistryCandidates

from pathlib import Path

import pytest

from pf.candidates import CandidateBuilder
from pf.errors import ConfigurationError, NoApplicableFloorError
from pf.project import ProjectLoader
from pf.schemas.project import (
    AvailableArtifact,
    AvailableCandidate,
    DependencySourceRoute,
    PackagePlan,
    SourceIdentity,
    SourcePlan,
    StaticWorkspaceMemberVersion,
    VersionPin,
)


class CandidateIndex:
    def query(self, **kwargs: object) -> RegistryCandidates:
        wheel = lambda filename: AvailableArtifact(
            filename=filename,
            kind="wheel",
            content_hash=f"sha256:{filename}",
            python_minors=("3.10",),
            targets=("x86_64-unknown-linux-gnu",),
        )
        sdist = lambda filename: AvailableArtifact(
            filename=filename,
            kind="sdist",
            content_hash=f"sha256:{filename}",
        )
        return registry_candidates((
            AvailableCandidate(version="0.9.9", artifacts=(wheel("dep-0.9.9.whl"),)),
            AvailableCandidate(
                version="1.0.0",
                yanked=True,
                artifacts=(wheel("dep-1.0.0.whl"),),
            ),
            AvailableCandidate(version="1.0.1", artifacts=(wheel("dep-1.0.1.whl"),)),
            AvailableCandidate(
                version="1.1.0rc1", artifacts=(wheel("dep-1.1.0rc1.whl"),)
            ),
            AvailableCandidate(version="1.1.0", artifacts=(sdist("dep-1.1.0.tar.gz"),)),
            AvailableCandidate(version="1.1.1", artifacts=(wheel("dep-1.1.1.whl"),)),
            AvailableCandidate(version="2.0.0", artifacts=(wheel("dep-2.0.0.whl"),)),
        ))


def configured_package(tmp_path: Path, policy: str) -> PackagePlan:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        f"""
[project]
name = "demo"
version = "0.1.0"
dependencies = ["demo-dep"]

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
{policy}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return ProjectLoader().load(root=tmp_path).target


class TestCandidateBuilder:
    @pytest.mark.parametrize("step", ["major", "minor", "patch"])
    def test_filtered_series_still_occupy_offset_positions(self, tmp_path: Path, step: str) -> None:
        class Index:
            def query(self, **kwargs):
                def candidate(version, *, yanked=False):
                    return AvailableCandidate(version=version, yanked=yanked, artifacts=(AvailableArtifact(
                        filename=f"dep-{version}.tar.gz", kind="sdist", content_hash=f"sha256:{version}"),))
                return RegistryCandidates(
                    release_versions=("1", "3", "5rc1", "7", "9", "9.1", "9.2"),
                    candidates=(candidate("1"), candidate("3", yanked=True), candidate("5rc1"),
                                candidate("9"), candidate("9.1"), candidate("9.2", yanked=True)),
                )
        package = configured_package(tmp_path, f'search-space = "majors[baseline-3:]"\nsearch-step = "{step}"')
        result = CandidateBuilder(Index()).build(package=package, cell=package.cells[0],
            baseline=(VersionPin(name="demo-dep", version="9.2"),), source_plan=SourcePlan.for_package(package, "SEARCH"))[0]
        assert result.selection.selected_keys == ((0, 3), (0, 5), (0, 7), (0, 9))
        assert [c.version for c in result.candidates] == (["9.1"] if step == "major" else ["9", "9.1"])

    def test_used_anchor_version_changes_snapshot_even_with_identical_representatives(self, tmp_path: Path) -> None:
        package = configured_package(tmp_path, 'search-space = "majors[baseline]"')
        builder = CandidateBuilder(CandidateIndex())
        snapshots = [builder.build(package=package, cell=package.cells[0],
            baseline=(VersionPin(name="demo-dep", version=version),), source_plan=SourcePlan.for_package(package, "SEARCH"))[0]
            for version in ("1.1.1", "1.1.2")]
        assert snapshots[0].candidates == snapshots[1].candidates
        assert snapshots[0].policy_identity == snapshots[1].policy_identity
        assert snapshots[0].digest != snapshots[1].digest

    def test_candidate_builder_queries_only_active_managed_project_dependencies(
        self,
        tmp_path: Path,
    ) -> None:
        class RecordingIndex:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def query(self, **kwargs: object) -> RegistryCandidates:
                dependency = kwargs["dependency"]
                assert isinstance(dependency, str)
                self.queries.append(dependency)
                return registry_candidates((
                    AvailableCandidate(
                        version="3.10",
                        artifacts=(
                            AvailableArtifact(
                                filename="idna-3.10-py3-none-any.whl",
                                kind="wheel",
                                content_hash="sha256:idna",
                                python_minors=("3.10",),
                                targets=("x86_64-unknown-linux-gnu",),
                            ),
                        ),
                    ),
                ))

        (tmp_path / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]

[dependency-groups]
test = ["pytest"]

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=tmp_path).target
        index = RecordingIndex()

        builder = CandidateBuilder(index)
        snapshots = builder.build(
            package=package,
            cell=package.cells[0],
            baseline=(VersionPin(name="idna", version="3.10"),),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert index.queries == ["idna"]
        assert [snapshot.dependency for snapshot in snapshots] == ["idna"]

        builder.build(
            package=package,
            cell=package.cells[0],
            baseline=(VersionPin(name="idna", version="3.10"),),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )
        assert index.queries == ["idna"]

    def test_candidate_builder_freezes_filtered_minor_representatives(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["demo-dep<2,!=1.0.0"]

    [tool.pf]
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    search-step = "minor"
    resolve-artifact = "wheel"
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=tmp_path).target

        snapshots = CandidateBuilder(CandidateIndex()).build(
            package=package,
            cell=package.cells[0],
            baseline=(VersionPin(name="demo-dep", version="1.1.1"),),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert len(snapshots) == 1
        assert [candidate.version for candidate in snapshots[0].candidates] == [
            "0.9.9",
            "1.0.1",
            "1.1.1",
        ]
        assert snapshots[0].series_representatives == (
            ("0.9", "0.9.9"),
            ("1.0", "1.0.1"),
            ("1.1", "1.1.1"),
        )
        assert len(snapshots[0].digest) == 64

    def test_candidate_builder_classifies_an_empty_eligible_space_as_no_floor(
        self,
        tmp_path: Path,
    ) -> None:
        class EmptyIndex:
            def query(self, **kwargs: object) -> RegistryCandidates:
                return registry_candidates(())

        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["demo-dep"]

    [tool.pf]
    search-space = "all"
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=tmp_path).target

        with pytest.raises(NoApplicableFloorError, match="empty candidate space"):
            CandidateBuilder(EmptyIndex()).build(
                package=package,
                cell=package.cells[0],
                baseline=(VersionPin(name="demo-dep", version="1.0"),),
                source_plan=SourcePlan.for_package(package, "SEARCH"),
            )

    @pytest.mark.parametrize(
        ("policy", "baseline", "expected"),
        (
            ('search-space = "majors[baseline]"', "2.0.0", ["2.0.0"]),
            (
                'search-space = "minors[baseline]"\nsearch-step = "patch"',
                "1.1.1",
                ["1.1.0", "1.1.1"],
            ),
            (
                'search-step = "patch"\n[[tool.pf.dep]]\nname = "demo-dep"\nsearch-space = ">=1.0,<1.1"',
                "1.1.1",
                ["1.0.1"],
            ),
            ('resolve-artifact = "sdist"', "1.1.1", ["1.1.0"]),
            (
                'resolve-artifact = "any"\nsearch-prereleases = true\nsearch-step = "patch"',
                "1.1.1",
                ["0.9.9", "1.0.1", "1.1.0", "1.1.1"],
            ),
        ),
    )
    def test_candidate_builder_applies_each_search_and_artifact_policy(
        self,
        tmp_path: Path,
        policy: str,
        baseline: str,
        expected: list[str],
    ) -> None:
        package = configured_package(tmp_path, policy)

        snapshots = CandidateBuilder(CandidateIndex()).build(
            package=package,
            cell=package.cells[0],
            baseline=(VersionPin(name="demo-dep", version=baseline),),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert [candidate.version for candidate in snapshots[0].candidates] == expected

    @pytest.mark.parametrize(
        ("prereleases", "expected"),
        ((False, ["0.9"]), (True, ["0.9", "1.0rc1"])),
    )
    def test_search_prereleases_only_changes_frozen_candidates(
        self,
        tmp_path: Path,
        prereleases: bool,
        expected: list[str],
    ) -> None:
        class PrereleaseIndex:
            def query(self, **kwargs: object) -> RegistryCandidates:
                def candidate(version: str) -> AvailableCandidate:
                    return AvailableCandidate(
                        version=version,
                        artifacts=(
                            AvailableArtifact(
                                filename=f"demo_dep-{version}-py3-none-any.whl",
                                kind="wheel",
                                content_hash=f"sha256:{version}",
                                python_minors=("3.10",),
                                targets=("x86_64-unknown-linux-gnu",),
                            ),
                        ),
                    )

                return registry_candidates((candidate("0.9"), candidate("1.0rc1")))

        package = configured_package(
            tmp_path,
            "\n".join(
                (
                    "[[tool.pf.dep]]",
                    'name = "demo-dep"',
                    'search-space = ">=0,<2"',
                    f"search-prereleases = {str(prereleases).lower()}",
                )
            ),
        )

        snapshot = CandidateBuilder(PrereleaseIndex()).build(
            package=package,
            cell=package.cells[0],
            baseline=(VersionPin(name="demo-dep", version="1.0rc1"),),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )[0]

        assert [candidate.version for candidate in snapshot.candidates] == expected

    def test_candidate_policy_identity_binds_selection_and_admission_policy(
        self,
        tmp_path: Path,
    ) -> None:
        first = configured_package(tmp_path / "first", "")
        unrelated = configured_package(
            tmp_path / "unrelated",
            'test-timeout = "1m"\nmax-cells = 1',
        )
        changed = configured_package(tmp_path / "changed", 'search-step = "patch"')

        def identity(package: PackagePlan) -> str:
            return CandidateBuilder(CandidateIndex()).build(
                package=package,
                cell=package.cells[0],
                baseline=(VersionPin(name="demo-dep", version="1.1.1"),),
                source_plan=SourcePlan.for_package(package, "SEARCH"),
            )[0].policy_identity

        import hashlib
        from pf.schemas.base import canonical_identity_json
        from pf.search_space import SEARCH_SPACE_PROFILE

        expected = hashlib.sha256(b"pf:candidate-policy:v1\0" + canonical_identity_json({
            "profile": SEARCH_SPACE_PROFILE,
            "policy": {"name": "demo-dep", "space": "majors[baseline-2:]", "step": "minor", "prereleases": False},
            "artifact": "any", "artifact_admission": "cell-eligibility-before-sha256",
        })).hexdigest()
        assert identity(first) == expected
        assert identity(first) == identity(unrelated)
        assert identity(first) != identity(changed)

    def test_candidate_builder_requires_baseline_and_registry_search_source(
        self,
        tmp_path: Path,
    ) -> None:
        package = configured_package(tmp_path, "")
        builder = CandidateBuilder(CandidateIndex())
        with pytest.raises(ConfigurationError, match="baseline is missing"):
            builder.build(
                package=package,
                cell=package.cells[0],
                baseline=(),
                source_plan=SourcePlan.for_package(package, "SEARCH"),
            )

        local = SourceIdentity(kind="workspace", locator="packages/demo-dep")
        local_only = package.model_copy(
            update={
                "source_routes": (
                    DependencySourceRoute(
                        dependency="demo-dep",
                        development_source=local,
                        search_source=local,
                        workspace_member_version=StaticWorkspaceMemberVersion(
                            value="1.0"
                        ),
                    ),
                )
            }
        )
        with pytest.raises(ConfigurationError, match="no registry search source"):
            builder.build(
                package=local_only,
                cell=local_only.cells[0],
                baseline=(VersionPin(name="demo-dep", version="1.1.1"),),
                source_plan=SourcePlan.for_package(local_only, "SEARCH"),
            )

    def test_candidate_builder_rejects_candidates_without_the_requested_artifact(
        self,
        tmp_path: Path,
    ) -> None:
        class WrongWheelIndex:
            def query(self, **kwargs: object) -> RegistryCandidates:
                return registry_candidates((
                    AvailableCandidate(
                        version="1.0",
                        artifacts=(
                            AvailableArtifact(
                                filename="demo.whl",
                                kind="wheel",
                                content_hash="sha256:abc",
                                python_minors=("3.11",),
                                targets=("aarch64-apple-darwin",),
                            ),
                        ),
                    ),
                ))

        package = configured_package(tmp_path, 'resolve-artifact = "any"')

        with pytest.raises(NoApplicableFloorError):
            CandidateBuilder(WrongWheelIndex()).build(
                package=package,
                cell=package.cells[0],
                baseline=(VersionPin(name="demo-dep", version="1.0"),),
                source_plan=SourcePlan.for_package(package, "SEARCH"),
            )


@pytest.mark.parametrize("defect", ("empty-hashes", "locator"))
def test_registry_inapplicable_series_occupies_search_space_offset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str) -> None:
    from io import BytesIO
    import json
    from pf.adapters.uv import UvAdapter
    from pf.adapters.process import SubprocessRunner

    package = configured_package(tmp_path, 'search-space = "majors[baseline-1:]"')
    files = [
        {"filename": f"demo_dep-{version}-py3-none-any.whl", "url": f"demo-{version}.whl", "hashes": {"sha256": "a" * 64}}
        for version in ("1.0", "3.0")
    ]
    files.append({"filename": "demo_dep-2.0-cp310-cp310-win_amd64.whl",
                  "url": "file:///demo.whl" if defect == "locator" else "demo.whl",
                  "hashes": {"sha256": "b" * 64} if defect == "locator" else {}})
    class Response(BytesIO):
        headers = {}
    monkeypatch.setattr("pf.adapters.uv.urlopen", lambda request, timeout: Response(json.dumps({"files": files}).encode()))
    result = CandidateBuilder(UvAdapter(SubprocessRunner())).build(package=package, cell=package.cells[0],
        baseline=(VersionPin(name="demo-dep", version="3.0"),), source_plan=SourcePlan.for_package(package, "SEARCH"))[0]
    assert result.series_inventory is not None
    assert result.series_inventory.series_keys == ((0, 1), (0, 2), (0, 3))
    assert result.selection.selected_keys == ((0, 2), (0, 3))
    assert [candidate.version for candidate in result.candidates] == ["3.0"]

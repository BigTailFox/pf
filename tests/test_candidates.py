from __future__ import annotations

from pathlib import Path

import pytest

from pf.candidates import CandidateBuilder
from pf.errors import ConfigurationError, NoApplicableFloorError
from pf.project import ProjectLoader
from pf.schemas.project import (
    AvailableArtifact,
    AvailableCandidate,
    PackagePlan,
    SourceIdentity,
    VersionPin,
)


class CandidateIndex:
    def query(self, **kwargs: object) -> tuple[AvailableCandidate, ...]:
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
        return (
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
        )


def configured_package(tmp_path: Path, policy: str) -> PackagePlan:
    (tmp_path / "pyproject.toml").write_text(
        f"""
[project]
name = "demo"
version = "0.1.0"
dependencies = ["demo-dep"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
{policy}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]


class TestCandidateBuilder:
    def test_candidate_builder_queries_only_active_managed_project_dependencies(
        self,
        tmp_path: Path,
    ) -> None:
        class RecordingIndex:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def query(self, **kwargs: object) -> tuple[AvailableCandidate, ...]:
                dependency = kwargs["dependency"]
                assert isinstance(dependency, str)
                self.queries.append(dependency)
                return (
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
                )

        (tmp_path / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]

[dependency-groups]
test = ["pytest"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )
        index = RecordingIndex()

        builder = CandidateBuilder(index)
        snapshots = builder.build(
            package=package,
            cell=package.cells[0],
            baseline=(VersionPin(name="idna", version="3.10"),),
        )

        assert index.queries == ["idna"]
        assert [snapshot.dependency for snapshot in snapshots] == ["idna"]

        builder.build(
            package=package,
            cell=package.cells[0],
            baseline=(VersionPin(name="idna", version="3.10"),),
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
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    release-granularity = "minor"
    distribution = "wheel"
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )

        snapshots = CandidateBuilder(CandidateIndex()).build(
            package=package,
            cell=package.cells[0],
            baseline=(VersionPin(name="demo-dep", version="1.1.1"),),
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
            def query(self, **kwargs: object) -> tuple[AvailableCandidate, ...]:
                return ()

        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["demo-dep"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )

        with pytest.raises(NoApplicableFloorError, match="empty candidate space"):
            CandidateBuilder(EmptyIndex()).build(
                package=package,
                cell=package.cells[0],
                baseline=(VersionPin(name="demo-dep", version="1.0"),),
            )

    @pytest.mark.parametrize(
        ("policy", "baseline", "expected"),
        (
            ('search-space = "current-major"', "2.0.0", ["2.0.0"]),
            (
                'search-space = "current-minor"\nrelease-granularity = "patch"',
                "1.1.1",
                ["1.1.1"],
            ),
            (
                'search-space = ["demo-dep>=1.0,<1.1"]\nrelease-granularity = "patch"',
                "1.1.1",
                ["1.0.1"],
            ),
            ('distribution = "sdist"', "1.1.1", ["1.1.0"]),
            (
                'distribution = "any"\nallow-prereleases = true\nrelease-granularity = "patch"',
                "1.1.1",
                ["0.9.9", "1.0.1", "1.1.0", "1.1.1"],
            ),
        ),
    )
    def test_candidate_builder_applies_each_search_and_distribution_policy(
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
        )

        assert [candidate.version for candidate in snapshots[0].candidates] == expected

    def test_candidate_builder_requires_baseline_and_unambiguous_source(
        self,
        tmp_path: Path,
    ) -> None:
        package = configured_package(tmp_path, "")
        builder = CandidateBuilder(CandidateIndex())
        with pytest.raises(ConfigurationError, match="baseline is missing"):
            builder.build(package=package, cell=package.cells[0], baseline=())

        original = package.declarations[0]
        conflicting = original.model_copy(
            update={
                "declaration_id": "conflicting",
                "source": SourceIdentity(
                    kind="registry",
                    locator="https://other.example/simple",
                ),
            }
        )
        cell = package.cells[0].model_copy(
            update={
                "active_declaration_ids": tuple(
                    sorted((original.declaration_id, conflicting.declaration_id))
                )
            }
        )
        ambiguous = package.model_copy(
            update={"declarations": (original, conflicting), "cells": (cell,)}
        )
        with pytest.raises(ConfigurationError, match="ambiguous source"):
            builder.build(
                package=ambiguous,
                cell=cell,
                baseline=(VersionPin(name="demo-dep", version="1.1.1"),),
            )

    def test_candidate_builder_rejects_candidates_without_the_requested_artifact(
        self,
        tmp_path: Path,
    ) -> None:
        class WrongWheelIndex:
            def query(self, **kwargs: object) -> tuple[AvailableCandidate, ...]:
                return (
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
                )

        package = configured_package(tmp_path, 'distribution = "any"')

        with pytest.raises(NoApplicableFloorError):
            CandidateBuilder(WrongWheelIndex()).build(
                package=package,
                cell=package.cells[0],
                baseline=(VersionPin(name="demo-dep", version="1.0"),),
            )

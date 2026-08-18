from __future__ import annotations

from pathlib import Path

import pytest

from pf.candidates import CandidateBuilder
from pf.errors import NoApplicableFloorError
from pf.project import ProjectLoader
from pf.schemas.project import AvailableArtifact, AvailableCandidate, VersionPin


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
            AvailableCandidate(version="1.1.0rc1", artifacts=(wheel("dep-1.1.0rc1.whl"),)),
            AvailableCandidate(version="1.1.0", artifacts=(sdist("dep-1.1.0.tar.gz"),)),
            AvailableCandidate(version="1.1.1", artifacts=(wheel("dep-1.1.1.whl"),)),
            AvailableCandidate(version="2.0.0", artifacts=(wheel("dep-2.0.0.whl"),)),
        )


def test_candidate_builder_freezes_filtered_minor_representatives(tmp_path: Path) -> None:
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
    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]

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
    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]

    with pytest.raises(NoApplicableFloorError, match="empty candidate space"):
        CandidateBuilder(EmptyIndex()).build(
            package=package,
            cell=package.cells[0],
            baseline=(VersionPin(name="demo-dep", version="1.0"),),
        )

"""Project a ResolutionPlan into static-subject-v2 bindings."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
from pathlib import Path, PurePosixPath

from packaging.version import Version

from pf.schemas.resolution import ResolutionPackage
from pf.schemas.base import canonical_identity_json
from pf.schemas.project import Cell, InterpreterIdentity
from pf.schemas.static import (
    ResolutionArtifactAvailableSet,
    ResolutionArtifactSelected,
    ResolutionArtifactSourceTree,
    ResolutionBinding,
    StaticContentUnavailable,
    StaticSubject,
    StaticSubjectCell,
    StaticSubjectInterpreter,
)


def available_set_preimage(
    artifacts: Sequence[tuple[str, str, str]],
) -> list[list[str]]:
    """Canonical JSON array shape for available-set triples."""
    unique = sorted(set(artifacts))
    return [list(item) for item in unique]


def available_set_digest(artifacts: Sequence[tuple[str, str, str]]) -> str:
    return hashlib.sha256(
        b"pf:resolution-artifacts:v1\0"
        + canonical_identity_json(available_set_preimage(artifacts))
    ).hexdigest()


def subject_interpreter(
    interpreter: InterpreterIdentity, cell: Cell,
) -> StaticSubjectInterpreter:
    version = Version(interpreter.version)
    if len(version.release) < 2:
        raise ValueError("static interpreter requires a Python minor")
    if ".".join(str(part) for part in version.release[:2]) != cell.python_minor:
        raise ValueError("static interpreter must match the Cell Python")
    return StaticSubjectInterpreter(
        implementation=interpreter.implementation, abi=interpreter.abi,
    )


def bind_resolution_package(
    package: ResolutionPackage, *, snapshot_root: Path | None = None,
) -> ResolutionBinding | StaticContentUnavailable:
    source = package.source
    if package.selected_artifact is not None:
        artifact: ResolutionArtifactSelected | ResolutionArtifactAvailableSet | ResolutionArtifactSourceTree
        artifact = ResolutionArtifactSelected(
            content_hash=package.selected_artifact.content_hash,
        )
    elif source.kind in {"registry", "url"}:
        available = package.available_artifacts
        if not available or any(not item.content_hash for item in available):
            return StaticContentUnavailable(detail="resolution-artifact-unbound")
        triples = tuple(
            (item.kind, item.filename, item.content_hash) for item in available
        )
        artifact = ResolutionArtifactAvailableSet(digest=available_set_digest(triples))
    elif source.kind == "git":
        if not source.commit:
            return StaticContentUnavailable(detail="resolution-artifact-unbound")
        artifact = ResolutionArtifactSourceTree()
    elif source.kind in {"path", "workspace"}:
        if source.locator is None:
            return StaticContentUnavailable(detail="resolution-artifact-unbound")
        if snapshot_root is not None and not _locator_inside_snapshot(
            source.locator, snapshot_root,
        ):
            return StaticContentUnavailable(detail="resolution-artifact-unbound")
        artifact = ResolutionArtifactSourceTree()
    else:
        return StaticContentUnavailable(detail="resolution-artifact-unbound")
    return ResolutionBinding(
        name=package.name, version=package.version, source=source, artifact=artifact,
    )


def resolution_projection(
    packages: Sequence[ResolutionPackage], *, snapshot_root: Path | None = None,
) -> tuple[ResolutionBinding, ...] | StaticContentUnavailable:
    bindings: list[ResolutionBinding] = []
    for package in packages:
        bound = bind_resolution_package(package, snapshot_root=snapshot_root)
        if isinstance(bound, StaticContentUnavailable):
            return bound
        bindings.append(bound)
    return tuple(sorted(bindings, key=lambda item: item.name))


def static_subject(
    *,
    source_snapshot_digest: str,
    cell: Cell,
    interpreter: InterpreterIdentity,
    packages: Sequence[ResolutionPackage],
    snapshot_root: Path | None = None,
) -> StaticSubject | StaticContentUnavailable:
    bindings = resolution_projection(packages, snapshot_root=snapshot_root)
    if isinstance(bindings, StaticContentUnavailable):
        return bindings
    try:
        projected = subject_interpreter(interpreter, cell)
    except ValueError:
        return StaticContentUnavailable(detail="installed-input-mismatch")
    return StaticSubject(
        source_snapshot_digest=source_snapshot_digest,
        cell=StaticSubjectCell.from_cell(cell),
        interpreter=projected,
        resolution_projection=bindings,
    )


def _locator_inside_snapshot(locator: str, snapshot_root: Path) -> bool:
    path = Path(locator)
    if path.is_absolute():
        try:
            path.resolve().relative_to(snapshot_root.resolve())
        except ValueError:
            return False
        return True
    parts = PurePosixPath(locator).parts
    return ".." not in parts

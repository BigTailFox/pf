from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

import pytest

from pf.adapters.uv_lock import parse_uv_pylock
from pf.resolution import ResolutionPackage
from pf.schemas.base import canonical_identity_json
from pf.schemas.policy import (
    SnapshotTyConfigMaterialized, SnapshotTyConfigUnavailable,
    TyToolVersionDistribution, TyToolVersionUnavailable,
)
from pf.schemas.project import Cell, InterpreterIdentity, SourceIdentity
from pf.schemas.static import StaticContentUnavailable, StaticSubject
from pf.static_projection import (
    available_set_digest, available_set_preimage,
    static_subject as build_static_subject,
)
from pf.static_request import may_start_ty
from pf.static_subject import ty_check_key
from pf.policy import guidance_policy
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import ProcessResult, ProcessTerminalUnavailable, ToolFailure, TyCheck, TyDiagnostic
from pf.schemas.ty_fact import TyCheckFact, TyCheckUnavailable, TyFactDocument
from pf.ty_fact import ty_fact_document
from test_uv_lock import REGISTRY_LOCK


@pytest.fixture
def static_subject() -> StaticSubject:
    cell = Cell(
        package="demo", target="x86_64-unknown-linux-gnu",
        python_minor="3.10", extra_surface=(),
    )
    interpreter = InterpreterIdentity(implementation="cpython", version="3.10.19", abi="cp310")
    packages = (
        ResolutionPackage(
            name="demo", version="1",
            source=SourceIdentity(kind="path", locator="."),
        ),
    )
    subject = build_static_subject(
        source_snapshot_digest="a" * 64, cell=cell, interpreter=interpreter, packages=packages,
    )
    assert isinstance(subject, StaticSubject)
    return subject


def _observation_policy(**overrides):
    payload = {
        "tool_version": TyToolVersionDistribution(version="0.0.74"),
        "snapshot_ty_config": SnapshotTyConfigMaterialized(digest="c" * 64),
    }
    payload.update(overrides)
    return guidance_policy(EffectiveConfig(), **payload).observation


class TestStaticSubjectIdentity:
    @pytest.mark.parametrize("field", ["source_snapshot_digest", "cell", "interpreter", "resolution_projection"])
    def test_each_preimage_field_changes_identity(self, static_subject, field) -> None:
        document = static_subject.model_dump(mode="json")
        if field == "source_snapshot_digest":
            document[field] = "c" * 64
        elif field == "cell":
            document[field]["python_minor"] = "3.11"
        elif field == "interpreter":
            document[field]["abi"] = "cp311"
        else:
            document[field] = []
        changed = StaticSubject.model_validate(document)
        assert changed.identity != static_subject.identity

    def test_round_trip_preserves_full_preimage_without_dynamic_identity(self, static_subject) -> None:
        payload = static_subject.model_dump_json()
        restored = StaticSubject.model_validate_json(payload)
        assert restored == static_subject
        assert restored.identity == static_subject.identity
        assert restored.model_dump_json() == payload
        projected = static_subject.model_dump(mode="json", exclude_none=True)
        assert StaticSubject.model_validate(projected) == static_subject

    def test_key_uses_subject_and_cache_identity(self, static_subject) -> None:
        static = guidance_policy(
            EffectiveConfig(),
            tool_version=TyToolVersionDistribution(version="1.0.0"),
            snapshot_ty_config=SnapshotTyConfigMaterialized(digest="c" * 64),
        )
        key = ty_check_key(static_subject, static.observation)
        changed = guidance_policy(
            EffectiveConfig.model_validate({"ty": {"timeout_seconds": 41}}),
            tool_version=TyToolVersionDistribution(version="1.0.0"),
            snapshot_ty_config=SnapshotTyConfigMaterialized(digest="c" * 64),
        )
        assert key == ty_check_key(static_subject, changed.observation)
        assert key.subject_identity == static_subject.identity
        assert key.cache_identity == static.observation.cache_identity
        assert static.observation.identity != changed.observation.identity


class TestStaticSubjectAdmission:
    @pytest.mark.parametrize("fault", [
        "missing-version", "unknown-version", "missing-field", "unknown-field",
        "unsorted-projection", "interpreter-version-leaked",
    ])
    def test_reader_rejects_incomplete_or_inconsistent_inputs(self, static_subject, fault) -> None:
        document = static_subject.model_dump(mode="json")
        if fault == "missing-version":
            document.pop("source_snapshot_digest")
        elif fault == "unknown-version":
            document["projection"] = "static-subject-v1"
        elif fault == "missing-field":
            document.pop("resolution_projection")
        elif fault == "unknown-field":
            document["proposal_id"] = "cannot-substitute-for-projection"
        elif fault == "unsorted-projection":
            document["resolution_projection"] = list(reversed(document["resolution_projection"]))
            document["resolution_projection"].append(document["resolution_projection"][0])
        else:
            document["interpreter"]["version"] = "3.10.19"
        with pytest.raises(ValueError):
            StaticSubject.model_validate(document)


class TestAvailableSetGolden:
    def test_canonical_identity_json_encodes_triples_as_arrays(self) -> None:
        triples = (
            ("wheel", "requests-2.32.5-py3-none-any.whl", "sha256:" + "a" * 64),
            ("sdist", "requests-2.32.5.tar.gz", "sha256:" + "b" * 64),
            ("wheel", "requests-2.32.5-py3-none-any.whl", "sha256:" + "a" * 64),
        )
        preimage = available_set_preimage(triples)
        encoded = canonical_identity_json(preimage)
        assert encoded == (
            b'[["sdist","requests-2.32.5.tar.gz","sha256:' + b"b" * 64
            + b'"],["wheel","requests-2.32.5-py3-none-any.whl","sha256:' + b"a" * 64
            + b'"]]'
        )
        assert available_set_digest(triples) == hashlib.sha256(
            b"pf:resolution-artifacts:v1\0" + encoded
        ).hexdigest()


class TestRegistryPlanSubject:
    def test_ordinary_registry_plan_forms_available_set_and_may_start_ty(self) -> None:
        packages = parse_uv_pylock(
            REGISTRY_LOCK, python_version="3.11.0", target="x86_64-unknown-linux-gnu",
        )
        assert packages[0].selected_artifact is None
        assert packages[1].name == "urllib3"
        cell = Cell(
            package="demo", target="x86_64-unknown-linux-gnu",
            python_minor="3.11", extra_surface=(),
        )
        interpreter = InterpreterIdentity(
            implementation="cpython", version="3.11.0", abi="cp311",
        )
        subject = build_static_subject(
            source_snapshot_digest="d" * 64, cell=cell, interpreter=interpreter,
            packages=packages,
        )
        assert isinstance(subject, StaticSubject)
        assert {item.artifact.kind for item in subject.resolution_projection} == {"available-set"}
        policy = _observation_policy()
        assert may_start_ty(policy)
        assert not may_start_ty(policy, tool_version_matches=False)
        assert not may_start_ty(_observation_policy(
            snapshot_ty_config=SnapshotTyConfigUnavailable(reason="undeclared-analysis-root"),
        ))
        assert not may_start_ty(_observation_policy(tool_version=TyToolVersionUnavailable()))
        assert "RECORD" not in subject.model_dump_json()


class TestRawTyFactCodec:
    @staticmethod
    def observation_policy(subject):
        del subject
        return _observation_policy()

    def test_complete_fact_preserves_multiplicity_and_round_trips_without_files(self, static_subject, tmp_path) -> None:
        diagnostic = TyDiagnostic(identity="snapshot|demo.py|1|invalid-assignment", origin="snapshot",
                                  path="demo.py", line=1, column=None, code="invalid-assignment",
                                  severity="major", message="incompatible assignment")
        outcome = TyCheck(process=ProcessResult(exit_code=1, duration_seconds=2), diagnostics=(diagnostic, diagnostic))
        policy = self.observation_policy(static_subject)
        document = ty_fact_document(static_subject, policy, outcome)
        assert isinstance(document.fact, TyCheckFact)
        assert document.fact.diagnostics == (diagnostic, diagnostic)
        encoded = document.model_dump_json()
        for child in tmp_path.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
        restored = TyFactDocument.model_validate_json(encoded)
        assert restored.fact_identity == document.fact.identity
        assert restored.model_dump_json() == encoded
        assert TyFactDocument.model_validate_json(document.model_dump_json(exclude_none=True)) == document
        one = ty_fact_document(static_subject, policy, TyCheck(process=outcome.process, diagnostics=(diagnostic,)))
        assert one.fact_identity != document.fact_identity

    def test_fact_identity_ignores_timing_and_display_but_binds_terminal(self, static_subject) -> None:
        policy = self.observation_policy(static_subject)
        original = TyDiagnostic(identity="snapshot|demo.py|1|invalid-assignment", origin="snapshot",
                                path="demo.py", line=1, column=None, code="invalid-assignment", severity="major", message="first message")
        shown = original.model_copy(update={"severity": "minor", "message": "second message"})
        first = ty_fact_document(static_subject, policy, TyCheck(process=ProcessResult(exit_code=1, duration_seconds=1), diagnostics=(original,)))
        second = ty_fact_document(static_subject, policy, TyCheck(process=ProcessResult(exit_code=1, duration_seconds=20), diagnostics=(shown,)))
        assert first.fact_identity == second.fact_identity
        assert isinstance(second.fact, TyCheckFact)
        assert second.fact.diagnostics == (shown,)
        different_exit = ty_fact_document(static_subject, policy, TyCheck(process=ProcessResult(exit_code=0, duration_seconds=1), diagnostics=(original,)))
        assert different_exit.fact_identity != first.fact_identity

    @pytest.mark.parametrize("process,reason", [
        (ProcessResult(exit_code=1, duration_seconds=1, timed_out=True), "timeout"),
        (ProcessResult(start_error="cannot launch", duration_seconds=0), "start-failed"),
        (ProcessResult(signal=9, duration_seconds=1), "signal"),
        (ProcessResult(exit_code=2, duration_seconds=1), "exit-code"),
        (ProcessResult(exit_code=1, duration_seconds=1, stdout_complete=False), "output-incomplete"),
        (ProcessResult(exit_code=0, duration_seconds=1), "invalid-output"),
        (ProcessTerminalUnavailable(), "terminal-unavailable"),
    ])
    def test_only_actual_ty_failure_becomes_typed_unavailable(self, static_subject, process, reason) -> None:
        outcome = ToolFailure(cause="TOOL_FAILURE", stage="ty", process=process)
        document = ty_fact_document(static_subject, self.observation_policy(static_subject), outcome)
        assert isinstance(document.fact, TyCheckUnavailable)
        assert document.fact.reason == reason
        assert TyFactDocument.model_validate_json(document.model_dump_json()) == document
        other = outcome.model_copy(update={"stage": "resolve-project"})
        with pytest.raises(ValueError, match="actual ty outcome"):
            ty_fact_document(static_subject, self.observation_policy(static_subject), other)

    @pytest.mark.parametrize("fault", ["subject", "policy", "fact", "failure-terminal"])
    def test_reader_recomputes_saved_inputs_and_rejects_forged_bindings(self, static_subject, fault) -> None:
        outcome = ToolFailure(cause="TOOL_FAILURE", stage="ty", process=ProcessResult(exit_code=2, duration_seconds=1))
        document = ty_fact_document(static_subject, self.observation_policy(static_subject), outcome).model_dump(mode="json")
        if fault == "subject":
            document["subject"]["source_snapshot_digest"] = "f" * 64
        elif fault == "policy":
            document["observation_policy"]["tool_version"] = {"kind": "unavailable"}
        elif fault == "fact":
            document["fact_identity"] = "f" * 64
        else:
            document["fact"]["terminal"]["exit_code"] = 0
        with pytest.raises(ValueError):
            TyFactDocument.model_validate(document)


class TestResolutionProjectionUnavailable:
    def test_registry_without_available_artifacts_is_unbound(self) -> None:
        cell = Cell(
            package="demo", target="x86_64-unknown-linux-gnu",
            python_minor="3.10", extra_surface=(),
        )
        interpreter = InterpreterIdentity(implementation="cpython", version="3.10.19", abi="cp310")
        packages = (
            ResolutionPackage(name="demo", version="1", source=SourceIdentity(kind="registry")),
        )
        result = build_static_subject(
            source_snapshot_digest="a" * 64, cell=cell, interpreter=interpreter, packages=packages,
        )
        assert result == StaticContentUnavailable(detail="resolution-artifact-unbound")

    def test_path_locator_outside_snapshot_is_unbound(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        cell = Cell(
            package="demo", target="x86_64-unknown-linux-gnu",
            python_minor="3.10", extra_surface=(),
        )
        interpreter = InterpreterIdentity(implementation="cpython", version="3.10.19", abi="cp310")
        packages = (
            ResolutionPackage(
                name="demo", version="1",
                source=SourceIdentity(kind="path", locator=str(tmp_path / "outside")),
            ),
        )
        result = build_static_subject(
            source_snapshot_digest="a" * 64, cell=cell, interpreter=interpreter,
            packages=packages, snapshot_root=snapshot,
        )
        assert result == StaticContentUnavailable(detail="resolution-artifact-unbound")

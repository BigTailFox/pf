from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil

import pytest

from pf.schemas.static import StaticContentManifest, StaticContentUnavailable, StaticSubject
from pf.static_subject import StaticContentCollector, ty_check_key
from pf.policy import guidance_policy
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import ProcessResult, ProcessTerminalUnavailable, ToolFailure, TyCheck, TyDiagnostic
from pf.schemas.ty_fact import TyCheckFact, TyCheckUnavailable, TyFactDocument
from pf.ty_fact import ty_fact_document


@pytest.fixture
def static_subject(tmp_path: Path) -> StaticSubject:
    for name in ("snapshot", "interpreter", "environment"):
        (tmp_path / name).mkdir()
    (tmp_path / "snapshot" / "pyproject.toml").write_bytes(b"[project]\nname='demo'\nversion='1'\n")
    (tmp_path / "snapshot" / "demo.py").write_bytes(b"x = 1\n")
    (tmp_path / "interpreter" / "python").write_bytes(b"interpreter bytes")
    (tmp_path / "interpreter" / "lib").mkdir()
    (tmp_path / "interpreter" / "lib" / "builtins.pyi").write_bytes(b"class object: ...")
    (tmp_path / "environment" / "demo.pyi").write_bytes(b"x: int\n")
    collector = StaticContentCollector()
    manifests = {}
    for name in ("snapshot", "interpreter", "environment"):
        content = collector.collect({name: tmp_path / name})
        assert isinstance(content, StaticContentManifest)
        manifests[name] = content.model_dump(mode="json")

    def ref(root, path="."):
        return {"root": root, "path": path}

    return StaticSubject.model_validate({
        "projection": "static-subject-v1",
        "source": {
            "snapshot_identity": "a" * 64, "content": manifests["snapshot"],
            "packages": [{"package": "demo", "source": ref("snapshot")}],
            "source_plan": {"source_mode": "SEARCH", "routes": []},
        },
        "target": {
            "cell": {"package": "demo", "target": "x86_64-unknown-linux-gnu", "python_minor": "3.10", "extra_surface": []},
            "interpreter": {"implementation": "cpython", "version": "3.10.19", "abi": "cp310"},
            "content": manifests["interpreter"], "executable": ref("interpreter", "python"),
            "stdlib_roots": [ref("interpreter", "lib")],
        },
        "installed_world": {
            "content": manifests["environment"], "support_files": [],
            "nodes": [{
                "name": "demo", "version": "1", "source": {"kind": "path", "locator": "."},
                "artifact": None, "dependencies": [], "install_mode": "source",
                "source_mapping": None, "files": [ref("environment", "demo.pyi")],
            }],
        },
        "analysis_layout": {
            "project_root": ref("snapshot"),
            "targets": [ref("snapshot")], "cwd": ref("snapshot"),
            "import_roots": [ref("snapshot"), ref("environment")],
            "type_roots": [ref("interpreter", "lib")],
            "root_placements": [
                {"root": root, "location": ref("materialization", root)}
                for root in ("environment", "interpreter", "snapshot")
            ],
        },
        "configuration": {
            "content": manifests["snapshot"],
            "effective_file": ref("snapshot", "pyproject.toml"),
            "files_in_precedence_order": [ref("snapshot", "pyproject.toml")],
            "discovery_boundaries": [ref("snapshot")], "external_roots": [],
        },
        "process_context": {
            "environment": [{"name": "LANG", "value_digest": "b" * 64, "logical_paths": []}],
            "filesystem_case": "sensitive", "environment_case": "sensitive",
        },
    })


class TestStaticContentCollector:
    def test_collect_binds_actual_installed_bytes_and_round_trips_offline(
        self, tmp_path: Path
    ) -> None:
        installed = tmp_path / "installed"
        installed.mkdir()
        metadata = installed / "METADATA"
        metadata.write_text("Name: example\nVersion: 1.0\n", encoding="utf-8")
        stub = installed / "module.pyi"
        stub.write_bytes(b"x: int\n")
        collector = StaticContentCollector()
        original = collector.collect({"environment": installed})
        assert isinstance(original, StaticContentManifest)
        file = next(item for item in original.entries if item.location.path == "module.pyi")
        assert file.content_digest == hashlib.sha256(b"x: int\n").hexdigest()
        encoded = original.model_dump_json()

        stub.write_bytes(b"x: str\n")
        changed = collector.collect({"environment": installed})
        assert isinstance(changed, StaticContentManifest)
        assert changed.identity != original.identity
        shutil.rmtree(installed)
        restored = StaticContentManifest.model_validate_json(encoded)
        assert restored.identity == original.identity
        assert restored.model_dump_json() == encoded

    def test_collect_preserves_logical_layout_across_materialization_roots(
        self, tmp_path: Path
    ) -> None:
        first = tmp_path / "first"
        first.mkdir()
        (first / "module.py").write_bytes(b"x = 1\n")
        (first / "empty").mkdir()
        second = tmp_path / "second"
        shutil.copytree(first, second)
        collector = StaticContentCollector()
        a = collector.collect({"snapshot": first})
        b = collector.collect({"snapshot": second})
        assert isinstance(a, StaticContentManifest)
        assert isinstance(b, StaticContentManifest)
        assert a.identity == b.identity
        (second / "module.py").rename(second / "renamed.py")
        c = collector.collect({"snapshot": second})
        assert isinstance(c, StaticContentManifest)
        assert c.identity != a.identity
        assert any(item.location.path == "empty" for item in a.entries)
        assert str(tmp_path) not in a.model_dump_json()

    @pytest.mark.skipif(os.name == "nt", reason="test requires symlink creation")
    def test_collect_closes_cross_root_symlinks_without_host_paths(
        self, tmp_path: Path
    ) -> None:
        interpreter = tmp_path / "python"
        interpreter.mkdir()
        executable = interpreter / "python"
        executable.write_bytes(b"interpreter contents")
        environment = tmp_path / "venv"
        environment.mkdir()
        (environment / "python").symlink_to(executable)
        result = StaticContentCollector().collect({
            "environment": environment, "interpreter": interpreter,
        })
        assert isinstance(result, StaticContentManifest)
        link = next(item for item in result.entries if item.kind == "symlink")
        assert link.link_target is not None
        assert link.link_target.root == "interpreter"
        assert link.link_target.path == "python"
        assert str(tmp_path) not in result.model_dump_json()

    @pytest.mark.skipif(os.name == "nt", reason="test requires symlink creation")
    def test_collect_does_not_discover_unregistered_external_inputs(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "root"
        root.mkdir()
        external = tmp_path / "external.pyi"
        external.write_bytes(b"x: int")
        (root / "module.pyi").symlink_to(external)
        result = StaticContentCollector().collect({"snapshot": root})
        assert result == StaticContentUnavailable(detail="unclosed-symlink")

    @pytest.mark.skipif(os.name == "nt", reason="test requires FIFOs")
    def test_collect_rejects_special_files_without_opening_them(self, tmp_path: Path) -> None:
        os.mkfifo(tmp_path / "fifo")
        assert StaticContentCollector().collect({"snapshot": tmp_path}) == (
            StaticContentUnavailable(detail="unsupported-file-kind")
        )


class TestStaticContentManifestAdmission:
    @pytest.mark.parametrize("fault", ["version", "digest", "parent", "extra", "order"])
    def test_reader_rejects_unverifiable_content(self, tmp_path: Path, fault: str) -> None:
        (tmp_path / "a.py").write_bytes(b"x = 1")
        collected = StaticContentCollector().collect({"snapshot": tmp_path})
        assert isinstance(collected, StaticContentManifest)
        document = collected.model_dump(mode="json")
        if fault == "version":
            document["format"] = "unknown"
        elif fault == "digest":
            document["entries"][1]["content_digest"] = "not-a-digest"
        elif fault == "parent":
            document["entries"].pop(0)
        elif fault == "extra":
            document["unregistered"] = "input"
        else:
            document["entries"].reverse()
        with pytest.raises(ValueError):
            StaticContentManifest.model_validate(document)


class TestStaticSubjectIdentity:
    @pytest.mark.parametrize("group", ["source", "target", "installed_world", "analysis_layout", "configuration", "process_context"])
    def test_each_input_group_changes_identity(self, static_subject, group) -> None:
        document = static_subject.model_dump(mode="json")
        if group == "source":
            document[group]["snapshot_identity"] = "c" * 64
        elif group == "target":
            document[group]["interpreter"]["version"] = "3.10.20"
        elif group == "installed_world":
            document[group]["content"]["entries"][-1]["content_digest"] = "d" * 64
        elif group == "analysis_layout":
            document[group]["import_roots"].reverse()
        elif group == "configuration":
            document[group]["files_in_precedence_order"].insert(0, {"root": "snapshot", "path": "demo.py"})
        else:
            document[group]["environment"][0]["value_digest"] = "e" * 64
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

    def test_key_uses_static_inputs_and_collection_policy(self, static_subject) -> None:
        config = EffectiveConfig()
        static = guidance_policy(
            config, tool_version="1", tool_content=static_subject.target.content,
            executable=static_subject.target.executable,
        )
        key = ty_check_key(static_subject, static.observation)
        changed = guidance_policy(
            EffectiveConfig.model_validate({"test": {"timeout_seconds": 41}}),
            tool_version="1", tool_content=static_subject.target.content,
            executable=static_subject.target.executable,
        )
        assert key == ty_check_key(static_subject, changed.observation)
        assert key.subject_identity == static_subject.identity
        assert key.observation_policy_identity == static.observation_identity


class TestStaticSubjectAdmission:
    @pytest.mark.parametrize("fault", ["missing-version", "unknown-version", "missing-group", "unknown-field", "conflicting-content", "dangling-path", "missing-node-file", "unclosed-graph", "wrong-python", "environment-case"])
    def test_reader_rejects_incomplete_or_inconsistent_inputs(self, static_subject, fault) -> None:
        document = static_subject.model_dump(mode="json")
        if fault == "missing-version":
            document.pop("projection")
        elif fault == "unknown-version":
            document["projection"] = "static-subject-future"
        elif fault == "missing-group":
            document.pop("process_context")
        elif fault == "unknown-field":
            document["proposal_id"] = "cannot-substitute-for-projection"
        elif fault == "conflicting-content":
            document["configuration"]["content"]["entries"][-1]["content_digest"] = "f" * 64
        elif fault == "dangling-path":
            document["analysis_layout"]["targets"] = [{"root": "external", "path": "."}]
        elif fault == "missing-node-file":
            document["installed_world"]["nodes"][0]["files"] = [{"root": "environment", "path": "missing.pyi"}]
        elif fault == "unclosed-graph":
            document["installed_world"]["nodes"][0]["dependencies"] = ["missing"]
        elif fault == "wrong-python":
            document["target"]["interpreter"]["version"] = "3.11.1"
        else:
            document["process_context"]["environment_case"] = "insensitive"
            document["process_context"]["environment"].append({"name": "lang", "value_digest": "c" * 64, "logical_paths": []})
        with pytest.raises(ValueError):
            StaticSubject.model_validate(document)


class TestRawTyFactCodec:
    @staticmethod
    def observation_policy(subject):
        return guidance_policy(EffectiveConfig(), tool_version="0.0.74",
                               tool_content=subject.target.content,
                               executable=subject.target.executable).observation

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
            document["subject"]["process_context"]["environment"][0]["value_digest"] = "f" * 64
        elif fault == "policy":
            document["observation_policy"]["tool_version"] = "different-tool"
        elif fault == "fact":
            document["fact_identity"] = "f" * 64
        else:
            document["fact"]["terminal"]["exit_code"] = 0
        with pytest.raises(ValueError):
            TyFactDocument.model_validate(document)

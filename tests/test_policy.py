from __future__ import annotations

from pathlib import Path

import pytest

from pf.errors import ConfigurationError
from pf.policy import (
    execution_policy,
    guidance_policy,
    guidance_policy_identity,
    report_provenance_identity,
    search_derivation_identity,
    search_derivation_policy,
)
from pf.schemas.config import EffectiveConfig
from pf.schemas.policy import ExecutionPolicy, GuidancePolicy, SearchDerivationPolicy
from pf.schemas.static import StaticContentManifest, StaticContentPath
from pf.static_subject import StaticContentCollector


@pytest.fixture
def tool_content(tmp_path: Path) -> StaticContentManifest:
    (tmp_path / "ty").write_bytes(b"exact ty executable")
    (tmp_path / "typeshed").mkdir()
    (tmp_path / "typeshed" / "builtins.pyi").write_bytes(b"class object: ...")
    manifest = StaticContentCollector().collect({"tool": tmp_path})
    assert isinstance(manifest, StaticContentManifest)
    return manifest


def guidance(config: EffectiveConfig, content: StaticContentManifest) -> GuidancePolicy:
    return guidance_policy(
        config, tool_version="1.0.0", tool_content=content,
        executable=StaticContentPath(root="tool", path="ty"),
    )


class TestPolicyIdentitySeparation:
    @pytest.mark.parametrize(
        "section,values,execution_changes,observation_changes,search_changes",
        [
            ("ty", {"timeout_seconds": 73}, False, True, True),
            ("ty", {"args": ["--error-on-warning"]}, False, True, True),
            ("test", {"timeout_seconds": 91}, True, False, False),
            ("test", {"command": ["python", "-m", "unittest"]}, True, False, False),
            ("resolution", {"artifact": "wheel"}, True, False, False),
            ("search", {"default": {"resolution": "patch"}}, False, False, True),
            ("scheduling", {"ty_jobs": 2}, False, False, False),
        ],
        ids=["ty-timeout", "ty-options", "verifier-timeout", "verifier-command", "artifact", "candidates", "permits"],
    )
    def test_factories_separate_authority_observation_and_search(
        self, tool_content, section, values, execution_changes, observation_changes, search_changes,
    ) -> None:
        original = EffectiveConfig()
        document = original.model_dump(mode="json")
        document[section].update(values)
        changed = EffectiveConfig.model_validate(document)
        a, b = guidance(original, tool_content), guidance(changed, tool_content)
        assert (execution_policy(original).identity != execution_policy(changed).identity) is execution_changes
        assert (a.observation_identity != b.observation_identity) is observation_changes
        assert (a.identity != b.identity) is observation_changes
        assert (
            search_derivation_policy(original, guidance=a).identity
            != search_derivation_policy(changed, guidance=b).identity
        ) is search_changes

    def test_mechanical_policy_changes_do_not_change_ty_observation(self, tool_content) -> None:
        config = EffectiveConfig()
        policy = guidance(config, tool_content)
        first = search_derivation_policy(config, guidance=policy, small_threshold=8)
        second = search_derivation_policy(config, guidance=policy, small_threshold=3)
        assert first.identity != second.identity
        assert first.guidance_identity == second.guidance_identity == policy.identity
        assert policy.observation_identity == policy.observation.identity

    def test_report_identities_split_execution_guidance_and_search(self) -> None:
        original = EffectiveConfig()
        ty_changed = EffectiveConfig.model_validate(
            {**original.model_dump(mode="json"), "ty": {**original.ty.model_dump(mode="json"), "timeout_seconds": 73}}
        )
        test_changed = EffectiveConfig.model_validate(
            {**original.model_dump(mode="json"), "test": {**original.test.model_dump(mode="json"), "timeout_seconds": 91}}
        )
        search_changed = EffectiveConfig.model_validate(
            {**original.model_dump(mode="json"), "search": {**original.search.model_dump(mode="json"), "default": {"resolution": "patch"}}}
        )
        assert execution_policy(original).identity != execution_policy(test_changed).identity
        assert execution_policy(original).identity == execution_policy(ty_changed).identity
        assert guidance_policy_identity(original) != guidance_policy_identity(ty_changed)
        assert guidance_policy_identity(original) == guidance_policy_identity(test_changed)
        assert search_derivation_identity(original) != search_derivation_identity(search_changed)
        assert search_derivation_identity(original) != search_derivation_identity(ty_changed)
        assert report_provenance_identity(original) != report_provenance_identity(ty_changed)
        assert report_provenance_identity(original) != report_provenance_identity(test_changed)

    def test_exact_tool_resources_change_observation_even_with_same_version(self, tool_content) -> None:
        config = EffectiveConfig()
        before = guidance(config, tool_content)
        document = tool_content.model_dump(mode="json")
        document["entries"][-1]["content_digest"] = "a" * 64
        after = guidance(config, StaticContentManifest.model_validate(document))
        assert before.observation.tool_version == after.observation.tool_version
        assert before.observation_identity != after.observation_identity
        assert before.identity != after.identity


class TestPolicyOfflineAdmission:
    def test_policy_preimages_round_trip_and_recompute_independently(self, tool_content) -> None:
        config = EffectiveConfig()
        static = guidance(config, tool_content)
        policies = (
            execution_policy(config), static, search_derivation_policy(config, guidance=static),
        )
        assert len({policy.identity for policy in policies}) == 3
        for policy in policies:
            serialized = policy.model_dump_json()
            restored = type(policy).model_validate_json(serialized)
            assert restored.identity == policy.identity
            assert restored.model_dump_json() == serialized

    @pytest.mark.parametrize("kind", ["execution", "guidance", "search"])
    def test_reader_rejects_unknown_policy_rules(self, tool_content, kind) -> None:
        config = EffectiveConfig()
        static = guidance(config, tool_content)
        policy = {
            "execution": execution_policy(config), "guidance": static,
            "search": search_derivation_policy(config, guidance=static),
        }[kind]
        document = policy.model_dump(mode="json")
        document["rules"] = "unsupported"
        with pytest.raises(ValueError):
            type(policy).model_validate(document)

    def test_reader_rejects_forged_observation_subidentity(self, tool_content) -> None:
        document = guidance(EffectiveConfig(), tool_content).model_dump(mode="json")
        document["observation_identity"] = "0" * 64
        with pytest.raises(ValueError, match="identity does not match"):
            GuidancePolicy.model_validate(document)

    @pytest.mark.parametrize(
        "args",
        (
            ("--output-format=concise",),
            ("--python", "/usr/bin/python"),
            ("--platform=darwin",),
            ("--config-file", "ty.toml"),
            ('--config=output_format="concise"',),
            ("-c", 'output-format="concise"'),
            ("--config", 'terminal.output_format="concise"'),
            ("-c", 'environment.python-version="3.12"'),
            ("--config", 'environment={python="/other/python"}'),
            ("--config", 'terminal={output-format="concise"}'),
            ("--config", 'environment."python-version"="3.12"'),
        ),
    )
    def test_observation_factory_preserves_owned_option_admission(self, tool_content, args) -> None:
        config = EffectiveConfig.model_validate({"ty": {"args": args}})
        with pytest.raises(ConfigurationError, match="adapter-owned"):
            guidance(config, tool_content)

    @pytest.mark.parametrize("schema", [ExecutionPolicy, SearchDerivationPolicy])
    def test_reader_requires_semantic_inputs(self, schema) -> None:
        with pytest.raises(ValueError):
            schema.model_validate({})

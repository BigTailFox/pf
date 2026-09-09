from __future__ import annotations

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
from pf.schemas.policy import (
    ExecutionPolicy, GuidancePolicy, SearchDerivationPolicy,
    SnapshotTyConfigMaterialized, SnapshotTyConfigUnavailable,
    TyToolVersionDistribution, TyToolVersionUnavailable,
)
from pf.ty_version import parse_ty_version_output, ty_version_matches_metadata


def _config(**changes) -> SnapshotTyConfigMaterialized:
    return SnapshotTyConfigMaterialized(digest="c" * 64)


def guidance(config: EffectiveConfig, **overrides) -> GuidancePolicy:
    payload = {
        "tool_version": TyToolVersionDistribution(version="1.0.0"),
        "snapshot_ty_config": _config(),
    }
    payload.update(overrides)
    return guidance_policy(config, **payload)


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
        self, section, values, execution_changes, observation_changes, search_changes,
    ) -> None:
        original = EffectiveConfig()
        document = original.model_dump(mode="json")
        document[section].update(values)
        changed = EffectiveConfig.model_validate(document)
        a, b = guidance(original), guidance(changed)
        assert (execution_policy(original).identity != execution_policy(changed).identity) is execution_changes
        assert (a.observation_identity != b.observation_identity) is observation_changes
        assert (a.identity != b.identity) is observation_changes
        assert (
            search_derivation_policy(original, guidance=a).identity
            != search_derivation_policy(changed, guidance=b).identity
        ) is search_changes

    def test_timeout_changes_generation_but_not_cache_identity(self) -> None:
        original = guidance(EffectiveConfig())
        changed = guidance(EffectiveConfig.model_validate(
            {"ty": {"timeout_seconds": 73}}
        ))
        assert original.observation.identity != changed.observation.identity
        assert original.observation.cache_identity == changed.observation.cache_identity
        assert original.identity != changed.identity

    def test_mechanical_policy_changes_do_not_change_ty_observation(self) -> None:
        config = EffectiveConfig()
        policy = guidance(config)
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
        tool = TyToolVersionDistribution(version="1.0.0")
        snapshot = _config()
        assert execution_policy(original).identity != execution_policy(test_changed).identity
        assert execution_policy(original).identity == execution_policy(ty_changed).identity
        assert guidance_policy_identity(original, tool_version=tool, snapshot_ty_config=snapshot) != (
            guidance_policy_identity(ty_changed, tool_version=tool, snapshot_ty_config=snapshot)
        )
        assert guidance_policy_identity(original, tool_version=tool, snapshot_ty_config=snapshot) == (
            guidance_policy_identity(test_changed, tool_version=tool, snapshot_ty_config=snapshot)
        )
        assert guidance_policy_identity(original, tool_version=tool, snapshot_ty_config=snapshot) == (
            guidance(original).identity
        )
        assert search_derivation_identity(original) != search_derivation_identity(search_changed)
        assert search_derivation_identity(original) != search_derivation_identity(ty_changed)
        assert report_provenance_identity(original) != report_provenance_identity(ty_changed)
        assert report_provenance_identity(original) != report_provenance_identity(test_changed)

    def test_guidance_identity_is_not_observation_digest(self) -> None:
        policy = guidance(EffectiveConfig())
        assert policy.identity != policy.observation.identity
        assert policy.identity != policy.observation.cache_identity


class TestUnavailableUnionsStillFormIdentities:
    def test_unavailable_tool_and_config_still_form_three_digests(self) -> None:
        policy = guidance(
            EffectiveConfig(),
            tool_version=TyToolVersionUnavailable(),
            snapshot_ty_config=SnapshotTyConfigUnavailable(reason="undeclared-analysis-root"),
        )
        assert len(policy.observation.identity) == 64
        assert len(policy.observation.cache_identity) == 64
        assert len(policy.identity) == 64
        assert {
            policy.observation.identity,
            policy.observation.cache_identity,
            policy.identity,
        } == {
            "c71dca3095bbf58f08537f0bc1fca696ad88171d6efdb138d4064653219462e2",
            "8ca6b4fcb7605d6543874cbe5e010655391127a0544c6714b34c0a23131a8ef6",
            "516cfc6306a4c8a2dfab3276fb96f4988a072af93a70a52a35055f88f203f39b",
        }

    def test_teaching_golden_locks_three_identity_bytes(self) -> None:
        policy = guidance(EffectiveConfig())
        assert policy.observation.identity == (
            "44d05ec7bd7b49b5dce62bab556e20b7782491f005c822a87575beb987564b87"
        )
        assert policy.observation.cache_identity == (
            "fe199b2337c0f74150b2ea4c7343617a5394ff8fe414978a22ba95ab67ac3bd8"
        )
        assert policy.identity == (
            "8fb8c3f293468b8f5952fa7d6c3d9056a4b69b4fced3f0a81e84929219a1b8a0"
        )


class TestTyVersionGolden:
    def test_ty_version_output_parses_as_pep440_and_matches_metadata(self) -> None:
        metadata = TyToolVersionDistribution(version="0.0.74")
        assert str(parse_ty_version_output("ty 0.0.74\n")) == "0.0.74"
        assert ty_version_matches_metadata("ty 0.0.74\n", metadata)
        assert not ty_version_matches_metadata("ty 0.0.75\n", metadata)
        assert not ty_version_matches_metadata("not-a-version\n", metadata)


class TestPolicyOfflineAdmission:
    def test_policy_preimages_round_trip_and_recompute_independently(self) -> None:
        config = EffectiveConfig()
        static = guidance(config)
        policies = (
            execution_policy(config), static, search_derivation_policy(config, guidance=static),
        )
        assert len({policy.identity for policy in policies}) == 3
        for policy in policies:
            serialized = policy.model_dump_json()
            restored = type(policy).model_validate_json(serialized)
            assert restored.identity == policy.identity
            assert restored.model_dump_json() == serialized

    def test_reader_rejects_unknown_policy_rules(self) -> None:
        config = EffectiveConfig()
        static = guidance(config)
        for policy in (execution_policy(config), static, search_derivation_policy(config, guidance=static)):
            document = policy.model_dump(mode="json")
            document["rules"] = "unsupported"
            with pytest.raises(ValueError):
                type(policy).model_validate(document)

    def test_reader_rejects_forged_observation_subidentity(self) -> None:
        document = guidance(EffectiveConfig()).model_dump(mode="json")
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
    def test_observation_factory_preserves_owned_option_admission(self, args) -> None:
        config = EffectiveConfig.model_validate({"ty": {"args": args}})
        with pytest.raises(ConfigurationError, match="adapter-owned"):
            guidance(config)

    @pytest.mark.parametrize("schema", [ExecutionPolicy, SearchDerivationPolicy])
    def test_reader_requires_semantic_inputs(self, schema) -> None:
        with pytest.raises(ValueError):
            schema.model_validate({})

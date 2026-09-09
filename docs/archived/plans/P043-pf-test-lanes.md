# P043 — PF 仓库测试车道与自举契约实施计划

- **状态：** 已完成并归档；S1–S6 与 AC1–AC10 均有证据。稳定规则已由现行 owner 接管
- **日期：** 2026-09-09
- **对应 Design：** [D040](../designs/D040-pf-test-lanes.md)，已完成并归档
- **流程：** [AGENTS.md](../../../AGENTS.md)；种类与消费者由吸收后的 [D002](../../designs/D002-pf-implementation.md) §11 与 [tests/README.md](../../../tests/README.md) 拥有

本 Plan 记录切片、决定与验收证据；目标行为由归档当时的 D040 规定。稳定规则已吸收进 D002 §11、`tests/README.md`、`pyproject.toml` 与 CI。

## 1. 切片与验收映射

| 顺序 | 接口、实现、文档与测试工作 | D040 AC | 证据槽 |
| --- | --- | --- | --- |
| S1 | 首切片种类表与 spawn helper 名单；本文件 §2 | AC6 | §2；`--collect-only` 对照 |
| S2 | `tests/process_lane.py` + `conftest` 钩子：`pytest_itemcollected`/`pytest_collection_finish` 做 `e2e ⇒ process`；`pytest_runtest_protocol` 绑定当前 item；`pytest_configure` 只包生产 `SubprocessRunner.run`。helper 入口检查。负向用例标 `infra` | AC5 | `tests/test_process_lanes.py`；日常与 PR pytest 自动执行 |
| S3 | 按 §2 给 `tests/test_*.py` 打标；直连 uv/ty/pf spawn 收口到 helper | AC5、AC6、AC8 | marker 与 collect 集合 |
| S4 | 注册 marker；`addopts` / `[tool.pf] test-command` 换为 D040 §4 数组；CI 显式 `-m` | AC1–AC4 | `pyproject.toml`、`.github/workflows/ci.yml`、ConfigLoader |
| S5 | D002 §11 换为 D040 §6（保留现行静态观察句）；`tests/README.md` 与用户摘要处理 targeted-runtime `C` 与既有报告；D001 不改 | AC7、AC9、AC10 | owner / README diff |
| S6 | 日常 / 自举 / 资格 collect 集合、门禁、AC 审计、吸收归档 | 全部 | §4–§5 |

## 2. S1 种类表与 spawn helper

### 2.1 Spawn helper 名单

| Helper | 位置 | 拉起的进程 |
| --- | --- | --- |
| `run_pf_cli` | `tests/visible_text.py` | `python -m pf` |
| `run_installed_pf` | `tests/visible_text.py` | 安装入口：`uv run --no-sync pf` |
| `run_ty_executable` | `tests/visible_text.py` | 真实 `ty` 可执行文件 |

嵌套 pytest / 真实 uv 经生产 `SubprocessRunner.run`（含 `ConfiguredVerifier`、`UvAdapter`、`TyAdapter`、`RecordingRunner.super().run`）。不把 git、`scripts/qualify_*.py` 的 `python` 启动、或 `check_docs.py` 列入名单。新增同类 helper 必须调用同一入口检查。

### 2.2 钩子

- **collection：** `pytest_itemcollected` 累积全部 item（含随后被 `-m` 取消选择的）；`pytest_collection_finish` 断言 `e2e ⇒ process`。
- **当前 item：** `pytest_runtest_protocol` hookwrapper。
- **生产 runner：** `pytest_configure` 包装 `pf.adapters.process.SubprocessRunner.run`；禁止全局 `subprocess`/`Popen` 补丁。

### 2.3 逐文件种类

未列出的 `tests/test_*.py` 整文件为**默认**（进程内产品，无 marker）。列出的是偏离默认的文件；混合文件写到 TestClass / 方法。

| 文件 | 种类 |
| --- | --- |
| `test_authorization.py` | `TestApplyAuthorizer` 默认；`TestApplyAuthorizationDriftAndCliRoundTrip` 中走 `run_pf_cli` 的方法 `process`；其余方法默认 |
| `test_cancellation.py` | `TestCancellation` 默认；`TestProcessCancellation` `process` |
| `test_candidates.py` | `TestCandidateBuilder` 默认；`TestCandidateRegistryAdmission.test_build_preserves_inapplicable_series_offsets` `process` |
| `test_cli.py` | `TestCliInterface` 中 `module_help` / `run_pf_cli` / `run_installed_pf` 的方法 `process`；其余类与方法默认 |
| `test_configured_verifier.py` | `TestConfiguredVerifierCommand.test_run_enforces_fail_fast_and_isolates_cache` `process`；其余默认 |
| `test_docs.py` | `infra` |
| `test_end_to_end.py` | 整文件 `process`；安装后 search/explain/apply 与读报告的方法另标 `e2e`；既有 `qualification` 方法保留 |
| `test_execution_qualification.py` | manifest 方法默认；`test_replay_searches_to_full_pass_after_execution_rejection` `qualification` |
| `test_process.py` | `process` |
| `test_process_lanes.py` | `infra`（机械检查与负向安全网） |
| `test_pytest_observer_integration.py` | `process` |
| `test_pytest_observer_plugin.py` | `infra` |
| `test_pytest_observer_qualification.py` | manifest / `--list-cases` 默认；`test_run_replays_current_profile_with_isolated_nested_progress` `qualification` |
| `test_pytest_pruning.py` | `TestFailedCasePruning` 中真实 `_run`/`_run_counted` 的方法 `process`；`TestPublicOperations` 默认；`TestPruningCollectionAuthority` `process` |
| `test_pytest_pruning_plugin.py` | `infra` |
| `test_runtime_static_scope.py` | `test_real_verifier_pass_is_saved_with_its_collected_consumer` `process`；其余默认 |
| `test_snapshot.py` | `test_git_snapshot_uses_tracked_and_unignored_worktree_manifest` `process`；其余默认 |
| `test_static_cache.py` | `TestRunStaticScope.test_global_comparison_replays_after_actual_environment_close` `process`；其余默认 |
| `test_static_configuration.py` | `TestRealTyConfigurationEquivalence` `process`；`TestTyConfigurationResolver` 默认 |
| `test_static_guidance_qualification.py` | 整文件 `qualification` |
| `test_static_ignores.py` | `TestRealTyGlobalIgnores` `process`；其余默认 |
| `test_static_inputs.py` | `process` |
| `test_static_journal.py` | 使用 `actual_static_journal` 的方法 `process`；scripted 方法默认 |
| `test_static_paths.py` | `TestRealTySearchPaths` `process`；`TestTySearchPaths` 默认 |
| `test_static_report.py` | 使用 `actual_report` 的方法 `process`；scripted 方法默认 |
| `test_static_request.py` | `TestRealStaticRequest`、`TestNonemptyStaticPreparation` `process`；对应 Qualification 类保持 `qualification` |
| `test_ty_adapter.py` | `test_real_ty_overrides_project_terminal_defaults`、`test_real_ty_validates_invalid_project_configuration` `process`；其余默认 |
| `test_uv_adapter.py` | `test_resolution_ignores_user_level_uv_configuration`、`test_uv_adapter_lists_real_cpython_inventory_beyond_default_summary` `process`；其余默认 |
| `test_uv_qualification.py` | manifest 方法默认；`test_runner_qualifies_a_certified_local_case` `qualification` |
| `test_uv_workspace_qualification.py` | manifest 方法默认；`test_runner_replays_unmanaged_workspace_fail_closed_locally` `qualification` |

默认整文件（进程内产品）：`test_baseline.py`、`test_check.py`、`test_config.py`、`test_diagnose.py`、`test_editor.py`、`test_environment.py`、`test_evaluation.py`、`test_evaluation_cache.py`、`test_execution_contract.py`、`test_execution_run.py`、`test_explain_terminal.py`、`test_failure.py`、`test_harness.py`、`test_marker_projection.py`、`test_markers.py`、`test_optional_test_group.py`、`test_policy.py`、`test_prepare_execution.py`、`test_project.py`、`test_projection.py`、`test_pytest_observer_protocol.py`、`test_pytest_progress.py`、`test_report.py`、`test_report_artifacts.py`、`test_report_schema.py`、`test_report_workflows.py`、`test_resolution.py`、`test_runlog.py`、`test_scheduling.py`、`test_schemas.py`、`test_search.py`、`test_search_coordinator.py`、`test_search_space.py`、`test_search_space_report.py`、`test_search_space_workflow.py`、`test_search_workflow.py`、`test_secure_runlog.py`、`test_smoke.py`、`test_static_comparison.py`、`test_static_external.py`、`test_static_guidance.py`、`test_static_lifecycle.py`、`test_static_ownership.py`、`test_static_process.py`、`test_static_relocation.py`、`test_static_subject.py`、`test_terminal.py`、`test_uv_diagnostics.py`、`test_uv_lock.py`、`test_validation_surfaces.py`、`test_verification.py`、`test_windows_runlog.py`。

`test_config.py` 增加一条**默认**用例：用 `ConfigLoader` 读本仓库 `pyproject.toml`，断言 `test.command` 等于 D040 §4 自举数组（AC3）。

## 3. 决策与行动

- 用户要求实现 D040，本变更把草案当作唯一目标契约；不保留旧默认收集。
- 不重跑 `pf search`、不替换根目录 `package-floor.json`。用户摘要与 `tests/README.md` 标明既有报告相对更宽的历史 `C`，不是现行 targeted-runtime `C` 的 floor。
- D039 未吸收：D002 §11 静态观察两句保留现行正文。
- D001 正文本变更不改写。
- 可选预防：`.cursor/rules/` 短规则只指向 `tests/README.md` 与 D002 §11，不复制种类表，不占验收。
- 当前 item 用进程级 `_CURRENT_ITEM`，不用 `ContextVar`：verification 线程池与 interrupt 测试的 worker 线程读不到 ContextVar，会误报 outside a pytest item。
- `TestRealTyGlobalIgnores` 的 xdg 参数项：ty 只在项目存在 `.git` 时应用 `excludesFile`。同类用例已建 `.git/info`；本机 HEAD 上该参数项已失败，与 spawn helper 无关。fixture 与同类对齐，不改断言语义。
- 全量 `-m ""` 覆盖率与 HEAD 逐项相同（语句 15506/16777、分支 4870/5914，综合 89.80%）。`fail_under` 仍为 90。本机 WSL 达不到 90.0 来自既有 Windows 专用分支，不是本变更引入。

## 4. 验证与证据

命令在仓库根、沙箱外运行（AGENTS.md Run Environment）。

| 命令 | 结果 |
| --- | --- |
| 日常 `pytest --no-testmon`（默认 addopts 收集） | 2395 passed, 202 deselected in 18.63s |
| 自举 `-m "not process and not e2e and not qualification and not infra"` `--collect-only` | 2324/2597 collected（273 deselected） |
| PR `-m "not qualification"` `--collect-only` | 2575/2597 collected（22 deselected） |
| 资格 `-m qualification --collect-only` | 22/2597 collected（2575 deselected） |
| AC8 三次 nodeid 集合 | 日常 2395 ⊃ 自举 2324，差集 71 项且等于 `-m infra`；资格 22 项与前两者不相交；PR = 全量 − 资格 |
| process `-m "process and not qualification"` | 179 passed, 1 skipped, 2417 deselected in 67.74s |
| `.venv/bin/python scripts/check_docs.py` | 归档后运行；exit 0 |
| ruff / ty / `git diff --check` | `ruff check src tests` 与 `ty check src tests` 通过；`git diff --check` 通过 |
| 3.10 coverage `fail_under=90`、`-m ""` | 2596 passed, 1 skipped in 322.51s；综合覆盖率 89.80%，与 HEAD 全量 JSON totals 逐字段相同；`fail_under` 配置仍为 90 |

## 5. 最终验收审计

| AC | 当前直接证据 | 结论 |
| --- | --- | --- |
| AC1 | `pyproject.toml` `markers` 四名；`tests/test_process_lanes.py::test_pytest_registers_the_four_lane_markers` | 通过 |
| AC2 | `addopts = ["--testmon", "-m", "not process and not e2e and not qualification"]`；同文件 `test_daily_addopts_match_the_lane_contract` | 通过 |
| AC3 | `[tool.pf] test-command` 与 D040 §4 逐元素相等；`test_bootstrap_test_command_matches_the_lane_contract` 与 `test_repository_test_command_is_the_targeted_runtime_contract` | 通过 |
| AC4 | CI 3.11/3.12 显式 `-m "not qualification"`；3.10 显式 `-m ""` 且 `--cov`；`fail_under = 90`；`test_ci_jobs_pass_explicit_marker_expressions` | 通过 |
| AC5 | `tests/process_lane.py` + `conftest` 钩子；负向用例 `tests/test_process_lanes.py` 标 `infra`；无全局 `subprocess` 补丁、无调用图扫描器 | 通过 |
| AC6 | 本文件 §2 种类表与 helper 名单；collect 集合与 marker 对照 | 通过 |
| AC7 | D002 §11 车道/真实性规则；`tests/README.md` 种类表与验证命令；2026-09-08 计时标明历史 | 通过 |
| AC8 | §4 collect 三次 nodeid：日常真超集自举且差集为 `infra`；资格不相交 | 通过 |
| AC9 | 本变更范围内 `git diff -- docs/designs/D001-pf.md` 为空 | 通过 |
| AC10 | README / README.zh.md / `tests/README.md` 标明 targeted-runtime `C` 与既有 `package-floor.json` 的历史关系 | 通过 |

# P049 — PF 遗留清理与测试治理执行计划

- **状态：** 已完成
- **日期：** 2026-09-11
- **对应 Design：** 现行 [D002](../../designs/D002-pf-implementation.md)、[D004](../../designs/D004-pf-ty-enhancement.md)；不新增临时 Design
- **关联 owner：** [D003](../../designs/D003-pf-search-algorithm.md)、[D006](../../designs/D006-pf-cli-enhancement.md)、[D007](../../designs/D007-pf-process-output.md)、[D008](../../designs/D008-pf-verification-run.md)、[D012](../../designs/D012-pf-harness-relaxation.md)、[D013](../../designs/D013-pf-pytest-observer.md)、[D014](../../designs/D014-pf-report-schema.md)
- **流程与测试：** [AGENTS.md](../../../AGENTS.md)、[tests/README.md](../../../tests/README.md)
- **评审基准：** B0 对应 `d1da96046a51b1213f4151918e4077ae81cbedf5` 的树；执行前另记实际工作树状态
- **执行状态：** S0–S6 完成；T3/T5 CLI 为未实现偏差；AC6 由 `c6fbd81` Ubuntu CI 闭合

本 Plan 承接本轮代码与测试评审，记录现行契约内的清理、测试修正和验证工作，不另立产品或测试政策。
90% 门禁继续由测试 owner 与现行 CI 定义；全仓补齐作为本计划的独立工作，不回写 D044 的交付责任。

## 1. 范围与执行边界

执行授权到达后，按 [AGENTS.md](../../../AGENTS.md) 的影响路由推进 S0–S6，无须逐切片另行确认。
范围包括：确认无消费者的遗留定义与测试材料、现行 marker 与公开 seam 纠偏、弱断言和并发同步修正、
静态准入及异常恢复的重要缺口。发现恢复现行契约所必需的局部实现修复时，先保留可复现失败证据，再修复并回归。

不改变公开命令、wire、policy identity、静态 authority、测试消费者定义、`[tool.pf].test-command` 数组、
覆盖率算法或门槛；不增加兼容层、测试专用产品 API、coverage 排除项或新的 CI OS 矩阵。
本计划不刷新 PF 自搜索 floor，不关闭 R010 的真实 Host / floor 开放项，不修改历史归档内容。

删除前核对当前消费者、导出、动态调用、Protocol / framework hooks、生成投影及现行 owner。
零引用扫描与低覆盖率只提供线索。若删除项实际属于承诺接口，或补测需要改变上述契约，暂停该项，
记录现行要求、冲突和目标选择，再修订对应 owner Design；其余独立工作继续。

## 2. 基准与证据

评审运行采用 Linux x86_64 / CPython 3.10.16；基准日志目录为
`tests/.cache/validation/test-governance-review/coverage-42picc_l/`，实际源码、依赖、命令和范围见其中的
`manifest.json`。以下日志文件名均相对该目录；这些是本地证据引用，不是随仓库分发的工件。

| 基准项 | 命令与结果 |
| --- | --- |
| B0 完整采集 | `uv run python scripts/validate.py coverage --log-dir tests/.cache/validation/test-governance-review`；六步通过；pytest 为 **2591 passed、1 skipped、2 warnings，63.71s**，见 `05.log` |
| B0 门禁 | `uv run coverage report --show-missing`；退出码 **2**，见 `07-coverage-gate.log` |
| B0 数值 | 综合覆盖率 **88.23%**；语句 15042/16541，分支 4618/5742；纯分支覆盖率 **80.42%**。check 100%、baseline 97%、verification 94%；schemas/static 43%、static/evaluator 77%、static/audit 78% |

覆盖率车道以 `--cov-fail-under=0` 采集，车道 PASS 不等于 90% 门禁通过。B0 仅为该次本机运行，
不代替 CI 矩阵或未来清理结果。约 89.45% 的遗留定义扣除估算不作为验收证据。

S0 将可复用基准的 manifest、日志、coverage 原始数据 / JSON、源码快照和收集清单固化到
`tests/.cache/p049/baseline/`，核对来源后再覆盖共享 coverage 文件。B0 的 JSON 临时导出在
`/tmp/pf-test-governance-coverage-final.json`，仅在仍存在且与源码、原始数据一致时使用。
缺失或状态不明时重新采集；不能用当前共享 `.coverage` 文件的存在推定它仍是 B0。

计划与索引修改不要求工作区重新变干净。S0 分别保存基准 commit / tree 和实际工作树 diff，
逐项判断变动对证据的影响；仅文档改动可复用满足条件的产品测试证据，但使对应文档检查失效。
不得从 manifest 隐去文档 diff；会读取这些文件的 infra / 文档测试也须纳入影响判断。

后续证据放在 `tests/.cache/p049/`，引用验证入口创建的实际 manifest / 日志路径，不假定生成目录名。
共享或长期验收需附固定可访问的 CI / 附件地址与源码身份；本地 cache 只作为本地记录。

## 3. 工作清单

以下是基于评审基准的执行清单。S0 复核后逐项记录删除、迁移、保留及理由；执行中以符号定位，行号不作契约。

### 3.1 遗留代码与测试材料

| ID | 位置与对象 | 处理与保留边界 |
| --- | --- | --- |
| L1 | [schemas/static.py](../../../src/pf/schemas/static.py)：`StaticContentPath`、`StaticTextLiteral/Root/FileValue/Projection`、`StaticContentEntry/Manifest`；`StaticPackageMapping`、`StaticSourceInput`、`StaticTargetInput`、`StaticInstalledArtifact/Node/World`、`StaticRootPlacement`、`StaticAnalysisLayout`、`StaticConfigurationInput`、`StaticEnvironmentValue`、`StaticProcessContext` | 删除无生产消费者的 18 个旧类、专属 helper / alias / import；D004 §6 已采用 v2。保留 `StaticContentUnavailable` 的完整返回契约，S0 固化其 `detail` literal 集合，L1 不收窄任何成员，包括当前无写入点的值。保留 `StaticSubjectCell/Interpreter`、`ResolutionArtifact*`、`ResolutionBinding`、`StaticSubject`；核对现行 report/schema 生成结果 |
| L2 | [test_static_subject.py](../../../tests/test_static_subject.py)：`TestStaticContentManifestAdmission` | 随 L1 删除 5 个参数项；保留现行 subject identity、key、raw fact codec 与准入用例 |
| L3 | [schemas/evaluation.py](../../../src/pf/schemas/evaluation.py)：`process_facts_match()`；[test_schemas.py](../../../tests/test_schemas.py) 对应 presence 测试 | 删除无生产消费者的函数、专属测试和 import；保留现行 failure/process 一致性验证 |
| L4 | [adapters/process.py](../../../src/pf/adapters/process.py)：`SecretRedactor.overlap_bytes()`、`project_output_cache()`；[test_process.py](../../../tests/test_process.py)：`_ExactOverlapRedactor` | 删除闲置实现与失效 override，测试使用现行 redactor；保留五个分块 / UTF-8 / URL / 多表面脱敏场景，以及仍由流式 builder 使用的 `_cache_budgets`、`_decode_tail` |
| L5 | [terminal/_explain.py](../../../src/pf/terminal/_explain.py) `_report_kind()`；[terminal/__init__.py](../../../src/pf/terminal/__init__.py) `_render_explain_overview()`、`_INFRA_REASONS`；[terminal/_presentation.py](../../../src/pf/terminal/_presentation.py) `cell_identity_title()` | 删除无调用的旧展示实现，保留实际 summary / result-card 路径及公开渲染测试 |
| L6 | [search.py](../../../src/pf/search.py) `_ProposalRunner.failure_record()`；[static_cache.py](../../../src/pf/static_cache.py) `find_consumer()`；[errors.py](../../../src/pf/errors.py) `CompatibilityError`；[policy.py](../../../src/pf/policy.py) `TY_DIAGNOSTIC_POLICY`；[uv_diagnostics.py](../../../src/pf/adapters/uv_diagnostics.py) `UV_DIAGNOSTIC_SHAPE_SET` | S0 对照 D004 / D005 / D012、typed `TyObservationPolicy` 与实际 uv qualification profile，记录两个常量的现行承接关系；无消费者且未掩盖 identity / 资格缺口才删除。按完整符号删除 `failure_record()`，保留 `failure_records`、typed outcomes、兼容失败退出码与现行 policy / profile；不模糊匹配批删 |
| L7 | [test_static_report.py](../../../tests/test_static_report.py) `assert_interned_static_audit()`；`tests/fixtures/admitted-static-journal.json`（562170 bytes） | 删除无人调用的旧 intern assertion 与无读取入口的 fixture；不改根报告中的历史 snapshot 元数据或归档证据 |
| L8 | [test_terminal.py](../../../tests/test_terminal.py) `candidate_snapshot_for()`；[test_static_module_graph.py](../../../tests/test_static_module_graph.py) `_module_name()`；[test_secure_runlog.py](../../../tests/test_secure_runlog.py) `windows_log_adapter()`；[process_lane.py](../../../tests/process_lane.py) `current_item()`；[test_static_guidance_qualification.py](../../../tests/test_static_guidance_qualification.py) `SCRIPT/CONTROLLED` | 删除无消费者脚手架及专属 import；保留仍使用的 fixture、adapter 和实际脚本入口 |

### 3.2 测试治理

| ID | 对象 | 执行结果要求 |
| --- | --- | --- |
| T1 | [test_static_request.py](../../../tests/test_static_request.py) 两个 `run_*` helper | 整体放在 S3；S1 不删这两个 helper 或其参数臂。S0 先在 §3.4 冻结去向；S3 删除无入口参数臂及专属 helper 时，先验证现行语义的保留 / 替代 node。保留真实采集、重定位、关闭后读取、invalidate 拒绝和 metadata 变化后的 subject 稳定性；纯 schema / comparison 矩阵迁入进程内 owner 测试，不恢复真实 uv/ty 笛卡尔矩阵 |
| T2 | `test_static_module_graph.py` 的 10 个架构测试，`TestStaticRequestAssembly` 与 [test_static_inputs.py](../../../tests/test_static_inputs.py) 的源码否定检查 | 有现行 ownership 价值的 AST 检查归 `infra`，合并重复定义扫描；迁移名称 / 字符串检查删除或改为公开行为证明。保留禁止产品调用 cache 域方法的护栏，不能因 L6 删除实现就把 `CACHE_DOMAIN_METHODS` 当作死脚手架。禁名按现行入口维护；可用等价有效的 infra 架构检查替换，不要求永久保存已删 `find_consumer` 的拼写。按 TestClass 与结果路径组织，并在 E3 / E5 记录移出自举 C 的集合变化 |
| T3 | [test_cli.py](../../../tests/test_cli.py) production composition 测试 | 按 D044 后的现行行为验证 Search / Highest 共用静态 evaluator 与 runner；Check / Smoke 复用 runtime runner，不向其 evaluator 注入 static、不调用 ty capture。经公开 workflow、composition 绑定处的 adapter 记录及生命周期效果证明，消除多层私有字段断言；纯结构护栏归 `infra`。不要求整个 `CliContext` 在 Check / Smoke 装配时不构造 static，也不把 `host_target()` 计数当作 static 构造证据 |
| T4 | [test_static_journal.py](../../../tests/test_static_journal.py)、`test_static_report.py` 的 old intern / retired authority 矩阵 | 合并为各 reader 的现行未知字段、非法 tag 和证据准入代表；保留 reader 专有错误分类、membership / identity 篡改与 update_path 处理无效现存报告的语义，不逐个枚举旧版本字段 |
| T5 | `test_cli.py` 的 interrupt 测试；[test_pytest_progress.py](../../../tests/test_pytest_progress.py) 的 stubborn-worker stop 测试 | CLI 从 composition 绑定处记录所装配 runner，经公开 workflow 与 `interrupt_processes()` 观察该实例的公开 `interrupt()` 调用，不读 runner 私有状态。真实进程组收拢 / 后续 run 拒绝复用 `test_process.py::TestSubprocessRunner::test_subprocess_runner_interrupt_stops_an_inflight_process_group`。progress 用合法 snapshot、`consume` 回调与 `start/stop` 观察生命周期；需要真实阻塞时用回调 Event 并保证释放收拢，删除 `_thread.is_alive` 恒真的 patch。只证明 D013 规定的进度 / terminal 行为；没有独立公开语义的 stubborn-worker 条目并入已有场景，不为 `_invalidate` 单独新增契约或接口 |
| T6 | [test_static_cache.py](../../../tests/test_static_cache.py) single-flight 与异常唤醒测试，以及 `test_static_ownership.py` 的对应并发场景 | 用确定同步证明 waiter 已选中并等待本次 pending 后再释放 owner，区分并发共享与后续缓存命中。现有 `joined.set()` 在 `super().collect()` 前，不能直接复用为已加入证据；先修正真正的等待点，再在适用的内部 owner 测试间复用，避免新增产品接口。保留异常不缓存及 retry，不依赖 sleep 或调度运气；仍只记录调度空隙，不声称已复现 flaky |
| T7 | `test_static_guidance_qualification.py` controlled 场景 | 默认合并现有证据：业务结果进既有 scripted Search，真实 uv/ty/verifier / 落盘义务优先并入既有 `process` / `e2e` Search / Journal 代表。先按 §3.4 映射两种真实性，不能以 scripted Search 替代真实协议证据。只有写明现有代表不能证明的独有进程风险及成本，才新增 `process`＋`e2e`；无缺口则删除重复 controlled 测试，不新开真实 prepare。分类依现行 tests/README，保留仍有消费者的脚本入口 |

### 3.3 优先补测

| ID | 现行要求与已有证据 | 本次增量与触发条件 |
| --- | --- | --- |
| G1 | D004 / D012；`TestNonemptyStaticPreparation::test_registry_selection_and_external_harness_round_trip` 已产生非空 environment plan，但 [static/audit.py](../../../src/pf/static/audit.py) `_admit_preparation_request()` 的该分支在 B0 未执行 | 使用带活跃 external harness 的 Cell 和现有 scripted adapter；明确断言 `environment_plan is not None`，经 `EnvironmentFactory.prepare` → `StaticEvaluator.capture_highest/collect_prepared` → `TyCheckCache.admitted_membership()`，触发完整 replay。覆盖 highest 原 harness 与 lower/exact 放宽 harness 的代表；只 collect 成功不算完成。内部负向代表是重算外层身份后篡改 environment request 仍被拒绝 |
| G2 | D004 §7；`test_static_cache.py::TestRunTyCache::test_membership_is_run_owned_even_with_identical_payload` 与 `TestRunStaticScope::test_closed_cache_rejects_foreign_and_closed_comparison_refs` 已有 cache 域隔离证据 | 只补 [StaticEvaluator](../../../src/pf/static/evaluator.py) 的公开 handle 边界：由实际 `collect_prepared` 获得 handle，经 `compare_global` 比较同 Run 仅环境关闭、跨 Run、cache 关闭后三种结果。前者仍合法，后两者 typed unavailable；使用 scripted adapter 即可，不再构造 `RunTyFactRef` 或复制 cache 域矩阵 |
| G3 | D004 §7；`test_static_ownership.py::TestStaticConsumerOwnership::test_terminal_cleanup_releases_both_consumers_and_wakes_waiters` 已有异常降级、cleanup 与不入 completed cache 断言；`test_static_cache.py::TestRunTyCache::test_unmodeled_exception_wakes_consumers_and_allows_retry` 已有 cache 层 retry。前者 waiter 同次参与的确定证据由 T6 修正 | 只补公开 collect 的连续成功恢复：首次 lower observation 抛未建模异常后，另一 key 继续成功，原 key 在仍可用或 reprepare 环境上重试成功；验证公开结果、调用次数、permit 与环境释放。复用已有失败准备，不重建整套异常 / 取消矩阵；取消保持原传播语义 |
| G4 | D002 §11 内部账本闭合；[static_cache.py](../../../src/pf/static_cache.py) 已有合法 / 伪造 admission 测试，但 append 后失败回滚在 B0 缺证据 | 在内部 owner 测试中补非法记录准入失败 → 原账本不变 → 合法记录成功的连续场景。使用允许的 snapshot / admission seam；选行为组代表，不为每个字段或 catch 分支构造测试 |

保留已有 `test_static_lifecycle.py`、`test_static_ownership.py`、`test_static_cache.py` 的取消、排空、
异常唤醒与重建证据，以及 Search 动态 floor、Journal 持久化失败边界。它们不是待从零补齐的功能。
S2 在 G1–G4 之外只从以下候选域筛选具体缺口：H1 `static_projection.py` 的 artifact binding / 快照边界 /
typed unavailable；H2 现行 ReportStore / RunLogStore reader 准入；H3 现行 environment 异常清理。
将入选项逐一写回本节为封闭清单：ID、owner、输入、公开结果、已有 node、缺失分支、计划 node、证据槽，
并记录项数与源码状态。不按文件低覆盖率整片展开，不把候选域本身当无限补测授权。
S3 后可删除已被治理补齐的项或调整顺序，但不自动增加新项；清单用尽仍不足 90% 时停止扩大测试，
记录实际差额与剩余现行缺口，按 §1 处理范围决定。仅扩大测试范围不自动要求新 Design；契约变化才走 Design。

S2 封闭清单（2026-09-11；项数 5；源码为 S1 后工作树）。S2 本机 `coverage report --fail-under=90` 已通过（TOTAL 90%，16152/1288 语句，5576/894 分支；综合 JSON 89.57%）。门禁通过不取消 G1–G4 / H1 的现行契约缺口。

| ID | owner | 输入 | 公开结果 | 已有 node | 缺失分支 | 计划 node | 证据槽 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G1 | D004 / D012；`static/audit._admit_preparation_request` | 活跃 external harness 的 Cell；scripted adapter；highest 原 harness 与 lower/exact 放宽各一 | `environment_plan is not None`；`prepare` → `capture_highest/collect_prepared` → `admitted_membership()` 完整 replay；篡改 request 仍拒绝 | `TestNonemptyStaticPreparation::test_registry_selection_and_external_harness_round_trip` 只证明非空 plan | `_admit_preparation_request` 非空 plan 分支（S2 仍 miss 179–207） | **已落地** `TestNonemptyPreparationAdmission::test_nonempty_environment_plan_replays_highest_relaxed_and_rejects_tampered_request` | E4 |
| G2 | D004 §7；`StaticEvaluator.compare_global` | `collect_prepared` 所得 handle；scripted adapter | 同 Run 仅环境关闭仍合法；跨 Run / cache 关闭 typed unavailable | cache 域隔离已有；evaluator 公开 handle 边界无 | evaluator 90–91 / 99 / 比较关闭路径 | **已落地** `TestStaticEvaluator::test_compare_global_handle_survives_environment_close_and_rejects_foreign_or_closed_cache` | E4 |
| G3 | D004 §7；公开 collect 恢复 | 首次 lower observation 抛未建模异常，另一 key 成功，原 key 重试 | 公开结果、调用次数、permit、环境释放；取消原语义 | cache retry 与 cleanup 已有；公开连续成功恢复无 | evaluator 未建模异常后续成功 | **已落地** `TestStaticConsumerOwnership::test_public_collect_recovers_after_unmodeled_lower_exception` | E4 |
| G4 | D002 §11；`static_cache` 账本回滚 | 非法记录准入失败 → 原账本不变 → 合法记录成功 | 内部 admission seam 连续场景 | 合法 / 伪造 admission 已有；append 后失败回滚无 | cache 448–453 / admission 失败回滚 | **已落地** `TestSearchAuditLedger::test_illegal_record_rolls_back_then_legal_record_succeeds`（`record_search` 准入失败回滚；448–453 为 `_anchor_pass_ref` 行号，不是回滚路径） | E4 |
| H1 | D004；`static_projection.bind_resolution_package` / `static_subject` | registry 无 hash 的 available set；path locator 越出 snapshot | 公开返回 `StaticContentUnavailable(detail="resolution-artifact-unbound")` | 无（S2 仍 miss 66/72–84/126–134） | artifact unbound 与 snapshot 边界 | **已落地** `TestResolutionProjectionUnavailable`（空 `available_artifacts` 与 path locator 越界） | E4 |

H2 不入选：现行 journal/report reader 未知字段、非法 tag、证据准入已由 T4 保留 / 合并代表覆盖，不另开 ReportStore / RunLogStore 矩阵。
H3 不入选：环境 close / cleanup 已有 lifecycle 证据；剩余未覆盖行是备选异常臂，不按文件展开。
`_live.py` 289–290 为 run-id 打印路径的残余 miss，不是 S1 删除造成的保留行为损失，不列入补测。

### 3.4 S0 去向表（已冻结，2026-09-11）

S0 已用当前 collect、调用链和 B0 覆盖集合核对下表。每项均有删除 / 迁移 / 保留结论；
无结论项不得进入 S1。S3 的替代测试必须先通过，才能移除对应现行断言。
证据：`tests/.cache/p049/baseline/`（E0）。工作树相对 B0 仅文档 diff（本计划与索引行），
产品测试证据可复用；文档车道须在文档变更后刷新。

`StaticContentUnavailable.detail` 完整 union（L1 不收窄任何成员，含当前无
`StaticContentUnavailable` 写入点的值）：`unreadable-content`、`unsupported-file-kind`、
`unclosed-symlink`、`content-changed`、`invalid-layout`、`inspection-unavailable`、
`installed-input-mismatch`、`undeclared-analysis-root`、`resolution-artifact-unbound`、
`configuration-context-unavailable`、`configuration-unreadable`。见 `baseline/detail-union.json`。

两个闲置常量的现行承接（无 identity / 资格缺口，S1 删除）：`TY_DIAGNOSTIC_POLICY` 由
typed `GuidancePolicy` / `TyObservationPolicy` 字段承接（`static-guidance-v1`、
`multiset-subtraction`、`gitlab`、`adapter-cli-overrides` 等）；`UV_DIAGNOSTIC_SHAPE_SET`
由 `uv-diagnostics-0.12.5-v1` profile（`EXECUTION_OUTCOME_POLICY`、
`schemas.evaluation`、`UV_DIAGNOSTIC_PROFILES`）承接。见 `baseline/policy-constants.json`。

| 原 node / 符号与分支 | 冻结去向 | 切片 |
| --- | --- | --- |
| `src/pf/schemas/static.py` 18 个旧类及专属 helper / alias（`StaticContentPath`、`StaticTextLiteral/Root/FileValue/Projection`、`StaticTextPart`、`_record_references`、`StaticContentEntry/Manifest`、`StaticPackageMapping`、`StaticSourceInput`、`StaticTargetInput`、`StaticInstalledArtifact/Node/World`、`StaticRootPlacement`、`StaticAnalysisLayout`、`StaticConfigurationInput`、`StaticEnvironmentValue`、`StaticProcessContext`） | 删除。生产只进口 `StaticContentUnavailable` 与 v2 subject 族；生成投影不含这些类。保留 `StaticContentUnavailable` 完整 detail union 与 `StaticSubjectCell/Interpreter`、`ResolutionArtifact*`、`ResolutionBinding`、`StaticSubject` | S1 |
| `tests/test_static_subject.py::TestStaticContentManifestAdmission::test_reader_rejects_unverifiable_content[{version,digest,parent,extra,order}]` | 随 L1 删除：仅验证退役模型。保留 `TestStaticSubjectIdentity` / `TestStaticSubjectAdmission` / `TestRawTyFactCodec` / `TestRegistryPlanSubject` / `TestAvailableSetGolden` | S1 |
| `src/pf/schemas/evaluation.py::process_facts_match`；`tests/test_schemas.py::TestEvaluationSchemas::test_process_facts_match_requires_matching_presence` | 删除函数、专属测试与 import。保留现行 failure/process 一致性：`test_diagnostic_describes_process_failure`、`test_process_result_requires_one_valid_terminal_observation`、`test_process_result_omits_captured_output_from_portable_facts`、`test_ty_check_rejects_noncomparable_process_results`、`test_probe_rejection_requires_its_failure_record`、`test_cell_failure_record_ids_are_unique`、`test_failure_record_rejects_a_tampered_stable_id` | S1 |
| `SecretRedactor.overlap_bytes()`；`project_output_cache()` | 删除。生产无调用；流式 builder 继续用 `_cache_budgets` / `_decode_tail` | S1 |
| `tests/test_process.py::_ExactOverlapRedactor` 及五个安全场景：`TestStreamRedaction::test_run_matches_one_shot_redaction_at_chunk_boundary[{0,8,20,23,24,27}]`、`test_run_hides_url_userinfo_at_chunk_boundary[{none,scheme,userinfo,at}]`、`test_streamed_multibyte_utf8_next_to_secret_stays_equivalent`、`test_overlapping_secrets_prefer_the_longest_value_on_every_surface`、`test_streamed_secret_is_hidden_on_stderr_cache_and_process_log` | 保留全部 13 个 node 的原安全结果；删除失效 override，改用现行 `SecretRedactor` | S1 |
| `terminal/_explain.py::_report_kind`；`terminal/__init__.py::_render_explain_overview`、`_INFRA_REASONS`；`terminal/_presentation.py::cell_identity_title` | 删除。仅定义、无调用。保留实际 summary / result-card 路径及公开渲染测试 | S1 |
| `search.py::_ProposalRunner.failure_record()` | 删除该方法。保留 `failure_records` 与 typed outcomes | S1 |
| `static_cache.py::TyCheckCache.find_consumer()` | 删除实现。`CACHE_DOMAIN_METHODS` 仍列 `find_consumer` 作为禁止产品调用的护栏，S3 可用等价 infra 检查替换拼写，不得当死脚手架删掉护栏 | S1+S3 |
| `errors.py::CompatibilityError` | 删除无消费者基类。保留 `MergeCompatibilityError`、`ExitCode.COMPATIBILITY_FAILED`、`InvocationError` | S1 |
| `policy.py::TY_DIAGNOSTIC_POLICY`；`uv_diagnostics.py::UV_DIAGNOSTIC_SHAPE_SET` | 删除。现行 typed policy / `uv-diagnostics-0.12.5-v1` 已承接，无 identity 缺口 | S1 |
| `tests/test_static_report.py::assert_interned_static_audit`；`tests/fixtures/admitted-static-journal.json` | 删除无人调用的 helper 与无读取入口的 fixture。不改根报告历史 snapshot。保留 `assert_report_has_no_static_intern` 与现行 reader 测试 | S1 |
| `tests/test_terminal.py::candidate_snapshot_for` | 删除无调用脚手架 | S1 |
| `tests/test_static_module_graph.py::_module_name` | 删除无调用脚手架；10 个架构测试本身留 S3 | S1 |
| `tests/test_secure_runlog.py::windows_log_adapter` | 删除无调用脚手架。保留 `TestSecureLogDirectory` 经 `secure_log_directory` 的公开选择 | S1 |
| `tests/process_lane.py::current_item` | 删除无调用导出。保留 `using_item` / `_CURRENT_ITEM` / `require_external_process_lane` | S1 |
| `tests/test_static_guidance_qualification.py` 的 `SCRIPT` / `CONTROLLED` 绑定 | 删除无调用脚手架与专属 import。保留 `scripts/qualify_static_guidance.py` 与经 subprocess 的 qualification 入口；产品组合去向见 T7 | S1 |
| `TestRealStaticRequest::test_complete_prepared_request_observes_and_survives_environment_close`：collect / cache hit / close 后 codec | 原 node 保留真实协议代表 | S3 |
| 同一 node 的 invalidate / metadata 变化分支 | 分别保留拒绝无效输入、metadata 改变不改变 v2 subject；纯 schema 字段拒绝可迁入进程内 owner node，S3 先指定替代再删 helper 臂 | S3 |
| `TestRealStaticRequest::test_relocation_reuses_the_run_cache` | 保留 relocation 的真实边界及一次 ty 观测结果 | S3 |
| `TestNonemptyStaticPreparation::test_registry_selection_and_external_harness_round_trip` | 保留非空 harness 真实代表；纯模型语义迁入进程内；完整 request replay 增量见 G1 | S3 |
| 两个 `run_*` helper 的 external-stub / lowest / exact / secondary-managed 无入口参数臂及专属 helper | S1 全部不动；S3 查清各语义已有或计划 node，按无消费者或明确替代删除 | S3 |
| `test_static_module_graph.py::test_schemas_obey_static_import_allowlist` | 现行 ownership AST；S3 标 `infra`，移出自举 C | S3 |
| `test_static_module_graph.py::test_schema_validators_do_not_replay_compare_hint_or_harness` | 现行 ownership AST；S3 标 `infra` | S3 |
| `test_static_module_graph.py::test_admit_uses_shared_derive_and_hint` | 现行 ownership；S3 标 `infra`，并吸收 `test_derive_and_hint_have_one_implementation` 的重复扫描 | S3 |
| `test_static_module_graph.py::test_derive_and_hint_have_one_implementation` | 与上一行重复定义扫描；S3 合并后删除本 node | S3 |
| `test_static_module_graph.py::test_product_callers_import_only_public_static_names` | 现行 ownership AST；S3 标 `infra` | S3 |
| `test_static_module_graph.py::test_search_has_no_handwritten_slice` | 名称 / 字符串检查为主；S3 改为公开行为证明或删除冗余字符串断言，残留 ownership 归 `infra` | S3 |
| `test_static_module_graph.py::test_no_test_only_public_static_exports` | 公开导出集合护栏；S3 标 `infra` | S3 |
| `test_static_module_graph.py::test_product_callers_do_not_invoke_cache_domain_methods` | **保留** cache 域禁名护栏（含 `find_consumer` 拼写，直到以等价 infra 检查替换）；S3 标 `infra`。不得因 L6 删除实现而删此护栏 | S3 |
| `test_static_module_graph.py::test_orchestrators_do_not_accept_failures` | 构造签名护栏；S3 标 `infra` | S3 |
| `test_static_module_graph.py::test_static_facts_do_not_associate_process_logs` | 现行 ownership AST；S3 标 `infra` | S3 |
| `test_static_request.py::TestStaticRequestAssembly::test_product_assembly_omits_platform_argument` | 源码否定 / 字符串检查；S3 删除或改为公开装配结果。真实 inspect 代表保留 `TestPreparedStaticInputs::test_real_prepare_inspects_interpreter_without_file_inventory` | S3 |
| `test_static_inputs.py::test_inspect_script_does_not_access_distribution_files` | 源码否定检查；S3 删除或并入 `infra`。公开行为已由上一行真实 prepare 代表证明 | S3 |
| `test_cli.py::TestDefaultContext::test_production_composition_shares_one_static_evaluator_and_process_runner` | **未实现偏差**（见 §6）。现行 D002 `compose` 只接受 workflow，不能注入 runner/adapter；不扩展契约、不读 `_runner`/`_static`、不 patch 装配。现有 node 仍读私有字段，不作为目标证明 | S3 |
| `test_static_journal.py::TestStaticJournal::test_reader_rejects_old_intern_tables[{static_contents,static_subjects,static_facts,static_comparisons,static_scopes}]` | S3 合并为 journal reader 的未知字段 / 非法 tag 代表，不逐字段枚举旧 intern | S3 |
| `test_static_report.py::TestStaticReport::test_reader_rejects_old_intern_tables`（`INTERN_FIELDS` 五参）与 `test_reader_rejects_retired_static_authority_fields[{regions,witnesses,runtime-interface-missing}]` | S3 合并为 report reader 的未知字段 / 退役 tag 代表。保留 `test_update_path_treats_intern_existing_as_absent`、无 intern 写入、merge 无 static 表 | S3 |
| `test_cli.py::TestDefaultContext::test_assembled_search_interrupt_stops_runner_without_children` | **未实现偏差**（见 §6）。`compose` 不能注入 runner；不读私有状态改写本 node。真实进程组代表仍是上一行 `test_process.py` node | S3 |
| `test_process.py::TestSubprocessRunner::test_subprocess_runner_interrupt_stops_an_inflight_process_group` | **保留** 为真实进程组收拢 / 后续 run 拒绝的唯一点名代表 | 保留 |
| `test_pytest_progress.py::TestPytestProgressMonitor::test_stop_invalidates_a_monitor_with_a_stubborn_worker` | 无独立公开语义；S3 并入已有 `start/stop` 场景（`test_start_stop_contains_progress_consumer_failure` 等）。删除 `_thread.is_alive` 恒真 patch；需要阻塞时用回调 Event 并保证释放 | S3 |
| `test_static_cache.py::TestRunTyCache::test_overlapping_consumers_share_one_actual_terminal[{False,True}]` | 保留场景；S3 把等待点改到 waiter 已选中本次 pending 之后，再区分并发共享与后续缓存命中 | S3 |
| `test_static_cache.py::TestRunTyCache::test_unmodeled_exception_wakes_consumers_and_allows_retry` | 保留异常不缓存及 retry；S3 按同一等待点修正，不依赖 sleep | S3 |
| `test_static_ownership.py::TestStaticConsumerOwnership::test_join_holds_each_proposal_inputs_until_static_completion`（8 参）与 `test_terminal_cleanup_releases_both_consumers_and_wakes_waiters[{cancel,exception}]` | 保留场景；`joined.set()` 在 `super().collect()` 前，只证明到达 wrapper。S3 先修正真正等待点，再在内部 owner 测试间复用 | S3 |
| `test_static_guidance_qualification.py::TestStaticGuidanceQualification::test_controlled_prepare_ty_and_verifier_persist_static_audit` | 业务矩阵复用 `TestSearchAuditLedger` 三条；真实持久化复用 `TestStaticJournal::test_real_pass_persists_membership_and_ty_cache`；跨命令 CLI 复用 `TestInstalledCli::test_installed_module_cli_completes_report_lifecycle`。S3 核对照义务后删重复 controlled 产品测试，不新开真实 prepare。保留 qualification 脚本入口 | S3 |

## 4. 执行切片与进度

依赖顺序：**S0 → S1 → S2 → S3 → S4 → S5 → S6 完成**。
S0 的 §3.4 去向表冻结后才能开 S1；S6 的文档整理可在 S5 等待 CI 时进行，完成与归档仍依赖验收闭合。
实施中可按影响调整文件分组，保留验收项与证据映射，不把表中未决项默认为可删除。

| 切片 | 工作与完成条件 | 验收项 | 状态 / 证据槽 |
| --- | --- | --- | --- |
| S0 基准固化 | 复核源码、owner 与 L/T/G 清单；固化 B0 或刷新基准；保存覆盖行 / 分支、完整与各消费者收集集合。在 §3.4 写全并冻结去向，保存 detail union 与两个 policy 常量的承接核对；记录 E0 后才开 S1 | AC1、AC2、AC6 | 已完成 / E0：`tests/.cache/p049/baseline/` |
| S1 遗留清理 | 仅执行 L1–L8 中去向表已确认的项，T1 全部留 S3；涉及安全测试替身时先保证原场景有效。按受影响 seam 运行 focused tests，复核动态引用、生成投影与有效脱敏场景 | AC1、AC2 | 已完成 / E1：`tests/.cache/p049/s1/` |
| S2 清理后重测 | 运行完整 coverage 车道，固化中间数据；与 S0 对照，区分删除遗留分母、有效行为覆盖损失及原有缺口。在 §3.3 写明 G1–G4 与 H1–H3 入选项的封闭清单和证据槽，不以门禁未过无限展开 | AC2、AC6 | 已完成 / E2：`tests/.cache/p049/s2/`；本机门禁已过，G1–G4+H1 仍为契约缺口 |
| S3 测试治理 | 完成 T1–T7；按冻结去向先验证保留 / 替代 node，再删或合并旧断言，回填实际映射。核对消费者收集变化、停止效果、确定并发窗口与 T7 的真实协议证据 | AC2–AC4 | 已完成（T3/T5 CLI 为未实现偏差）/ E3：`tests/.cache/p049/s3/` |
| S4 现行缺口 | 完成 G1–G4 与 S2 入选项，已由 S3 证明的项复用证据；不自动扩清单。替身通过现有 adapter seam 注入；证明异常不会污染后续执行 | AC5、AC6 | 已完成 / E4：`tests/.cache/p049/s4/` |
| S5 完整验收 | 本机闭合 AC1–AC5 与 Linux 3.10 证据；AC6 按现行 Ubuntu CI 矩阵闭合，写明 AC7 的适用范围。CI 未完成时记录具体待完成 job / 产物与下一步，状态不冒充完成 | AC1–AC6、AC7 的范围说明 | 已完成 / E5：本机 `tests/.cache/p049/s5/`；CI [run 34566256369](https://github.com/BigTailFox/pf/actions/runs/34566256369) |
| S6 文档收尾 | CI 等待期间可整理唯一 owner、索引与结果；AC1–AC6 闭合后在同一审查变更中或紧随 CI 成功的文档收尾中归档 P049，修复入链并跑 docs 车道，闭合 AC7。不新增人为阶段批准，不在缺证据时提前归档 | AC7 | 已完成 / E6：本归档变更 |

## 5. 验收与验证安排

以下 AC 是本次现行契约修复的执行验收，不增加新的 Design 契约。

| AC | 验收结果 | 主要证据 | 当前状态 |
| --- | --- | --- | --- |
| AC1 | L1–L8 逐项有消费者 / owner 结论；S1 前已冻结去向表；确认遗留及专属材料清除，detail union、现行 identity / qualification profile、承诺接口及生成投影完整 | E0、E1、E5 | 本机已闭合 |
| AC2 | 有效行为与安全路径保留；删除 / 合并测试有去向，覆盖集合损失均已修复或证明仅对应退役代码 / 冗余路径 | E1–E5 的 node 与覆盖映射 | 本机已闭合 |
| AC3 | T2、T7 归入现行消费者；真实进程代表留存，产品测试不被 qualification 隐藏，基建不进入自举 C | E3、E5 收集集合与完整运行 | 本机已闭合 |
| AC4 | T3、T5、T6 经允许 seam 证明装配、停止与并发结果；无效 no-op 会被断言发现，不靠私有结构或调度运气 | E3 focused 结果与受控反例 / 同步证据 | 部分闭合：T5 progress 与 T6 已证明；T3 与 T5 CLI 为未实现偏差 |
| AC5 | G1–G4 的准入、隔离、恢复、回滚结果均有现行证据；既有取消 / 动态 authority / 持久化保证保留 | E4、E5；逐项关联实际 node | 本机已闭合 |
| AC6 | 现行 `ubuntu-latest` × Python 3.10 / 3.11 / 3.12 矩阵成功：3.10 全量 coverage 采集及合并 job 的 90% 门禁，3.11 / 3.12 的 pr 车道。本计划不要求 Darwin / Windows，不降低门槛或新增排除 | E5 CI job / artifact、源码身份与门禁结果；本机证据单列 | 已闭合：`c6fbd81` [CI run 34566256369](https://github.com/BigTailFox/pf/actions/runs/34566256369) 的 test 3.10/3.11/3.12 与 coverage 均为 success |
| AC7 | 实际自举集合变化及旧证据适用范围明确；文档 / schema 检查成功；结果与局限完整，完成后归档 | E3、E5、E6 | 已闭合：§7 范围说明保留；本文件归档 |

验证命令由 [scripts/validate.py](../../../scripts/validate.py) 拥有；在仓库根按 AGENTS 的环境要求执行，
实际 argv、退出码、源码状态、环境、测试计数和日志路径回填 E0–E6。

| 时点 | 验证安排 |
| --- | --- |
| 本次计划编写 | `uv run python scripts/validate.py docs`；只证明文档与生成投影一致 |
| S0 / S3 收集 | `uv run pytest --no-testmon --collect-only -q -m ""` 保存全量 node；以现行 marker 表与 `[tool.pf].test-command` 的 selector 派生 / 收集日常、PR、自举集合并对照。另保存 marker / consumer 归属，避免仅 node 名一致掩盖移道 |
| 切片迭代 | `uv run pytest --no-testmon -q tests/<affected>.py`，按种类显式选择 marker；生产 runner / ty 场景不得误用默认日常过滤。运行范围与被排除项写入证据 |
| S2 / S5 canonical 采集 | Python 3.10：`uv run python scripts/validate.py coverage`；立即保存该次原始数据并执行 `uv run coverage json --fail-under=0 -o <evidence-path>` 导出集合 |
| S5 PR | 现行 `ubuntu-latest` 的 Python 3.11 / 3.12 job：`uv run python scripts/validate.py pr`；记录 CI 产物，需要比较提交范围时按文档规范传 `--base REF` |
| S5 90% 门禁 | 现行 `ubuntu-latest` 的 Python 3.10 job 跑 coverage，合并 job 仍按既有算法执行 `uv run coverage report --show-missing`，当前 OS 并集只有 Ubuntu。Linux 本机 3.10 结果单列，不能代替完整 CI job；不把未跑 Darwin / Windows 记为缺口 |
| S6 收尾 | 归档及入链修改后运行 `uv run python scripts/validate.py docs`；仅文档变化不重复仍有效的产品回归 |

覆盖集合按源码 diff 映射仍存在的函数与分支，不能直接用改动前后行号相减。逐项解释「退休删除」
与「保留行为失去覆盖」；后者不能用总百分比上升抵消。测试数量和耗时仅作诊断，不作为删测试配额。
本次不以运行资格矩阵证明产品组合，canonical 全量仍按 owner 收集资格测试。

marker 纠偏及用例变化即使未修改 test-command 数组，也会改变实际自举集合与源码。
E3 / E5 记录变更后的收集范围，不沿用根 `package-floor.json` 或旧运行的 floor 结论。
只说明其证据限制；重新自搜索属于独立任务。

若 S2 清单全部完成而最终仍低于 90%，停止追加测试，记录剩余差额、实际缺口和范围选择，按 §1 分流。
未达门禁、缺失必要矩阵或 AC 未闭合时保留「进行中」。仅等待 CI 时将 AC6 标为「待 CI」，
记录提交、run / job / artifact 地址（未触发则明确写明）和待取得项；CI 成功后直接完成 S6。
产品输入未变的纯文档收尾可复用已成功的产品验证，只刷新受影响文档检查；不能为结束等待而提前标完成。

## 6. 决定与执行记录

| 日期 | 记录 |
| --- | --- |
| 2026-09-11 | 用户确认本轮采用执行计划，沿用现行 owner，不新增 Design；本次仅授权编写计划。B0 是此前评审证据，尚无清理后的行为结果 |
| 2026-09-11 | 草拟文档检查：`uv run python scripts/validate.py docs`，退出码 0，2 steps PASS；日志 `tests/.cache/validation/docs-1e4gdhim/`。仅证明当次计划 / 索引与生成投影检查，不作为 S0–S6 或 AC 的完成证据 |
| 2026-09-11 | 复评修订：S0 先冻结去向，T1 全部留 S3；L1 冻结完整 detail union，L6 核对 typed policy / profile；G1 明确活跃 external harness 与 admission 触发，G2 / G3 限于公开边界增量；T5 固定公开观察面；S2 冻结补测清单，AC6 明列 Ubuntu 矩阵，CI 等待与文档准备分开。只修改计划，未开始 S0 |

本次复评的事实校正与保留边界：

| 评审意见 | 核对结果与采用方式 |
| --- | --- |
| 复用现有 joined 屏障即可 | 不直接采用。`test_static_ownership.py` 在 `super().collect()` 前设 Event，只证明到达 wrapper；T6 必须先证明已选中本次 pending，已有异常场景的并发共享证据也据此修正 |
| 前一懒装配测试已证明 static 共享 | 不采用该证据解释。`test_cli.py` 该测试只计 `host_target()`；`CliContext._ensure_evaluation()` 当前也为 Check / Smoke 构造 context 持有的 static。T3 证明业务对象不消费 static / 不执行 ty，不新增整个 context 不构造 static 的要求 |
| P044 分类约束与当前 T7 冲突 | P044 是历史取舍，不与现行 owner 构成并行约束。采纳避免重复真实 prepare 的成本原则；但 scripted Search 不能代替真实工具或落盘证据，T7 分别映射并优先复用已有两层代表 |
| detail 是 D004 / D014 的公开 union | 采纳保留全部成员；准确依据是现行 evaluator 返回类型与运行期审计 seam。D014 当前 report 不携带该静态模型，不称其为报告 wire union |
| 两个闲置常量可能缺失于 identity | 采纳删除前核对；当前有效语义分别在 typed execution / observation / guidance policy，uv 当前 profile 为 `uv-diagnostics-0.12.5-v1`，未发现必须补回旧常量字面的缺口。S0 记录实际承接关系，不新增旧 identity 输入 |
| 保留 find_consumer 禁名、忽略计划自身 diff、避免 CI 卡归档 | 保留现行 cache ownership 护栏，但等价 infra 检查也可替换禁名；使用 B0 commit 树但仍记录实际 diff；允许等待 CI 时准备文档及 CI 成功后立即收尾，完成状态仍以必要证据为准 |

每个切片结束或发生重要决定时追加：完成项、保留 / 删除依据、偏差、确切命令与结果、证据路径、
下一步；同时更新 §4 状态与 §5 AC。基准和草拟验证不能填作实施完成证据。

| 2026-09-11 | S0 完成并冻结 §3.4。B0 复用：HEAD `d1da960`，工作树仅文档 diff；`tests/.cache/cov/.coverage` 与 `/tmp/pf-test-governance-coverage-final.json` 及导出 JSON 一致（15042/16541、88.23%）。收集：full 2592 / daily 2474 / bootstrap_c 2354 / pr 2584 / infra 120 / process 110 / e2e 11 / qualification 8。证据 `tests/.cache/p049/baseline/`。实施冲突处不改契约、不用旁路：T3/T5 的 compose 注入 runner/adapter 与现行 D002 `compose` 只接受 workflow 冲突，S3 若无法用现有公开缝证明则记未实现偏差 |
| 2026-09-11 | 用户补充：存在冲突无法实现时，不采用 tricky 方案或自作主张改契约，该项留作未实现偏差并记录原因 |

| 2026-09-11 | S1 完成 L1–L8。删除 18 个旧 static 类、`process_facts_match`、闲置 redactor/cache/展示/lookup/常量/脚手架与无入口 fixture；13 个脱敏 node 改用现行 `SecretRedactor`。`CACHE_DOMAIN_METHODS` 仍含 `find_consumer`。Focused：`uv run pytest --no-testmon -q` 上述受影响文件 `-m ""`，370 passed / 1 skipped，日志 `tests/.cache/p049/s1/focused.log`；ruff+ty PASS，`tests/.cache/p049/s1/lint.log`。T1 helper 未动 |
| 2026-09-11 | S2 完成。本机 `coverage report --fail-under=90` 通过（TOTAL 90%；JSON 89.57%）。§3.3 冻结 G1–G4+H1；H2/H3 不入选。证据 `tests/.cache/p049/s2/` |
| 2026-09-11 | S3 完成 T1/T2/T4/T6/T7；T3 与 T5 CLI 记未实现偏差（见下表）。T1 删除 `run_*` 的 external-stub / lowest / exact / secondary-managed 无入口臂，保留两个真实 process node。T2：`test_static_module_graph.py` 标 `infra`，合并并删除 `test_derive_and_hint_have_one_implementation`，删除装配/inspect 源码否定检查，去掉 `test_search_has_no_handwritten_slice` 的冗余字符串断言；`CACHE_DOMAIN_METHODS` 仍含 `find_consumer`。T4：journal/report 各留一个未知 intern 代表与一个退役 tag 代表。T5 progress：删除 stubborn-worker patch。T6：`_signal_when_waiter_awaits_pending` 证明 waiter 已选中本次 pending。T7：无未标记 controlled 产品测试可删；qualification 脚本入口保留。收集：full 2578 / daily 2460 / bootstrap_c 2331 / pr 2570 / infra 129 / process 110 / e2e 11 / qualification 8（相对 B0：full −14，infra +9，C −23）。E3：`tests/.cache/p049/s3/` |
| 2026-09-11 | S4 完成 G1–G4 与 H1。G1：`TestNonemptyPreparationAdmission::test_nonempty_environment_plan_replays_highest_relaxed_and_rejects_tampered_request`。G2：`TestStaticEvaluator::test_compare_global_handle_survives_environment_close_and_rejects_foreign_or_closed_cache`。G3：`TestStaticConsumerOwnership::test_public_collect_recovers_after_unmodeled_lower_exception`。G4：`TestSearchAuditLedger::test_illegal_record_rolls_back_then_legal_record_succeeds`。H1：`TestResolutionProjectionUnavailable`。Focused 73 passed。E4：`tests/.cache/p049/s4/focused-new.log` |
| 2026-09-11 | S5 本机：`uv run python scripts/validate.py coverage --log-dir tests/.cache/p049/s5`，6 steps PASS，日志 `tests/.cache/p049/s5/coverage-d2gjyvlf/`；pytest **2577 passed、1 skipped**。`uv run coverage report --fail-under=90` exit 0，TOTAL 90%（16152/1264 语句，5576/889 分支）；JSON `percent_covered` 89.73%，语句 14888/16152。计划/索引回填后 `validate.py docs` PASS，`tests/.cache/p049/s5/docs-d4yp4_y1/`。E5 本机：`tests/.cache/p049/s5/`。AC6 未触发 Ubuntu 3.10 coverage / 3.11/3.12 pr CI，保持待 CI，不归档 |

### 未实现偏差

| 项 | 目标 | 冲突 | 处理 |
| --- | --- | --- | --- |
| T3 `test_production_composition_shares_one_static_evaluator_and_process_runner` | 经 `CliContext.compose` 注入 recording runner / adapter，用公开 workflow 与 recorded `ty` / `evaluate` 证明 Search/Highest 共用、Check/Smoke 不消费 static / 不 capture ty；不读 `_runner` / `_static` | 现行 D002 `compose` 只接受 workflow，不能注入 runner/adapter。扩展签名或 patch 私有装配会改契约或走旁路 | **未实现**。不扩展 `compose`、不读 `_runner`/`_static`、不 patch 装配。现有 node 仍读私有字段，不作为 T3 目标证明 |
| T5 CLI `test_assembled_search_interrupt_stops_runner_without_children` | `compose` 注入 recording runner，经公开 workflow 与 `interrupt_processes()` 观察该实例的公开 `interrupt()` | 同上：`compose` 不能注入 runner；`interrupt_processes()` 只停懒装配的内部 runner。无公开缝可在不读私有状态的前提下观察该实例 `interrupt()` | **未实现**。真实进程组代表仍是 `test_process.py::TestSubprocessRunner::test_subprocess_runner_interrupt_stops_an_inflight_process_group`。现有 CLI interrupt node 几乎无断言，不改写成旁路 |

| 2026-09-11 | S6 等待期整理：写下 §7 自举集合变化、旧证据适用范围与 AC6 待取得 job。本变更集仍未提交（HEAD `b063272`，相对 `origin/main` 另超前 4 个无关 commit；P049 计划未入 git）。无 CI run / artifact。本机不可替代 Ubuntu 矩阵。不归档 |

## 7. S6 等待期整理

本节是 AC6 等待期间写下的文档准备；归档后只作历史范围说明。

### 7.1 AC7 草稿：自举变化、旧证据范围、结果与局限

**自举集合（E3 collect，相对 B0）：**

| 消费者 | B0 | S3 后 | 差 |
| --- | --- | --- | --- |
| full | 2592 | 2578 | −14 |
| daily | 2474 | 2460 | −14 |
| bootstrap_c（`not process and not e2e and not qualification and not infra`） | 2354 | 2331 | −23 |
| pr | 2584 | 2570 | −14 |
| infra | 120 | 129 | +9 |
| process | 110 | 110 | 0 |
| e2e | 11 | 11 | 0 |
| qualification | 8 | 8 | 0 |

C 减少 23、infra 增加 9：T2 将 `test_static_module_graph.py` 标 `infra` 并移出自举 C；其余净减来自 S1/T1/T4 删除与 S4 增补。`[tool.pf].test-command` 数组未改。根 `package-floor.json` 仍是历史更宽 C 的产物，不得写成已相对现行 `test-command` 或本次 C 验证。

**旧证据适用范围：** B0（`d1da960`，88.23%）只描述清理前树。S2/S5 本机 coverage 只描述当时工作树，不能代替 Ubuntu CI 合并门禁。T7 未新开真实 prepare；真实协议仍用既有 process/e2e/journal 代表。

**本机结果：** S5 coverage 车道 PASS；2577 passed / 1 skipped；`coverage report --fail-under=90` exit 0（TOTAL 90%；JSON 89.73%；14888/16152 语句）。docs 车道在计划回填后 PASS。

**局限：**

- T3 / T5 CLI 未实现：现行 `compose` 不能注入 runner/adapter；不改 D002、不读 `_runner`/`_static`。AC4 因此只部分闭合。
- AC6 无 CI 身份：实现未提交，计划文件未跟踪，没有 run / job / artifact 地址。
- 未刷新自搜索 floor，未关 R010 Host / floor 开放项。

### 7.2 AC6 待取得项

现行触发：`.github/workflows/ci.yml` 在 `push` 到 `main` 或 `pull_request`。待取得：

| Job | 矩阵 | 命令 / 门禁 |
| --- | --- | --- |
| `test` | `ubuntu-latest` × 3.10 | `validate.py coverage --base $PF_VALIDATION_BASE`；上传 `coverage-ubuntu-latest` |
| `test` | `ubuntu-latest` × 3.11 | `validate.py pr --base $PF_VALIDATION_BASE` |
| `test` | `ubuntu-latest` × 3.12 | `validate.py pr --base $PF_VALIDATION_BASE` |
| `coverage` | `ubuntu-latest` | `coverage combine` 后 `coverage report --show-missing`（`fail_under=90`） |

当前记录（`tests/.cache/p049/s5/ac6-pending.txt`）：HEAD `b063272`；工作树相对该 HEAD 有未提交 P049 变更；`docs/plans/P049-pf-test-governance-cleanup.md` 未入 git；未 push；本机无 `gh`，未查询远程 run。**未触发。**

闭合路径：提交本变更集并 push / 开 PR，取得上述四个 job 成功后，在同一审查变更或紧随 CI 成功的文档收尾中归档 P049、更新 `docs/README.md` 与 `docs/archived/README.md` 入链，再跑 `validate.py docs`。

2026-09-11 追加：上述待取得项已由 `c6fbd81` [CI run 34566256369](https://github.com/BigTailFox/pf/actions/runs/34566256369) 闭合；本文件随 S6 归档。

| 2026-09-11 | 用户授权提交并 push `main`。commit `c6fbd8136ea2c4d63f0d74827f2b65eb47a868aa`。已 push `origin/main` |
| 2026-09-11 | AC6 闭合：[CI run 34566256369](https://github.com/BigTailFox/pf/actions/runs/34566256369) `conclusion=success`；`test (ubuntu-latest, 3.10)` / `3.11` / `3.12` 与 `coverage` 均为 success。S6 归档本文件并修复入链 |

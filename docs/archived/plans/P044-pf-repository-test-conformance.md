# P044 — PF 仓库测试符合性实施计划

- **状态：** 已完成并归档；S1–S7 与 AC1–AC11 均有证据。稳定规则已由现行 owner 接管
- **日期：** 2026-09-09
- **对应 Design：** [D041](../designs/D041-pf-repository-test-conformance.md)，已完成并归档
- **目标 owner：** [D002](../../designs/D002-pf-implementation.md) §11、[tests/README.md](../../../tests/README.md)
- **流程：** [AGENTS.md](../../../AGENTS.md)

本 Plan 记录切片、决定、去向与验收证据；目标行为只由 D041 规定。不另立契约。2026-09-09 对照现行 collect 核对去向并优化切片耦合；S1–S7 命令与结果见 §4。稳定规则已吸收进 D002 §11 与 `tests/README.md`。

## 1. 切片与验收映射

| 顺序 | 接口、实现、文档与测试工作 | D041 AC | 完成标准 | 证据槽 |
| --- | --- | --- | --- | --- |
| S1 | 核对 §2 去向表与当前 `--collect-only`；冻结覆盖率行/分支基线；导出 admitted journal 文本供 S4 | AC1、AC6、AC10、AC11 | 去向表无漏项；基线 JSON 路径写入 §4 | §2 对照；覆盖率基线 |
| S2 | 改标与描述：e2e 补标、help 保持 process、`unknown package` 等 S3 完成后收回；更新 `pyproject.toml` e2e/qualification **描述**；`tests/README.md` 种类表与资格验证句与标记一致。**不删除**尚无未标记替换的真实项 | AC1、AC6 | `-m e2e` nodeid ⊆ §2.2 且 ≤10；资格车道在 S4 删除完成后无产品 CLI/Environment/Static | collect 集合 |
| S3 | 公开缝：`CliContext.compose` 可选 workflow（§3.1）；`PreparedEnvironment.relocate_to`；收回 argv 识别 ty、`_secure_runlog` 产品进口、手写 ty argv、patch `_read_progress`；同步 D002 §5 composition 与 environment 句 | AC2、AC3、AC8 的 CliContext 部分 | AC2 扫描为空；进度测试走公开 artifact；D002 写明 compose / relocate_to | 源码扫描；CLI/进度测试 |
| S4 | 先补未标记语义/冻结 journal/Authorizer，再删除真实重复；ty/observer/pruning 按 §2.3 收缩 | AC4、AC1 资格清场 | journal 拒绝未标记且不依赖 `actual_static_journal`；原 Qualification 类与 13 条产品组合消失 | 未标记类；collect |
| S5 | specifier 语义比较；`parametrize` ids 判定；模块级测试入类；3 处固定睡眠改 Event/barrier；§2.4 负向合并 | AC5、AC11 | 无 `[False-False-False]` 类 nodeid；无模块级 `test_*`；指定 3 处 `sleep(0.05)` 消失 | 测试源码；失败项 id |
| S6 | `NO_PASS_IN_SEARCH_SPACE` 文案与 D006 测试同一变更；关闭或收窄 [R010 §2.1](../reviews/R010-pf-engineering-document-audit.md#21-p2-no-pass-文案夸大已验证范围) | AC7 | 代码与测试不再锁 fully evaluated；reason/退出码不变 | terminal/explain 测试；R010 |
| S7 | 吸收 D041 进 D002 §11 与 `tests/README.md` 剩余句；日常/process/资格 collect 对照；覆盖率行集合；归档 D041 与本文件 | AC8–AC11 | owner 正文与 D041 §3/§3.3/§4/§5 一致；D001 与自举 `C` 未改；覆盖率行不丢 | owner diff；§4–§5 |

不可颠倒：S1→改测试。S3 的 `compose` 先于 S2 收回 `unknown package`。S4 每簇「先替换、再删除」。S6 可与 S4/S5 并行，不得拖到 S7。S2 本机 PR 墙钟在 S4 清场后写入 §4（不引用 D041 §3.1 的 81.48s）。

## 2. 去向表

实施时只允许把「偏差」记进 §3；不得在未记录的情况下扩大真实进程或资格集合。

### 2.1 资格（从产品车道清出去）

S1 现行 `-m qualification` **22** 项，与 D041 一致。13 条产品组合 + measure 回放在 S4 删除；其余凭据回放保留。

| 现状 | 去向 |
| --- | --- |
| `test_end_to_end.py` `test_other_commands_verify_project_only_environment`（smoke/check/minimize × 两组，6 项） | S4：命令×组矩阵进未标记 workflow（`test_smoke.py` / `test_check.py` / minimize 已有公开缝则只补缺口）后删除真实。**不**升 e2e，**不**加 `pf check` e2e |
| `TestRealStaticRequestQualification` 4 项 | S4：lowest-direct / exact-vector 语义进未标记后删除这两条真实。external-stub：默认删除真实，若 scripted 无法冻结 stub 再升一条 `process` 并记 §3。relocation：一条 `process`（`relocate_to` 缓存复用），不标 qualification |
| `TestNonemptyStaticPreparationQualification` 3 项 | S4：语义进未标记后删除三条真实 |
| `test_static_guidance_qualification.py` `test_measure_script_keeps_guided_and_mechanical_floors_equal` | S4：删除资格回放。缺口并入未标记 `test_static_guidance.py`，否则只删 |
| 同文件 `test_controlled_prepare_ty_and_verifier_persist_static_audit` | 保留 `qualification`：只断言 guidance 资格 profile 仍 PASS。不另开第二套真实 prepare |
| `test_uv_qualification.py` certified 回放、`test_uv_workspace_qualification.py` unmanaged fail-closed、`test_execution_qualification.py` replay、`test_pytest_observer_qualification.py` 当前 profile | 保持 `qualification`。未标记 manifest 对照保持日常。`tests/README.md` 写明仅凭据变化时跑 `-m qualification` 或直接跑脚本。S5：execution 回放的布尔 id 改为显式 `ids=` |
| `scripts/qualify_pytest_pruning.py` | 本 Plan **不**加 pytest 回放、不把矩阵灌进 PR。process 代表项按 §2.3 收缩 |

### 2.2 e2e（上界 10）

S1 现行 `-m e2e` **4** 项，全在 `tests/test_end_to_end.py`。目标集合：

| 项 | 标记 | 说明 |
| --- | --- | --- |
| `test_missing_group_and_missing_verifier_record_actual_start_failure` | `e2e`+`process` | 留 |
| `test_cli_verifies_project_only_environment` | `e2e`+`process` | 只留 **一条** group（`missing-group`）。`empty-group` 进未标记规划断言 |
| `test_installed_module_cli_completes_report_lifecycle` | `e2e`+`process` | 留 |
| `test_static_workspace_member_version_controls_cli_apply_without_metadata_edits`（2.5 与 1.5） | 补 `e2e` | 两条 CLI 退出（0 与 3），保留 |
| `test_dynamic_workspace_member_blocks_cli_apply_before_edit` | 补 `e2e` | 只留 `force=False`。`force=True` 在 Authorizer 层无覆盖：S4 补未标记 `TestApplyAuthorizer`（及必要的 `create_app`）后删除真实项 |
| `test_sequential_scoped_apply_starts_a_new_generation_and_reprojects_group` | 补 `e2e` | 留（含 explain） |
| `test_force_source_drift_is_a_successful_stderr_warning` | 补 `e2e` | 留 |
| `--help` / 非法 option / `unknown --package` | 非 e2e | help/option 保持 `process`；`unknown package` 在 S3 `compose` 后收回 `create_app` 未标记 |

计数：3 + 2 + 1 + 1 + 1 = **8**。禁止把 §2.1 的 13 条产品组合改标进来。

### 2.3 process 内部收缩（仍在 PR，但减真实成本）

S1 现行 `process and not e2e and not qualification` **176**；`process and not qualification` **180**（含 4 条 e2e）。

| 簇 | 去向 |
| --- | --- |
| `test_static_journal.py` 12 条 reader 拒绝 | 未标记；冻结 admitted journal 文本（S1 导出 `tests/.cache/p044/admitted-journal.json`）。persist / producer-log 索引留 `process` |
| 真实 ty 路径/ignore/config/非法 config/terminal defaults | 解析器未标记已有则删真实重复。留下：extra-paths 冻结、global-ignore 冻结、非法 project config 各 **至多一条** `process`，经 `TyAdapter`，不手写 `ty check` argv |
| Observer summary-fault × exit 约 16 | 预制 artifact + recording，未标记。process 留 pass / fail / xdist / nested / 不改写 interrupt 与 usage |
| Pruning `--lf/--ff/--sw` 与 `TestPruningCollectionAuthority` 2×2×2 | recording 已覆盖的回退改未标记。process 留：原命令 additions、failed-set 跳过原命令、动态 collection 回退、xdist 无 controller |
| `test_static_inputs.py` | 删除 `with_dependency = False` 死分支；capture 断言并入 highest prepare 代表项或留一条 `process` |
| `test_static_report.py` 两条真实 | 合并为一条「真实 search 写入新 scope 且 roundtrip」 |
| `test_cli.py` 三条 help | 合并命令列表与 120 列；console script 对 `python -m pf` 留一条 `process` |
| `test_process.py` | 保留超时杀组、中断、redact、安全目录。纯 JSON 索引拒绝若未生子进程则改为未标记。`WindowsDirectoryAdapter` 两处进口改为 infra（可留在 `test_secure_runlog.py`）；产品 process 测试不进口 `pf._secure_runlog` |
| `test_secure_runlog.py` | 标 `infra`（直接驱动 POSIX/Windows adapter 协议） |
| `RecordingRunner` 按 argv 识别 `ty check`（`tests/test_static_request.py`） | S3：经 `TyAdapter` / `ProcessSpec` 装配计数，不按 argv 识别产品命令 |

嵌套 pytest **不**因昂贵改标 `qualification`。

### 2.4 负向、组织、等待、视觉

| 现状 | 去向 |
| --- | --- |
| `test_config.py` `package = {}` 与 `surprise = true` | 只留一条未知键 |
| 三套非法 scheduling Request + CLI `--ty-jobs nope` | Request 合成一个 `parametrize`；CLI 留一条调用错误 |
| schema 直构拒名 vs `ReportStore.read` 漂移 | 领域类型各一条安全代表项；wire 留 reader |
| `test_read_rejects_a_legacy_report_missing_apply_identity` | 保留拒绝；改名去掉 legacy |
| `test_report_schema` 其余 `read_rejects_*` | 按 D014 规则去重，不按「负向就删」 |
| `test_static_comparison.py` 模块级 `test_scripted_pair_*` | 收入 `Test<Subject><Aspect>` |
| `time.sleep(0.05)`：`test_ty_permits_bound_concurrent_public_static_evaluations`、`test_test_permits_bound_concurrent_public_runtime_evaluations`、`test_check_workflow_runs_host_cells_in_parallel` | Event/barrier；不断言删掉。`test_process.py` 杀组后有上限的 PID 轮询不是这条，不改 |
| `test_terminal` / explain 锁 Rich 折行与 fully evaluated | S5 宽度只留可读性；S6 改 D006 文案。子进程 argv 内 `sleep` 不动 |
| `str(specifier)`：`test_authorization.py`、`test_environment.py`、`test_resolution.py` 等 | `SpecifierSet` / `Version in specifier` |
| `TestPruningCollectionAuthority` 三层布尔 | 显式 `ids=` |
| `relocated_prepared` 直构 `PreparedEnvironment(` | S3：改为公开 `PreparedEnvironment.relocate_to`；测试不再出现该调用 |

## 3. 决策与行动

实施前锁定：

1. **`CliContext`：** 生产 `CliContext.compose(presenter, run_logs, *, root=..., <workflow>=...)`；`build_context()` 走同一入口且不传 workflow。可选参数写入 D002 §5。测试经 `compose` 注入替身，不写 `_check_workflow` 一类字段，不加测试专用 API。生产默认仍为懒装配。
2. **不加 `pf check` e2e。** composition root 由未标记 workflow + 一条 search e2e + apply/explain CLI 覆盖。
3. **资格刷新：** 昂贵回放继续只标 `qualification`；日常只跑未标记 manifest。不把 `-m qualification` 写成定期卫生，不加定时 CI job。不新增 pruning 的 pytest 包装。
4. **S2/S4 墙钟：** CI PR job `pytest --no-testmon -m "not qualification"` 的 collect 规模与墙钟相对整改前增量，在 S4 清场后测量。D041 §3.1 的 81.48s 只解释为何不把 13 条改标进 PR。
5. **D039：** 本 Plan 仍走现行 `StaticEvaluator.lookup/collect/compare` 与 `EnvironmentFactory.prepare`。两份都吸收时 D002 §11 同时保留两句。
6. **不改** D001 正文、自举 `C` 数组、marker **名**、`addopts` 表达式、CI `-m` 表达式、`fail_under = 90`。
7. **切片耦合：** 删除真实项必须先有未标记（或保留的 process/e2e 代表项）替换。S2 只改标与描述。
8. **`PreparedEnvironment`：** 公开 `relocate_to`（D002 environment interface）。它在新根重写 RECORD 并返回同一安装，供静态 capture 的路径无关 identity 使用。测试只调用该方法。
9. **dynamic `force=True`：** 现行只有 CLI 真实项覆盖动态 workspace member。S4 补 Authorizer 未标记后删真实 `force=True`；e2e 只留 `force=False`。
10. **失败用例延后修复：** 迁移期间碰到失败的用例写入 §6，不立即修复。S1–S7 的契约迁移先做完，再按 §6 集中修复。记录项必须含 nodeid、命令、失败摘要与所属切片。

行动日志（实施时追加）：

- 2026-09-09：接受 D041；本文件锁定去向；未改 `src/` 或 `tests/`。
- 2026-09-09：对照 collect 核对去向；优化 S2/S4 耦合、`compose`/`relocate_to`、`_secure_runlog` infra、dynamic force。S1 collect 已写入 §4；journal 已导出；覆盖率基线仍在跑。
- 2026-09-09：用户指示失败用例先记录、完成迁移后再修复。见决策 10 与 §6。
- 2026-09-09：S4 资格清场（删除 13 条产品组合与 measure 回放）；Authorizer 补动态 workspace member；e2e 只留 `force=False`；收缩真实 ty/observer/pruning；journal 拒绝已冻结；help/report/inputs/specifier/scheduling ids 收口。S6 文案已改。S7 开始吸收 D002 §11。
- 2026-09-09：按决策 10 清 §6：`build_context` 失败路径改 patch `CliContext.compose`；冻结 journal 经 wire `schema` → 领域 `schema_version` 再 `write_journal`；`RelocatableStaticRequest.roots`/`subject` 改为 `@property` 以兼容 frozen dataclass。S5 补 `ids=`：`TestOperationBinding`、prepare-execution 未归因拒绝、runtime static pass 可用性。
- 2026-09-09：S7 覆盖率对照。未改动文件相对 S1 丢失的行/分支已由未标记/process 代表项补回：failed-set 收集 PASS 后续跑原命令、url `project-constraints`、slice `anchor-missing`/`_admit_slice`、非空 harness 的 highest 关系、`$STUBS` extra-paths 冻结 remap、无 `~` 的 excludesFile。`cli.py` / `environment.py` / `terminal/__init__.py` 只计行号平移。归档 D041 与本文件。

## 4. 验证与证据

命令在仓库根、沙箱外运行（AGENTS.md Run Environment）。权威 collect 计数以 pytest `X tests collected` 为准。

| 命令 | 时机 | 结果 |
| --- | --- | --- |
| `uv run pytest --no-testmon --collect-only -q` 日常 / 自举 / `process and not qualification` / `process and not e2e and not qualification` / `qualification` / `e2e` | S1 基线 | 日常 **2395**（`not process and not e2e and not qualification`）；自举 **2324**（再排除 infra）；`process and not qualification` **180**；`process and not e2e and not qualification` **176**；`qualification` **22**；`e2e` **4**；全集 2597。原始输出 `tests/.cache/p044/collect-*.txt` |
| `uv run pytest --no-testmon --cov=pf --cov-report=json:tests/.cache/p044/s1-coverage.json --cov-fail-under=0 -m ""` | S1 基线 | **2596 passed, 1 skipped in 327.22s**；covered_lines **15506**/16777；covered_branches **4870**/5914；JSON `tests/.cache/p044/s1-coverage.json`；行清单 `tests/.cache/p044/s1-covered-lines.txt`；分支清单 `tests/.cache/p044/s1-covered-branches.txt` |
| admitted journal 导出 | S1 | `uv run python tests/.cache/p044/dump_journal.py` → `tests/.cache/p044/admitted-journal.json` **562170** bytes；墙钟约 8s |
| `uv run pytest --no-testmon --collect-only -q -m e2e` / `-m qualification` | S2 后、S4 后、S7 | **S7：** `-m e2e` **8** 项，nodeid 与 §2.2 一致（上界 10）；`-m qualification` **8** 项，无产品 CLI/Environment/Static 组合。日常 **2425**；自举 **2338**；`process and not e2e and not qualification` **111**；`process and not qualification` **119**；全集 **2552**。原始输出 `tests/.cache/p044/collect-*.txt` |
| `uv run pytest --no-testmon -q -m "not qualification"` | S7 本机；CI PR 增量 | **2543 passed, 1 skipped, 8 deselected in 85.05s**（`wall_seconds=85.65`）。相对 S1 process **176** 条，现 **111** 条 process-not-e2e；PR 墙钟与 D041 §3.1 解释用的 81.48s 同量级，未把 13 条资格产品组合改标进 PR |
| 源码扫描：`PreparedEnvironment(` 成功值、`pf._secure_runlog` 产品进口、`CliContext._*_workflow` 赋值、`_read_progress` patch | S3 后、S7 | 测试树无 `PreparedEnvironment(`；产品测试无 `from/import pf._secure_runlog`（仅 `tests/test_secure_runlog.py` infra）；无 `CliContext._*_workflow` 赋值；无 `_read_progress` patch；无模块级 `def test_`；无 `str(specifier)`；`time.sleep(0.05)` 仅 `test_process.py` PID 轮询 |
| `uv run pytest --no-testmon --cov=pf --cov-report=json:tests/.cache/p044/s7-coverage.json --cov-fail-under=0 -m ""` 行/分支集合对照 S1 基线 | S7 | **2551 passed, 1 skipped in 185.52s**；covered_lines **15569**/16835；covered_branches **4907**/5946；`percent_covered_display` **90**。未改动文件相对 S1 **lost lines 0 / lost branches 0**（`cli.py` / `environment.py` / `terminal/__init__.py` 只计插入导致的行号平移）。JSON `tests/.cache/p044/s7-coverage.json` |
| `.venv/bin/python scripts/check_docs.py` | 每切片改文档后 | **2026-09-09** 通过；归档 D041/P044 后再跑仍通过 |
| `uv run ruff check tests src`；`uv run ty check`；`git diff --check` | 合入前 | ruff / ty / `git diff --check` 通过 |
| `git diff -- docs/designs/D001-pf.md` 在本变更范围内为空 | S7 | 空。`pyproject.toml` 只改 e2e/qualification **描述**；addopts、自举 `C`、CI `-m`、`fail_under = 90`、marker **名**未改 |

## 5. 最终验收审计

| AC | 当前直接证据 | 结论 |
| --- | --- | --- |
| AC1 | S7 collect：资格 **8**（凭据回放，无产品 CLI/Environment/Static）；e2e **8** ⊆ §2.2、≤10；无 13 条产品组合 | 通过 |
| AC2 | 扫描：无直构 `PreparedEnvironment(`；产品测试无 `_secure_runlog` 进口；无 `CliContext._*_workflow` 赋值；无 `_read_progress` patch | 通过 |
| AC3 | `CountingTy` 经 `TyAdapter.observe` 计数；真实 ty 代表项经 `TyAdapter`；手写 `ty check` argv 的 extra-paths/ignore/config 真实项已删 | 通过 |
| AC4 | journal 拒绝走冻结 admitted 文本；Qualification 类已删；slice/global/admission 有未标记 `test_static_comparison.py` | 通过 |
| AC5 | 模块级 `test_scripted_pair_*` 已入类；指定 3 处 `sleep(0.05)` 已改；布尔已 `ids=`；specifier 比较已改 `SpecifierSet`/`Version in specifier`；无 `[False-False-False]` nodeid | 通过 |
| AC6 | S7：日常 **2425**；自举 **2338**；`process and not e2e and not qualification` **111**；`process and not qualification` **119**；资格 **8**；e2e **8**；marker **名**/addopts/CI `-m` 未改，描述已改 | 通过 |
| AC7 | `NO_PASS_IN_SEARCH_SPACE` 文案已改；R010 §2.1 已关闭 | 通过 |
| AC8 | D002 §11 已吸收种类/刷新/公开缝；`CliContext.compose` 与 `relocate_to` 已写入 D002；`tests/README.md` 已对齐 | 通过 |
| AC9 | `git diff -- docs/designs/D001-pf.md` 为空；自举 `C` 与 `fail_under = 90` 未改 | 通过 |
| AC10 | S7 JSON 对照 S1：未改动文件 lost lines/branches **0**；`percent_covered_display` **90**；`fail_under` 仍为 90 | 通过 |
| AC11 | §2 去向已落地；负向合并与真实收缩已做；§6 四项均已修 | 通过 |

停止条件见 D041 §8。S7 终审时均不成立。

## 6. 延后修复的失败用例

迁移完成前只追加、不在本表内改产品或测试来让该项变绿。S7 吸收前清空或转为已修证据。2026-09-09 三项均已修，表内无未清项。

| 日期 | 切片 | nodeid / 命令 | 失败摘要 | 状态 |
| --- | --- | --- | --- | --- |
| 2026-09-09 | S3 | `tests/test_cli.py::TestDefaultContext::test_build_context_closes_presenter_when_context_construction_fails` | `build_context` 现经 `CliContext.compose`；测试仍把 `pf.cli.CliContext` 补丁成会抛错的 lambda，故 `AttributeError: 'function' object has no attribute 'compose'` | 已修：patch `pf.cli.CliContext.compose`；13 条 journal + 该 CLI 项 passed |
| 2026-09-09 | S4 | `tests/test_static_journal.py::TestStaticJournal::test_reader_rejects_*`（12 条：9 invalid-scope + 2 undecodable + 1 unsupported） | `_write_frozen_journal` 对 `tests/fixtures/admitted-static-journal.json` 做 `model_validate_json`；dump 含 wire 字段 `schema`，领域类型拒 extra | 已修：wire `schema` → `schema_version` 后 `write_journal`；不改产品 schema |
| 2026-09-09 | S3 | `uv run ty check`：`tests/test_static_request.py` `PreparedEnvironment.relocate_to(request)` | `StaticTyRequest.roots` 与协议 `RelocatableStaticRequest.roots` 写兼容性不一致（2 diagnostics） | 已修：协议 `roots`/`subject` 改为 `@property` |
| 2026-09-09 | S4 | `tests/test_static_report.py::TestStaticReport::test_search_workflow_writes_a_fresh_scope_that_roundtrips` | 合并后的 process 项把 fixture 里额外 `static.compare` 才有的 `comparisons==1` 套到 `SearchCommandWorkflow` 产物上；workflow 写入 facts/consumers/passes，roundtrip 后 comparisons 仍为 0 | 已修：roundtrip 只断言 workflow 实际持久化的 facts/consumers/passes 与字节稳定；compare 回放仍由 scripted 报告覆盖 |

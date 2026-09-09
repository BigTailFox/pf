# P045 — D043 静态 subject v2 实施计划

- **状态：** 已完成
- **日期：** 2026-09-09
- **对应 Design：** [D043](../designs/D043-pf-static-subject-v2.md)
- **目标 owner：** [D004](../../designs/D004-pf-ty-enhancement.md)、[D003](../../designs/D003-pf-search-algorithm.md)、[D014](../../designs/D014-pf-report-schema.md)、[D008](../../designs/D008-pf-verification-run.md)、[D012](../../designs/D012-pf-harness-relaxation.md)、[D001](../../designs/D001-pf.md)、[D002](../../designs/D002-pf-implementation.md)
- **起点：** `a22461c0f6cdc2707ff357f9a4ce9e07aa7c8420`
- **流程与测试：** [AGENTS.md](../../../AGENTS.md)、[测试说明](../../../tests/README.md)
- **来源：** [I002](../investigations/I002-pf-self-search-py310-static-collection.md)

本 Plan 记录切片、决定、去向与验收证据；目标行为只由 D043 规定。不另立契约。未开切片时不改 `src/` 或 `tests/`。

## 1. 切片与验收映射

依赖：**S1 → S2 → S3 → S4 → S5**。S1 把 runtime payload 切到 v2 后，现行
report / Journal v1 intern codec 已不再是合法落盘目标；因此 **S1–S4 是同一个不可拆分合并的
contract-cutover**，E1–E4 是其内部有序检查点，不是可分别发布的兼容阶段。各检查点跑本片目标测试，
S4 完成后再跑日常全量、process 与 PR 门禁。禁止为了让中间态全绿而实现
「v2 payload 装进旧 intern 表」的临时 codec。

每片同步迁移直接调用方、fixture 与公开测试。预发布干净替换：无兼容层、无双读、无历史
parser；S5 的 owner 吸收与 D043 / P045 / I002 归档仍在**同一完成变更**内。

| 顺序 | 接口、实现、文档与测试工作 | D043 AC | 完成标准 | 证据槽 |
| --- | --- | --- | --- | --- |
| S1 | v2 subject / `available-set`；三层 policy identity；`tool_version` 与 PATH 中 ty 一致；`snapshot_ty_config` union；配置物化不含 owned overrides；宿主/`TY_CONFIG_FILE` 规则；§7 admission；停止三条 census | AC1–AC3、AC9–AC11 | 普通 registry plan 形成 v2 subject 并可启动 ty；metadata / 配置 unavailable 仍算出三层 digest；不等 `--version` 或不物化则不启动 ty；不同 `TyCheckKey` 不得 COMPARED | E1 |
| S2 | `EnvironmentFactory` 在首次 prepare 成功时登记 Run-owned `ReprepareRecipe`；`reprepare` 用既有 plan 重建且不 resolve / Attempt / verifier；`open_static_slice` 走 prepare-only 重建；`record_pass` 不要求静态 consumer；Check：highest prepare 成功后任意 `StaticContentUnavailable` 仍 lowest-direct | AC4 | 直接 PASS 上端可独立采集 `S_slice`；recipe 缺失/不一致/重建失败为 `NO_HINT` 且无 oracle prepare failure；Check 对 §5.3 各类静态失败继续 declaration | E2 |
| S3 | 删除五张报告 intern 表；`ProbeObservation` / `ProbeObservationV1` 增加 required-nullable `selection_reason`；`SearchProbeRequest.mechanical` 拆 lowest/midpoint；重生成 D014 投影 | AC5、AC6 | `generate_report_schema.py --check` 通过；旧 intern 报告短路径失败；`update_path` 当缺席；baseline/final 为 `null` | E3 |
| S4 | Journal 名称保持 `verification-journal-v3`，旧 intern 非法，写入 `static_membership`；`pf-ty-cache-v1` 外层落盘；writer/audit 与 diagnose reader 分离；覆盖 cache→Journal→latest 部分失败；diagnose 两条路径不读 ty-cache、不渲染静态；静态不分配 Failure ID | AC7、AC8、AC12 | 同 Run 先 cache 后 Journal 再 latest；部分失败不产生悬空 membership/latest；普通 Journal decode 不读 ty-cache；reader 拒绝旧 intern；diagnose 只展示 Failure 权威 | E4 |
| S5 | 吸收 D001/D003/D004/D008/D012/D014/D002/D006 与 CONTEXT；按 D043 §11 修订 D039 AC4/AC10；审计 AC1–AC14；同变更归档 D043/P045/I002 并更新索引与全部现行入链 | AC13、AC14 | owner 正文与 D043 一致；D039 排除本次删除/字节变化；D043/P045/I002 不再留在现行目录，P045 状态为已完成，文档检查通过 | E5 |

## 2. 接口与所有权迁移

实施时按本表改公开缝；物理文件名可记入 §6，但不另立契约。吸收前 D043 是唯一目标契约；S5 之前不把条款零散写进 owner。

| 现行缝 | 目标 | 吸收后 owner |
| --- | --- | --- |
| `StaticSubject` 六组文件树；`StaticContentCollector.collect` 编身份 | §3.1 预像；`resolution_projection` 含 `selected` / `available-set` / `source-tree` | D004 |
| inspect `-c` 枚举 `distribution.files`（`adapters/static_inputs.py` `_INSPECT`） | 只核 interpreter + 诊断前缀；name/version 复用 D012 | D004 / D012 |
| `StaticTyRequest.revalidate` 再 `collect` 整树 | §5.3：lease/`tested`/前缀；不整树再散列 | D004 |
| `TyObservationPolicy` v1（`tool_content` / `executable` / 嵌套 `TyConfig`） | v2：顶层 `args`/`timeout_seconds`、`tool_version` union、`snapshot_ty_config` union、`host_config` | D004 |
| `policy.guidance_policy_identity` 哈希 `config.ty` + metadata 字符串 | `GuidancePolicy.identity`（完整 guidance 预像，域 `pf:guidance-policy:v1`） | D014 / D004 |
| `TyCheckKey` 含文件树 / tool content | `(StaticSubject.identity, cache_identity)`；fact 绑 generation identity | D004 |
| 报告五张 intern 表；`static_scopes` | 删除；不新增 `static_audits` | D014 / D003 |
| `ProbeObservation` 无选择原因；`SearchProbeRequest.selection_reason` 含 `mechanical` | 公开观察 required-nullable；坐标枚举见 D043 §8.1；运行期请求仍非空 | D014 / D003 |
| Journal intern + `static_scopes` | 名称保持 v3；`static_membership[]`；旧 intern 非法 | D008 |
| 静态事实只在 Journal/报告 intern | `.pf/logs/<run-id>/ty-cache.json`，外层 `pf-ty-cache-v1` | D002 / D008 |
| `diagnose_static_associations` / Index 静态交叉 / report-side 静态 association | 删除。`diagnose_available` 只要求 Journal + verifier Process Log | D001 / D006 / D008 |
| `open_static_slice` 依赖未 close 的 PASS 环境；`record_pass` 要求 consumer | `reprepare(已有 Proposal)`；PASS 后可 close | D003 / D012 |
| D008「无合法 `S_hi` 不得 declaration」 | 与现行 `CompatibilityChecker` 对齐：prepare 成功即继续 lowest-direct | D001 / D008 |
| D039 AC10「Schema 1 字段与 identity 字节不变」 | 排除五表删除、`selection_reason` 与 v2 identity 字节；先吸收 D043 再搬家 | D039（仍为临时 Design） |

`EnvironmentFactory.prepare` 首次成功时按 `proposal_id` 登记 D043 §6 的私有不可变
`ReprepareRecipe`。recipe 持有重建所需的 `PackagePlan`、resolution kind / selection / harness
baseline、project / environment `ResolutionPlan` 原生载荷与 digest，以及 snapshot / source-plan
identity；关闭 `PreparedEnvironment` 不删除，Run / factory 结束统一释放。

`EnvironmentFactory.reprepare(proposal, snapshot, source_plan)` 只按 recipe 重建并逐项核对 identity /
digest；不得重新 resolve。它复用已有 Attempt/Proposal，不分配新 Attempt，不做 verifier；recipe
缺失、核对失败、安装或 inspect 失败均返回 `StaticContentUnavailable`，不调用
`record_prepare`，不写入该向量 oracle `prepare_failures`。

`RunLogStore` 拥有 ty-cache 的编解码、canonical snapshot 与原子写入，并删除
`lookup_static` / `index_report_static` / `lookup_report_static` 等 report-static index API。编排器不读
文件树采集器。ty-cache 不是 Process Log（D007 不变）。

普通 Journal decode（含 diagnose 回退）只校验 Journal 自身，不打开 ty-cache。writer admission 在
Journal 提交前校验 membership→cache；需要跨文件复证时调用显式的 Run 内 static-audit seam，不能
复用 diagnose reader。

## 3. 决策（实施前锁定）

1. **唯一目标契约。** 实施期间只实现 D043。现行 owner 正文在 S5 一次性吸收。禁止兼容层、别名、双读/双写、旧 intern inflate。
2. **identity 预像随 S1 一次切齐。** 凡构造 `StaticSubject` / `TyObservationPolicy` /
   `GuidancePolicy` 的测试与 fixture，在 S1 改为 v2 预像。Journal / 报告旧 intern 表分别在 S4 /
   S3 删除；S1–S4 之间不实现、测试或提交「旧表 + v2 payload」形状，contract-cutover 只有 S4
   后一个可合并状态。
3. **三层 digest 不得合并。** 教学 golden 锁定 observation / cache / guidance 三组字节，并锁定 `tool_version.kind=unavailable` 与 `snapshot_ty_config.kind=unavailable` 仍能算出三层 digest。`TyCheckFact.observation_policy_identity` 绑 generation；lookup 只用 `cache_identity`。
4. **`selection_reason` 分层。** `SearchProbeRequest` 仍只表示坐标 probe，required 非空；`mechanical` 拆成 `mechanical-lowest` / `mechanical-midpoint`。公开 `ProbeObservation` 在 `dependency=None` 时为 `null`。去重保存首次进入该 Slice 观察集合的原因。Reader 只验字面量，不把 `static-suspect` 当 authority。
5. **配置 union。** 无物化字节时写 `snapshot_ty_config.kind=unavailable`，仍形成 generation/guidance。effective TOML 不含 PF-owned CLI overrides；digest 只哈希实际 `--config-file` 字节。快照内 `TY_CONFIG_FILE` 为最高优先级输入；快照外为 `undeclared-analysis-root`。
6. **工具版本。** 身份用 `importlib.metadata.version("ty")`，不把 `--version` 原文写入 digest。metadata available 时规范解析 `ty --version`，必须相等，否则不启动 ty。metadata unavailable 时不 collect，用 `kind=unavailable` 形成报告身份。一次 Run 至多读一次元数据、至多一次 `--version`。
7. **测试车道。** 身份、admission、reader 拒绝、diagnose workflow 走未标记公开缝。真实 ty 协议各留代表项（经 `TyAdapter`）。不新增产品命令 e2e；既有 check/search e2e 随契约更新。不把产品矩阵扩进 `qualification`。负向测试只留现行错误/安全代表项。
8. **AC1「真实 plan」。** 固定一份现行 pylock 文本，经现行 parser / adapter 公开缝得到
   `ResolutionPlan`，再取其中普通 registry `selected_artifact is None` 且
   `available_artifacts` 带 hash、含传递依赖的 `ResolutionPackage` 进入 subject / recording-ty
   seam；不得只手工构造目标 model。不要求本切片现场 `uv lock`。现场 prepare+collect 若需要，
   标 `process`，每种协议至多一条。
9. **D039。** 本 Plan **不**实施 D039 搬家。S5 只修订 D039 §4.1 / AC4 / AC10，使其不再把五表删除与 identity 字节变化算进 D039 AC10。比较重放改在夹具或 Run ty-cache 的 `TyFactDocument` 上陈述。
10. **性能证据非硬门禁。** §8 对照 I002，不作为 AC 通过条件，也不授权为墙钟引入跨 Run cache。
11. **失败先记。** 迁移中失败的用例写入 §6，含 nodeid、命令、摘要与所属切片；不把失败改写成新契约。
12. **cache→Journal→latest 事务。** ty-cache 写失败则本次不写 Journal、不更新 latest；Journal
    写失败允许留下无引用 sidecar、但不更新 latest；latest 更新失败时 Journal 仍可按 run-id
    读取，不宣称 diagnose available。重试原子覆盖 canonical cache snapshot，不从 sidecar
    恢复 oracle。普通 Journal reader 不做跨文件校验。
13. **完成与归档同变更。** E1–E5 证据齐全、owner 吸收并 reconciliation 后，将 D043、P045、
    I002 分别移至 `docs/archived/designs/`、`docs/archived/plans/`、
    `docs/archived/investigations/`；更新相对链接、`docs/README.md` 现行/历史索引与全部现行入链。

## 4. 验收矩阵

状态在实施时回填为「证明：」加公开 nodeid 或命令。同一测试可覆盖多项 AC，不能省略映射。E5 最终审计包含全部 AC，不替代各项直接证据。

| AC | 切片 / 证据 | 必需正向与错误行为 | 状态 |
| --- | --- | --- | --- |
| AC1 | S1 / E1 | 固定 pylock 经现行 parser 产生 `selected_artifact is None` 的普通 registry（含传递依赖），形成 `available-set` 与 v2 subject，并进入 recording-ty 启动 seam；不读 RECORD | 证明：`tests/test_static_subject.py::TestStaticSubject::test_ordinary_registry_plan_forms_available_set_and_may_start_ty`；`tests/test_static_subject.py::TestStaticSubject::test_canonical_identity_json_encodes_triples_as_arrays` |
| AC2 | S1 / E1 | 运行期 subject / 观测策略不含文件清单；公开 inspect 测试令 `distribution.files` 访问失败而流程仍成功；源码扫描无该访问 | 证明：`tests/test_static_inputs.py::test_inspect_script_does_not_access_distribution_files`；`tests/test_static_inputs.py::TestStaticInputs::test_real_prepare_inspects_interpreter_without_file_inventory`；`rg -n "distribution\\.files" src` 无产品访问 |
| AC3 | S1 / E1 | 宿主 ty 配置或快照外分析根 → `undeclared-analysis-root`，`snapshot_ty_config.kind=unavailable`，不启动 ty | 证明：`tests/test_static_configuration.py::test_host_user_config_is_undeclared_analysis_root`；`tests/test_static_configuration.py::test_ty_config_file_outside_snapshot_is_undeclared` |
| AC4 | S2 / E2 | Run-owned recipe 驱动 `reprepare` 且不 resolve / Attempt / verifier；recipe 各失败分支为 `NO_HINT` 且无 oracle prepare failure；独立 `S_slice`；highest prepare 成功后，§5.3 每类 `StaticContentUnavailable` 仍 lowest-direct | 证明：`tests/test_environment.py::TestReprepareRecipe::test_reprepare_rebuilds_without_resolve_or_new_attempt`；`tests/test_static_lifecycle.py::test_reprepare_collects_after_original_environment_close`；`tests/test_check.py::TestCompatibilityChecker::test_compatibility_checker_captures_highest_before_testing_lowest_direct` |
| AC5 | S3 / E3 | 报告无五张静态表；`dependency=None` 时 `selection_reason=null`；坐标观察为 D043 §8.1 枚举；生成投影 `--check` 通过 | 证明：`tests/test_static_report.py::test_search_workflow_writes_a_report_without_static_intern`；`tests/test_search_coordinator.py` 的 `assert_public_selection_reasons`；`scripts/generate_report_schema.py --check` |
| AC6 | S3 / E3 | 旧 intern 报告校验失败即停；`update_path` 把缺席/坏 existing 当缺席 | 证明：`tests/test_static_report.py` intern 字段拒绝与 `update_path` 缺席路径 |
| AC7 | S4 / E4 | ty-cache 外层 `schema`/`run_id`/`entries`；完整 `TyFactDocument`；`TyCheckKey=(subject.identity, cache_identity)`；fact 绑 generation；Journal 名称 v3 且拒绝旧 intern；三种部分失败不提交悬空 membership/latest | 证明：`tests/test_static_journal.py::test_real_pass_persists_membership_and_ty_cache`；`tests/test_static_journal.py::test_reader_rejects_old_intern_tables`；`tests/test_static_journal.py` 三条部分失败；`tests/test_static_subject.py::TestStaticSubject::test_key_uses_subject_and_cache_identity` |
| AC8 | S4 / E4 | 报告命中与 Journal 回退都不读 ty-cache、不渲染静态；ty-cache 缺失/损坏不阻断合法 Journal Failure | 证明：`tests/test_static_journal.py::test_ordinary_journal_read_does_not_open_ty_cache`；`tests/test_search_coordinator.py` 报告路径 `static_associations == ()` |
| AC9 | S1 / E1 | `tool_version` 两种 kind 都能形成三层 identity；metadata available 且 `--version` 不等则不启动 ty；metadata unavailable 不 collect，仍能出报告 | 证明：`tests/test_policy.py::test_unavailable_tool_and_config_still_form_three_digests`；`tests/test_policy.py::test_teaching_golden_locks_three_identity_bytes`；`tests/test_policy.py::test_ty_version_output_parses_as_pep440_and_matches_metadata` |
| AC10 | S1 / E1 | §7：两端 `snapshot_ty_config`（含 unavailable）与 `cache_identity` 相等才可 COMPARED；不同 key 不得 COMPARED | 证明：`tests/test_static_comparison.py::test_scripted_pair_covers_global_slice_and_scope_contracts`；`tests/test_policy.py::test_timeout_changes_generation_but_not_cache_identity` |
| AC11 | S1 / E1 | §5.3 决策表；仅 `materialized` 的 digest 哈希 `--config-file` 字节；配置 unavailable 仍形成 generation/guidance | 证明：`tests/test_static_configuration.py::test_ty_file_inside_snapshot_materializes_without_owned_overrides`；`tests/test_policy.py::test_unavailable_tool_and_config_still_form_three_digests` |
| AC12 | S4 / E4 | 静态 unavailable / `NO_HINT` 的公开结果无 `FailureRecord`；`tests/test_static_ownership.py` 锁定静态路径不进入 Failure 写入 seam | 证明：`tests/test_static_ownership.py::test_unavailable_collection_still_runs_verifier`；`tests/test_static_ownership.py::test_changed_host_bytes_outside_subject_do_not_block_verifier` |
| AC13 | S5 / E5 | D039 §4.1 / AC4 / AC10 按 D043 §11 修订 | 证明：D039 §4.1 离线读入链改为夹具 / ty-cache；AC4/AC10 排除五表删除、`selection_reason` 与 v2 identity 字节 |
| AC14 | S5 / E5 | D001 §5、D008 §3.2/§8–§9 与 D043 一致；其余 §2 吸收表同步 | 证明：D001/D003/D004/D008/D012/D014/D002/D006 与 CONTEXT 已吸收；本文件与 D043/I002 同变更归档 |

停止条件（任一成立则本 Plan 未交付；与 D043 §12 对齐）：普通 registry plan 无法形成 subject；`--version` 失败或 metadata unavailable 导致无法写报告；metadata 与 PATH 中 ty 版本不一致仍启动 collect；`reprepare` 重新 resolve、分配 Attempt 或缺失 Run-owned recipe；diagnose 读取/依赖 ty-cache 或仍展示静态；cache / Journal 部分失败提交了悬空 membership / latest；`StaticSubject.identity` 再取子集；`GuidancePolicy.identity` 写成观测 digest；配置无法物化时无法形成 policy；缓存绑 RECORD；公开报告再 intern 静态表；`selection_reason` 无法表示 baseline/final；Check 因任意静态 unavailable 跳过 lowest-direct；Journal reader 仍接受旧 intern；静态授权 PASS/boundary。

## 5. 验证命令

cwd 固定仓库根。PF CLI、pytest、文档脚本按 AGENTS.md 在沙箱外运行。下列为模板；实际命令与结果在实施时写入对应 E 槽，不得把未跑的命令记为已通过。

```sh
# 文档（每片结束；只改文档的切片可只跑第一行）
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/generate_report_schema.py --check

# E1：身份、投影、配置、admission（S1 落地后回填精确路径）
uv run pytest --no-testmon -q --tb=short \
  tests/test_uv_lock.py tests/test_static_subject.py tests/test_policy.py \
  tests/test_static_configuration.py tests/test_static_inputs.py \
  tests/test_static_comparison.py tests/test_static_request.py \
  tests/test_static_cache.py tests/test_evaluation.py

# E2：reprepare / slice / Check（S2）
uv run pytest --no-testmon -q --tb=short \
  tests/test_static_guidance.py tests/test_check.py \
  tests/test_static_lifecycle.py tests/test_environment.py \
  tests/test_search.py tests/test_search_coordinator.py

# E3：报告 wire 与生成投影（S3）
uv run pytest --no-testmon -q --tb=short \
  tests/test_static_report.py tests/test_report_schema.py \
  tests/test_projection.py tests/test_report_workflows.py \
  tests/test_search_coordinator.py
.venv/bin/python scripts/generate_report_schema.py --check

# E4：Journal / ty-cache / diagnose（S4）
uv run pytest --no-testmon -q --tb=short \
  tests/test_static_journal.py tests/test_static_cache.py \
  tests/test_runlog.py tests/test_diagnose.py \
  tests/test_execution_run.py tests/test_search_coordinator.py \
  tests/test_static_ownership.py

# S4 contract-cutover 完成后的日常与真实进程代表项
uv run pytest --no-testmon -q --durations=30
uv run pytest --no-testmon -q --tb=short \
  -m "process and not qualification" tests/test_static_process.py
uv run ruff check tests src
uv run ty check
git diff --check

# E5：owner 吸收、reconciliation 与归档
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/generate_report_schema.py --check

# 最终 PR 门禁
uv run pytest --no-testmon -q -m "not qualification"
```

真实 ty 代表项与既有 e2e 随 S4 后的 contract-cutover 回归，不在日常默认车道新增真实进程。
最终 `-m "not qualification"` 覆盖日常、process 与 e2e。资格脚本仅在 S3/S5 因 identity / 字段变化
必须改冻结值时重跑，不扩矩阵。

E1 的 AC2 必须回填「inspect 不访问 `distribution.files`」的公开 nodeid 与源码扫描命令；E4 的
AC12 必须回填静态 unavailable / `NO_HINT` 不产生 Failure 的公开 nodeid。E5 逐行核对 D043 §2
吸收表和 AC1–AC14，记录每项证据去向；上述命令不能代替该人工 reconciliation。

## 6. 行动、偏差与未决

行动日志：

- 2026-09-09：接受 D043；本文件锁定 S1–S5 与 AC1–AC14；未改 `src/` 或 `tests/`。
- 2026-09-09：Plan review 后补齐原子 contract-cutover、Run-owned reprepare recipe、
  cache→Journal→latest 失败语义、公开测试与同变更归档；同步澄清 D043 §6、§9 与 AC4/AC7/AC8。
- 2026-09-09：开工对齐 §4 停止条件与 D043 §12（reprepare 再 resolve、部分失败提交
  latest、diagnose 读 ty-cache）；D043 状态改为实施中。
- 2026-09-09：S1–S4 落地。E1 教学 golden：observation
  `44d05ec7bd7b49b5dce62bab556e20b7782491f005c822a87575beb987564b87`，cache
  `fe199b2337c0f74150b2ea4c7343617a5394ff8fe414978a22ba95ab67ac3bd8`，guidance
  `8fb8c3f293468b8f5952fa7d6c3d9056a4b69b4fced3f0a81e84929219a1b8a0`。
  日常未标记 2420 passed；`ruff check tests src`、`ty check`、`git diff --check` 通过；
  `tests/test_static_process.py` process 代表项 2 passed。
- 2026-09-09：S5 吸收 D001/D003/D004/D008/D012/D014/D002/D006、CONTEXT、D039；
  同变更归档 D043/P045/I002。

偏差与失败用例（实施时追加；须含切片、决定或 nodeid）：

- S4 门禁：`tests/test_ty_adapter.py::TestTyOutputDecoder::test_real_ty_validates_invalid_project_configuration`
  原期望 `invalid-layout` 或启动 ty 后 `TOOL_FAILURE`。v2 对非法 `[tool.ty]` 在物化阶段
  记 `configuration-context-unavailable` 且不启动 ty；已按现行决策表改断言。

## 7. 文档与生成物

| 切片 | 允许改的文档 / 生成物 |
| --- | --- |
| 本变更（Plan 起草） | D043 状态；本文件；`docs/README.md` 开放事项；I002 后续指针 |
| S1 | 测试与教学 golden；固定 pylock parser fixture；不手改 `docs/schemas/` |
| S2 | 无生成投影 |
| S3 | `scripts/generate_report_schema.py` 输出的 schema/examples；删除 intern 的报告 fixture |
| S4 | Journal fixture（如 `tests/fixtures/admitted-static-journal.json`）；不手改 schema |
| S5 | D001/D003/D004/D008/D012/D014/D002/D006、CONTEXT、D039；D043/P045/I002 状态与归档；`docs/README.md` 现行/历史索引及全部现行入链；必要时再跑生成投影 `--check` |

S5 吸收对照 D043 §2 表。CONTEXT 区分 Journal / ty-cache / 公开报告；diagnose 不审计静态。
D039 AC10 改为：不涵盖五表删除、`selection_reason` 与 v2 identity 字节。完成后 P045 状态改为
「已完成」，D043 记录已吸收 owner，再与 I002 一并移动到对应 `docs/archived/` 目录；归档文件
只保留历史证据，不继续充当现行规范。

## 8. 非硬门禁证据槽

对照 [I002](../investigations/I002-pf-self-search-py310-static-collection.md)。不作为 AC 通过条件。数值在实施后回填。

| 槽 | 观察 | 结果 |
| --- | --- | --- |
| P1 | 构造 subject 时 venv / stdlib / ty 可执行文件树 walk = 0 | 未测 |
| P2 | inspect 不读 `distribution.files` | 未测 |
| P3 | 约 40-fact persist 墙钟（对照 I002 3.10 intern） | 未测 |
| P4 | 新报告体积与 `ReportStore.read`（对照 I002 42MB / ~37s） | 未测 |
| P5 | 3.10 卡片与收尾分段（对照 I002 B） | 未测 |

## 9. 明确不做

与 D043 §14 相同：跨 Run cache；git 管理 ty-cache；跟随未知 symlink；宿主配置读进 digest；用安装后文件树隔离同一 artifact 集合；`diagnose FAILURE_ID` 展示静态；关闭 R006/R008/R010 其它项；实施 D039 搬家。

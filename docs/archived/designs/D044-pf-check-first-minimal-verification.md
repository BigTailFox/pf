# D044 — PF check 稳态与最小验证序列

- **状态：** 已完成并归档；2026-09-11 通过 AC1–AC10 验收，稳定规则已由现行 owner 接管；实施与证据见 [P048](../plans/P048-pf-check-first-minimal-verification.md)
- **日期：** 2026-09-11
- **性质：** 已归档临时迁移 Design；不再承担现行规范
- **目标 owner：** [D001](../../designs/D001-pf.md)、[D002](../../designs/D002-pf-implementation.md)、
  [D004](../../designs/D004-pf-ty-enhancement.md)、[D006](../../designs/D006-pf-cli-enhancement.md)、
  [D008](../../designs/D008-pf-verification-run.md)、[D012](../../designs/D012-pf-harness-relaxation.md)
- **验收标准：** [§6](#6-验收标准)
- **实施 Plan：** [P048](../plans/P048-pf-check-first-minimal-verification.md)
- **来源：** [C005](../concepts/C005-pf-check-first-lifecycle.md)；
  [R008](../reviews/R008-pf-search-performance-review.md) §10
- **关联：** [D003](../../designs/D003-pf-search-algorithm.md) 搜索算法、
  [D005](../../designs/D005-pf-failure-and-diagnose.md) verifier/prepare 资格、
  [D007](../../designs/D007-pf-process-output.md) 日志与安全读取、[D014](../../designs/D014-pf-report-schema.md) 报告契约继续由原 owner 定义

本文保存已完成的 check 稳态与 smoke/check 最小验证序列迁移。稳定规则已归并现行 owner。
正文保留迁移时的目标与理由，不再承担现行规范。实施与证据见 [P048](../plans/P048-pf-check-first-minimal-verification.md)。
2026-09-11 范围收缩：回执及其声明／报告事务扩展移回 C005 待证，不再属于已接受目标；
现有 apply 安全契约保持。此前含回执的版本见 Git 历史 `bd8e9ae`。

## 1. 产品周期与范围

```text
接入     pf smoke → pf search → pf explain → pf apply
稳态     pf check（可加入 CI）
重定界   作者主动治理，或 check 失败后选择 pf search → pf apply
```

- Search 报告回答固定契约 `C` 上得到什么 floor；apply 继续按当前内容 identity 严格授权。
- Check 只消费当前 package 声明与配置，不要求报告、Git 仓库或曾执行 search/apply。
  每次 invocation 都重新准备环境；declaration prepare 成功后运行原 configured verifier 的完整命令阶段。
  历史 PASS、REJECTED 或相同 snapshot 均不跳过本次验证。
- Check 继续使用现行 `lowest-direct` 语义；不从旧报告恢复精确向量，不证明旧向量仍然最小，
  也不扩大到未运行的 Cell 或所有允许版本。源码/测试变化后，旧报告不自动取得新快照权威。
- Check 不搜索、不 apply、不读写报告，不保存“最近成功 identity”或供下次跳过验证的状态。
  现有 Process Log、Failure Journal 与诊断功能仍按原职责工作。
- 用户自行提交、回滚或修改文件；Git dirty 与内容 drift 是不同事实，不增加“项目已被 PF 管理”状态。

现有 apply 的严格授权、NOOP/合法重入、source/policy/declaration 校验、有限 `--force` 豁免，
以及 workspace snapshot 复核、raw CAS、原子替换、写后验证、恢复与全量回滚，继续由 D001 §6
和 D002 §9 拥有。本次不改变这些规则、既有参与文件范围或恢复入口时序。

本次不新增 `apply_receipts`、回执专属 `floor_identity`/`apply_id`、历史展示或报告写回；
不把报告加入 apply 共同提交范围，不新增为此服务的 guard/pending 协议及跨命令协调。
其他非目标：跨 Run ty/runtime/FailedCaseSet 复用、观察存储、check 成功历史、Git commit/检索、
增量 extra/受管依赖 apply、apply 历史与用户回滚命令、跨 generation 证据继承、C006 影响面。
现有 Run 内 search 去重和两阶段算法继续由 D003/D004 拥有。

## 2. 替换的 owner 规则

| Owner | 接受并吸收后的变化 |
| --- | --- |
| D001 §5、§7 | 增加产品周期；smoke/check 零 ty，删除两命令的 --ty-jobs；无活跃 harness 时按命令区分静态行为；check 按 §3 条件取得 HarnessBaseline，每次执行完整 verifier |
| D002 §3、§5、§7、§11 | SmokeCommandWorkflow 改用 SmokeVersionVerifier；移除 Smoke/Check static 依赖与 request 的 ty_jobs override；保留 Search cache；同步 typed outcome、composition 与公开验证边界 |
| D004 §7、§9 | static capture/compare/Direct-PASS 仅服务 Search；smoke/check 不取得 TyCheck、不登记 runtime PASS；Run 内身份和拒绝规则不变 |
| D006 §5–§7、附录 A.1、A.6 | 更新周期 epilogue、选项适用范围、真实 context/stage 与 harness preparation 摘要 |
| D008 §2–§7、§10 | command-specific operation、最小 Attempt 序列、harness-prepare Role、初始 context、Check 聚合及准备失败 impact；总 deadline 只适用 Search；Smoke/Check 保留空 ty-cache sidecar 与空 static membership |
| D012 §3.1–§3.2、§6 | 按命令重写 baseline 取得规则：仅 Check DEGENERATE 可无 highest 构造 baseline；Search 包括全 fixed 仍取得完整 satisfaction evidence；同步 harness-prepare 名称，保留 resolution/install/graph 复证与 request/Attempt identity |

D003、D005、D007 不新增 authority、failure cause、process provenance 或缓存规则。
D001 §6/§9、D002 的 apply 持久化职责与 D014 的 Schema 1/reader/merge/update 不作合同替换；
报告模型、generation、Attempt/Proposal/Failure identity 算法不因本次改写。

## 3. smoke/check 最小验证序列

StaticEvaluator 不参与 smoke/check；不运行 ty、不 capture/compare `S_hi`、不建立 Direct-PASS
ledger。准备失败仍走 D005；准备成功后的 PASS/REJECTED 只来自本次完整 runtime Evaluation。

Smoke 每个宿主 Cell：

```text
prepare(highest, original harness, DEVELOPMENT) → full evaluate → close
```

Check 在 prepare 前，通过 D012 唯一 harness policy owner，对已资格化的 active external
requirements 作纯决定：

```text
HarnessBaselineRequirement =
  REQUIRED     任一 active requirement 为 ceiling_eligible
  DEGENERATE   其余情况
```

Check 不另写 specifier/source 解析。该决定不增加持久 schema。

`DEGENERATE` 包括无 active external harness，以及全部 active requirement 为固定 source 或精确
`==X` / `===X`。构造同一 Cell 的普通 HarnessBaseline：保存排序唯一的 active external declaration
IDs，`observations=()`。这些 requirements 不删下限、不追加 ceiling，original/relaxed harness
语义相同。该 baseline 正常计算 digest 并进入 request/Attempt identity，不用缺失哨兵。
无 highest prepare 而构造此形状的权限仅属于 Check DEGENERATE；非空 IDs 加空 observations
表示本路径无需 baseline ceiling，不表示已经观察过 harness satisfaction。随后 declaration
仍按 D012 完整 resolve/install/inspect；active IDs 非空仍须复证 environment plan，不能改走
project-only 安装。

```text
degenerate HarnessBaseline
→ prepare(lowest-direct, baseline, SEARCH) → full evaluate → close
```

`REQUIRED` 仍从原始 harness 的最高环境取得 `U_B` 与 project/harness ownership：

```text
prepare(highest, original harness, SEARCH)
→ derive HarnessBaseline → close                 # 零 ty，零 verifier
prepare(lowest-direct, baseline, SEARCH)
→ full evaluate → close
```

首个 prepare 失败时不继续 declaration。只要任一 requirement ceiling-eligible 就走 REQUIRED；
`~=` 与 wildcard equality 仍属此分支。本次不在 lowest plan 形成后再懒启动 highest，不持久化
HarnessBaseline。D012 §3.1 的最高环境安装路径适用于 Smoke/Search baseline 与 Check REQUIRED：
有活跃 harness 时安装 `E(B)`，否则安装 `G(B)`，均完整复证 graph。Search 与 Check REQUIRED
保留供后续 relaxation 消费的完整 satisfaction observations，包含 fixed 项；Search 无活跃
harness 时得到 IDs/observations 均为空的 baseline。Smoke 不要求在成功结果中保留
HarnessBaseline 或 observations。Search 即使全部 harness fixed 也执行 highest prepare、
`S_hi` capture 和 full baseline verifier，再进入候选冻结与两阶段搜索；不得套用 Check
DEGENERATE 省略这一步。

## 4. 编排、Role 与展示

公开 operation 分别为：

```text
CheckCellOperations.check(package, cell, snapshot, source_plan, baseline_requirement)
SmokeCellOperations.verify(package, cell, snapshot, source_plan)
CellSearchOperations.search(..., run_cache)
```

`CompatibilityChecker` 与新增 runtime-only `SmokeVersionVerifier` 不接收 StaticEvaluator 或
TyCheckCache。SmokeVersionVerifier 与 Search 使用的 HighestVersionVerifier 位于现有
`baseline.py`，可共享 private preparation helper；HighestVersionVerifier 只供 Search。
不通过公开 purpose boolean 切换两种契约。

Runner 仍拥有 Run cache 生命周期与 Journal 提交，只向 Search 注入 TyCheckCache。
启用现有 Run 日志时，Smoke/Check 仍创建并持久化 `ty-cache.json`，其 entries 和 Journal
`static_membership` 均为空；不省略该 sidecar，也不增加成功历史表。沿用 D008 的
cache → Journal → latest 提交顺序与失败处理，不为这两条命令执行静态收集。

| 命令/路径 | Attempt Role | 初始及后续 context |
| --- | --- | --- |
| Smoke | baseline，full runtime | baseline |
| Check REQUIRED | harness-prepare 只取得 HarnessBaseline；之后 declaration full runtime | baseline → declaration |
| Check DEGENERATE | 只有 declaration | declaration |
| Search | 现行 baseline/probe | 现行 context |

Runner 仍唯一拥有 initial CellContextEvent；调用同一 harness policy 决定 Check 初始 context，
并把该不可变决定传给 Check，避免重复解析或不同分支。REQUIRED 在 declaration 开始前切换一次，
DEGENERATE 不发布 baseline，也不重复发布 declaration。只展示实际执行的 stage。

`harness-prepare` 干净替换 `declaration-capture`：同步 CheckCellOutcome、VerificationRole、
Journal 的 command/request/Role 校验、live/final projector 与 diagnose reader，不保留旧 Role alias。
它仍是 highest request，prepare 成功后不形成通过验证的 Evaluation；Role 不进入 Attempt、
Proposal 或 Failure identity。D008 §10 的该 Role impact 固定为：

- Rejected：`The highest-version harness baseline could not be prepared, so declared lower bounds were not verified for this cell.`
- Indeterminate：`Whether the highest-version harness baseline can be prepared is unknown, so declared lower bounds were not verified for this cell.`

D008 §6 的 Check 逐 Cell 结果选择改为：

- DEGENERATE 只使用 declaration 的结果。
- REQUIRED 若最高环境 prepare 失败，使用 `harness-prepare` 的原 Rejected/Indeterminate；
  不启动 declaration，不伪造下界 Evaluation。否则使用随后 declaration 的结果。
- 命令聚合仍为任一 Rejected → `COMPATIBILITY_FAILED`，否则任一 Indeterminate →
  `INDETERMINATE`，其余 → `PASS`。最高环境准备失败不证明声明下界不兼容。

`max-cells` 仅限制并发，排队不产生结果。Smoke/Check 沿用没有总 deadline 的命令表面，合法
Run 的 `max_duration_seconds` 必须为 None；正常完成覆盖全部选定宿主 Cells，typed failure
不截断其他 Cell。D008 §4 的未启动 deadline Cell 规则明确只适用 Search，
仍为 `TIMEOUT @ scheduler-deadline` 的 Cell-scoped Indeterminate，无 Attempt 或 initial context。
Smoke/Check 不新增对应 outcome variant。取消或基础设施异常造成未启动 Cell 时，按现行中断或
命令失败路径收尾，不给它补 capture/declaration 结果或 PASS；合法空 host Cell 集的处理不变。
REQUIRED 的 `harness-prepare` 已成功而 declaration 尚未开始时，若发生取消或基础设施异常，
同样走现行中断或命令失败路径；不得把成功的准备阶段当作 Cell PASS 或 declaration 结果。

D006 §7 的 Check 摘要同步更名：聚合为 `COMPATIBILITY_FAILED` 且含失败的 `harness-prepare`
outcome 时，使用 `Check failed · harness preparation did not pass · N cells`；只有 declaration
rejection 支持下界不兼容结论。聚合为 `INDETERMINATE` 时仍使用 unknown 摘要。
D005 title/next step、各命令数值退出码保持原契约。

D001 §5、§7 与 D006 附录 A.1 的命令表面同步为：

```text
pf smoke [--package PACKAGE] [--max-cells auto|N] [--test-jobs auto|N]
pf check [--package PACKAGE] [--max-cells auto|N] [--test-jobs auto|N]
```

两命令删除 `--ty-jobs` 及 CheckRequest/SmokeRequest 的同名 override 字段，不保留无操作选项。
Search/minimize 继续提供 `--ty-jobs`。共享 `[tool.pf].ty-jobs`、`ty-args`、`ty-timeout` 仍可配置并
按既有规则校验，但只影响 Search 的静态工作；Smoke/Check 不因这些设置执行 ty。共享 RunLimits
可保留解析后的 ty_jobs，不能据此把静态依赖重新注入 Smoke/Check。D001 §7 的无活跃 harness 规则
改为：smoke/check/search 均直接安装并复证 project plan、运行 configured verifier；只有 Search 仍运行 ty。

D006 附录 A.1 的 help epilogue 固定为：

```text
Onboarding: pf smoke -> pf search -> pf explain -> pf apply. Steady state: pf check. Use pf minimize to search and apply in one command.
```

双语 README 按各自语言表达同一流程，引用 owner；check 不是必须先 apply 才能使用的命令。

## 5. 不变量

1. Check 与报告/apply 历史解耦；每次 declaration prepare 成功后都运行完整 verifier，无跨 Run 复用。
2. Smoke/Check 零 ty；只有 ceiling-eligible harness 令 Check 建立 highest Attempt。
3. 最高环境准备成功不构成 declaration 或 Cell PASS；失败、聚合及中断均按实际执行事实表达。
4. Search 的 highest/static/full baseline、候选冻结和后续搜索顺序保持现行契约。
5. 现有 apply 准入、NOOP 与写入安全保持；报告不新增应用状态或成为 apply 写入参与者。
6. 用户负责版本控制；PF 不创建 commit，不要求 Git clean，不维持项目管理状态。

## 6. 验收标准

按 [tests/README.md](../../../tests/README.md) 选择公开 seam。进程内 recording adapter 证明路径和调用
次数；真实 CLI 连续运行证明每次验证与跨命令使用，不用 schema 构造或仅退出 0 替代。
实现 Plan 为每项保留具体命令、结果和日志；本文不填写尚未取得的 PASS。

| ID | 标准 |
| --- | --- |
| AC1 | D001、双语 README 与 D006 help 表达 §1 周期；smoke/check CLI 与 typed request 只保留适用的 scheduling override，search/minimize 保留 --ty-jobs；共享 ty 配置不触发 smoke/check 静态工作；当前声明即使来自手写、无报告，也可 check；不引入 Git 或自动 search |
| AC2 | Smoke 每 Cell 只 prepare highest 一次、full verifier 一次、ty 零次；相同快照再次 invocation 仍实际执行 verifier |
| AC3 | Check 无 active harness 与全部 fixed 两类 DEGENERATE 场景只 prepare lowest-direct；baseline 含正确 active IDs 与空 observations；fixed requirements 不删下限、不加 ceiling；非空 active IDs 仍完整复证 environment plan |
| AC4 | Check REQUIRED（含 ~=、wildcard equality）先 prepare highest 取得完整 HarnessBaseline、零 ty/verifier；成功后 declaration prepare 并 full verifier；highest 失败不启动 declaration |
| AC5 | 同快照连续两次真实 check 均执行完整 verifier；源码/测试变化后仍执行；不读写报告、不要求 apply provenance、不保存成功 identity 或观察缓存 |
| AC6 | Smoke/Check 构造不接收 StaticEvaluator/TyCheckCache；Search 包括全部 fixed harness 仍取得完整 highest satisfaction evidence 并执行 S_hi/full baseline；highest/static/candidates/probes 顺序及公开结果保持现行契约 |
| AC7 | DEGENERATE 初始 context 只有 declaration；REQUIRED baseline→declaration 恰一次切换；harness-prepare 的 typed outcome、Journal、live/final/diagnose 语义闭合；无 ty/static stage；有 Run 日志时实际持久化空 ty-cache sidecar 和空 Journal membership；多 Cell、max_cells=1 的两分支及失败混合遵守 §4 聚合并全部调度；未启动或 highest prepare 成功后、declaration 开始前的中断/异常不伪造结果或 PASS；Search 保留 Cell-scoped deadline |
| AC8 | 无 Git 的真实 smoke→search→explain→apply→重复 apply→check→修改源码/测试→check 流程闭合；重复 apply 遵守既有 NOOP；apply/check 均不改报告；minimize 继续使用现有 search/apply 流程 |
| AC9 | 既有 apply 授权、source/policy/declaration drift、--force、NOOP/合法重入及编辑器安全写入/恢复的公开回归保持；受影响的 Cell 契约沿用三类 target family 代表用例；不新增报告 Schema 字段或回执事务 |
| AC10 | 所需 daily/PR/coverage 车道、文档及 schema/example 一致性检查完成；逐项审计 AC，完成 §7 owner 吸收后归档本 Design 与实施 Plan |

## 7. 吸收与后续

按 §2 回到各唯一 owner。D001 吸收产品周期与命令选项；D002 吸收 Smoke/Check 接口及
composition；D006/D008 补齐 context、impact、Check 聚合、阶段间中断与 help；D004/D012
吸收 static 消费范围和按命令取得 HarnessBaseline 的规则。逐条替换旧全称句、Role 和
选项适用范围，不只追加 enum/段落。

模型、公开测试、Journal fixtures、双语 README、owner map 与现行入链在同一完成变更闭合。
CONTEXT 只同步实际受影响的验证术语，不增加应用回执/目标词条；Schema 1 不安排迁移。
D014 的生成投影仍参加一致性检查。

C005 继续跟踪增量 apply、跨 Run 观察复用、可选应用记录与可回滚历史。回执须先证明具体
消费者及收益，再独立决定普通操作日志或与声明共同提交的记录，不预设存放位置、identity
或强一致事务。R008 的跨运行缓存非目标保持，C006 不是本次前置。

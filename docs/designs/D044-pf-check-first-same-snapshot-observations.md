# D044 — PF check 稳态与同快照观察准入

- **状态：** 草案
- **日期：** 2026-09-10
- **性质：** 临时迁移 Design；接受前不构成目标契约，吸收完成前不冒充已交付行为
- **目标 owner：** [D001](D001-pf.md)、[D002](D002-pf-implementation.md)、
  [D004](D004-pf-ty-enhancement.md)、[D006](D006-pf-cli-enhancement.md)、
  [D008](D008-pf-verification-run.md)
- **验收标准：** [§9](#9-验收标准)
- **来源：** [C005](../concepts/C005-pf-check-first-lifecycle.md) 第一刀；
  [R008](../reviews/R008-pf-search-performance-review.md) §7 / §10
- **关联：** [D003](D003-pf-search-algorithm.md) 仍只消费直接 runtime 观察，不改两阶段搜索；
  [D005](D005-pf-failure-and-diagnose.md) 分类与 PASS 资格不变；
  [D007](D007-pf-process-output.md) Output Cache / Process Log 不变；
  [D014](D014-pf-report-schema.md) 公共报告仍不是观察存储；
  [C006](../concepts/C006-pf-test-dependency-association.md) 不是前置

本文定义 C005 第一刀的**目标**契约：把 `pf check` 写成库作者日常权威，并把「同契约 `C` 上已取得的
直接观察」做成可跨 invocation 准入的记录。它不是新的长期 owner。吸收后稳定规则分别回到上列
目标 owner；本文件归档。

不做持久「观察缓存 Design」：观察准入是 D004/D002 对现有 Run cache 的加深，产品周期是 D001 的
叙事，help 是 D006 的固定文案。另立缓存 owner 会变成 R008 已否决的通用 cache 服务。

接受本身不授权改生产代码；实施以后续 Plan 为准。吸收完成前，现行行为仍以 D001/D002/D004/D006/D008
为准。

## 1. 结论

现行 `pf check` 已经「不搜索、不写报告、只验证声明下界」。缺口是产品把 search 当主路径：README
与 help epilogue 仍是 `smoke → search → apply`；TyCheck / Evaluation 的复用停在一次 Verification
Run 的内存句柄，进程结束后即使契约 `C` 未变也必须重跑 configured verifier。

目标：

```text
接入     pf smoke → pf search → pf explain → pf apply
稳态     pf check
重定界   check 已失败且作者选择搜索，或主动治理时，再 search → apply
```

同契约 `C` 上，已准入的直接 `PASS` / `REJECTED` 观察在后续 invocation 中仍是直接观察。命中只跳过
ty 进程与 configured verifier，不跳过 `EnvironmentFactory.prepare`，不借用可写 venv，不把命中写成
快照已变后的 floor。

## 2. 范围与非目标

**接受并吸收后替换：**

| 目标 owner | 替换内容 |
| --- | --- |
| D001 §1 | 「同 context 的既有直接完整 PASS 可复用」扩展为：同契约 `C` 上已准入的直接完整 PASS，不限于同一次 Verification Run |
| D001 §4 | 「相同 Proposal 与 Evaluation context 的完整测试一次运行内最多执行一次」扩展为：同一 `RuntimeObservationKey` 在已准入存储中对完整 verifier 至多一次；同一次 Run 内仍至多一次 |
| D001 §5 | 命令表标明 check 是稳态；search 是接入与重定界。命令、参数、退出码、不搜索/不写报告的行为本身不改 |
| D001 §9 | 「跨运行 Proposal/Evaluation environment cache」改为禁止：跨运行共享可写环境、把观察命中写成快照已变后的 PASS、通用 cache 服务。身份闭合的观察记录不再列为非目标 |
| D002 §3、§7、§9 | 不新增公开 ObservationStore / cache 服务。`TyCheckCache` 与 `EvaluationCache` 仍是调用方可见的 Run 句柄，改为同一观察存储的热投影；持久化仍由 `RunLogStore` 落在 `.pf/`，与 Journal / 每 Run ty-cache sidecar / 报告分家 |
| D002 §11 | 准入命中从公开 Check/Highest/Search outcome 与 recording adapter 的 ty/verifier 次数观察，不进口存储内部 |
| D004 §7、§10、§12 | 「跨 Run/Cell 与已关闭 refs 即使 key/payload 相同也被拒绝」改为：跨 Cell 仍拒绝；Run-local 句柄不得跨 Run 传递；跨 Run 仅当 §5 准入命中。删除「没有跨运行 Evaluation cache」中与本文件冲突的句。搜索主路径内存 miss 可按策略读存储 |
| D004 `record_runtime` | 准入的本 Run `PassEvaluation` 可写入 Direct-PASS ledger，不要求本 Run 的 verifier ProcessObservation |
| D006 附录 A.1 | help 分组已把 check 放在 Verify；改 epilogue，使稳态为 check |
| D008 §1、§4、§7 | 每 Run 仍构造 ty-cache 热投影并写该 Run sidecar / Journal。观察存储不是 ty-cache、不是 Journal、不服务 `diagnose`。准入命中不伪造本 Run Process Log；`diagnose_available` 仍只在本 Run 确有 Journal + verifier sidecar 时为真 |

**保持不变：** D001 命令形状、退出码、apply 整组准入、`--force` 范围、`C` 绑定快照；D003 两阶段搜索与静态无 compatibility disposition；D005 PASS 只来自原命令阶段 `NormalExit(0)`；FailedCaseSet 仍只在同一次 Verification Run、当前下降坐标内；D007 Process Log / Output Cache；D012 每次 Attempt 仍 prepare、不同 Proposal 不共用可写环境；D014 Schema 1 字段与「报告不是 Evaluation cache」。

**本文件不覆盖：** C005 的增量 extra / 增量受管 apply、apply 本机历史与回滚；跨快照复用；registry 候选观察；FailedCaseSet 持久化；R008 的跨运行 hints、single-flight、materialize、xdist；C001 树搜索；C004 非单调 refinement；C006 影响面；新命令或开关。

## 3. 产品周期

吸收后 D001 在 §5 命令表之前增加周期，而不改变三条命令的 Attempt 序列（仍见 D008 §3）：

| 阶段 | 命令 | 权威 |
| --- | --- | --- |
| 接入 | `search` 写出报告，授权后 `apply` 写入声明下界 | 该次 `C` 上的 search 结果 |
| 稳态 | `check` 在**当前**快照上重新证明声明下界 | 当前 `C` 上的 declaration 完整 verifier |
| 重定界 | 作者在 check 失败后选择，或主动治理时，再 `search` / `apply` | 新的 search；CI 不因 check 失败隐式 search |

`check` 通过：下界在当前 `C` 上成立；不写报告、不 apply。`check` 失败：退出码与现行 D001/D008 相同，默认就是失败。

`smoke` 仍用 DEVELOPMENT SourcePlan，只回答「当前声明允许的最高版本能否新鲜安装并测试」，不是稳态转运，也不是 search 的观察来源。

双语 README 与 D006 help 是该周期的用户投影，不能另写一套主路径。目标 help epilogue 固定为：

```text
Onboarding: pf smoke -> pf search -> pf explain -> pf apply. Steady state: pf check. Use pf minimize to search and apply in one command.
```

## 4. 观察记录与一套存储

观察记录只有一种。Run 内 `TyCheckCache` / `EvaluationCache` 是同一记录的热投影；进程结束后仍按
§5 策略再读。不维护平行的「短期 cache API」和「长期 cache API」，composition root 不出现第二套
lookup。

记录保存完整身份与完整观察，不先按命中规则折成短 key。换策略只改准入，不强迫迁另一套存储。

**可准入的种类（本文件仅此两种）：**

| 种类 | 观察内容 | 不可准入 |
| --- | --- | --- |
| 原始静态事实 | `TyCheckFact` 或 `TyCheckUnavailable` | 某次 guidance 解释、比较结果、search/skip 账本 |
| 完整 runtime Evaluation | `PassEvaluation` 或 `VerifierRejectedEvaluation` | `IndeterminateEvaluation`、failed-set 阶段 `NormalExit(0)`、PrepareFailure |

**明确不作为可准入 payload：** `PreparedEnvironment` / venv、Process Log 正文、Journal 条目、
`package-floor.json`、FailedCaseSet、registry 候选快照、Role、run-id、attempt-id。

存储可以记载「某逻辑向量曾物化过」，但新 invocation 不得打开或复用旧 venv。每次需要环境的
Attempt 仍走 `EnvironmentFactory.prepare`。命中只使 `TyOperations.observe` 与
`ConfiguredVerifier.run` 不再发生。

Lookup 返回记录。**当前契约能否采用**由准入策略回答。本文件只接受一条策略：
`same-contract-direct-v1`（§5）。快照已变、Cell 不同、SourcePlan 不同或执行身份不同 → miss，
重跑对应操作。不得把 miss 写成当前 `C` 的 PASS。

损坏、截断、身份与 payload 不一致的记录当作 miss，不形成 Indeterminate，不形成 PASS。

## 5. 身份与 `same-contract-direct-v1`

现行 `proposal_id`（`environment_identity_digest`）含 `attempt_id`，不能当跨 invocation 的存储键。
Run-local 句柄继续用现行 key；**存储键**不含 Role、run-id、attempt-id、Journal id。

### 5.1 RuntimeObservationKey

```text
source_snapshot_digest
Cell identity          # target, CPython minor, extra surface
SourcePlan.identity
ExecutionPolicy.identity
resolution_graph_id    # 已解析图；与 D014 图身份同一口径
project_plan_digest
environment_plan_digest  # 含 project-only 的 null
kind = full-verifier-evaluation
```

准入当且仅当当前 Attempt 的这些字段与记录逐位相等，且 payload 为 `PASS` 或 `REJECTED`。
命中后把 disposition 与 verifier terminal 事实**绑定到当前 Proposal / Attempt / Role**；历史记录
不改写。本 Run 的 `EvaluationCache` 随后按现行 `(proposal_id, policy_identity)` 命中。

因此：check 的 declaration（lowest-direct）与 search 对同一已解析图的 exact-vector probe 可以
共用一条 runtime 观察；check 的 DEVELOPMENT-incompatible 路径不存在，因为 check 与 search 都是
SEARCH SourcePlan。smoke 的 DEVELOPMENT plan 与它们不相等，不得命中。

check 只对 highest 做 static capture，不对 highest 跑完整 verifier。随后的 search 仍须对 highest
跑完整 verifier，除非存储里已有该图的 runtime 观察（例如先前 search 留下的 baseline）。

### 5.2 TyCheckKey

沿用 D004：`(StaticSubject.identity, TyObservationPolicy.cache_identity)`。另要求记录中的
`source_snapshot_digest` 等于当前 Run 快照。跨 Cell 仍拒绝。命中后在本 Run 重建
`CollectedStaticSubject` 句柄，不传递上一 Run 的句柄。

`TyCheckUnavailable` 可准入为「该 subject 的原始静态事实」，语义仍是 UNAVAILABLE / 无 hint，
不是 REJECTED。

### 5.3 冲突

本 Run 对同一当前 Proposal 已有不同 disposition → 仍为 `NONDETERMINISTIC`。存储写入在本 Run
成功取得 `PASS` / `REJECTED` 之后；不写入 Indeterminate。后一次同 key 的不同
`PASS`/`REJECTED` 不得覆盖前一条以使单调假设或 floor 变得「看起来一致」；按现行 cache conflict
处理，不得丢弃已观察 disposition。

## 6. 本 Run 句柄与 Direct-PASS

产品调用方仍只看见：

```text
VerificationRunner 构造并关闭 TyCheckCache
Check / Highest / Search 转交 cache
RuntimeEvaluator.evaluate → Evaluation
StaticEvaluator.collect_prepared / capture_highest / record_runtime / ...
```

不新增 `ObservationStore.lookup` 产品入口。admission 在 cache/evaluator implementation 内。

准入的 `PassEvaluation` 进入本 Run 后，Check / Highest / Search 仍在 `evaluate` 之后、prepared
close 之前调用 `record_runtime`。此时允许没有本 Run verifier ProcessObservation；ledger 记录当前
Proposal 为 Direct-PASS owner。不得伪造 Process Log 或 ty process 对象去满足现行「diagnostics
process 必须是本 Run 进程」检查。

`CollectedStaticSubject`、Preparation registry、每 Run `ty-cache.json` 仍是 Run-local。每 Run
sidecar 继续服务 Journal `static_membership` 与保存审计，不是跨 Run 的 lookup API。不得打开另一
`run-id` 的 `ty-cache.json` 当 oracle。

## 7. 模块与持久化

D002 §9 增加一条分界，而不是新 module：

| Module | 本文件下唯一负责 | 不负责 |
| --- | --- | --- |
| `RunLogStore` | `.pf/` 内观察记录的编解码与原子写入；与现有 Process Log / Journal / 每 Run ty-cache sidecar 分文件 | disposition、apply 权威、报告、diagnose 读取面 |
| `TyCheckCache` / `EvaluationCache` | 本 Run 热投影、`same-contract-direct-v1` 准入、写入存储 | 通用 cache API、跨进程 venv、hints |

物理子路径由 Plan 选择，必须：在 SourceSnapshot 排除且默认 gitignore 的 `.pf/` 下；不进入
`package-floor.json`；不与 `.pf/logs/<run-id>/ty-cache.json` 混成同一 lookup。绝对路径、checkout
root 不进入 Schema 1、report identity 或 Journal。

D002 §1 继续禁止通用 filesystem/repository、DI、daemon。本存储不是后台服务。

## 8. 不变量

1. 当前快照上的 check / oracle 证据决定真值。观察命中只降低 ty 与 configured verifier 次数。
2. 完整 PASS 仍只来自原命令阶段 `NormalExit(0)` 的已准入记录或本 Run 新跑；static-only 与
   failed-set 不足以作为 final。
3. 最终精确向量在本 Run 仍须 prepare、复证图；verifier 仅在 runtime 观察已准入时跳过。
4. 可写环境不跨 invocation 借用；不同 Proposal 不原地升降级。
5. 跨 Cell、跨 SourcePlan、跨 ExecutionPolicy、快照已变 → miss。
6. 不伪造本 Run Process Log。准入的 REJECTED 可供 CoordinateSearch 定界；该 Failure 若无本 Run
   verifier sidecar，则 `diagnose_available` 为 false。
7. 公共报告、Journal、Process Log、观察存储、每 Run ty-cache 五者职责不合并。
8. 不因本文件改变 apply。合法 apply 仍开始新 snapshot；新快照上旧观察全部 miss。

## 9. 验收标准

吸收前每一项都要有公开 seam 证据。进程内 recording adapter 计数即可证明「跳过了 ty/verifier」；
不得用内部 store API 代替产品 outcome。

| ID | 标准 |
| --- | --- |
| AC1 | D001 在 §5 命令表之前写出周期；§1/§4 复用范围改为同契约 `C`；§5 check=稳态、search=接入/重定界；§9 非目标按 §2 改写。命令形状、退出码与现行 §3 矩阵编号不变 |
| AC2 | 双语 README 常见流程与 D006 附录 A.1 epilogue 等于 §3 固定英文句；check 仍不搜索、不写报告 |
| AC3 | 同快照、同 Cell、同 SEARCH SourcePlan、同 ExecutionPolicy 下，第二次 check 不调用 configured verifier，也不调用 ty observe（若第一次已留下对应 TyCheck）；两次 declaration outcome 相同 |
| AC4 | 任意改变 source snapshot digest 后，第三次 check 必须重新调用 verifier；不得沿用 AC3 的 runtime 观察 |
| AC5 | 同快照上 check 通过后再 search：declaration 已解析图的 probe 不重跑 verifier；highest 完整 verifier 仍跑，除非存储已有该图 runtime 观察 |
| AC6 | smoke（DEVELOPMENT）留下的观察不能被随后的 check/search 准入 |
| AC7 | 跨 Cell、或 ExecutionPolicy / SourcePlan 不等，不得准入 |
| AC8 | 本 Run 对 Indeterminate 不写入可准入存储；下一 invocation 对同一逻辑向量必须再跑 verifier |
| AC9 | 第二次 invocation 仍调用 prepare；测试不得观察到跨 invocation 复用同一可写环境对象或 venv 路径 |
| AC10 | composition root / 产品测试不进口观察存储内部类型；Check/Highest/Search 仍只转交 `TyCheckCache` |
| AC11 | 准入 PASS 后 `record_runtime` 成功且不要求本 Run verifier process；不得出现伪造 Process Log。准入 REJECTED 且无本 Run sidecar 时 `diagnose_available` 为 false |
| AC12 | `package-floor.json`、Journal 字段、Schema 1、apply 授权规则与 D014 identity 字节不因本文件增加字段 |
| AC13 | FailedCaseSet、registry 候选、跨快照 hints 均未持久化为可准入观察 |
| AC14 | `scripts/check_docs.py` 与 D002 §11 车道通过；本文件规则已写入目标 owner 后本 Design 归档 |

## 10. 吸收

稳定规则归并：

- D001：周期、复用范围、§9 非目标
- D002：§3/§7 句柄、§9 持久化分界、§11 测试表面
- D004：跨 Run 准入、`record_runtime`、删除与本文件冲突的「无跨运行 Evaluation cache」句
- D006 附录 A.1：epilogue
- D008：每 Run ty-cache 与观察存储分家；`diagnose_available` 与伪造 Process Log
- CONTEXT：吸收时增加「观察存储 / 观察记录」现行术语，并 `_Avoid_` 通用 cache、跨运行 environment cache；不把 C005 的「PF 地板」写入词汇（本文件未定义它）
- 双语 README：用户投影，不另立契约

C005 仍开放，跟踪增量 apply 与 apply 历史。C006 仍独立。R008 §7 对「跳过当前契约权威的
Evaluation cache / 共享可写环境 / 通用 cache 服务」继续有效；本文件不是那三项的例外，而是把
「当前契约」明确为身份闭合的 `C`。

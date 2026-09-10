# D044 — PF check 稳态、最小验证序列与同快照观察准入

- **状态：** 草案
- **日期：** 2026-09-10
- **性质：** 临时迁移 Design；接受前不构成目标契约，吸收完成前不冒充已交付行为
- **目标 owner：** [D001](D001-pf.md)、[D002](D002-pf-implementation.md)、
  [D003](D003-pf-search-algorithm.md)、[D004](D004-pf-ty-enhancement.md)、
  [D005](D005-pf-failure-and-diagnose.md)、[D006](D006-pf-cli-enhancement.md)、
  [D008](D008-pf-verification-run.md)、[D012](D012-pf-harness-relaxation.md)
- **验收标准：** [§9](#9-验收标准)
- **来源：** [C005](../concepts/C005-pf-check-first-lifecycle.md) 第一刀；
  [R008](../reviews/R008-pf-search-performance-review.md) §7 / §10
- **关联：** [D003](D003-pf-search-algorithm.md) 仍只消费直接 runtime 观察，不改两阶段搜索；
  [D005](D005-pf-failure-and-diagnose.md) PASS / REJECTED 资格不变；
  [D007](D007-pf-process-output.md) Output Cache / Process Log 不变；
  [D014](D014-pf-report-schema.md) 公共报告仍不是观察存储；
  [C006](../concepts/C006-pf-test-dependency-association.md) 不是前置

本文定义 C005 第一刀的**目标**契约：把 `pf check` 写成库作者日常权威，删除 smoke/check
不参与 outcome 的 static 工作并条件化 check 的 highest baseline acquisition，再把「同一源码快照与
同一直接观察上下文中已取得的观察」做成可跨 invocation 准入的记录。这里的 runtime 上下文是契约
`C` 中动态 authority 所需事实的投影，不等于完整 `C` identity：candidate / guidance / search
derivation 改变不使同一实际 runtime subject 的 verifier 事实失效。它不是新的长期 owner。吸收后
稳定规则分别回到上列目标 owner；本文件归档。

不做持久「观察缓存 Design」：观察准入是 D004/D002 对现有 Run cache 的加深，产品周期是 D001 的
叙事，help 是 D006 的固定文案。另立缓存 owner 会变成 R008 已否决的通用 cache 服务。

接受本身不授权改生产代码；实施以后续 Plan 为准。吸收完成前，现行行为仍以上列现行 owner 为准。

## 1. 结论

现行 `pf check` 已经「不搜索、不写报告、只验证声明下界」。缺口是产品把 search 当主路径：README
与 help epilogue 仍是 `smoke → search → apply`；TyCheck / Evaluation 的复用停在一次 Verification
Run 的内存句柄，进程结束后即使契约 `C` 未变也必须重跑 configured verifier。另一个缺口是
smoke/check 都运行了不改变其 outcome 的 ty；check 还对没有 ceiling 需求的 Cell 无条件先 prepare
highest。

目标：

```text
接入     pf smoke → pf search → pf explain → pf apply
稳态     pf check
重定界   check 已失败且作者选择搜索，或主动治理时，再 search → apply
```

同一源码快照只是必要条件，不是充分条件。§5 身份完整相等时，已准入的直接 `PASS` / `REJECTED`
观察在后续 invocation 中仍是直接观察。runtime 与 ty 两类记录分别只跳过对应的 configured verifier
或 `TyOperations.observe`；两者都不跳过 `EnvironmentFactory.prepare`，不借用可写 venv，不把命中
写成快照已变后的 floor。

## 2. 范围与非目标

**接受并吸收后替换：**

| 目标 owner | 替换内容 |
| --- | --- |
| D001 §1 | 「同 context 的既有直接完整 PASS 可复用」扩展为：同一 §5 runtime subject 上已准入的直接完整 PASS，不限于同一次 Verification Run |
| D001 §4 | 「相同 Proposal 与 Evaluation context 的完整测试一次运行内最多执行一次」扩展为：同一 `RuntimeObservationSubject` 可采用已准入的 terminal verifier 观察；同一次 Run 内仍至多一次 |
| D001 §5 | 命令表标明 check 是稳态；search 是接入与重定界。smoke/check 不运行 ty；check 仅在 §3.1 要求时先取得 HarnessBaseline。命令、参数、退出码、不搜索/不写报告的行为本身不改 |
| D001 §9 | 「跨运行 Proposal/Evaluation environment cache」改为禁止：跨运行共享可写环境、把观察命中写成快照已变后的 PASS、通用 cache 服务。身份闭合的观察记录不再列为非目标 |
| D002 §3、§7、§9 | 不新增公开 ObservationStore / cache 服务。`CompatibilityChecker` 与 runtime-only smoke verifier 移除 StaticEvaluator/TyCheckCache 依赖；search-baseline 与 probe 继续使用 Run-owned `TyCheckCache`。Composition root 按 §6 只借出同一 `RunLogStore`；`RuntimeEvaluator` 在 implementation 内准入持久 runtime 观察，并返回 §5.3 的 closed outcome union；`_ProposalRunner`-owned `EvaluationCache` 只保留当前 search 的动态热投影。持久化与 Journal / 每 Run ty-cache sidecar / 报告分家 |
| D002 §11 | 准入命中从公开 Check/Highest/Search outcome 与 recording adapter 的 ty/verifier 次数观察，不进口存储内部 |
| D003 §3、§5、§12 | pre-prepare 的 vector/direct lookup 与可写环境复用仍限本次 search；prepare 后形成相同 §5 runtime subject 时可采用持久 runtime 观察。删除与本文件冲突的「跨运行 Evaluation cache」非目标，不改变两阶段搜索、边界或终止 |
| D004 §7、§9、§10、§12 | static capture/compare 只服务 search；smoke/check 不取得 TyCheck、不建立 Direct-PASS ledger。Search 的 Run-local 句柄不得跨 Run 传递；跨 Run 只以 §6 closed provenance 重建 §5 已准入记录的本 Run 句柄，不伪造 process/ref。删除「没有跨运行 Evaluation cache」中与本文件冲突的句，search 内存 miss 可按策略读存储 |
| D004 `record_runtime` | 准入的本 Run `PassEvaluation` 可写入 Direct-PASS ledger，不要求本 Run 的 verifier ProcessObservation |
| D005 §1、§4–§5 | 保存的 PASS / REJECTED terminal 资格不变；§5.3 `RuntimeObservationConflict` 经唯一 FailurePolicy seam 在当前 Attempt 上形成 `INDETERMINATE / NONDETERMINISTIC @ observation-admission` 的 structured authority，坏 record 仍只是 miss |
| D006 §5–§6、附录 A.1 | help 分组已把 check 放在 Verify；改 epilogue，使稳态为 check。Live/completion 只展示实际执行的 stage，smoke/check 不再出现 ty/static stage |
| D008 §1–§5、§7 | Role、operation protocol 与命令序列按 §3.1/§6 收窄：smoke 为一次 highest prepare + full runtime，零 ty；check 为最小 prepare 序列 + declaration full runtime，零 ty；search 序列不变。Runner 只向 Search 注入 TyCheckCache；Smoke/Check 的 sidecar entries 与 Journal static membership 为空。Runtime outcome 接受 §5.3 的 conflict 分支。观察存储不是 ty-cache、不是 Journal、不服务 `diagnose`；准入命中不伪造本 Run ty/verifier ProcessObservation 或 Process Log，无本 Run verifier sidecar 时 `diagnose_available=false` |
| D012 §3、§6 | 定义 §3.1 `HarnessBaselineRequirement` 与无观察的 degenerate baseline；现行 `EnvironmentIdentity` / `proposal_id` 继续作为本 Run Proposal 与 Evaluation cache 身份。跨 invocation runtime subject 改由已复证 preparation 的实际安装/验证投影构造，明确排除 request-bound plan digest、Attempt 与 release cutoff |

**保持不变：** D001 命令形状、退出码、apply 整组准入、`--force` 范围、`C` 绑定快照；D003 两阶段搜索与 search 的静态 guidance；D005 PASS 只来自原命令阶段 `NormalExit(0)`；FailedCaseSet 仍只在同一次 Verification Run、当前下降坐标内；D007 Process Log / Output Cache；每个实际建立的 Attempt 仍 prepare、不同 Proposal 不共用可写环境；D014 Schema 1 字段与「报告不是 Evaluation cache」。

**本文件不覆盖：** C005 的增量 extra / 增量受管 apply、apply 本机历史与回滚；跨快照复用；
registry 候选观察；FailedCaseSet 持久化；R008 的跨运行 hints、single-flight、materialize、xdist；
C001 树搜索；C004 非单调 refinement；C006 影响面；自动清理/配额与手工 cache 命令；新命令或开关。

## 3. 产品周期

吸收后 D001 在 §5 命令表之前增加周期，并按 §3.1 收窄 smoke/check 的 Attempt 序列：

| 阶段 | 命令 | 权威 |
| --- | --- | --- |
| 接入 | `search` 写出报告，授权后 `apply` 写入声明下界 | 该次 `C` 上的 search 结果 |
| 稳态 | `check` 在**当前**快照上验证声明下界 | 当前 runtime subject 上新取得或按 §5 准入的 declaration terminal verifier 观察 |
| 重定界 | 作者在 check 失败后选择，或主动治理时，再 `search` / `apply` | 新的 search；CI 不因 check 失败隐式 search |

`check` 通过：下界在当前 `C` 上成立；不写报告、不 apply。命中只复用当前 runtime subject 的直接
观察，不把旧快照或不同安装投影提升为当前权威。`check` 失败：退出码与现行 D001/D008 相同，默认
就是失败。

`smoke` 仍用 DEVELOPMENT SourcePlan，只回答「当前声明允许的最高版本能否新鲜安装并测试」，不是稳态转运，也不是 search 的 runtime 观察来源。

### 3.1 smoke/check 最小序列

StaticEvaluator 不参与 smoke/check；两条命令都不运行 ty、不做 `S_hi` capture/compare，也不建立
Direct-PASS ledger。Static 不产生 compatibility disposition；prepare 成功后，PASS / REJECTED
只来自 full runtime Evaluation，prepare 失败仍走 D005 的现行失败语义。

Smoke 每个宿主 Cell 恰好：

```text
prepare(highest, original harness, DEVELOPMENT)
-> full evaluate
-> close
```

最高 preparation 仍必要，因为它就是 smoke 的验证对象；prepare 产生的 HarnessBaseline 只是该
PreparedEnvironment 的闭合事实，不触发额外 static 工作。

Check 先用已资格化 active external harness 计算：

```text
HarnessBaselineRequirement =
  REQUIRED     if 任一 active requirement 的 harness policy 为 ceiling_eligible
  DEGENERATE   otherwise
```

判定必须调用 D012 的唯一 harness policy owner，不得在 Check 里另写 specifier/source 解析；它是
prepare 前的纯决定，不是持久 schema。

`DEGENERATE` 包括没有 active external harness，以及全部 active requirement 都是固定 source 或精确
`==X` / `===X`。此时构造与当前 Cell 闭合的 HarnessBaseline：保存排序唯一的 active external
harness declaration IDs，`observations=()`；因为这些 requirement 既不删除下限也不追加 ceiling，
relaxed harness 与 original harness 语义相同。它仍是普通、可计算 digest 并进入 Attempt identity
的 HarnessBaseline，不用 `None`、空哨兵或兼容分支表达「缺失」。每个 Cell 只执行：

```text
degenerate HarnessBaseline
-> prepare(lowest-direct, baseline, SEARCH)
-> full evaluate
-> close
```

`REQUIRED` 时仍须从原始 harness 的最高环境取得 `U_B` 与 project/harness ownership：

```text
prepare(highest, original harness, SEARCH)
-> derive HarnessBaseline
-> close                         # 不运行 ty，不运行 verifier
prepare(lowest-direct, baseline, SEARCH)
-> full evaluate
-> close
```

第一版不在 lowest project plan 形成后再懒启动 highest，也不持久化 HarnessBaseline。只要存在任一
ceiling-eligible requirement 就保守走 `REQUIRED`；后续若要再省一次 prepare，须另证「当前
`G(P)` 已拥有全部相关 harness distribution」的安全时序。Search 的 highest `S_hi` capture、完整
baseline verifier 与后续两阶段搜索保持不变。

D008 的 Role literal 不新增：`baseline` 仍是 full runtime（Smoke 不 capture static，Search
capture）；`REQUIRED` 的首个 Attempt 仍用 `declaration-capture`，其 contract 改为
HarnessBaseline acquisition only；`DEGENERATE` 不产生该 Role/Attempt；`declaration` 是不再相对
`S_hi` 的 full runtime；`probe` 不变。

双语 README 与 D006 help 是该周期的用户投影，不能另写一套主路径。目标 help epilogue 固定为：

```text
Onboarding: pf smoke -> pf search -> pf explain -> pf apply. Steady state: pf check. Use pf minimize to search and apply in one command.
```

## 4. 观察记录与一套存储

观察存储只有一个判别 record family，不是把 ty 与 runtime 塞进同一种 payload。Run-owned
`TyCheckCache` 是静态热投影；`RuntimeEvaluator` 负责 runtime 持久准入，现行
`_ProposalRunner`-owned `EvaluationCache` 仍只做本次 search 的当前 Proposal 去重。进程结束后，
前两者可在 implementation 内按 §5 读同一存储；不维护平行的「短期 cache API」和「长期 cache
API」，composition root 不出现第二套产品 lookup。

记录使用 strict/frozen schema 与 canonical encoding，保存完整 subject 和 terminal observation，
不把 opaque digest 当作未经复算的 authority。换准入策略只改 policy，不强迫迁另一套存储。

持久 envelope 固定为 `schema = pf-observation-v1` 与判别 union：

```text
StaticObservationRecord(kind=ty, document=TyFactDocument)
RuntimeObservationRecord(
  kind=runtime,
  subject=RuntimeObservationSubject,
  subject_id,
  verifier=VerifierPass | VerifierRejected,
)
```

`subject_id` 必须从完整 subject 复算；static record 的 subject/fact/policy identity 由
`TyFactDocument` 自闭合。envelope 不保存 `recorded_at`、路径、run-id 或历史 Proposal，读取顺序也不
取得「最新记录优先」语义。每条 envelope 的
`record_id = sha256("pf:observation-record:v1\0" + canonical envelope)`；物理 locator 不取得
identity 权威，同一 subject 的不同合法 payload 因而能并存并触发 §5.3。

**可准入的种类（本文件仅此两种）：**

| 种类 | 持久 payload | 不可准入 |
| --- | --- | --- |
| 原始静态事实 | 完整 `TyFactDocument`，其 fact 为 `TyCheckFact` 或 `TyCheckUnavailable` | 某次 guidance 解释、比较结果、search/skip 账本 |
| terminal runtime 观察 | `RuntimeObservationSubject` + `VerifierPass` 或 `VerifierRejected` | 历史 `Evaluation` / Proposal 句柄、`IndeterminateEvaluation`、failed-set 阶段 `NormalExit(0)`、PrepareFailure |

**明确不作为可准入 payload：** `PreparedEnvironment` / venv、Process Log 正文、Journal 条目、
`package-floor.json`、FailedCaseSet、registry 候选快照、Role、run-id、attempt-id。

runtime 记录不保存历史 `Evaluation` 供调用方直接复用，因为其 Proposal 绑定历史 Attempt。准入后只把
保存的 terminal verifier fact 绑定到本 Run 当前 Proposal，形成新的
`PassEvaluation` / `VerifierRejectedEvaluation`；历史记录本身不改写。

存储可以记载「某逻辑向量曾物化过」，但新 invocation 不得打开或复用旧 venv。每次需要环境的
Attempt 仍走 `EnvironmentFactory.prepare`。命中只使 `TyOperations.observe` 与
`ConfiguredVerifier.run` 中对应的一个不再发生；runtime 命中本身不授权跳过 ty，静态命中本身也不
授权跳过 verifier。

Lookup 返回记录。**当前契约能否采用**由准入策略回答。本文件只接受一条策略：
`same-snapshot-direct-v1`（§5）。runtime subject 不同 → runtime miss；静态 subject / 观测策略
不满足 §5.2 → ty miss。不得把任一种 miss 写成当前 `C` 的 PASS。

单条记录损坏、截断、schema/identity 与 payload 不一致时只排除该记录，等同 miss，不形成
Indeterminate，不形成 PASS。`.pf` / 存储根的 symlink、目录替换或安全能力失败仍按 D007
fail closed，不能伪装成普通 cache miss。

## 5. 身份与 `same-snapshot-direct-v1`

现行 `proposal_id`（`environment_identity_digest`）含 `attempt_id`，不能当跨 invocation 的存储键。
`project_plan_digest` / `environment_plan_digest` 又包含 request digest 与每 Run `release_cutoff`：
直接拿它们作键既无法跨 invocation 命中，也无法让 `lowest-direct` 与等价 `exact-vector` 共享观察。
Run-local 句柄继续用现行 key；持久 runtime 身份改用下列实际安装/验证投影。

### 5.1 RuntimeObservationSubject

```text
rules = runtime-observation-subject-v1
source_snapshot_digest
Cell                         # 完整 Cell，含 active_declaration_ids
source_plan_identity
execution_policy_identity
InterpreterIdentity           # CPython 完整 patch 与 ABI
managed_vector
fixed_declaration_ids
project_resolution_projection
environment_resolution_projection  # required-nullable
installed_resolution_graph_id       # 与 D014 图身份同一口径
```

两个 resolution projection 都从已经通过 D012 复证的 `ResolutionPlanEvidence` 派生，只保存实际结果：
`kind`、按名称排序的 package `name/version/source/dependencies/artifact`，以及 environment 的
`direct_harness`；project-only 时 environment 明确为 null。`artifact` 复用 D004 的规范投影：
有可靠 selected hash 时为 `selected`，普通 registry/url 为完整 `available-set` digest，
path/workspace/git 为 `source-tree`。因此同名版本但 artifact 集合变化仍 miss。

不进入 subject：
`requested_resolution`、selected-candidate evidence、HarnessBaseline/request/context digest、
`release_cutoff`、project/environment plan digest、Role、run-id、attempt-id、proposal-id、
Journal id、candidate/guidance/search derivation policy。前者属于请求来源，后者不改变已经闭合的
动态 authority；实际 interpreter、source、artifact、安装图或 ExecutionPolicy 改变仍会改变 subject。

`RuntimeObservationSubject` 的 key 是以 `pf:runtime-observation-subject:v1\0` 为 domain、对完整
canonical subject 计算的 digest；reader 必须从保存的 subject 重算，不能只信 opaque key。

准入当且仅当当前 preparation 重新构造的完整 subject 与记录逐字段相等，且 payload 是合法
`VerifierPass` 或 `VerifierRejected`。命中后把 terminal verifier fact**绑定到当前 Proposal /
Attempt / Role**；历史记录不改写。本 Run 的 `EvaluationCache` 随后仍按现行
`(proposal_id, policy_identity)` 命中。

因此：check 的 declaration（lowest-direct）与 search 的 exact-vector probe 只有在上述**完整
runtime subject** 相同（不只是 version graph 相同）时才可共用一条观察。check 与 search 都是
SEARCH SourcePlan；smoke 的 DEVELOPMENT plan 与它们不相等，其 runtime 观察不得命中。

现行 D012 对普通 registry resolution 可能只能形成 `available-set`，而 exact-vector 会形成
`selected` artifact；这两种投影不冒充相等。因此本文件不保证典型 registry declaration 与
exact-vector 一定跨 Role 命中。若以后要扩大该命中率，必须先取得两次 installation 的同一实际
artifact/content identity；不能只比较 version graph，也不能删除 artifact 字段。

smoke/check 不产生 static observation。Search 仍须对 highest capture `S_hi` 并取得完整 runtime
Evaluation；verifier 只在存储已有其完整 runtime subject 的观察时跳过（例如先前 search 留下的
baseline），static capture 仍按 §5.2 独立准入。

### 5.2 TyCheckKey

持久 lookup bucket 沿用 D004：
`(StaticSubject.identity, TyObservationPolicy.cache_identity)`。`StaticSubject` 已绑定
`source_snapshot_digest`、Cell、interpreter ABI 与完整 resolution projection；reader 仍须逐字段验证
完整 `TyFactDocument`，并要求 subject 的 snapshot digest 等于当前 Run。

`cache_identity` 只用于定位 bucket；本文件第一版对 `TyCheckFact` 与 `TyCheckUnavailable` 都要求
完整 `TyObservationPolicy.identity` 相等，避免把不同 timeout 下的事实带入另一
GuidancePolicy。命中后在本 Run 重建 `CollectedStaticSubject` 句柄，不传递上一 Run 的句柄，也不
要求历史 search role / Proposal 相等：若两个 search Attempt 实际形成完全相同的 static subject，
原始 ty 事实可共享，但不共享任何 runtime disposition。

`TyCheckUnavailable` 可准入为「该 subject 的原始静态事实」，语义仍是 UNAVAILABLE / 无 hint，
不是 REJECTED。

### 5.3 冲突

存储按 record identity 不可变发布，同一 payload 的重复写入幂等；并发 writer 不得以
read-modify-replace 丢失记录。

同一 runtime subject 出现不同合法 verifier payload（包括两个不同 nonzero terminal）时，必须保留
全部冲突记录并走现行 `NONDETERMINISTIC` 路径，不得任取一条，也不得以后一条覆盖前一条使单调假设
或 floor 变得「看起来一致」。本 Run 新取得的 `PASS` / `REJECTED` 仅在验证完成后写入；不写入
Indeterminate。

`RuntimeEvaluator.evaluate` 的目标返回是 closed union：

```text
RuntimeEvaluationOutcome =
  RuntimeEvaluationRun
  | RuntimeObservationConflict(
      proposal,                 # 当前 Proposal
      subject_id,               # 当前完整 subject 复算值
      conflicting_record_ids,   # 至少两个，排序唯一
    )
```

`RuntimeObservationConflict` 是 process-local frozen fact，不进入 observation payload、Journal 或
Schema 1。

读侧发现冲突时不再运行 verifier 来仲裁，也不返回任一 Evaluation；本 Run 新观察与已有记录冲突时
同样返回 conflict，不采用任一 terminal。`FailurePolicy.record_observation_conflict` 验证当前
Attempt scope 与 `proposal.attempt_id`，用当前 plan digests 形成
`INDETERMINATE / NONDETERMINISTIC @ observation-admission` 的 structured authority。固定 detail
为 `code=runtime-observation-conflict`、`message=conflicting terminal observations for runtime
subject <subject_id>`；record IDs 不进入 Failure identity。Check/Smoke/Search 都消费该
FailureRecord；没有 Evaluation、runtime diagnostics 或 verifier Process Log association，
`diagnose_available=false`。这复用 D005 现有 cause/disposition 与 authority schema，不 bump
`failure-execution-v4`，也不改变 D014 wire。

同一 static lookup bucket 出现多个不同、均可准入的 `TyFactDocument` 时，不任取一条；持久 lookup
退化为 miss，本 Run 重新 collect。若 fresh collect 仍不可用则按 UNAVAILABLE / NO_HINT 继续
verifier。静态冲突本身不形成 compatibility disposition。

## 6. 本 Run 句柄与 Direct-PASS

产品调用方仍只看见：

```text
VerificationRunner 构造并关闭 TyCheckCache
CheckCellOperations.check(package, cell, snapshot, source_plan)       # 无 run_cache
SmokeCellOperations.verify(package, cell, snapshot, source_plan)     # 无 run_cache
CellSearchOperations.search(..., run_cache)

SmokeVersionVerifier              # highest prepare + runtime only
HighestVersionVerifier            # search baseline：highest + static + runtime
RuntimeEvaluator.evaluate → RuntimeEvaluationOutcome
_ProposalRunner 内部持有 EvaluationCache
StaticEvaluator.collect_prepared / capture_highest / record_runtime / ...
```

两种 highest operation 位于现有 `baseline.py` module，可共享 private prepare/evaluate helper，但公开
构造依赖和方法不使用 `purpose` boolean：SmokeVersionVerifier 不接收 StaticEvaluator/TyCheckCache，
HighestVersionVerifier 只由 SearchCoordinator 持有。Runner 为既有 Journal 提交仍可构造 cache，但只
向 Search operation 注入；Smoke/Check 持久化空 ty-cache snapshot 与空 static membership。

Composition root 只构造一个 invocation-scoped `RunLogStore`：直接借给 `RuntimeEvaluator` 做 runtime
observation read/write，并交给 `VerificationRunner`；Runner 用同一实例构造带 static observation
read/write 能力的 `TyCheckCache`。两者都不关闭所借 store，生命周期仍由 CLI context 独占。不得再
包装或注入第二个 ObservationStore/protocol。

不新增 `ObservationStore.lookup` 产品入口。静态 admission 在 `TyCheckCache` implementation 内；
runtime admission 在 `RuntimeEvaluator` implementation 内；`EvaluationCache` 不负责磁盘 I/O。

所有 full-runtime Attempt 的共同顺序为：

```text
EnvironmentFactory.prepare
-> RuntimeEvaluator lookup
   -> unique terminal hit: 当前 Evaluation
   -> conflicting hits: RuntimeObservationConflict
   -> miss: ConfiguredVerifier.run
      -> immutable terminal write-through + reconcile
      -> 当前 Evaluation 或 RuntimeObservationConflict
```

lookup 只在 prepare 成功并复证 plan/graph 后发生。`RuntimeEvaluator` 对本 Run 新跑的 PASS /
REJECTED 负责写 runtime record；并发/既有 terminal 冲突按 §5.3 返回，conflict 自身不作为第三种
observation 写入。只有 search 的 static 路径在 runtime 前执行：

```text
TyCheckCache: Run hit -> observation-store hit -> TyOperations.observe
-> optional compare/guidance
-> full runtime
-> static record_runtime（PASS only）
```

`TyCheckCache` 对 search 新观察的 `TyFactDocument` 负责写 static record。storage 命中不重复写相同
record，但仍进入下述本 Run 热投影。

本 Run 内部 provenance 使用 closed union，不用 nullable process：

```text
RunObservationProvenance =
  ProcessObservationProvenance(kind=process, process)
  | StoredObservationProvenance(kind=stored-observation, record_id)

StaticMembershipProvenance =
  ProcessRefProvenance(kind=process, process_ref)
  | StoredRecordProvenance(kind=stored-observation, record_id)
```

`RunTyFactRef` 与 Direct-PASS entry 各持有一个 `RunObservationProvenance`；投影
`StaticFactMembership` / `StaticPassMembership` 时改持有 `StaticMembershipProvenance`。process
分支必须闭合到本 Run `StaticProcessRecord`；stored 分支必须闭合到已通过 §5 准入、kind 与当前
fact/runtime 相符的 observation record，不创建 process/ref。该 provenance 只服务本 Run static
scope/audit，不进入 Journal、Schema 1 或公共报告。

准入的 `TyFactDocument` 在本 Run 建立新的 fact/consumer ref，但没有本 Run
`ProcessObservation`。每 Run `ty-cache.json` 保存本 Run 实际采用的事实，无论它是本 Run 新观察还是从
观察存储准入；rehydrate 后该 document 必须进入本 Run `TyCheckCache.documents()`，使 D008
`ty-cache -> Journal static_membership` 闭包与提交顺序不变。内部 audit 必须表达「stored
observation」来源，不得伪造 ty process/ref，也不得打开另一 run 的 sidecar。

准入的 `PassEvaluation` 进入 search 后，search-baseline / probe 仍在 `evaluate` 之后、prepared
close 之前调用 `record_runtime`。此时允许没有本 Run verifier ProcessObservation；ledger 记录当前
Proposal 为 Direct-PASS owner。Smoke/Check 不调用 `record_runtime`，其持久 runtime write-through
由 `RuntimeEvaluator` 完成。admitted `RuntimeEvaluationRun` 的 diagnostics 为空、failed-case
additions 为空；Direct-PASS ledger 与内部 audit 必须表达 stored provenance。不得伪造 Process Log、
verifier process 或 ty process 对象去满足现行「process 必须是本 Run 进程」检查。
`open_slice` 只依赖已绑定当前 Proposal 的 PASS terminal 与可用 consumer，不因该 ledger 行没有
历史 process 而拒绝。

准入的 `VerifierRejected` 仍从当前 Attempt 构造当前 `FailureRecord`。没有本 Run verifier
ProcessObservation 时不建立 Diagnosis Index association，`diagnose_available=false`。静态或 runtime
命中都不发布新的 ty/test stage，不复制历史 duration。

`CollectedStaticSubject`、Preparation registry、每 Run `ty-cache.json` 仍是 Run-local。Smoke/Check
的 static membership 为空；Search sidecar 继续服务 Journal `static_membership` 与保存审计，不是
跨 Run 的 lookup API。不得打开另一 `run-id` 的 `ty-cache.json` 当 oracle。

## 7. 模块与持久化

D002 §9 增加一条分界，而不是新 module：

| Module | 本文件下唯一负责 | 不负责 |
| --- | --- | --- |
| `RunLogStore` | repo-scoped `.pf/` 观察 record family 的 strict codec、安全读取与不可变原子发布；与现有 run-scoped Process Log / Journal / 每 Run ty-cache sidecar 分文件 | 准入 policy、disposition、apply 权威、报告、diagnose 读取面；不得把 observation 当作 Process Log / Output Cache 正文来源 |
| `TyCheckCache` | 本 Run 静态热投影、`same-snapshot-direct-v1` 静态准入与 observation write-through | 通用 cache API、动态 disposition、跨进程 venv、hints |
| `RuntimeEvaluator` | 当前 preparation 的 runtime subject 构造、准入、当前 Proposal Evaluation / `RuntimeObservationConflict` 与 observation write-through | FailureRecord/搜索边界、Journal/diagnose、可写环境复用 |
| `FailurePolicy` | 将 `RuntimeObservationConflict` 映射为 D005 structured FailureRecord | observation lookup/持久化、Evaluation 合成 |
| `EvaluationCache` | 当前 `_ProposalRunner` 的 Proposal 去重与现行 conflict | 持久化、跨 invocation lookup |

物理子路径由 Plan 选择，必须：相对 checkout root、位于 SourceSnapshot 排除且默认 gitignore 的
repo-scoped `.pf/` 下；与 `.pf/logs/<run-id>/` 及未来 apply history 使用不同保留前缀；不进入
`package-floor.json`；不与任一 Run 的 `ty-cache.json` 混成同一 lookup。绝对路径、checkout root
不进入 observation、Schema 1、report identity 或 Journal。

D002 §1 继续禁止通用 filesystem/repository、DI、daemon。本存储不是后台服务，也不建立新的产品
module；schema/codec 与 admission 是上述现有 module 的内部 seam。

发布语义必须满足：

- record 先完整编码到临时文件，再以不会覆盖既有不同 payload 的方式原子发布；相同 record 重试幂等；
- 多进程并发写同一 subject 不丢失任一不同合法 payload，reader 能按 §5.3 发现冲突；
- 单条记录 decode/identity 失败只排除该记录；存储根安全失败仍 fail closed；
- 普通 read/write I/O 失败只使本次 lookup miss 或 write-through 失败，并给出 warning；不得覆盖本 Run
  已直接取得的 Evaluation、不得合成 Indeterminate。若 I/O 失败同时表明 D007 的目录安全不变量被破坏，
  则仍 fail closed；
- 中断发生在原子发布前可以不留下记录；删除任意 observation 只会造成以后 miss。自动清理、配额与
  手工清理命令不在本 Design。

## 8. 不变量

1. Smoke/Check 不运行 ty、不做 static compare、不建立 Direct-PASS ledger；Search 的 static
   guidance、highest capture 与两阶段算法不变。
2. Check 仅在任一 active external harness requirement 为 ceiling-eligible 时取得 highest
   HarnessBaseline；DEGENERATE 路径不得启动 highest Attempt。
3. 当前快照上重新构造的 static/runtime subject 决定准入；同快照但实际 interpreter、source、
   artifact、安装图或执行 policy 不同仍 miss。
4. 完整 PASS 仍只来自原命令阶段 `NormalExit(0)` 的已准入记录或本 Run 新跑；static-only 与
   failed-set `NormalExit(0)` 不足以作为 final。
5. 最终精确向量在本 Run 仍须 prepare、复证 plan/graph 并构造完整 runtime subject；verifier 仅在
   terminal runtime 观察已准入时跳过。
6. 可写环境不跨 invocation 借用；不同 Proposal 不原地升降级。
7. runtime 跨 Cell、SourcePlan、ExecutionPolicy 或 snapshot 必 miss；static 只按完整
   `StaticSubject` 与 §5.2 观测策略准入，不借 runtime disposition。
8. 不伪造本 Run ProcessObservation 或 Process Log。准入的 REJECTED 可供 CoordinateSearch 定界；
   该 Failure 若无本 Run verifier sidecar，则 `diagnose_available` 为 false。
9. 准入的静态事实必须 rehydrate 到本 Run ty-cache 后才能进入 Journal membership；跨 Run lookup
   只读 observation store，不读任一 Run sidecar。
10. 公共报告、Journal、Process Log、观察存储、每 Run ty-cache 五者职责不合并；diagnose 不读
   observation store。
11. observation storage 的普通不可用只损失复用，不改变本 Run 已取得的产品 outcome；合法记录冲突
   除外，按 `NONDETERMINISTIC` fail closed。
12. 不因本文件改变 apply。合法 apply 仍开始新 snapshot；新快照上旧观察全部 miss。

## 9. 验收标准

吸收前每一项都要有公开 seam 证据，不得用内部 store API 代替产品 outcome。strict codec、identity、
原子发布与冲突矩阵可在进程内经 `RunLogStore` / evaluator 的公开 interface 验证；进程内 recording
adapter 计数只证明同一产品对象生命周期内跳过了 ty/verifier。凡声称跨 invocation 复用的产品场景，
必须按 `tests/README.md` 使用共享同一 workspace `.pf/` 的连续 `e2e` 场景，执行两次真实产品 CLI；
identity 字段组合可由进程内参数化覆盖，但不能替代至少一个真实跨进程命中与 miss。

| ID | 标准 |
| --- | --- |
| AC1 | D001 在 §5 命令表之前写出周期；§1/§4 复用范围改为 §5 runtime subject；§5 check=稳态、search=接入/重定界，并按 §3.1 删除 smoke/check static、条件化 check highest。命令形状、退出码与现行 §3 矩阵编号不变 |
| AC2 | 双语 README 常见流程与 D006 附录 A.1 epilogue 等于 §3 固定英文句；check 仍不搜索、不写报告 |
| AC3 | 同快照上两次 check 形成逐字段相同的 `RuntimeObservationSubject` 时，第二次不调用 configured verifier；两次 declaration outcome 相同，且都不调用 ty，第二次仍完成其 §3.1 最小 prepare/plan/graph 复证 |
| AC4 | 任意改变 source snapshot digest 后，第三次 check 必须重新调用 verifier；不得沿用 AC3 的 runtime 观察 |
| AC5 | 同快照上 check 通过后再 search：当 declaration 与 exact-vector probe 的完整 runtime subject 相同时不重跑 verifier；只让 version graph 相同而 source/artifact projection 不同的 fixture 必须 miss。highest 完整 verifier 仍跑，除非存储已有其完整 runtime subject |
| AC6 | smoke 不调用 ty；其 DEVELOPMENT runtime 观察不能被随后的 check/search 准入，但相同 subject 的后续 smoke 可以准入 |
| AC7 | runtime 跨 Cell、或 ExecutionPolicy / SourcePlan / interpreter / resolution projection 任一不等，不得准入；static 跨 `StaticSubject` 不得准入 |
| AC8 | verifier/operation Indeterminate 不写入可准入存储，下一 invocation 对同一逻辑向量必须再跑 verifier；§5.3 由已持久 terminal 冲突形成的 Indeterminate 例外，仍直接返回 conflict 且不重跑仲裁 |
| AC9 | 第二次 invocation 仍按 §3.1 调用必要 prepare；测试不得观察到跨 invocation 复用同一可写环境对象或 venv 路径 |
| AC10 | composition root / 产品测试不进口观察存储内部类型；`SmokeVersionVerifier` / Check operation 不接收 `StaticEvaluator` / `TyCheckCache`，Search 的 `HighestVersionVerifier` 与 probe 仍只转交 `TyCheckCache`，`EvaluationCache` 不读磁盘；RuntimeEvaluator、Runner 与 TyCheckCache 借用同一 RunLogStore，且不另建 observation service |
| AC11 | Search 准入 TyCheck 与 runtime PASS 后分别以 §6 `StoredObservationProvenance` 形成当前 Run consumer / Direct-PASS owner，均不要求本 Run process；对应 static membership 使用 stored-record 分支，不出现伪造 ProcessObservation、process ref 或 Process Log。准入 REJECTED 且无本 Run verifier sidecar 时 `diagnose_available` 为 false |
| AC12 | `package-floor.json`、Journal 字段、Schema 1、apply 授权规则与 D014 identity 字节不因本文件增加字段 |
| AC13 | FailedCaseSet、registry 候选、跨快照 hints 均未持久化为可准入观察 |
| AC14 | 同 runtime subject 的相同 record 重写幂等；并发或先后写入不同合法 verifier payload 不丢记录，lookup/写后 reconcile 返回 `RuntimeObservationConflict` 而非 Evaluation，经唯一 FailurePolicy seam 形成 `NONDETERMINISTIC @ observation-admission`；不同 nonzero terminal 也算冲突。多个不同 static payload 不被任取为 hint |
| AC15 | 单条坏 record 形成 miss；普通 observation read/write I/O 失败不改变本 Run 直接 outcome；`.pf` symlink / root replacement 按 D007 fail closed |
| AC16 | D012 的 `lowest-direct` 与 `exact-vector` 即使 request/plan digest 与 release cutoff 不同，只要完整 runtime subject 相同即可命中；任一实际安装/验证投影不同则 miss |
| AC17 | 首次得到 REJECTED 后，相同 runtime subject 的下一 invocation 不调用 verifier，仍以当前 Proposal / Attempt 形成 `VerifierRejectedEvaluation` 与 `VERIFIER_EXITED_NONZERO` Failure；无本 Run sidecar 时 `diagnose_available=false` |
| AC18 | Search 首次得到 `TyCheckUnavailable` 后，相同 StaticSubject 与完整 TyObservationPolicy 的下一 invocation 不调用 ty，静态语义仍为 UNAVAILABLE；观测 policy 或 ty tool version 改变时必须重新 observe |
| AC19 | 只有 static fact、failed-set `NormalExit(0)` 或历史 verifier/operation Indeterminate 时不得形成 final PASS，必须运行原命令 verifier；已持久 terminal conflict 只形成 §5.3 Indeterminate，不运行 verifier 仲裁 |
| AC20 | runtime 命中仍建立当前 Attempt / Proposal 并复证 plan/graph；Search 准入 Ty fact 进入本 Run `TyCheckCache.documents()` 与 sidecar 后 Journal membership 闭合，且不打开另一 run 的 `ty-cache.json` |
| AC21 | 准入 PASS 的 process-less Direct-PASS owner 在当前 consumer 可建立时仍可 `open_slice`；不得因缺历史 process 而降为无 PASS |
| AC22 | observation 读写不增加 Schema 1 / Journal / Process Log 字段；`diagnose` 不读 observation store，Process Log / Output Cache 不从 observation payload 合成 |
| AC23 | 合法 apply 开始新 snapshot 后，下一次 check 对旧 runtime observation miss；下一次 search 对旧 static/runtime observation 都 miss，并重新调用对应操作 |
| AC24 | Smoke 每个 Cell 只 prepare highest 一次、调用 full verifier 一次、ty 调用为零；runtime hit 时仍 prepare、verifier 为零；Journal `static_membership=()` 且 ty-cache sidecar entries 为空 |
| AC25 | Check 无 active external harness 与全部 active requirement fixed 两类 DEGENERATE 场景均不建立 `declaration-capture` / highest Attempt；只 prepare lowest-direct 一次、ty 为零，并用正确 active external harness declaration IDs + 空 observations 的 baseline 完成 full verifier；fixed harness 的 resolution requirements 与 original 语义相同，不删除下限、不添加 ceiling；Journal `static_membership=()` 且 ty-cache sidecar entries 为空 |
| AC26 | Check 存在 ceiling-eligible active requirement 时，先以 `declaration-capture` prepare highest 取得完整 HarnessBaseline，但该 Attempt 的 ty/verifier 调用均为零；随后 prepare lowest-direct 并只对 declaration 运行 full verifier；Journal `static_membership=()` 且 ty-cache sidecar entries 为空 |
| AC27 | Search 的 highest prepare、`S_hi` capture、full baseline verifier、候选冻结与两阶段 probe 顺序不因 §3.1 改变 |
| AC28 | `scripts/check_docs.py` 与 D002 §11 / `tests/README.md` 车道通过；本文件规则已写入全部目标 owner 后本 Design 归档 |

## 10. 吸收

稳定规则归并：

- D001：周期、smoke/check 最小序列、复用范围、§9 非目标
- D002：§3/§7 移除 Smoke/Check static 依赖、保留 Search cache、单一 RunLogStore 注入拓扑与
  admission owner，§9 持久化分界，§11 测试表面
- D003：prepare 前 Run-local direct lookup 与 prepare 后跨 invocation runtime admission 的分界；
  process-less Direct-PASS / `open_slice` 与跨 Run conflict seam；删除冲突的非目标
- D004：static guidance 只服务 Search、跨 Run 准入的 process/stored closed provenance、
  `record_runtime`、删除与本文件冲突的「无跨运行 Evaluation cache」句
- D005：保存 terminal 的 PASS / REJECTED 资格不变；`RuntimeObservationConflict` 与唯一
  FailurePolicy 映射为当前 Attempt 的 `NONDETERMINISTIC @ observation-admission`
- D006 §5–§6、附录 A.1：smoke/check stage 投影与 epilogue
- D008：smoke/check Role 与最小 Attempt 序列、command-specific operation protocol、runtime outcome、
  空 static membership/sidecar、每 Run ty-cache 与观察存储分家；admitted fact 的 Run provenance；
  `diagnose_available` 与禁止伪造 ProcessObservation / Process Log
- D012：`HarnessBaselineRequirement` / degenerate baseline、runtime subject 的 preparation/result
  投影与 request-bound identity 排除
- CONTEXT：吸收时增加「观察存储 / 观察记录」现行术语，并 `_Avoid_` 通用 cache、跨运行 environment cache；不把 C005 的「PF 地板」写入词汇（本文件未定义它）
- 双语 README：用户投影，不另立契约

C005 仍开放，跟踪增量 apply 与 apply 历史。C006 仍独立。R008 §7 对「未重建当前 subject 就跳过
runtime 权威的 Evaluation cache / 共享可写环境 / 通用 cache 服务」继续有效；本文件只允许 prepare
与复证完成后的身份闭合直接观察。

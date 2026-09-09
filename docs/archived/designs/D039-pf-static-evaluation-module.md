# D039 — PF 静态评价深模块

- **状态：** 已完成并归档；2026-09-10 通过 AC1–AC14 验收，稳定规则已由现行 owner 接管；实施与证据见 [P046](../plans/P046-pf-static-evaluation-module.md)
- **日期：** 2026-09-08
- **性质：** 已归档临时迁移 Design；不再承担现行规范
- **目标 owner：** [D002](../../designs/D002-pf-implementation.md)、[D004](../../designs/D004-pf-ty-enhancement.md)、[D008](../../designs/D008-pf-verification-run.md)
- **验收标准：** [§9](#9-验收标准)
- **实施计划：** [P046](../plans/P046-pf-static-evaluation-module.md)
- **来源：** [R011](../../reviews/R011-pf-architecture-review.md) §3–§6
- **关联：** [D003](../../designs/D003-pf-search-algorithm.md) 两阶段搜索与「静态无 compatibility disposition」不变；S4 必须改写 D003 §5 的 Direct-PASS / `record_runtime` / `open_static_slice` 责任，不是只改 StaticSlice 提供方指针。[D005](../../designs/D005-pf-failure-and-diagnose.md) 分类 interface 与 Failure Process Log 资格不变；[D012](../../designs/D012-pf-harness-relaxation.md) 保留 harness 变换；[D014](../../designs/D014-pf-report-schema.md) 公开报告无静态 intern 表；比较重放只在完整的 Run-local static audit closure 上进行，不能只凭 `TyFactDocument`。[D043](D043-pf-static-subject-v2.md) 已吸收的五表删除、`selection_reason` 与 v2 identity 字节不在本文件 AC10 范围内

本文定义 R011 的**目标** module interface：把静态采集、Run cache、准入、比较与纯 guidance 收成一个深模块，并把 FrozenSchema 从这些算法中拆出；同一吸收改写 D002 模块地图，并收回 `FailurePolicy` 可选注入。行为语义仍以现行 D003/D004/D005 为准；本文件只改规则住在哪里、调用方必须学习什么。吸收完成前，现行行为仍以 D002/D004/D008 为准，本文件不冒充已交付行为。

2026-09-10 按 D043 切齐并经 interface review 修订：v1 文件树叶子与 intern codec 已不存在；离线 admission 不再假设 ReportStore inflate，也不再把 `TyFactDocument` 误当完整比较/hint closure。目标由 AC1–AC14 验收。2026-09-10 评审后接受；接受本身不授权改生产代码，实施以 Plan 为准。inspect / path-in-root / cell key 去重是吸收 Plan 的内部卫生，不另立 AC。

2026-09-10 起草 Plan 前的复核修订：`compare_global` 不再向调用方索取 `GuidancePolicy`；`capture_highest` 的 UNAVAILABLE 不合并 Journal `collected` / `uncollected`；保存审计允许 PASS 先于 consumer；`schemas.static_preparation` 的 `static_projection` 进口列入 §4.1；AC1 只约束静态相关进口。

2026-09-10 二次评审后修订：§3.3 固定 Direct-PASS ledger 与后续 collect 的原子绑定；§3.2 写入 `record_runtime` / `capture_highest` / `compare_global` 的 fail-closed 错误契约（现行 RuntimeEvaluator 对 foreign cache 静默不登记，改为拒绝）；§4.1 固定 `StaticAuditDocument` 最小结构、Runner 可见方法、每 Cell 完成时 admission，以及 `ValueError` → `InfrastructureError` 的 writer 映射。

2026-09-10 三次评审后修订：Preparation registry 与 Direct-PASS ledger 分主键；`record_runtime` 先校验身份再按 outcome 记账；未绑定 PASS 的 `StaticAuditDocument` 必填 `scope_ref` / `run_identity` / `preparations[]` / `pass.preparation_ref`；`open_slice` 固定 context 来源；S4 改写 D003 §5 的 Direct-PASS 责任。

2026-09-10 收尾修订：AC9 时序与 §4.1 对齐（Cell 完成时 admission，Run 收尾 `stop` → `documents` → `close`）；`open_static_slice` 以 Direct-PASS ledger 为准，不得只凭 Search 内存中的 `PassEvaluation`。

## 1. 结论

当前公开 seam 写成 `StaticEvaluator.lookup/collect/compare`，实现却是十余个平行文件，且 `schemas/` 向上执行准入、二分与 harness 变换。Evaluator 通不过删除测试：删掉它之后复杂度仍留在 cache、request factory、schema 比较函数和 SearchCoordinator 手写的 slice 里。

目标是**一个**静态 module，产品 interface 固定为：

```text
StaticEvaluator.collect_prepared / capture_highest / compare_global
StaticEvaluator.record_runtime / open_slice
CollectedStaticSubject               # module 构造的不透明 Run handle
TyCheckCache                          # Run 拥有，VerificationRunner 构造并显式传入
StaticGuidanceEvaluator.open_static_slice
  -> 静态 module 提供的 StaticSlice adapter
locate_static_hint(slice, versions)   # CoordinateSearch 的纯 hint 入口
```

`record_runtime` 是直接 runtime PASS 进入静态 anchor ledger 的唯一通道；`RuntimeEvaluator` 不再读
`PreparedEnvironment.static_consumer` 或写 `TyCheckCache`。路径解析、configuration materialize、
inspect、request 装配与保存审计的 admission 留在 implementation。v1 文件树 ignore / relocation /
external freeze / process-env binding 已随 D043 删除，不是本 module 必留叶子。产品代码与产品测试走同一
公开表面。`TyCheckCache` 不收成 service locator。

R011 要求 §3 与 §4 同一份 Design：先把算法从 schemas 拿回静态 module，再收调用方 import。只搬家名或只建 `pf/static/` 子目录而不减少调用方知识，视为未完成。D002 过时文件名与 `FailurePolicy` 假想 seam 随本次吸收一起改，不另开 Design、不留给下一次文档修正。

## 2. 范围与非目标

**替换（接受并吸收后）：**

| 目标 owner | 替换内容 |
| --- | --- |
| D002 §3 | 包布局改成 **module 地图**，与代码同一变更吸收。静态是一个 module；D002 不再把不存在的 `src/pf/static.py` 画成独立 module（`schemas/static.py` 记录模块保留）；补上 `cancellation.py`；不把每个 `static_*.py` / `ty_fact.py` / `ty_options.py` 写成独立 owner |
| D002 §2、§4 | Schema 只保存记录与结构/identity 闭合；禁止 schemas 进口或执行 admit、hint、harness、ty-args。§4 表补上 policy / journal / static_* 记录 |
| D002 §7 | 静态公开 interface 收成上列名字；补上 module 所需的 Ty / slice collector adapter interface；`StaticRequestFactory` 不再出现在 composition root。`RuntimeEvaluator` 不再依赖 Run cache。Check / Highest / Search / `_ProposalRunner` 不再接受可选 `failures=` |
| D002 §11 | 静态产品测试只经 Evaluator / Run cache / guidance seam 观察 outcome。分类从公开 Check/Highest/Search outcome 观察，不注入假 `FailurePolicy` |
| D004 §6–§7、§10 | 规范投影与采集请求是 Evaluator implementation；比较准入、减法、hint 定位的唯一实现在静态 module，不在 FrozenSchema 里 |
| D008 §4、§7–§8 | Run cache 在 capture 前构造、显式传递、收尾 stop/close；static audit snapshot 在写 Journal membership 前 admission。静态 fact 不建立日志 association；所有 Failure 的 Process Log 关联仍按 D005/D008 |
| D003 §5 | `open_static_slice` 只转交 collector 与该坐标 `CandidateSnapshot`，slice 由 `StaticEvaluator.open_slice` 构造；`record_pass` 改为调用方在 evaluate 后、close 前调用 `record_runtime`；Direct-PASS 按 Proposal 至多一个 runtime owner，后续 collect 只绑定 consumer。两阶段搜索与无静态 disposition 不改 |

**保持不变：** D001 命令与退出码；D003 两阶段搜索与「静态无 compatibility disposition」（D003 §5 的 `record_pass`、手写 slice 与 Direct-PASS 责任由 S4 改写，见下表与 §3.2/`open_slice`）；D004 的 `S_hi`/`S_slice`、多重集减法、Run 内原始 TyCheck 缓存、lookup 只读 / collect 占 permit；D005 的 cause/disposition/authority 与 `classify` / `record_prepare` / `record_evaluation`；D008 既有 Run/Journal 顺序及所有 Failure 的 Process Log sidecar；D012 original/relaxed 变换本身。D014 Schema 1 字段集合与 identity 字节在 D043 已吸收的五表删除、`selection_reason` 与 v2 subject/policy identity 之外保持不变。D038 已归档，不把静态拒绝权限改回去。

**本文件不覆盖：** R006/R008/R010 开放项；按行数拆 `report.py` / `schemas/evaluation.py`；把 Check/Smoke/Search 收成评价 facade。

## 3. 目标 module interface

静态是一个 module：小 interface，完整 implementation。物理文件可以是 `pf.static` 包或少数私有文件；Plan 选定布局。D002 §3 只画 module，不把每个内部 `.py` 写成独立规则所有权。

### 3.1 调用方必须学习的名字

| 名字 | 谁用 | 义务 |
| --- | --- | --- |
| `StaticEvaluator` | composition root、Check、Highest、Search、产品测试 | collect/capture、GLOBAL 比较、runtime PASS 登记、SLICE 构造；内部装配 request |
| `CollectedStaticSubject` | Check/Highest/Search 在同一 Run 内只作不透明值传递 | 已形成 `TyCheckFact | TyCheckUnavailable` raw document 的 handle；不能构造、拆解、跨 Run 使用 |
| `TyCheckCache` | `VerificationRunner` 构造并关闭；Check/Highest/Search 只接收并转交 | 一个 Verification Run 的原始事实与静态审计状态；不是 service locator |
| `StaticGuidanceEvaluator` / `StaticSlice` | `CoordinateSearch`；产品 SearchCoordinator 实现前者 | `open_static_slice` 至多每坐标一次；返回的 slice **由 `StaticEvaluator.open_slice` 构造** |
| `locate_static_hint` / `StaticPoint` / `StaticHint` / `StaticSearchResult` | `CoordinateSearch` | 纯 hint 算法；schema 不得重放 |
| producer/log 关联 | 无独立静态 association interface | 静态 fact 不关联 Process Log；Failure 的 verifier、resolve、install、inspect 等 sidecar 全部仍按 D005/D008 关联 |

产品路径不再进口：`StaticRequestFactory`、`static_paths` / `static_configuration` / `static_subject` / `static_admission` / `ty_fact` / `ty_options` / `adapters.static_inputs`。`RunStaticPassRef` / `RunStaticConsumerRef` 不用于 SearchCoordinator 手写 slice。`report.py` / `runlog.py` 不进口 schema 内的重放实现。

`lookup`、任意 context 的 `compare`、cache consumer/pass/ref、保存审计 admission 都是 module-private
interface；不为产品测试增加公开出口。D004 的 lookup 只读与比较准入语义由重复 collect、
`compare_global`、真实 `StaticSlice` 与 module 内部测试共同证明。

### 3.2 产品 interface

生产构造隐藏 request factory。Ty 仍是真实 adapter seam：

```text
StaticEvaluator(ty, *, processes, events=None, permits=None)
```

`processes` 是 `ProcessRunner`，不是 `VerificationRunner`。生产 `TyAdapter` 与静态 request/inspect/version
装配必须绑定这个**同一实例**；composition test 固定该约束。`cli.py` 不再构造
`StaticRequestFactory`。删除 `StaticRequestOperations` 产品 Protocol，不为 request 装配保留
测试专用替身 seam。module 所需的 adapter-facing interface 见 §3.4。

公开方法：

```text
collect_prepared(prepared, *, package, run_cache)
    -> CollectedStaticSubject | StaticContentUnavailable
    先按 §3.3 把 exact prepared 对象及其 portable `StaticPreparationEvidence` 登记到该 Run 的
    Preparation registry，再按 v2 subject 与配置物化装配 request、revalidate、只让 owner 占 ty
    permit、原子登记 consumer。同一 Proposal 已有另一 registered prepared 时仍允许登记本对象。
    CollectedStaticSubject 绑定 TyCheckFact 或 TyCheckUnavailable raw document、该次 preparation
    与 Run；StaticContentUnavailable 表示未形成 raw document。调用方只转交，不读 prepared side channel。
    关闭/停止的 cache、或把已在另一 cache 注册的 prepared 传入：`ValueError`。装配或 ty 未形成
    raw document 仍返回 `StaticContentUnavailable`，但 prepared 必须已经注册。
    若 Direct-PASS ledger 该 Proposal 已有 runtime owner 且 `consumer is None`，本次形成的
    consumer 只绑定到该行，不得替换 owner 的 prepared / process / evidence / preparation_ref。

capture_highest(prepared, *, package, run_cache)
    -> CollectedStaticSubject | StaticContentUnavailable
    只接受 `requested_resolution == "highest"` 的 preparation，否则 `ValueError`。
    同样先登记 prepared。三种结果都表示 D004 的 S_hi 状态，但 Journal 形态不得合并：
    TyCheckFact → AVAILABLE S_hi，Journal `highest.kind=collected`；
    TyCheckUnavailable handle → UNAVAILABLE S_hi（无 diagnostic baseline digest），
    仍是已采集 raw document，Journal `kind=collected`；
    StaticContentUnavailable → UNAVAILABLE S_hi，Journal `kind=uncollected`。
    Check/Highest 的 highest 路径只走这一方法。调用方不读 prepared side channel，
    删除 assert prepared.static_consumer is not None。
    该 Cell 的 S_hi 已固定为另一基线时 `ValueError`。

record_runtime(prepared, runtime, *, run_cache) -> None
    Check / Highest / Search 在每次 RuntimeEvaluator.evaluate 返回后、prepared close 前调用。
    校验顺序固定；不得把 outcome 门闸放到身份校验之前（现行 RuntimeEvaluator 对 foreign cache
    静默跳过，本契约改为拒绝）：
    1. 身份校验，失败一律 `ValueError`：该 cache 已关闭/停止；prepared 未向该 cache 注册；
       prepared 注册在另一 cache；`runtime.evaluation.proposal != prepared.proposal`。
    2. outcome 门闸：非直接 `PassEvaluation`，或无 diagnostics process → 返回；不写 ledger、
       不抛错、不产生静态 disposition。
    3. PASS 记账校验，失败一律 `ValueError`：diagnostics process 与任一 ty process 或另一
       PASS process 是同一对象；该 Proposal 已有 runtime owner 且 prepared 或 process 与 owner
       不同；已绑定不同 consumer。
    4. 写入 Direct-PASS ledger。同一 prepared、同一 diagnostics process、同一 Proposal 的
       重复调用幂等（不写第二行）。

compare_global(collected, *, run_cache)
    -> 对已登记 S_hi 做 GLOBAL 比较
    collected 必须是同一次 `collect_prepared` 返回、且属于该 cache / 该 Run 的 handle。
    module 从该 handle 的观测策略构造 GuidancePolicy；调用方不拆解 handle、不传入 policy。
    传入 `StaticContentUnavailable`、`capture_highest` 返回的 handle（其 Proposal 已是该 Cell
    的 S_hi）、跨 Run / 错 cache handle、未登记 handle：一律 `ValueError`。

open_slice(
    *, run_cache, upper_proposal, dependency, versions, candidates, collector
) -> StaticSlice | None
    要求 upper_proposal 已经由 record_runtime 登记为该 cache 的直接 PASS，否则 `ValueError`。
    candidates 是该坐标的 CandidateSnapshot，collector 满足 §3.4。
    SliceComparisonContext 来源固定，调用方不得另传一份：
    `fixed_other_coordinates` 等于 `upper_proposal.managed_vector` 去掉 `name == dependency`
    的 `VersionPin`，保持其余顺序；
    `anchor_pass` 等于 Direct-PASS ledger 该行的 `evidence`；
    `candidates.dependency` 必须等于 `dependency`；`dependency` 必须是
    `upper_proposal.managed_vector` 中的一个 name；
    `window` 必须等于 `tuple(candidates.select(version) for version in versions)`。
    任一不一致 `ValueError`。
    若该行尚无 consumer，module 经 collector 重建**另一个** prepared 并 `collect_prepared`，
    只绑定 consumer，不得替换 runtime owner。仍无合格 consumer 时由 module 记 omission
    并返回 None。无 PASS 不是 omission。
```

内部 lookup / collect 的现行时序与 negative cache 规则不改。`collect(prepared, request)` 与任意
context compare 若保留，只作 module 内部方法，不再作为 composition、Check/Search 或产品测试入口。
去掉 `StaticEvaluator` 后，request 装配、PASS/consumer 关联、baseline、比较与 slice 审计必须重新
散回调用方，因而通过删除测试。

### 3.3 Run cache

`VerificationRunner` 在 baseline capture 前构造 `TyCheckCache`，经 `check/verify/search(..., run_cache=)` 传入。同一 `StaticEvaluator` 实例可服务多个 Run，但不得隐式共享 cache。

Cache 只拥有 Run 局部的原子状态与生命周期：原始 TyCheck/Unavailable、preparation consumer、
独立 direct-PASS ledger、最高基线、比较审计、静态 search/omission/skip/oracle 账本，以及 Cell
分区 snapshot。`VerificationRunner` 只调用构造，以及按 §4.1 时序的 `admitted_membership`
（Cell 完成 / `finalize`）与收尾 `stop` → `documents` → `close`。这不是把四者并列成一条链。
Check/Highest/Search 只传递 cache。consumer/pass/baseline/compare/账本写入均为
`StaticEvaluator` / `StaticSlice` 的 module-private 操作，产品调用方不得直接调用 cache 领域方法。
Runner 不进口 `StaticAuditDocument`。

SearchCoordinator **停止**进口 cache ref 去实现 `_RunnerStaticSlice`。它只提供 collector，然后向
Evaluator 要 slice。上端直接 PASS 以 Direct-PASS ledger 为准：

```text
SearchCoordinator.open_static_slice(vector, dependency, versions)
    该向量的 full evaluation 必须已经 record_runtime
    仅有 Search 内存里的 PassEvaluation（如 _full_runs）不够
    return static.open_slice(          # 只认 ledger；无行则 ValueError，不是 omission
        run_cache=run_cache,
        upper_proposal=direct_pass.proposal,
        dependency=dependency,
        versions=versions,
        candidates=candidates,   # 该坐标冻结的 CandidateSnapshot；Search 显式传入
        collector=search_static_collector,
    )
```

`CandidateSnapshot` 仍由 Search 冻结并拥有。静态 module 用传入的 snapshot 装配 `SliceComparisonContext.window`（`snapshot.select(version)`）和 `StaticSearchAudit.candidates`。window / audit 字段装配不留在 SearchCoordinator。缺这条通道则 slice 无法在不进口 cache ref 的前提下复现现行 hint 语义。

上端在 full evaluation 时即使没有静态 consumer，`record_runtime` 也先保存直接 PASS/process；以后
`open_slice` 可经 collector 重建**另一个** prepared 并 collect，再由 module 把 consumer 绑定到
原 runtime owner。`inspect` 的 prepare 与环境保留/释放仍由 Search/`_ProposalRunner` 的 collector
拥有；比较、`StaticPoint`、omission、search audit 与 keep-version 决策由静态 module 的
`StaticSlice` 完成。`CoordinateSearch` 继续只依赖 `StaticGuidanceEvaluator` 与 `locate_static_hint`，
不进口 cache。

#### Preparation registry

运行期按 **exact prepared 对象身份** 登记，主键不是 Proposal。同一 Proposal 允许有多个
registered prepared（later collect / `open_slice` collector 会再建一个）：

```text
PreparationEntry
  prepared          exact prepared 对象（主键）
  preparation       由该 prepared 装配的 StaticPreparationEvidence
                    （与现行 request 装配同一 portable 记录）
```

`collect_prepared` / `capture_highest` 在装配 request 之前写入。关闭/停止的 cache，或把已在
另一 cache 登记的 prepared 传入：`ValueError`。装配失败仍保持本条登记。不得把「同一 Proposal
已有另一 prepared」当作注册错误。

#### Direct-PASS ledger

运行期不沿用「PASS 必须持有 consumer」的 `RunStaticPassRef`。Cache 拥有 module-private
**Direct-PASS ledger**，按该 Run 内精确 `Proposal` 至多一行 **runtime owner**：

```text
DirectPassEntry
  proposal          该 Run 的精确 Proposal（主键）
  evidence          SliceAnchorPass
  process           verifier diagnostics ProcessObservation（不可与 ty/另一 PASS 复用）
  prepared          runtime owner：首次成功 record_runtime 的 exact prepared
  preparation_ref   该 owner 在 Preparation registry 中的 portable preparation
  consumer          RunStaticConsumerRef | None
```

原子绑定（同一 cache lock）：

- `record_runtime` 按 §3.2 顺序写入一行。该 Proposal 已有 consumer 则该行立即带 consumer；
  否则 `consumer=None`。`prepared` / `process` / `evidence` / `preparation_ref` 固定为 owner。
- 之后另一 prepared 的 `collect_prepared` / `capture_highest` / `open_slice` collector 对同一
  Proposal 形成 consumer 时，只把未绑定行的 `consumer` 从 `None` 写成该 consumer，**不得**
  替换 runtime owner。
- 已绑定同一 consumer：无操作。已绑定不同 consumer：`ValueError`。
- 再次 `record_runtime`：同一 prepared + 同一 process + 同一 Proposal → 幂等；不同 prepared
  或不同 process → `ValueError`。
- 跨 Run 的 prepared / handle / process 不得写入或绑定。

Admission / snapshot 投影见 §4.1：`StaticPassMembership.consumer_ref` 为 `str | None`，
`preparation_ref` 必填。这是内部审计记录变更，不是 Schema 1。现行 snapshot 只输出已有
consumer 的 PASS；目标必须输出未绑定 PASS 及其 verifier process，并带上足以证明「属于本
Run/Cell 的原始 registered prepared」的 portable preparation。

### 3.4 Module 所需的 adapter interface

Ty adapter seam 固定为：

```text
TyOperations.observe(StaticTyRequest, *, cancellation)
    -> TyCheck | ToolFailure | StaticContentUnavailable
```

`StaticTyRequest` 是静态 module 提供给 `TyAdapter` / recording adapter 的 adapter-facing interface，
不是产品编排 interface；其 request/progress/cancellation/error 形状必须由 D002/D004 与测试保护。
`adapters/ty.py` 若留在 `pf.adapters`，就允许进口这个名字；不能再把跨 module 的 request 称为纯内部形状。

Search 提供一个真实的内部 adapter：

```text
StaticSliceCollector.inspect(version)
    -> CollectedStaticSubject | StaticProbeUnavailableEvidence
StaticSliceCollector.finish(keep_versions) -> None
```

`inspect` 拥有 prepare、事件与环境保留；返回的成功 handle 必须来自同一 Evaluator/cache。
`finish` 由 `StaticSlice.finish` 恰好调用一次，释放不在 keep 集合中的物化环境。生产
`_ProposalRunner` 与 CoordinateSearch/静态 module tests 是两套 adapter，因此这是真实 seam，不是
parameter bundle 或 manager。

`StaticSlice` 的最小 interface 固定为：

```text
anchor -> StaticPoint
known_points -> tuple[StaticPoint, ...]
inspect(version) -> StaticPoint
finish(StaticSearchResult) -> static_search_ref
```

`locate_static_hint` 只经这四项工作，并对每个打开的 slice 调用一次 `finish`。

### 3.5 内部 implementation

下列行为留在 module 内，可以有内部 seam 和内部测试：

- v2 `StaticSubject` 投影与 `StaticTyRequest` 装配
- 仍被 request 装配使用的搜索路径、ty 配置物化、inspect
- cache lookup/consumer/pass refs、`admit_*`、diagnostic subtraction 与任意 context compare
- `StaticAuditDocument` 的保存证据 admission（§4.1）

inspect 名称/版本核对、path-in-root、cell canonical key 若有重复实现，吸收 Plan 可收进 module 内部，不另立 AC。

`PreparedEnvironment` 可以继续持有不透明的 static materialization 句柄，供 StaticEvaluator 在
`static_use` 租约内使用；不再保存供 RuntimeEvaluator 或编排器读取的 static consumer side channel。

### 3.6 evaluation.py

`RuntimeEvaluator`、`EvaluationCache`、`StagePermitPools` 留在评价/动态侧。`StaticEvaluator` 不再作为
`evaluation.py` 的平行出口。`RuntimeEvaluator.evaluate` 删除 `run_cache` 参数，不进口静态 cache、
handle 或 request；它只返回 `RuntimeEvaluationRun`。Check/Highest/Search 随后调用
`StaticEvaluator.record_runtime`。composition root 仍把各自同一个 Environment/Static/Runtime 实例
共享给 Check、Highest、Search，不引入 facade 或 service registry。

## 4. Schema 底层

FrozenSchema 只保存不可变记录与结构/identity 闭合。比较减法、hint 定位、harness 变换、ty argv 语义不是结构不变量。

### 4.1 算法迁回静态 module

| 现行位置 | 目标 |
| --- | --- |
| `schemas.static_comparison` 调用 `admit_static_consumer_context` / `derive_static_comparison` | 唯一实现在静态 module；`compare_global`、`StaticSlice` 与 `_admit_saved_static_audit` 调用同一内部函数。记录只保存已承认的 context/result/identity |
| `schemas.static_search` 重放 `locate_static_hint`（`StaticSearchAudit.validate_in_scope`） | 审计记录的语义闭合由静态 module 的 `_admit_saved_static_audit` 执行。Schema 不进口 `StaticPoint` |
| `schemas.static_scope`：`StaticScopeEvidence.validate_closure` 对每条 comparison 调用 `self.compare` 复算 identity/result，并对 searches 调用 `validate_in_scope` | `StaticAuditDocument` 构造只做 ref/membership 结构闭合。语义重放改由下列 module-private admission 执行 |
| `schemas.static_preparation` 调用 `original_harness` / `relax_harness` / `active_harness_requirements` | 构造该记录的 prepare/D012 路径负责变换；schema 核对已保存字段的结构关系与 identity 字符串，不再生 harness |
| `schemas.static_preparation` 进口 `pf.static_projection`（`resolution_projection` / `subject_interpreter`） | 投影是 D004 算法，离开 schema。记录只核对已保存字段的结构关系与 identity |
| `schemas.policy` 调用 `validate_ty_args` | `ConfigLoader` 与静态 request 装配在写入前资格化；policy 记录只保存已资格化的 args 投影 |

保存审计的完整输入固定为 module-private `StaticAuditDocument`。它**投影**现行
`StaticScopeEvidence` 各表并补上 Run/Cell 身份与独立 preparation 表，不做 derive/hint 重放，
并允许未绑定 PASS。`SliceAnchorPass` 仍只保存 `proposal_id` / `execution_policy_identity` /
`verifier`；admission **不得**只凭这三项承认未绑定 PASS。

```text
StaticAuditDocument
  scope_ref                # uuid5(UUID(run_identity), cell.model_dump_json()).hex
  run_identity             # 该 TyCheckCache 的 Run identity
  cell
  preparations[]           StaticPreparationMembership   # ref + StaticPreparationEvidence
  processes[]              StaticProcessRecord           # ty + unbound/bound verifier
  facts[]                  StaticFactMembership          # 全部可达 TyFactDocument
  consumers[]              StaticConsumerMembership      # preparation_ref + fact_ref
  passes[]                 StaticPassMembership          # preparation_ref 必填；consumer_ref: str | None
  comparisons[]            StaticComparisonMembership
  highest_reference_ref | highest_uncollected
  searches[] / omissions[] / skips[] / selections[]
```

`searches[].candidates` 继续携带该坐标 `CandidateSnapshot`。构造只做 ref/membership 结构闭合。
`TyFactDocument` 单独只证明原始 ty fact，**不足以**重放比较或 hint。Plan 不得把该类型改名。
若仍保留 `StaticScopeEvidence` 作为结构记录，必须同步 `scope_ref`、`preparations[]`、
`pass.preparation_ref` 与可空 `consumer_ref`；不得再让未绑定 PASS 只靠 `SliceAnchorPass` 闭合。

`StaticPreparationMembership` / `StaticConsumerMembership.preparation_ref` /
`StaticPassMembership.preparation_ref` 是内部审计字段，不是 Schema 1。later collect 形成的
consumer 可以引用**不同于** PASS runtime owner 的 preparation；二者的 `proposal.proposal_id`
必须相同。PASS 的 `preparation_ref` 永远指向 runtime owner 登记时的 portable preparation。

```text
_admit_saved_static_audit(document: StaticAuditDocument) -> StaticAuditDocument
```

该函数名、输入、返回与失败模式由本 Design 固定：任何 dangling/cross-Run ref、raw fact 缺失、
ty producer process 缺失/错绑/复用、slice/comparison 声称的 PASS/consumer/context 绑定不存在、
`scope_ref` 与 `run_identity`+`cell` 派生不一致、PASS 缺少 `preparation_ref` 或该 preparation
不属于本 cell / 与 `evidence.proposal_id` 及 `execution_policy_identity` 不符、consumer 的
preparation 与 PASS 不是同一 Proposal、重算 comparison/result/identity 不一致、或 hint 查询
序列/端点不一致均抛 `ValueError`；成功返回同一 immutable document。`consumer_ref is None` 的
PASS 合法，但必须仍能经 `scope_ref` / `run_identity` / `preparation_ref` 证明属于当前 Run/Cell
的原始 registered prepared。声称绑定却对不上 consumer 的 PASS 非法。

Runner 可见方法（固定名字）：

```text
TyCheckCache.admitted_membership(cell) -> JournalStaticMembership | None
TyCheckCache.documents() -> tuple[TyFactDocument, ...]
TyCheckCache.stop() / close()
```

`admitted_membership` 在内部：从 ledger 投影 `StaticAuditDocument` → `_admit_saved_static_audit`
→ 只取 highest 投影 `JournalStaticMembership`。`snapshot()` 不再是 Runner 入口。

时序分两段，不得读成 `stop` / `documents` / `admitted_membership` / `close` 的线性顺序：

- **Cell 完成时**调用一次 `admitted_membership`（现行 `_capture_static_scope` 位置）；`finalize`
  对尚未 capture 的 Cell 再调用，已 capture 且 ledger 未变则同一 membership。
- **Run 收尾**固定为 `stop` → `documents` → `close`。不在 `stop` 之后补跑 admission。
  `logs=None` 仍不 capture。

writer 路径上 `_admit_saved_static_audit` 的 `ValueError` 必须变成 D008 的 persist 失败：
Runner 记 `InfrastructureError`，本次不写 Journal、不更新 latest，completion 的
`diagnose_available=false`，`finalize` 上抛。不得写成 reader 的 `invalid-static-evidence`
（那只拒绝已落盘的旧 intern / 非法 membership）。报告、普通 Journal 与 ty-cache reader
均不调用 `_admit_saved_static_audit`。

公开报告不含五张静态 intern 表，ReportStore 不 inflate audit。Journal 只保存
`static_membership`，普通 decode 不打开 ty-cache；ty-cache 只保存 `TyFactDocument`，普通 decode
不声称恢复 comparison/hint closure。schemas 不再保留 intern codec 类型或
`intern_static_scopes`；旧 intern 字段名在 Journal / 报告 reader 上按字段拒绝。报告、Journal 与
ty-cache reader 均不调用 `_admit_saved_static_audit`。

**进入 FrozenSchema `model_validator` 的 derive/hint 重放必须离开**。`StaticComparisonDocument`
不再于 `model_validator` 里执行 subtraction。

这样 schemas 不再依赖 domain 算法，也不再出现 `static_search` → `static_guidance` → `static_comparison` 的往返，也不再经 `static_scope` 间接重放。

### 4.2 Schema 仍可做的 identity 闭合

记录若必须从已保存 preimage 复算 digest，函数必须住在 **schemas 可依赖的纯层**（与 `canonical_identity_json` 同层，或随记录类型下放到 `schemas/`）。`environment_identity_digest` / `resolution_graph_id` / `resolution_request_digest` 以及 `ResolutionPlanEvidence` 这类 FrozenSchema 记录，若继续被静态 records 引用，其**定义**下放到该层；`pf.resolution` 改为使用它们，而不是被 `schemas/` 进口。

下放后 digest **字节不变**。禁止为搬家复制第二套算法。迁移前固定
`environment_identity_digest`、`resolution_graph_id`、`resolution_request_digest` 各自至少一组完整
preimage → 64-hex golden vector；迁移后同一向量逐字节相等。round-trip 或 JSON Schema
`--check` 不能替代这些向量。

本 Design 的 schema import **禁入**（AC4 扫描这些，命中即未完成）：目标静态 module 根
`pf.static`（若 Plan 选 package 布局则含全部子模块）、现行 `pf.static_*`、`pf.harness`、
`pf.ty_options`、为执行搜索/准入/harness/ty-args/digest 而进口 `pf.resolution`。允许：
`pf.schemas.*`、标准库、`packaging`、Pydantic，以及下表逐项例外。

下列**既有例外不在本次范围**，AC4 不得把它们当失败：

| 位置 | 进口 | 说明 |
| --- | --- | --- |
| `schemas/config.py` | `pf.errors.ConfigurationError` | 配置资格错误类型；非静态算法 |
| `schemas/config.py` | `pf.search_space`（`parse` / defaults） | 搜索 DSL；非 D004 |
| `schemas/project.py` | `pf.search_space.SpaceSelection` | 候选空间值类型；非 D004 |

禁止只豁免 `schemas.config` 而漏掉 `schemas.project` 的 `search_space`。本 Design 不把这些例外改成「schemas 只能进口 schemas」。

### 4.3 顺序约束

Plan 必须先完成 §4（算法离开 schemas——含 `static_scope` / report / journal 读入链，identity 纯函数就位），再收 §3 的产品 import。禁止把「换了路径的同一函数仍被 schema validator 调用」当作切片完成。

## 5. 调用方与测试表面

### 5.1 产品进口

对 `src/pf` 的目标 import graph 作正向核对：

- `cli.py`、`check.py`、`baseline.py`、`search.py`、`verification.py`、`workflow.py`、
  `coordinate_search.py`、`report.py`、`runlog.py` 的**静态相关进口**只允许 §3.1 的产品名字；
  不进口任何静态名字也满足本条。它们可以继续进口 `pf.schemas.*`（含 §4.2 下放到该层的
  identity 函数）以及其他非静态 domain 名字。本条不是「整个文件只能进口 §3.1」。
- `search.py` 为实现真实 slice collector，额外允许进口 §3.4 的 `StaticSliceCollector`
  adapter-facing interface；不允许因此进口其实现、cache refs 或 `StaticPoint`；
- `evaluation.py` 不进口 static cache、handle、request 或 evaluator；不 re-export `StaticEvaluator`；
- `environment.py` 不进口 consumer/pass/cache ref；若保留 static materialization slot，只把它当
  opaque object，不解释其形状；
- `adapters/ty.py` 只额外允许进口 §3.4 的 `StaticTyRequest`，因为这是受保护的真实 adapter-facing
  interface；其余 adapter 不获得静态 internals 豁免；
- `schemas/` 服从 §4.2 的正向允许表，不能通过目标 `pf.static` 根、re-export 或延迟 import
  绕过。

composition test 还必须证明 Check/Highest/Search 共用同一个 StaticEvaluator 实例，生产
`TyAdapter` 与 request/inspect 装配共用同一个 ProcessRunner。

### 5.2 测试

产品测试与调用方走同一 seam：`collect_prepared` / `capture_highest` / `record_runtime` /
`compare_global` / `open_slice`、`StaticGuidanceEvaluator.open_static_slice` /
`locate_static_hint`，以及真实 Check/Highest/Search 图。lookup 只读由同一 prepared/request 连续
collect 的第二次结果、stage/permit/recording Ty 调用数证明；任意 context compare 与
`_admit_saved_static_audit` 只在 module 内部测试。静态事实从公开 outcome 观察，不读取
Evaluator/cache private state，不直接构造 `PreparedEnvironment` 成功值。

路径/configuration/inspect/subject 的现行叶子测试不再写入 D002 §11，也不再单独规定产品契约。它们要么改写为公开表面的语义断言（v2 subject / 配置物化变化、未闭合输入不启动 ty），要么降为静态 module 内部测试。v1 文件树叶子测试已随对应实现删除，不列入去向表。

Plan **首切片**必须列出逐文件去向，AC7/AC8 按该表核对，至少包括：

`tests/test_static_{cache,comparison,configuration,guidance,guidance_qualification,inputs,journal,lifecycle,ownership,paths,report,request,subject}.py`、`tests/test_runtime_static_scope.py`、`tests/scripted_static.py`、`tests/static_fixtures.py`。每项标明：改写到 §3.1 表面 / 降为 module 内部 / 删除。采用 replace-don't-layer：公开 outcome 已覆盖的 request factory、路径文件布局、cache ref、浅转发与 private shape 测试必须删除，不得再以内部测试叠加保留。漏列或保留测试专用公开导出视为 AC7 未完成。

`CoordinateSearch` 测试继续注入 `StaticGuidanceEvaluator`；产品 Search 测试消费真实 slice，不在测试里复制 `_RunnerStaticSlice`。

Ty 与 filesystem 仍用 lower adapter / 临时项目替换；这是已有真实 seam，不为本 module 新增假想 Protocol。

## 6. D002 地图（与加深同一吸收）

吸收时重写 D002 §3 为 **module 地图**，不以文件清单冒充所有权。地图与静态加深必须同一次 owner 吸收，避免先改文件名、后再改 interface 造成两次漂移。

必须成立：

```text
static module                 原始 TyCheck、Run cache、准入/比较、纯 guidance
evaluation.py                 RuntimeEvaluator、动态 cache、stage permits
cancellation.py               Run 取消（现行代码已有，地图补上）
failure.py                    FailurePolicy 分类实现；构造/注入规则由本节 module composition 拥有
```

不再把不存在的 `src/pf/static.py` 画成独立 module。`schemas/static.py` 是记录模块，保留。`static_request.py`、`static_guidance.py`、其余 `static_*.py`、`ty_fact.py`、`ty_options.py` 不是独立 module；内部文件名由实现决定，地图不跟踪。现行其余 module 行（cli、project、environment、search、verification、report 等）按代码核对后保留或更正，不借本次删掉真实 seam。

§4 schema 表在现行 config/project/evaluation/report/apply 之外补上 `schemas.policy` / `schemas.journal` / `schemas.static_*` / `schemas.ty_fact` 记录范围，并写明：validator 只做结构与 identity；D004 算法不在此执行。

## 7. FailurePolicy 构造与 owner

`CompatibilityChecker`、`HighestVersionVerifier`、`SearchCoordinator` 与 `_ProposalRunner` 现行接受 `failures: FailurePolicy | None = None`，缺省再 `FailurePolicy()`。生产只有一个实现；`cli.py` 不传入；测试也不替换它。这是假想 seam。`VerificationRunner` 已内部构造，作为目标形状。

目标：四个编排器内部构造，删除可选参数。这条构造/注入规则只吸收进 D002 的 module composition，
不写入 D005。`FailurePolicy.classify` / `record_prepare` / `record_evaluation` 仍是 D005 的公开分类
interface，产品测试与 `tests/test_failure.py` 可直接调用以构造记录或断言分类；
Check/Highest/Search 的产品测试从公开 outcome 观察 disposition/cause，不注入第二份 policy。

不能仅因「一个生产实现」删除 `ConfiguredVerifier`、`UvOperations` 或 CLI 七个 workflow Protocol：那些有生产与测试两套 adapter，是真实 seam。

## 8. 明确不做

- 只把浅文件挪进子目录，或让公开 interface 与现在的 Evaluator + Cache + RequestFactory + 叶子类型等宽。
- 新增 evaluator facade、parameter bundle、DI、hint/cache manager。
- 把三个产品编排器合成一个评价 module。
- 恢复 region、witness、静态 compatibility disposition，或跨运行 Evaluation cache。
- 改 Schema 1 字段、generation 规则或 JSON 投影手改。
- 另开一份只搬 schema 函数路径的平行 Design。
- 把 inspect / path-in-root / cell key 去重写成独立 AC；那是吸收 Plan 的内部卫生。
- 以本文件为现行 D002/D004/D008；吸收前调用方仍以现行 owner 为准，D005 分类与日志资格不变。
- 仅因单实现而删除 `ConfiguredVerifier`、`UvOperations` 或 workflow Protocol。

## 9. 验收标准

| AC | 必须成立的目标 | 公开 seam / 证据 |
| --- | --- | --- |
| AC1 | §5.1 的目标 **静态** import graph 成立：九个产品调用方的静态相关进口只允许 §3.1，`search.py` 仅有 `StaticSliceCollector` 额外豁免；`evaluation.py` 无静态 import/re-export，`environment.py` 无 consumer/pass/cache ref；`adapters/ty.py` 只有 §3.4 的 request 豁免。公开报告、普通 Journal 与 ty-cache decode 不 inflate、不重放 | 全 `src/pf` AST 静态进口/export 扫描；调用点扫描；失败即未完成 |
| AC2 | 生产构造 `StaticEvaluator` 不出现 `StaticRequestFactory`；request 装配在 module 内；生产 `TyAdapter` 与 request/inspect 装配绑定同一 `ProcessRunner` | `cli.py` composition test 与 recording process test |
| AC3 | SearchCoordinator 不定义手写 slice、不进口 cache ref 去构造 `StaticPoint`；只实现 §3.4 collector 并调用唯一的 `StaticEvaluator.open_slice`；后者显式接收该坐标 `CandidateSnapshot`，返回满足固定四项 interface 的 adapter。上端直接 PASS 以 Direct-PASS ledger 为准：调用前必须已经 `record_runtime`，不得只凭 Search 内存中的 `PassEvaluation` 打开 slice | `search.py` 结构扫描与 Search/CoordinateSearch 产品测试；hint 语义与现行 D003/D004 一致；无 ledger 行时 `open_slice` 为 `ValueError` |
| AC4 | `src/pf/schemas/` 满足 §4.2 正向允许表：禁入目标 `pf.static` 根、现行 static_*/harness/ty_options/resolution 算法进口；既有逐项例外不报失败；任何 validator 不重放 compare、hint 或 harness | AST import 扫描 + validator 调用图；无延迟 import/re-export 绕过 |
| AC5 | 准入、减法、`locate_static_hint` 各只有一处实现；`compare_global`、StaticSlice 与 `_admit_saved_static_audit(StaticAuditDocument)` 委托同一内部 derive/hint；`TyFactDocument` 单独不能进入比较/hint admission；未绑定 PASS 缺少 `scope_ref` / `run_identity` / `preparation_ref` 或 preparation 对不上 Proposal/Cell 时 fail closed | 结构调用图/禁止第二实现扫描 + 三入口语义测试；保存审计对多 raw facts、缺失/错绑/复用 ty process、未绑定 PASS 无 preparation 与其余坏 closure 逐类 fail closed |
| AC6 | 吸收后 D002 §3 是与代码一致的 module 地图：静态一个 module、有 `cancellation.py`、无虚构的 `src/pf/static.py`、保留 `schemas/static.py` 记录、不把静态叶子/`ty_fact`/`ty_options` 写成独立 module；§4 含 policy/journal/static_* 且禁止向上执行算法 | owner 正文与索引；与静态加深同一 Plan 吸收 |
| AC7 | 吸收后 D002 §11 与 D004 §6–§7 描述 §3 表面；叶子测试不再作为产品契约。Plan 首切片逐文件去向表覆盖 §5.2，执行 replace-don't-layer，无测试专用公开导出 | owner 正文；去向表；产品/内部测试 import 扫描；被公开 outcome 覆盖的旧测试已删除 |
| AC8 | lookup 只读、collect 占 permit、同 key 单次 ty、GLOBAL/SLICE 准入、无静态 disposition、Run 作用域 cache 均保持 | 同一公开 collect 连续调用证明 hit 不启动 ty/不占 permit；compare_global/真实 slice/Check/Highest/Search 语义测试，不读 private state |
| AC9 | `TyCheckCache` 只由 VerificationRunner 构造，在 capture 前存在。时序按 §4.1：每个 Cell 完成时（及 `finalize` 补跑）调用 `admitted_membership`；Run 收尾在成功/异常/取消路径上为 `stop` → `documents` → `close`。不得把四者读成线性顺序，也不得在 `stop` 之后补跑 admission。Check/Highest/Search 只转交，只有 StaticEvaluator/StaticSlice 调用 cache 领域方法；不跨 Run 共享 | 生命周期/取消/关闭后拒绝使用测试 + 产品调用点扫描 |
| AC10 | Schema 1 字段集合与 identity digest 字节在 D043 已吸收范围之外不变；三个下放 digest 的固定 preimage→hex golden vectors 逐字节不变。不涵盖五表删除、`ProbeObservation.selection_reason` 与 v2 subject/policy identity 字节 | 三组以上 golden vectors、`generate_report_schema.py --check`、现行报告 round-trip |
| AC11 | 生产路径无 static witness/region/compatibility 拒绝；静态失败最多 NO_HINT | 现行 D003/D004 权威测试，无回归 |
| AC12 | Check / Highest / Search / `_ProposalRunner` 不再接受 `failures=`；内部构造规则只吸收进 D002。D005 分类规则不变，产品编排测试不注入假 policy | 构造器与 owner 扫描；公开 outcome；`ConfiguredVerifier` / `UvOperations` / workflow Protocol 仍在 |
| AC13 | `RuntimeEvaluator.evaluate` 无 `run_cache` 参数且不读 static consumer；collect/capture 即使未形成 raw fact 也先把 exact prepared object 及其 portable preparation 登记到 Preparation registry；Check/Highest/Search 在每次 runtime 结果后、close 前调用 `record_runtime`。Preparation registry 按 prepared 对象身份、允许同一 Proposal 多个 registered prepared；Direct-PASS ledger 每 Proposal 一个 runtime owner。`record_runtime` 先做身份校验再按 outcome 记账。later collect 只绑定 consumer。wrong-cache、未注册/错对象 prepared、proposal mismatch、process 复用、对已有 owner 使用不同 prepared/process 均 `ValueError`，不再静默跳过 | signature/调用点扫描；available-at-PASS 与 PASS-after-later-collect 产品测试；§3.2 逐条负向测试，含「Rejection 但未注册仍 ValueError」 |
| AC14 | 静态 fact/TyCheck 不建立 Diagnosis Index association；verifier 以及 prepare 的 resolve/install/inspect 等 Failure Process Log sidecar 与 diagnose availability 完全保持 D005/D008 | 共享 association 调用图检查 + verifier 与每个保留的 prepare stage/authority family 参数化代表测试；无 static fact association |

停止条件（任一成立则本 Design 未交付）：新公开表面与 Evaluator + Cache + RequestFactory + 叶子类型
等宽；只改目录；schemas 仍调用换了路径的 admit/hint/harness；保存审计 admission 仍由 Plan 定义或只拿
`TyFactDocument` 重放比较/hint；RuntimeEvaluator 仍写 cache/读 static consumer；Search 仍手写
slice/omission；产品调用方仍调用 cache 领域方法；D002/D004/D008 地图与 owner 另一次变更才改；
`failures=` 仍留在四个编排器上。

## 10. 接受状态

已完成并归档。2026-09-10 按 [P046](../plans/P046-pf-static-evaluation-module.md) 实施 AC1–AC14；
稳定规则已归并 D002/D003 §5/D004/D008 与 CONTEXT，D005 分类规则未改。正文保留迁移时的目标与理由，不再承担现行规范。

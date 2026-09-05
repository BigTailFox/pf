# D033 — PF Predecessor Revalidate

- **状态：** 已接受；评审修订已纳入，待建立实施 Plan，未开始实施
- **日期：** 2026-09-05
- **性质：** 临时性搜索调度、配置命名与 evaluator 缓存契约迁移 Design
- **核对基线：** `8883a65`；原拆分基线为 `6271752`
- **来源：** 从原 D031 拆出目标 1、5、6、7；树方案移入 [C001](../concepts/C001-pf-multi-resolution-coordinate-search.md)
- **稳定 owner：** [D001](D001-pf.md)、[D002](D002-pf-implementation.md)、
  [D003](D003-pf-search-algorithm.md)、[D006](D006-pf-cli-enhancement.md)、
  [D014](D014-pf-report-schema.md)
- **关联：** [D004](D004-pf-ty-enhancement.md)、[D005](D005-pf-failure-and-diagnose.md)、
  [D008](D008-pf-verification-run.md)、[D013](D013-pf-pytest-observer.md)、
  [E005](../experiments/E005-pf-multi-resolution-search-simulation.md)

本文定义已接受的独立目标契约。生产实施前须建立 durable Plan，映射全部验收标准、接口迁移、
生成物和证据。本轮完善 Design，纳入 baseline 证据准入与首次 promotion 的评审修订，
不创建 Plan 或修改生产实现。
C001 的树搜索延期，不是本文实施或验收的前提。

## 1. 目标与范围

在现行平面坐标搜索上，后续 sweep 优先在当前 context 下直接重验当前 floor 的 predecessor，
避免对已经稳定的边界重新定位。每次提交仍只降低一个坐标，当前完整向量始终有直接 PASS。

| 原 D031 目标 | 本文保留的决定 |
| --- | --- |
| 1 | `search-resolution = major \| minor \| patch` 替换 `search-step`，默认 minor；表示系列代表粒度 |
| 5 | 保留最低候选快速路径；后续 sweep 优先重验当前 floor 的直接 predecessor |
| 6 | 缓存统一在 evaluator 入口命中；搜索不维护“是否运行过”的分支或独立执行结果缓存 |
| 7 | 同 Slice 的直接非单调矛盾立即终止；动态排序、跨坐标失败记忆和失败用例打分留待独立提案 |

一维定位继续使用现行平面 lower-bound 与小窗口策略。CandidateBuilder 继续按所选 resolution
取每个系列的最高合格精确代表并冻结 `C[d]`；保留现有 Candidate `series_key` 与 snapshot
`series_representatives`。完整合格 U 冻结、树结构与逐层 refinement 留在 C001 作为待证设想。
配置改名不会让平面算法隐含执行 `major → minor → patch`。

结果仍是局部 `REJECTED* PASS*` 假设下相对于 `C[d]` 的 coordinate-minimal passing vector，
不认证未探测 hole 或笛卡尔积全局最小值。探测顺序变化可能改变非单调空间中观察到的反例或终态。

## 2. Resolution 改名与候选语义

```toml
[tool.pf]
search-resolution = "minor"
search-space-defaults = { with-lower-bound = "majors[declaration-1:]", without-lower-bound = "majors[baseline-2:]" }

[[tool.pf.dep]]
name = "zero-series-lib"
search-space = "minors[declaration:]"
search-resolution = "patch"

[[tool.pf.dep]]
name = "other-lib"
search-space = ">=1,<5"
search-resolution = "major"
```

全局与逐依赖配置均支持 resolution；继承为逐依赖 → 全局 → 默认 minor，root/member 合并沿用 D001。
CLI 统一改为 `--search-resolution`，内部策略和报告 binding 字段统一为 `resolution`。
按 pre-release 规则直接替换配置、CLI、model 与 wire 中相应的 step 字段，不保留别名或 dual reader。
这里的 resolution 是 PF 系列代表粒度，不改变 uv dependency resolution 的术语或行为。

| Resolution | 平面候选序列 |
| --- | --- |
| major | 每个 major 系列的最高合格精确 release，按版本升序 |
| minor | 每个 minor 系列的最高合格精确 release，按版本升序 |
| patch | 每个 patch 系列的最高合格精确 release，按版本升序 |

相同 registry 观测、space 和粒度下，候选精确 version/artifact 与当前对应 step 完全相同。
不足三段的 release 补零；系列 key 继续包含 epoch；同 key 的 pre/post/local 或额外 release 段
按完整 Version 顺序取最高合格代表，patch 不增加逐个精确 release 穷举层。
例如 minor 代表为 `2.3.7`，结果和 apply 使用经过直接验证的 `2.3.7`，不截断为 `2.3`。

`search-space`、条件默认表、过滤前系列 inventory、anchor 求值与错误时序、公共候选资格、
prerelease、artifact 和 baseline cap 沿用 D001/D003/D014。所有 space × resolution 组合合法；
不根据 `0.x`、发布密度或 SemVer 含义自动改选粒度。

## 3. 统一证据入口与缓存

### 3.1 生命周期与所有权

缓存属于一次 Cell search 的 evaluator 实例。该实例固定 Cell、SourceSnapshot、SEARCH SourcePlan、
evaluation policy、static/harness baseline、resolver profile、release cutoff 与解析上下文。
在这一固定作用域内，规范化的完整 managed vector 可作为 lookup key；不是跨运行或跨 Cell 的持久 key。
同一精确解析请求继续消费现行解析缓存，不在命中旧证据时重新建立另一个解析图。

`_ProposalRunner` 拥有实际 prepare/static/runtime 结果表及 prepared environment 生命周期；
`CoordinateSearch` 只保存算法所需的观测、Slice、区间、边界与历史提示，不再另建一份执行结果缓存。
baseline 的真实 PASS 及其 evidence 引用按 §3.4 在创建 evaluator 时预置。该 seed 引用已完成的
highest 评价，不声称 highest 与 exact-vector 请求的 Attempt、harness 或解析图相同。
普通算法测试通过合法 evaluator 提供同等的已知结果，不以只有 status 的搜索私有对象代替产品证据。

跨定位、跨 sweep 的重复请求都走同一入口，不要求调用方先查 cache 或标记“已测”。空间外 sentinel
是一个不可 probe 的已知高端，其特殊性来自候选资格，不来自缓存命中与否。

### 3.2 命中规则

| 已有状态 | 请求 | 行为 |
| --- | --- | --- |
| 直接完整结果或已分类 prepare 终态 | 同向量请求 | 返回原 evidence，跳过 prepare 与验证 |
| 本 Slice 的 static-only observation | 调度 probe | 返回该 Slice 合法 guidance；不复制成直接结果 |
| 只有 static/prepared 状态 | floor/predecessor promotion | 取得直接 runtime evidence，不能以 static 命中跳过验证 |
| 相同向量但不同 active dependency 的 static-only 状态 | 调度 probe | 共享已有静态事实，按新的 Slice 判定 guidance |
| 无缓存 | 任意实际 probe | 按现行 prepare → static → runtime 规则执行并记录 |

缓存命中不能绕过算法对当前 Slice 的 observation 归属、非单调检测、边界引用与 final evidence 闭合。
例如完整结果可跨 active dependency 复用，但每个 Slice 仍需登记实际使用的直接结果。命中不制造新的
Attempt、验证进程或成功耗时；活动展示不把一次 lookup 冒充一次新验证。

结果表必须保留证据强度与失败类型；不使用一个无类型的 `vector -> bool` 表覆盖 static 与 runtime。
保留现行 `evaluate_in_slice(request)` / `promote(request)` 的调度与认证区分，两者共享结果入口。
已有完整结果优先于 static-only；promotion 产生反证时保留历史 observation，并以直接结果重新定界。
同一精确 Proposal 的完整评价最多执行一次。不同 Proposal 不共享被 verifier 污染的可写环境。

### 3.3 失败资格

只有 D005 的直接 `REJECTED` 可以建立拒绝边界，`INDETERMINATE` 立即停止 Cell；非完整执行不能当成 FAIL。
FailedCaseSet 拒绝预言继续归 `_ProposalRunner` / RuntimeEvaluator / ConfiguredVerifier，保持现行主动
坐标作用域与 collection 证明。其命中可拒绝当前 probe；其通过不能建立 PASS。
本次不改 verifier 顺序，不把失败用例集传给坐标排序，也不新增 top-K 策略。

### 3.4 Baseline 直接证据与准入

现行 baseline 的 Attempt 为 `highest`，没有 `requested_managed_vector`，使用 original harness；
普通搜索 Probe 的 Attempt 为 `exact-vector`，绑定 selected candidate evidence 与 harness baseline。
现行 `ProbePass`、Cell observation validator 和报告 reader 只接受后者，不能仅将 baseline 放入
结果表就完成迁移。目标契约保留这两种请求的真实身份，按下表统一直接 PASS 的消费：

| 项目 | 目标契约 |
| --- | --- |
| 领域表示 | 沿用 `ProbePass(attempt, proposal_id, evaluation)`；PASS 支持 exact-vector 或本 Cell 的 baseline highest 两种来源，均持有真实 `PassEvaluation` |
| seed 构造 | SearchCoordinator 将成功的 `HighestVersionPass` 注入 `_ProposalRunner`；以其 Proposal 的规范完整 managed vector 为 key，保存原 Attempt、Proposal、Evaluation 引用 |
| 局部校验 | 两种 PASS 都要求 Proposal 属于该 Attempt，且 Cell、snapshot、policy 和 proposal ID 一致；exact-vector 继续要求 Proposal 向量等于 Attempt 请求向量；highest 保留原请求与 harness 约束 |
| Cell 准入 | highest PASS 必须与当前 CellResult 的 `baseline_attempt`、`static_baseline.proposal`、`baseline` 完整一致；同 Cell 的另一 highest 评价、不同 baseline 或跨 Cell/context 引用均不准入 |
| 向量来源 | highest PASS 的 observation 向量必须等于其已验证 Proposal 的 managed vector；不能从为空的 highest 请求向量展开，也不能接受调用者另报向量 |

此项只扩展直接 PASS 的合法来源。`ProbeRejection`、`ProbeIndeterminate`、`StaticOnlyEvidence`
继续要求 exact-vector Attempt；baseline 失败仍由既有 baseline 终态处理，不进入搜索 seed。
普通算法测试的 exact-vector PASS 仍合法；产品 seed 的构造和准入由真实 SearchCoordinator 图测试。

evaluator 对 baseline 向量返回原完整结果，不新建 Attempt/Proposal，不重新 prepare、static、runtime
或解析，不把 original harness 改写为 relaxed harness，也不移交已经关闭的 baseline 可写环境。
只在请求完整向量等于 seed key 时命中；其他坐标已降低的新向量不能借用 baseline PASS。

`evaluate(start)` 取得起点直接证据，并登记 `dependency=None` 的 observation。进入某坐标时，
若 current 位于该坐标的冻结候选内，复用其直接 PASS 并登记当前 Slice；即使它是首个候选、无需
predecessor，也执行该证据登记。若 current 位于候选外，它只作为已有证据支持的 sentinel：
不构造该点的候选请求、Slice candidate observation 或 region point，不产生候选窗口或执行活动。
初始完整向量 observation 不使 sentinel 获得候选资格。

baseline 在候选内时，其原 static evaluation 可与原 PASS 一起登记当前 Slice 的 region point，
按现行相邻性及 fingerprint 规则参与 guidance；引用仍指向 baseline 的原 Proposal。final 等于
baseline 时直接复用原 Evaluation，但仍须满足所有坐标的候选资格与最终 context 边界；final
不同于 baseline 时继续要求该精确向量自身的 exact-vector Attempt、Proposal 与 PASS 闭合。

### 3.5 Evaluator interface 与 Slice 登记

public `minimize` 移除 `start_is_known_pass`，保留普通 `VectorEvaluator.evaluate(vector)` 和
runtime-backed `evaluate_in_slice(request)` / `promote(request)` 两种既有 seam：

| evaluator | 调度 probe | floor/predecessor 直接认证 |
| --- | --- | --- |
| 普通 VectorEvaluator | `evaluate(vector)`，只返回直接 ProbeEvidence | 同样调用 `evaluate(vector)`；由 evaluator 复用完整结果，不要求实现 promote 或依赖搜索私有缓存 |
| RuntimeBackedVectorEvaluator | `evaluate_in_slice(request)`，可返回合法 static guidance | `promote(request)`，始终取得直接 ProbeEvidence |

promotion 不以此前已有调度 probe、prepared environment 或 region point 为前提。首次请求即为
promotion 时，统一入口完成 prepare → static → runtime，或返回已分类 prepare 终态；只有
static/prepared 状态时补齐 runtime，已有完整结果时直接复用；任一阶段 Indeterminate 立即返回并
停止 Cell，不执行后续阶段。普通 evaluator 缺少 runtime-backed
方法不是 `NONDETERMINISTIC`；它本来就只提供直接证据。

两层登记分别由既有 owner 完成，不合并为第二份结果缓存：

- `_ProposalRunner` 在每次带 Slice 的请求返回前，以当前 active dependency、other coordinates
  和冻结 candidate order 登记 region point。首次 promotion 和跨 active dependency 的完整命中
  都必须登记；具备完整 static 事实的直接 PASS/Rejection 可成为该 Slice 的 runtime reference。
  缺少完整 static 事实的 prepare 终态不制造 region point；已有静态事实可从保存的 Evaluation
  取得，不为登记重新创建环境。相同 point 重复登记幂等，promotion 更新直接 reference 并保留历史事实。
- `CoordinateSearch` 对每个返回结果登记 observation，直接结果进入当前 Slice 的非单调检查，
  再用于边界提交。首次 promotion、cache hit 和首候选的 current PASS 走同一登记规则；相同
  observation 可去重，static-only 被直接结果推翻时保留两条。不同 active dependency 的登记
  相互独立，不能仅因完整向量在其他 Slice 已使用而跳过。

登记不产生 Attempt、验证进度或新增成功耗时。Static guidance 只能消费当前 Slice 的合法 region；
直接命中优先，region 中出现不同直接状态时按 D003 停用 guidance。同 Slice 的直接非单调矛盾
仍由 CoordinateSearch 立即终止，不因缓存或 region 登记延后。

## 4. 平面搜索与 predecessor 重验

### 4.1 进入坐标

进入 d 时 current 完整向量已有直接 PASS，固定 `context = V_-d`，仅考虑 `C[d]` 中不高于
`current[d]` 的候选。history 只保存本次 search 中上一 sweep 的坐标边界；旧 context 的结果
只能作为选点提示，不能作为当前 Slice 的拒绝事实。

1. 没有候选，返回 `NO_PASS_IN_SEARCH_SPACE`。
2. current 已是首个候选，按 §3.4–§3.5 复用并登记其直接 PASS，建立没有 predecessor 的边界。
3. 若 history 的 floor 等于 current，且 predecessor 仍是冻结 `C[d]` 中的直接前驱，
   优先通过 §3.5 的直接认证入口请求该 predecessor 在当前完整 context 下的直接结果；
   runtime-backed evaluator 使用 `promote(request)`，允许这是该向量在新 context 下的首次请求。
   REJECTED 则以 current 的真实 PASS 与此次直接拒绝建立当前 Slice 的边界，结束该坐标；
   PASS 则将搜索 upper 降到 predecessor，继续向下定位；INDETERMINATE 则立即停止 Cell。
4. 无可用历史边界，或 predecessor 已 PASS 后需要继续下降，进入最低候选快速路径和现行平面定位。

同 context 的重验可以命中 evaluator 原有直接结果，无需新增验证；context 改变后按新完整向量
请求证据。搜索流程不自行判断 cache hit。predecessor 转 PASS 不能使旧 floor 继续充当最低点，
也不能跳过更低候选的定位与边界认证。

### 4.2 最低候选、平面定位与 sentinel

最低候选快速路径使用 `C[d]` 的首个代表：直接 PASS 即取得最低 floor；仅有 PASS guidance
则先 promotion；拒绝方向进入现行平面 lower-bound，INDETERMINATE 停止。
可用 predecessor 重验已证明边界不变时，不再执行最低候选快速路径。

首次搜索的 hints 继续服从 D003，只影响选点、不形成硬下界；后续 sweep 的有效历史边界重验
优先于 hint。无可用历史边界时沿用现行 hint/最低候选路径，不引入跨运行或跨 Cell hints。
保持现行确定的小窗口阈值（默认 8）、升序线性与二分规则，不同时调参。

空间外 current 仅提供不可 probe 的虚拟 PASS sentinel，不进入快照、窗口端点或候选计数。
最高真实候选不能借用 sentinel 的 PASS；空间内未找到 PASS 时返回 `NO_PASS_IN_SEARCH_SPACE`。

### 4.3 Promotion、窗口与非单调

floor 必须有当前完整向量的直接 runtime PASS；非首个候选的直接 predecessor 必须取得直接
Rejection。static guidance 被 promotion 推翻时保留历史 observation，以新的直接事实重新定位；
每次修正增加直接事实，无法取得一致证据时沿用 `NONDETERMINISTIC` 终止保护。

每次取得或复用直接 observation 都检查同 Slice 中 `v_low < v_high` 且
`PASS(v_low), REJECTED(v_high)` 的矛盾；发现立即返回 `NON_MONOTONIC` 并保存反例，
不执行局部或全空间 fallback。不同 context 与 static guidance 不参与此判断。

Static region 的相邻性仍按冻结 `C[d]` 的已观测连续点确定。普通定位请求的窗口继续反映尚未
排除的连续候选区间；predecessor 重验和边界 promotion 使用当前 floor/predecessor 对，
候选计数为这对真实候选的数量。首个候选无 predecessor 时使用单点 promotion 窗口。
终端消费现有 typed request/event，cache lookup 不制造新的执行进度。

### 4.4 Sweep 与最终证据

```text
current = verified baseline
repeat:
    changed = false
    for d in canonical dependency order:
        floor, boundary = search_coordinate(d, current, candidates[d], history[d])
        if floor < current[d]:
            current[d] = floor  # 已直接 PASS 的完整向量
            changed = true
        history[d] = boundary
until not changed
```

依赖顺序固定，每轮覆盖全部坐标。候选有限且每次提交严格下降，故 sweep 终止。
最终无变化 sweep 在最终 context 下重新建立全部边界；final 自身形成精确 PassEvaluation 闭环。
旧 boundary 的位置可复用，旧 context 的 evidence 不得直接接到新边界上。

## 5. Owner、报告与迁移

| Owner | 本次迁移 |
| --- | --- |
| ConfigLoader / ProjectLoader / CLI | resolution 输入、继承、默认、named policy 与 help |
| CandidateBuilder | 消费 resolution 命名，保持现行代表候选与系列证明形状 |
| CoordinateSearch | 重验调度、history、Slice observation、promotion、窗口与确定 sweep |
| SearchCoordinator / _ProposalRunner | 真实 highest baseline seed、唯一结果入口、首次 promotion、Slice region 登记、prepare/static/runtime 缓存和资源清理 |
| RuntimeEvaluator / ConfiguredVerifier | 保持直接证据与 FailedCaseSet 拒绝预言的现行职责 |
| ReportStore / schemas / ApplyAuthorizer | baseline PASS 准入与向量展开、resolution 策略 binding、identity、直接边界验证和授权 |
| Explain / terminal | resolution 与精确 floor 展示，消费现有候选窗口与实际执行活动 |

Plan 须明确移除搜索私有 known-pass/cache 与 `start_is_known_pass` shortcut 的接口迁移，
通过 evaluator 注入 baseline 的真实 evidence。`CoordinateSearch` 可保存算法观测，但不另持一份
执行结果权威。不新增 CacheManager 或 HintProvider。策略测试继续使用 public minimize seam，
产品测试通过现有 lower adapters 装配真实 evaluator 图。

### 5.1 Baseline PASS 报告闭合

baseline-backed PASS 沿用 Schema 1 的 `DirectPassV1(kind="DIRECT", status="PASS", attempt_ref=...)`，
引用原 baseline Attempt；不新增 baseline observation wire 变体，不增加向量副本或伪造 exact ref。
`ProbePass` 的局部结构校验与 CellResult 的 baseline 准入校验共同负责 §3.4，不通过普遍放宽
`_require_shared_evaluation_context` 或所有 direct evidence 的 exact-vector 要求来实现。

reader 展开 observation 时先取得当前 CellResult 的已验证 baseline roots：exact observation
继续从 Attempt 请求向量展开；highest direct PASS 从原 baseline Proposal 展开，并核对其 Attempt、
Proposal、完整 PASS Evaluation 与当前 roots 一致。拒绝悬空 ref、其他 highest roots、向量漂移及
highest Rejection/Indeterminate/static-only observation。此规则同时覆盖 SUCCESS、SEARCH_FAILED
和含搜索证据的 CELL_INDETERMINATE；后两者也可能保存搜索开始时的 baseline PASS observation。

build/reintern、read、merge、region runtime references 与 final authority 一起消费该规则。
相同 baseline 证据只 intern 一次，read → write 保持 canonical byte stability；final 为 baseline
继续引用原 final Proposal，空间外 sentinel 不因读写或 apply 获得 floor authority。该准入变更不
改变 baseline 的 Attempt/Proposal identity preimage、解析事实或 verifier authority。

### 5.2 Resolution 策略迁移

`candidate_policy_identity` 的 step 输入替换为 resolution；报告 `inputs.search_policy` binding
使用 required `resolution`，完整请求策略继续进入 generation。即使候选和 floor 相同，策略字段
也须按新契约计算 identity；不从旧 wire 字段推断新字段。

本次保留 `registry-series-slice-v1` search profile、`runtime-static-v1` 算法版本、Schema 1 与现有
v1 identity 前缀；runtime evaluation policy 不变。`multi-resolution-coordinate-v1` profile 和
完整 U 快照迁移仅属于 C001 的后续设想，不能随本次改名提前引入。

reader、build/reintern、merge、纯 host-partial、apply 与 explain 一起消费改名后的目标形状。
apply 在 force waiver 前验证完整 requested search policy，保持原声明/projected/no-op 授权和
离线幂等行为。保留代表快照、series_key、series_representatives 与过滤前 inventory 的现有职责。

## 6. 收益证据与验证边界

[E005](../experiments/E005-pf-multi-resolution-search-simulation.md) 的历史 A/B 对照表明：
2,883 个单坐标场景中 B 的 direct oracle miss 比 A 少 36.75%；36 个多坐标场景少 25.00%。
这些只证明所列合成输入的探针变化，未测真实 evaluator/verifier 成本或产品 wall-clock。
本文保留原实验及其基线，不把缓存迁移或树的变化混入重验收益。

后续 Plan 以现行平面定位 A 与加入重验的 B 作独立对照，固定源码、候选、space、resolution、
baseline、坐标顺序、小窗口阈值与测试命令。各组使用独立 invocation-local 结果表，给予等价缓存
复用，区分 logical request、cache hit、唯一 vector、prepare/runtime miss 和 sweep 数。

在有限真实 evaluator harness 中保留 static guidance、promotion、FailedCaseSet 与资源隔离；
交替运行顺序，记录冷/热 registry 和 resolution cache 条件、各阶段次数与耗时、Cell/Run wall-clock。
历史 trace 缺失的精确向量标为 missing，不能补造 outcome 或拼接不同评价上下文的证据。
预先选定场景和重复次数，记录波动及退化，不以探针节省百分比代替耗时收益。
Plan 将真实 evaluator A/B 安排为证据入口闭合后的早期验证切片；正确性失败先修复再比较成本，
耗时未改善或出现退化时记录适用范围和结论，不以合成实验结果覆盖真实结果。
本文的正确性与产品验收独立于树实验；C001 的 C/D 实测不阻塞本文。

## 7. 验收标准与 owner 吸收

| AC | 必须取得的证据 |
| --- | --- |
| 1 | global/dep/CLI resolution、minor 默认、继承、raw layer 与非法输入分类；配置/model/wire/help 完整替换 |
| 2 | 相同观测与粒度下 public CandidateBuilder 返回相同精确代表/artifact；各 space × resolution、特殊版本与资格错误时序保持 |
| 3 | public minimize 覆盖后续 sweep predecessor 仍拒绝/转 PASS、context 改变、同 context、无历史、首候选无前驱与 hint 优先级 |
| 4 | 最低候选快速路径、平面定位、虚拟 sentinel/no-pass、promotion 反证及准确窗口；边界与 final 直接证据闭合 |
| 5 | evaluator 真实 baseline seed；同向量跨定位/sweep 复用；完整 cache hit 不触发 prepare/static/runtime/解析；invocation 和 Proposal 隔离 |
| 6 | static-only 不成为直接 PASS；跨 active dependency guidance 隔离；直接 cache hit 仍登记 Slice observation/region reference 并触发必要的非单调检查 |
| 7 | NON_MONOTONIC 立即停止并保存反例；Indeterminate 不裁剪；确定顺序、有限终止及最终 context 下全部边界 |
| 8 | resolution identity/binding、代表快照 round-trip、reader/build/merge/host-partial/explain、apply policy/force/幂等公共行为 |
| 9 | A/B 等价缓存的探针对照与真实 evaluator 成本记录；missing、波动、退化与证据边界如实记录，不要求树实验闭合 |
| 10 | focused/full checks、typing/lint/build、Schema/examples 再生成与链接检查；长期测试只保留当前 public contract |
| 11 | README 双语、help、配置/model/schema/fixtures/scripts 同步；稳定规则归并 owner，Design 与 Plan 完成逐项审计并同步归档 |
| 12 | 真实 highest seed 保留原 Attempt/Proposal/Evaluation；候选内 baseline（含首候选）、空间外 sentinel、final=baseline 与 final 不同于 baseline；SUCCESS/SEARCH_FAILED/CELL_INDETERMINATE 的 baseline observation/region refs 经 build/read/reintern/merge 闭合且 round-trip 稳定；拒绝其他 baseline、跨 Cell/context、漂移向量及非 PASS highest observation |
| 13 | public minimize 的普通 evaluator 支持直接认证且复用结果；真实 evaluator 首次请求即 promotion、新 context predecessor、static-only 后 promotion、跨 active dependency 完整命中均正确登记；prepare 终态不制造 region、完整命中不新增执行活动、promotion 反证保留历史且停用不一致 guidance |

D001 接收 resolution 配置、默认与系列代表语义；D002 接收 evaluator 结果入口、真实 highest seed
与资源 ownership；D003 接收普通/runtime-backed evaluator 认证、baseline Slice 归属、首次 promotion、
region 登记、predecessor 重验、缓存消费、window 与 sweep；D006 接收命名与活动展示；D014 接收
baseline PASS 的领域/wire 准入、向量展开与 roots 闭合，以及 resolution wire/identity/授权。
D004/D005/D008/D013 保持现行证据与执行规则；baseline 捕获及执行事实不变，搜索对其直接 PASS
的引用由 D002/D003/D014 吸收。
后续树方案转入独立 Design 并实施时单独吸收树与完整候选规则，不把延期范围加入本文完成条件。

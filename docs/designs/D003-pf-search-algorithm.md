# PF 单 cell 搜索算法

- **状态：** 现行
- **算法版本：** `runtime-static-v1`
- **最后核对：** 2026-09-06
- **产品输入与结果：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **静态 transition 与 witness：** [D004](D004-pf-ty-enhancement.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **已归并决策：** [D011](../archived/designs/D011-pf-runtime-backed-static-search.md)

本文是单个 package/cell 的坐标搜索、static region、probe 顺序、不变量与终止条件的唯一所有者。候选冻结由 [D037](D037-pf-candidate-search-policy.md) 定义；静态事实和 witness 由 D004 定义；`PASS` / `REJECTED` / `INDETERMINATE` 由 D005 定义。跨 cell 并发、报告合并和 apply 不属于本文。

## 1. 模型

受管依赖按规范化名称排序，每个依赖有一份按 D037 过滤并冻结的升序候选列表：

```text
D = [d1, ..., dn]
C[d] = [c0, ..., ck]
S[d] = C[d] union {B[d]}
V = {d1: version, ..., dn: version}
```

- `B = V_hi`：SEARCH SourcePlan 下受管 direct coordinates 的最高合格 registry release 解析；开始搜索前已直接完整通过。Workspace member 当前版本不充当 baseline sentinel。
- `S[d]`：完整 exact-vector 的 artifact 选择域，不是搜索候选；`B[d]` 在 `C[d]` 外时仍可作为其他坐标的固定 context。
- `current`：每个坐标提交后的向量；始终有该精确向量的原命令阶段直接 `test-command` pass。
  failed-set Rejection 可以成为 predecessor 或拒绝边界，但不能更新 `current`、floor 或 final。
- `static frontier`：只有本 Proposal 的 TyCheck、increment 和 fingerprint，可用于调度但不是 PASS、boundary 或 current。
- `V_final`：最终不动点；等于成功结果的 `final_vector`。

prepare 成功后才有 Proposal。prepare Rejection/Indeterminate 只有 Attempt；相同向量在不同 cell、源码、策略或解析图中不是同一证据。

## 2. Slice 与 static region

固定一维切片为：

```text
Slice = (
  cell,
  source snapshot,
  source plan identity (routes + SEARCH mode),
  policy identity,
  static baseline digest,
  active dependency,
  exact values of every other coordinate,
)
```

在该 Slice 的冻结候选顺序中，fingerprint 相同且**已观测连续**的最大区间构成一个 region。相同 fingerprint 若中间尚有未观测点、出现另一 fingerprint，或任一 Slice 字段不同，都不能预先复用。

Region 只保存调度事实：Slice、fingerprint、已观测连续版本和直接 runtime representative 引用。一个 Proposal 的 runtime 结果不能复制成另一个 Proposal 的 Evaluation。

## 3. 核心不变量

1. `B` 有直接完整 PASS，并冻结本 cell 唯一的 D004 静态基线。
2. 候选快照在本次 search 内不变；每个 snapshot 绑定与 exact probe 相同的 registry search route 和 SourcePlan identity，同时冻结 `C[d]` 与 `B[d]` 的 baseline selection，不包含 workspace HEAD/member version。
3. `current` 只能由该精确向量的原命令阶段直接 `test-command` pass 更新。
4. static-only observation 没有 disposition；不能成为 ProbePass、ProbeRejection、boundary 或 final。
5. 只有 D005 的 Probe Rejection 能移动拒绝边界；Indeterminate 立即停止 cell。
6. floor 与 predecessor 在提交边界前必须晋升为直接 runtime evidence。
7. 每次提交只严格降低一个坐标；每个 sweep 按规范依赖顺序覆盖全部坐标。
8. static、witness 和 test 共享同一 cell/snapshot/policy/baseline context。
9. 非单调判断只读取相同 Slice 中的直接 runtime observation，不读取 region guidance。
10. 同一精确 Proposal 的 prepare/static/完整 Evaluation 在一次 search 内最多执行一次；执行结果 cache 只属于 evaluator。
11. `CandidateSnapshot` 只冻结target受管project direct dependency的registry搜索候选；workspace member自身依赖、harness与任意transitive distribution完全属于uv resolution，不建立PF catalog、coordinate或floor。
12. 一次 Verification Run 固定精确 uv profile、唯一 SEARCH SourcePlan 对象、release cutoff 与共享 cache；baseline、CandidateSnapshot freeze 与全部 exact probe 由 Runner 注入并消费该对象及其 identity。相同 project/environment resolution input 最多解析一次；PF 直接观察的 source 访问失败、registry artifact不闭合或managed coordinate泄漏到local/workspace source仍为Indeterminate，不回退到development route，也不把cache miss解释为候选不存在。后端自由文本不等于这些直接事实，未知正常非零按 D005 拒绝 Attempt。
13. 搜索产生的每个完整向量都必须属于各坐标 `S[d]`；越界在 prepare/Attempt 前形成 Cell-scope `INTERNAL_INVARIANT`，不能回退为自由解析。

prepare rejection 与 verifier rejection 使用同一 ProbeRejection：可指导当前 Slice、形成直接
predecessor 边界并参与直接非单调检测。没有 Proposal/静态事实的 prepare failure 不登记 region，
也不填充 FailedCaseSet。它不证明某个单版本、整个区间或其他 Cell/source/policy/Slice 的失败。

重复观察与冲突承诺仅限实际存在的 seam：

| Seam | 观察/缓存范围 | 冲突含义 |
| --- | --- | --- |
| Cell search prepare/full-run cache | 固定 Cell/source/policy/baseline，完整排序向量 key | 命中复用同次观察，不重复 prepare，也不检测 attribution 漂移或证明复现 |
| EvaluationCache | 相同 Proposal ID/static baseline 的实际写入；static 比 status，full 比 status 与既有 verifier authority | CacheConflict 经 static-cache/full-cache 形成 NONDETERMINISTIC，不扩展到无 Proposal 的 prepare failure |
| CoordinateSearch | 同 Cell/Slice/candidate 的直接 disposition | 已观察 status 冲突按 NONDETERMINISTIC；两个 Reject 不比较归因 |
| ReportStore.merge | 同 generation/Cell 结果、同 evidence ID payload | 冲突直接拒绝报告合并，不合成运行期 NONDETERMINISTIC |

不同 failure ID 仅表示事实不同，不自动证明 nondeterminism；本次不增加 prepare 重试或归因漂移探测。

候选准入、系列切片、采样与 baseline artifact 选择只见 [D037](D037-pf-candidate-search-policy.md)；
`CandidateBuilder`/`CandidateSnapshot.select` interface 见 [D002 §7](D002-pf-implementation.md#7-verification-modules)。
具体 identity/preimage 与离线复算由 [D014 §1.2](D014-pf-report-schema.md#12-inputs) 拥有。
空间求值错误的 Run 收尾由 [D008 §3.3](D008-pf-verification-run.md#33-search) 拥有。

## 4. SearchCoordinator 状态机

```text
BASELINE B = V_hi
  ├── BaselineRejection     -> 终止 cell
  ├── BaselineIndeterminate -> 终止 cell
  └── direct PASS + frozen static baseline
        ↓
FREEZE CANDIDATE SNAPSHOTS
  ├── source/tool failure   -> Cell Indeterminate
  ├── empty search space    -> NO_PASS_IN_SEARCH_SPACE
  ├── invalid anchor/scope  -> SearchSpaceResolutionError（Run abort，D008）
  └── C[d] + required baseline selection
        ↓
ONE RUNTIME-BACKED COORDINATE SEARCH FROM B
  ├── CoordinateSuccess     -> exact final PassEvaluation -> CellSuccess
  ├── ProbeIndeterminate    -> CellIndeterminate
  └── NON_MONOTONIC / NONDETERMINISTIC / NO_PASS -> CellSearchFailure
```

不再存在 static fixpoint、`V_static`、联合测试 fast path 或第二轮 dynamic search。Baseline capture 的同一次 TyCheck 是 `B` 的空增量静态事实，不重跑；`B` 的完整 PASS 由 HighestVersionVerifier 提供。SearchCoordinator 把完整 `HighestVersionPass` 注入 evaluator，以 baseline vector 为 key 保存原 Attempt、Proposal 与 PassEvaluation；不伪造 exact-vector request、selected-candidate digest 或 relaxed harness。

模块依赖与测试替换点只见 [D002 §7、§11](D002-pf-implementation.md#7-verification-modules)。

## 5. CoordinateSearch interface

```text
minimize(start, candidates, evaluator, hints=())
  -> CoordinateOutcome
```

普通 `VectorEvaluator.evaluate(vector)` 直接返回 Probe evidence。Search 使用 runtime-backed seam：

```text
evaluate_in_slice(SearchProbeRequest)
  -> ProbeEvidence | StaticOnlyEvidence
promote(SearchProbeRequest)
  -> ProbeEvidence
regions
  -> tuple[StaticRegion, ...]
```

`SearchProbeRequest` 把以下事实绑定在同一次实际 probe 上：

```text
vector
active_dependency
candidate_version
lower_version / upper_version / candidate_count
```

`candidate_version` 必须等于 `vector[active_dependency]`，并位于非空窗口内。窗口是本次 lower-bound 定位尚未排除的有序离散候选区间：首次/hint probe 使用当下完整区间；线性扫描从当前点收缩到已知高端；二分使用当前显式候选 low/high 区间；floor/predecessor promotion 使用提交边界对。CandidateSnapshot 之外的虚拟 baseline sentinel 只是已知 PASS evidence bound，不计入窗口端点或 `candidate_count`，也不产生 runtime-backed probe identity。窗口是算法已有状态的只读投影，不改变 probe 顺序、cache key、证据状态或 floor authority；终端只消费该结构化事实，不反推算法窗口。

`evaluate(start)` 必须返回并登记真实直接证据；产品 evaluator 对完整 baseline vector 返回原 highest
Attempt/Proposal/Evaluation，不重新 prepare、static 或 runtime。普通 evaluator 也只通过自身 `evaluate`
提供并复用直接 evidence，不要求搜索知道它是否命中 cache。`CoordinateSearch` 不再接受 known-pass 标记，
也不维护 execution/evidence cache；它只去重报告 observation，并保存相同 Slice 的直接状态用于矛盾检测。

产品 evaluator 的完整结果、prepare terminal、static 与保留的 PreparedEnvironment 都是一次 Cell search
invocation-local。完整 vector 结果可跨 active dependency 复用，但每个 Slice 仍登记自己的 observation 与
region reference；相同向量在另一 active dependency 下不能借用 static-only guidance。完整 lookup hit 不创建
Attempt、环境、验证活动或成功耗时。不同 Proposal、Cell 与 invocation 不共享可写环境或结果。

Evaluator 在完整结果或 prepare 终态后立即关闭本次环境；static-only 后保留未污染的环境供 promotion，
退出 Cell 时清理全部保留环境。生命周期由 `_ProposalRunner` 独占，算法不持有资源句柄。

产品测试边界由 [D002 §11](D002-pf-implementation.md#11-验证边界) 定义；算法矩阵从 `CoordinateSearch.minimize(...)` 的公开 evaluator seam 验证。

## 6. 一个 candidate probe

调用 evaluator 时先按规范完整 vector 查唯一结果入口。已有直接完整结果或 prepare terminal 立即返回；只有
prepared/static 状态时，`promote` 从该状态补齐 runtime。没有结果时 prepare 成功后的顺序固定：

1. 取得该 Proposal 自身完整的 D004 static transition；
2. 若是当前 region 的首次直接 observation，按 D004 运行 witness 或 `test-command`，得到 ProbePass/Rejection/Indeterminate；
3. 若与相邻已观测点形成同 fingerprint region，且该连续 component 已有唯一一致的直接 PASS 或 REJECTED representative，可以只保存 `StaticOnlyEvidence`；
4. 若 component 内已有不同直接状态，不使用 guidance，直接运行 runtime evaluation；
5. Ty/witness 必需协议不完整，或 verifier 异常终态，均为 ProbeIndeterminate；verifier 日志截断
   不改变已有 normal exit 的处置，pytest telemetry 不决定分类。

Static-only evidence 保存本 Proposal 的 Attempt、Proposal、TyCheck、fingerprint、Slice、guidance 和 representative Proposal ID，但没有 `status`。它只影响 lower-bound 探测方向。首次请求就是 `promote` 时仍完成 prepare → static → runtime；不要求先有调度 observation。prepare terminal 不制造 region point；完整结果若含完整 static 事实，则首次 promotion 或跨 dependency 命中都登记当前 region。相同 point 登记幂等，promotion 更新直接 reference 并保留先前 static-only observation。

## 7. 一维定界

在固定 `current` 上搜索依赖 `d` 时只改变 `d`。只考虑 `C[d]` 中不高于 `current[d]` 的样本。没有样本时返回 `NO_PASS_IN_SEARCH_SPACE`。进入坐标时 current 已有直接 PASS；若 current 在 `C[d]` 中，仍通过直接认证入口登记当前 Slice。current 是首个候选时建立无 predecessor 边界。current 在 `C[d]` 外时只是已有 baseline/direct evidence 支持的虚拟 sentinel，不建立候选 observation、region point 或 runtime-backed request。

算法为每个坐标保存上一 sweep 的 `CoordinateBoundary` 位置，不保存旧 Slice 的拒绝 authority。若 history
floor 等于 current 且其 predecessor 仍是 `C[d]` 中的直接前驱，优先在**当前完整 context** 直接认证该
predecessor：Rejection 立即以 current PASS 建立新边界；PASS 把本次搜索上界降到 predecessor 后继续向下
定位；Indeterminate 立即停止。新 context 可以是该向量的首次请求；同 context 由 evaluator 自行命中完整
结果。没有有效 history，或 predecessor PASS 后，才进入最低候选/hint/平面定位；history 优先于 hint。

首次 probe 默认是最早候选；若有 hint，则使用不高于 hint 的最新有效候选。Hint 只改变顺序，不是硬下界。

- guidance/direct PASS：必要时再 probe 最早候选，并在最早候选与该点间定位第一个 PASS guidance；
- guidance/direct REJECTED：以已有直接 PASS 的 current 为高端定位第一个 PASS guidance；
- Probe Indeterminate：立即停止。

显式搜索空间不含 current 时，current 作为虚拟 PASS sentinel；它不加入 CandidateSnapshot `candidates`、不能作为活动候选 probe，也不能作为 floor 返回。它在其他坐标的 context 中只通过 `baseline_selection` 闭合 exact artifact。若只有虚拟 high 而空间内没有 PASS guidance，返回 `NO_PASS_IN_SEARCH_SPACE`。

索引距离不超过 `small_threshold`（默认 8）时升序线性 probe；更大区间使用确定 lower-bound 二分。该步骤允许读取 static-only guidance，但尚未提交结果。

## 8. Promotion 与边界提交

定位得到候选 floor 后，必须对该精确 Proposal 调用 `promote`：

- 直接 PASS：可以继续验证 predecessor；
- 直接 Rejection：把该点改为直接拒绝事实，在当前 Slice 重新定位；
- Indeterminate：立即停止。

若 floor 不是首个候选，其直接前驱也必须 promote：

- predecessor Rejection：形成 `CoordinateBoundary(floor, predecessor, failure_id)`；
- predecessor PASS：先前 floor 不是最低点，把直接通过的 predecessor 作为新 upper 后重新定位；
- predecessor Indeterminate：立即停止。

因此 boundary 的 floor 与 predecessor 都是当前完整 context 的直接 runtime observation。Region representative 的结果或 cheap observation 不能代替 promotion。Promotion 与 guidance 相反时，两条 observation 都保留：前者证明旧调度假设，后者是实际兼容性事实。普通 evaluator 的直接 `evaluate` 同样用于 floor/predecessor 认证；缺少 runtime-backed 方法不是 NONDETERMINISTIC。

## 9. 非单调检测

每次加入**直接** observation 后，检查相同 Slice 的已知点。若存在：

```text
v_low < v_high
PASS(v_low)
REJECTED(v_high)
```

立即返回 `NON_MONOTONIC` 并保存 `(v_low, v_high)`。Static-only guidance 不参加检测。稀疏 probe 不证明未观察 hole；`REJECTED* PASS*` 是 v1 的局部假设，不是范围认证。

## 10. 坐标不动点与终止

```text
current = B
repeat:
    changed = false
    for dependency in canonical order:
        floor, boundary = find_and_promote_floor(
            current, dependency, previous_boundary[dependency]
        )
        if floor < current[dependency]:
            current[dependency] = floor
            changed = true
        current_boundary[dependency] = boundary
    previous_boundary = current_boundary
until not changed
```

后续坐标降低可能改变早先 Slice，因此最终无变化 sweep 必须在最终上下文重新建立所有边界；旧 boundary
只提供 predecessor 位置提示，不能把旧 context evidence 接到新边界。predecessor PASS 也不能保留旧 floor
或跳过更低候选。候选有限且每次提交严格降低一个坐标，所以算法终止。依赖顺序、候选顺序、中点与
threshold 固定后，probe 顺序确定。

## 11. 输出与 validator

成功 `CellSuccess` 保存：

- frozen baseline、原 highest baseline direct PASS 与含 baseline selection 的 CandidateSnapshot；
- runtime-backed observations，包括无 disposition 的 static-only observation；
- regions 及其 direct runtime references；
- Rejection/Indeterminate 的 FailureRecord；
- 最终向量、该向量自身的 PassEvaluation、坐标边界和 sweep 数。

起点 observation 复用真实 highest Attempt、Proposal 与 PassEvaluation，vector 从该 Proposal 展开；Cell validator
要求它等于当前结果的 baseline roots。若 final 等于 `B`，复用同一 baseline direct PASS；否则 final vector、
exact-vector Attempt、Proposal、ProbePass、PassEvaluation 与完整 selected-candidate digest 必须形成同一精确
闭环。Schema 1 以 D014 定义的 refs 保存这些证据，validator 拒绝跨 Cell/context highest、跨 Slice region、
A-B-A 合并、static-only boundary、representative pass 复制和未直接测试的 final。

`observed_upper` 没有独立语义，不进入 Schema 1；final vector 与 final Evaluation 从 `final_proposal_ref` 唯一展开。跨 cell 覆盖、marker 投影和 apply 授权由 D001/D002 的报告模块决定，wire ownership 与规范验证由 D014 拥有。

## 12. 非目标

- 任意非单调空间中的全局最低点或 hole certification；
- static-only floor、region runtime 等价证明或 witness pass；
- 上界搜索、progressive budget；
- 任意用户选测或用失败子集冒充一次原命令 PASS；PF 可以用已知失败 nodeid 做拒绝预言并在首败后提前结束；
- 单 cell 并行 probe、cost-aware 或 best-first 顺序；
- flaky retry 和跨运行 Evaluation cache。

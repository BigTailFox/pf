# PF 单 cell 搜索算法

- **状态：** 现行
- **算法版本：** `direct-first-coordinate-guidance-v1`
- **最后核对：** 2026-09-08
- **产品输入与结果：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **静态事实与比较：** [D004](D004-pf-ty-enhancement.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **已归并决策：** [D011](../archived/designs/D011-pf-runtime-backed-static-search.md)、
  [D038](../archived/designs/D038-pf-static-guidance-authority.md)

本文是单个 package/cell 的坐标搜索、直接证据 fast path、局部静态 guidance、oracle
continuation、不变量与终止条件的唯一所有者。候选冻结由
[D037](D037-pf-candidate-search-policy.md) 定义；原始 TyCheck 与比较准入由 D004 定义；
`PASS` / `REJECTED` / `INDETERMINATE` 由 D005 定义。跨 cell 并发、报告合并和 apply 不属于本文。

静态事实可以改变探测顺序，但不能排除候选或更新兼容性边界。

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
- `S_hi`：本 Cell 最高版本的全局诊断基线，只用于 GLOBAL 比较与审计，不随 sweep/coordinate 改变。
- `S_slice`：本坐标静态阶段冻结的局部 guidance anchor，绑定当前直接 PASS 上端 `U`。
- `V_final`：最终不动点；等于成功结果的 `final_vector`。

prepare 成功后才有 Proposal。prepare Rejection/Indeterminate 只有 Attempt；相同向量在不同 cell、源码、策略或解析图中不是同一证据。

## 2. Slice 与两阶段窗口

固定一维切片为：

```text
Slice = (
  cell,
  source snapshot,
  source plan identity (routes + SEARCH mode),
  execution policy identity,
  active dependency,
  exact values of every other coordinate,
)
```

每个坐标先消费可定界的直接动态证据。仍未定界时执行纯静态二分，再进入 oracle continuation。
静态搜索窗口与兼容性（oracle）窗口分开；前者只产出 hint，不能排除后者的候选，也不能把静态
lo/hi 展示为已确认的通过/拒绝范围。

`S_hi` 回答“相对最高版本发生了什么静态变化”。`S_slice` 回答“相对本坐标直接 PASS 上端有什么
局部静态变化”。两种比较都使用多重集 subtraction。局部 unchanged 不证明相对最高版本 clean。

## 3. 核心不变量

1. `B` 有直接完整 PASS。`S_hi` 尽量由同一次 highest capture 形成；ty 不可用时全局比较不可用，不阻止 verifier。
2. 候选快照在本次 search 内不变；每个 snapshot 绑定与 exact probe 相同的 registry search route 和 SourcePlan identity，同时冻结 `C[d]` 与 `B[d]` 的 baseline selection，不包含 workspace HEAD/member version。
3. `current` 只能由该精确向量的原命令阶段直接 `test-command` pass 更新。
4. 静态观察没有 compatibility disposition；不能成为 ProbePass、ProbeRejection、boundary 或 final。
5. 只有 D005 的 Probe Rejection 能移动拒绝边界；Indeterminate 立即停止 cell。
6. floor 与 predecessor 在提交边界前必须是当前 context 的直接 runtime evidence。
7. 每次提交只严格降低一个坐标；每个 sweep 按规范依赖顺序覆盖全部坐标。
8. 静态探测、oracle 探测和 test 共享同一 cell/snapshot/execution context；GuidancePolicy 改变不单独改变动态身份。
9. 非单调判断只读取相同 Slice 中的直接 runtime observation，不读取静态 hint。
10. 同一 Run/Cell 的等价静态投影只执行一次 ty；精确动态 Proposal 的完整 Evaluation 在一次 search 内最多执行一次。物化重建、ty 去重与精确动态复用是三条独立规则。
11. `CandidateSnapshot` 只冻结 target 受管 project direct dependency 的 registry 搜索候选；workspace member 自身依赖、harness 与任意 transitive distribution 完全属于 uv resolution，不建立 PF catalog、coordinate 或 floor。
12. 一次 Verification Run 固定精确 uv profile、唯一 SEARCH SourcePlan 对象、release cutoff 与共享 cache；baseline、CandidateSnapshot freeze 与全部 exact probe 由 Runner 注入并消费该对象及其 identity。相同 project/environment resolution input 最多解析一次；PF 直接观察的 source 访问失败、registry artifact 不闭合或 managed coordinate 泄漏到 local/workspace source 仍为 Indeterminate，不回退到 development route，也不把 cache miss 解释为候选不存在。后端自由文本不等于这些直接事实，未知正常非零按 D005 拒绝 Attempt。
13. 搜索产生的每个完整向量都必须属于各坐标 `S[d]`；越界在 prepare/Attempt 前形成 Cell-scope `INTERNAL_INVARIANT`，不能回退为自由解析。

prepare rejection 与 verifier rejection 使用同一 ProbeRejection：可指导当前 Slice、形成直接
predecessor 边界并参与直接非单调检测。没有 Proposal 的静态 prepare failure 只返回 NO_HINT，
不进入 oracle 观测集，也不填充 FailedCaseSet。

重复观察与冲突承诺仅限实际存在的 seam：

| Seam | 观察/缓存范围 | 冲突含义 |
| --- | --- | --- |
| Cell search 直接结果 cache | 固定 Cell/source/execution policy，完整排序向量 key | 命中复用同次观察，不重复 prepare/verifier |
| TyCheckCache | 同一 Run 内规范静态投影与采集策略 | 共享原始 TyCheck/Unavailable；不缓存 guidance 解释 |
| CoordinateSearch | 同 Cell/Slice/candidate 的直接 disposition | 已观察 status 冲突按 NONDETERMINISTIC；两个 Reject 不比较归因 |
| ReportStore.merge | 同 generation/Cell 结果、同 evidence ID payload | 冲突直接拒绝报告合并；execution 与 search provenance mismatch 分开 |

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
  └── direct PASS + optional S_hi
        ↓
FREEZE CANDIDATE SNAPSHOTS
  ├── source/tool failure   -> Cell Indeterminate
  ├── empty search space    -> NO_PASS_IN_SEARCH_SPACE
  ├── invalid anchor/scope  -> SearchSpaceResolutionError（Run abort，D008）
  └── C[d] + required baseline selection
        ↓
ONE COORDINATE SEARCH FROM B
  ├── CoordinateSuccess     -> exact final PassEvaluation -> CellSuccess
  ├── ProbeIndeterminate    -> CellIndeterminate
  └── NON_MONOTONIC / NONDETERMINISTIC / NO_PASS -> CellSearchFailure
```

不再存在 static fixpoint、`V_static`、region/promotion、witness rejection 或第二轮 dynamic search。
Baseline capture 的同一次 TyCheck 是 `B` 相对自身的空增量事实，不重跑；`B` 的完整 PASS 由
HighestVersionVerifier 提供。SearchCoordinator 把完整 `HighestVersionPass` 注入 evaluator，
以 baseline vector 为 key 保存原 Attempt、Proposal 与 PassEvaluation；不伪造 exact-vector
request、selected-candidate digest 或 relaxed harness。

模块依赖与测试替换点只见 [D002 §7、§11](D002-pf-implementation.md#7-verification-modules)。

## 5. CoordinateSearch interface

```text
minimize(start, candidates, evaluator, hints=())
  -> CoordinateOutcome
```

普通 `VectorEvaluator.evaluate(vector)` 直接返回 Probe evidence。产品 evaluator 提供：

```text
lookup_direct_in_slice(SearchProbeRequest) -> ProbeEvidence | None
evaluate_in_slice(SearchProbeRequest)      -> ProbeEvidence
open_static_slice(vector, dependency, versions) -> StaticSlice | None
finish_coordinate()
record_direct_bound(...)
```

只读 lookup 不 prepare、ty 或 verifier。`evaluate_in_slice` 必须返回直接 Probe evidence，
不能返回静态比较结果。`open_static_slice` 至多每个坐标一次；失败或不可用返回 NO_HINT。
坐标结束时关闭未消费物化环境，保留完整结果和 Run-owned 原始静态事实。

`SearchProbeRequest` 把以下事实绑定在同一次实际 probe 上：

```text
vector
active_dependency
candidate_version
lower_version / upper_version / candidate_count
selection_reason
```

`candidate_version` 必须等于 `vector[active_dependency]`，并位于非空窗口内。窗口是本次
lower-bound 定位尚未排除的有序离散候选区间。CandidateSnapshot 之外的虚拟 baseline sentinel
只是已知 PASS evidence bound，不计入窗口端点或 `candidate_count`。静态窗口与 oracle 窗口
分别展示，终端不把静态 lo/hi 当成已确认边界。

`evaluate(start)` 必须返回并登记真实直接证据；产品 evaluator 对完整 baseline vector 返回原
highest Attempt/Proposal/Evaluation，不重新 prepare、static 或 runtime。`CoordinateSearch`
不再接受 known-pass 标记，也不维护 execution cache；它只去重报告 observation，并保存相同
Slice 的直接状态用于矛盾检测。

完整 lookup hit 不创建 Attempt、环境、验证活动或成功耗时。不同 Proposal、Cell 与 invocation
不共享可写环境或结果。同 ty key 不代表环境可以提前释放。

## 6. 每个坐标的两阶段执行

```text
direct fast path
  ├── 已有直接证据可定界 -> StaticPhaseSkip(direct-bound)，不获取 anchor
  └── 未定界
        ↓
local static phase（不运行 verifier）
  ├── 无合格 S_slice -> NO_HINT / StaticPhaseOmission
  ├── 静态二分 -> StaticHint(suspect, clean_neighbor)
  └── 静态失败 -> NO_HINT，不更新兼容性失败集合
        ↓
oracle continuation
  ├── suspect PASS -> 向低定位
  ├── suspect REJECTED -> 有意义时探测 clean neighbor
  └── 无 hint -> 原机械最低候选 / midpoint 路径
```

静态阶段可按需新增 prepare/ty probe，占 ty permit，不占 test permit。已建模的局部失败只影响
hint：prepare 失败、ty timeout/异常 exit/坏 JSON、输出截断、静态结果冲突或无法比较。不返回
ProbeIndeterminate，不把未知状态填成 regression 或 unchanged。同一不可用事实不在静态阶段反复重试。

oracle 阶段的 prepare/verifier 失败及动态 identity/cache 冲突仍遵循现行 D005/D012。该阶段的
ty 失败仍只是辅助诊断不可用。

## 7. 一维定界

在固定 `current` 上搜索依赖 `d` 时只改变 `d`。只考虑 `C[d]` 中不高于 `current[d]` 的样本。没有样本时返回 `NO_PASS_IN_SEARCH_SPACE`。进入坐标时 current 已有直接 PASS；若 current 在 `C[d]` 中，仍通过直接认证入口登记当前 Slice。current 是首个候选时建立无 predecessor 边界。current 在 `C[d]` 外时只是已有 baseline/direct evidence 支持的虚拟 sentinel，不建立候选 observation 或 runtime-backed request。

算法为每个坐标保存上一 sweep 的 `CoordinateBoundary` 位置，不保存旧 Slice 的拒绝 authority。若 history
floor 等于 current 且其 predecessor 仍是 `C[d]` 中的直接前驱，优先在**当前完整 context** 直接认证该
predecessor：Rejection 立即以 current PASS 建立新边界；PASS 把本次搜索上界降到 predecessor 后继续向下
定位；Indeterminate 立即停止。没有有效 history，或 predecessor PASS 后，才进入静态阶段或机械定位。
history 优先于外部 hint，也优先于静态 hint。跨坐标变更后旧 `S_slice` / hint 失效，必须打开新 slice。

首次 oracle probe 默认是最早候选；外部 hint 使用不高于 hint 的最新有效候选。有效静态 suspect
优先于普通 hint。Hint 只改变顺序，不是硬下界。

- suspect PASS：向低定位；
- suspect REJECTED：再选尚有意义的 clean 候选，clean 的真实拒绝才能推进动态下端；
- NO_HINT：原机械路径；
- Probe Indeterminate：立即停止。

显式搜索空间不含 current 时，current 作为虚拟 PASS sentinel；它不加入 CandidateSnapshot `candidates`、不能作为活动候选 probe，也不能作为 floor 返回。若只有虚拟 high 而空间内没有 PASS，返回 `NO_PASS_IN_SEARCH_SPACE`。

索引距离不超过 `small_threshold`（默认 8）时升序线性 probe；更大区间使用确定 lower-bound 二分。
静态二分有独立的对数查询上限，不把静态 bracket 传入 oracle 动态窗口。

精确直接证据或有效 predecessor 重验已能定界时，不获取 anchor，不新增 prepare/ty guidance 成本。
最终 no-change sweep 同样跳过静态阶段。

## 8. 边界提交

定位得到的 floor 与 predecessor 必须是当前完整 context 的直接 runtime observation。静态 hint
或比较结果不能代替直接证据。普通 evaluator 的直接 `evaluate` 同样用于 floor/predecessor 认证；
缺少 runtime-backed 方法不是 NONDETERMINISTIC。

## 9. 非单调检测

每次加入**直接** observation 后，检查相同 Slice 的已知点。若存在：

```text
v_low < v_high
PASS(v_low)
REJECTED(v_high)
```

立即返回 `NON_MONOTONIC` 并保存 `(v_low, v_high)`。静态矛盾只产生 NO_HINT / static-inconsistent，
不生成动态 NON_MONOTONIC。稀疏 probe 不证明未观察 hole；`REJECTED* PASS*` 是 v1 的局部假设，不是范围认证。

## 10. 坐标不动点与终止

```text
current = B
repeat:
    changed = false
    for dependency in canonical order:
        floor, boundary = find_floor(
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
threshold 固定后，probe 顺序确定。有限单调 oracle 上，任意静态状态分配或静态不可用都不改变 floor。

## 11. 输出与 validator

成功 `CellSuccess` 保存：

- frozen baseline、原 highest baseline direct PASS 与含 baseline selection 的 CandidateSnapshot；
- 直接 runtime-backed observations 与 oracle 选择时序；
- 静态阶段、omission、direct-bound skip 与 hint 端点审计；
- Rejection/Indeterminate 的 FailureRecord；
- 最终向量、该向量自身的 PassEvaluation、坐标边界和 sweep 数。

起点 observation 复用真实 highest Attempt、Proposal 与 PassEvaluation，vector 从该 Proposal 展开；Cell validator
要求它等于当前结果的 baseline roots。若 final 等于 `B`，复用同一 baseline direct PASS；否则 final vector、
exact-vector Attempt、Proposal、ProbePass、PassEvaluation 与完整 selected-candidate digest 必须形成同一精确
闭环。Schema 1 以 D014 定义的 refs 保存这些证据，validator 拒绝跨 Cell/context highest、静态事实伪装
boundary/final、以及未直接测试的 final。

`observed_upper` 没有独立语义，不进入 Schema 1；final vector 与 final Evaluation 从 `final_proposal_ref` 唯一展开。跨 cell 覆盖、marker 投影和 apply 授权由 D001/D002 的报告模块决定，wire ownership 与规范验证由 D014 拥有。

## 12. 非目标

- 任意非单调空间中的全局最低点或 hole certification；
- 静态 floor、静态等价证明或孤立 runtime interface missing；
- 上界搜索、progressive budget；
- 任意用户选测或用失败子集冒充一次原命令 PASS；PF 可以用已知失败 nodeid 做拒绝预言并在首败后提前结束；
- 单 cell 并行 probe、cost-aware 或 best-first 顺序；
- flaky retry 和跨运行 Evaluation cache；
- ty × testcase selection。

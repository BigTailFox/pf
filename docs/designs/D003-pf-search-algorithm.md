# PF 单 cell 搜索算法

- **状态：** 现行
- **算法版本：** `runtime-static-v1`
- **最后核对：** 2026-08-28
- **产品输入与结果：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **静态 transition 与 witness：** [D004](D004-pf-ty-enhancement.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **已归并决策：** [D011](../archived/designs/D011-pf-runtime-backed-static-search.md)

本文是单个 package/cell 的坐标搜索、static region、probe 顺序、不变量与终止条件的唯一所有者。候选冻结由 D001 定义；静态事实和 witness 由 D004 定义；`PASS` / `REJECTED` / `INDETERMINATE` 由 D005 定义。跨 cell 并发、报告合并和 apply 不属于本文。

## 1. 模型

受管依赖按规范化名称排序，每个依赖有一份按 D001 过滤并冻结的升序候选列表：

```text
D = [d1, ..., dn]
C[d] = [c0, ..., ck]
V = {d1: version, ..., dn: version}
```

- `B = V_hi`：当前声明的最高解析；开始搜索前已直接完整通过。
- `current`：每个坐标提交后的向量；始终有该精确向量的直接 `test-command` pass。
- `static frontier`：只有本 Proposal 的 TyCheck、increment 和 fingerprint，可用于调度但不是 PASS、boundary 或 current。
- `V_final`：最终不动点；等于成功结果的 `final_vector`。

prepare 成功后才有 Proposal。prepare Rejection/Indeterminate 只有 Attempt；相同向量在不同 cell、源码、策略或解析图中不是同一证据。

## 2. Slice 与 static region

固定一维切片为：

```text
Slice = (
  cell,
  source snapshot,
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
2. 候选快照在本次 search 内不变。
3. `current` 只能由该精确向量的直接 `test-command` pass 更新。
4. static-only observation 没有 disposition；不能成为 ProbePass、ProbeRejection、boundary 或 final。
5. 只有 D005 的 Probe Rejection 能移动拒绝边界；Indeterminate 立即停止 cell。
6. floor 与 predecessor 在提交边界前必须晋升为直接 runtime evidence。
7. 每次提交只严格降低一个坐标；每个 sweep 按规范依赖顺序覆盖全部坐标。
8. static、witness 和 test 共享同一 cell/snapshot/policy/baseline context。
9. 非单调判断只读取相同 Slice 中的直接 runtime observation，不读取 region guidance。
10. 同一精确 Proposal 的完整 Evaluation 在一次 search 内最多执行一次。
11. `CandidateSnapshot` 只冻结受管 project direct dependency 的搜索候选；harness 与任意 transitive distribution 完全属于 uv resolution，不建立 PF catalog、coordinate 或 floor。
12. 一次 Verification Run 固定精确 uv profile、source policy、release cutoff 与共享 cache；相同 project/environment resolution input 最多解析一次，但 source 访问失败仍为 Indeterminate，不把 cache miss 解释为候选不存在。

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
  └── candidates
        ↓
ONE RUNTIME-BACKED COORDINATE SEARCH FROM B
  ├── CoordinateSuccess     -> exact final PassEvaluation -> CellSuccess
  ├── ProbeIndeterminate    -> CellIndeterminate
  └── NON_MONOTONIC / NONDETERMINISTIC / NO_PASS -> CellSearchFailure
```

不再存在 static fixpoint、`V_static`、联合测试 fast path 或第二轮 dynamic search。Baseline capture 的同一次 TyCheck 是 `B` 的空增量静态事实，不重跑；`B` 的完整 PASS 由 HighestVersionVerifier 提供。

## 5. CoordinateSearch interface

```text
minimize(start, candidates, evaluator, hints=(), start_is_known_pass=False)
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

`start_is_known_pass=True` 时不重复评价 `B`。一次调用的 cache、observation、Slice 状态与 regions 全部 invocation-local；同一 CoordinateSearch 实例可以嵌套或并发调用。

Observation cache 以 `(active dependency, full vector)` 为 scope。相同向量在不同 active dependency 下不能借用另一 Slice 的 static-only guidance。

## 6. 一个 candidate probe

prepare 成功后顺序固定：

1. 取得该 Proposal 自身完整的 D004 static transition；
2. 若是当前 region 的首次直接 observation，按 D004 运行 witness 或 `test-command`，得到 ProbePass/Rejection/Indeterminate；
3. 若与相邻已观测点形成同 fingerprint region，且该连续 component 已有唯一一致的直接 PASS 或 REJECTED representative，可以只保存 `StaticOnlyEvidence`；
4. 若 component 内已有不同直接状态，不使用 guidance，直接运行 runtime evaluation；
5. Ty/witness/test 不完整均为 ProbeIndeterminate。

Static-only evidence 保存本 Proposal 的 Attempt、Proposal、TyCheck、fingerprint、Slice、guidance 和 representative Proposal ID，但没有 `status`。它只影响 lower-bound 探测方向。

## 7. 一维定界

在固定 `current` 上搜索依赖 `d` 时只改变 `d`。只考虑 `C[d]` 中不高于 `current[d]` 的样本。没有样本时返回 `NO_PASS_IN_SEARCH_SPACE`；若最早样本就是 current，直接得到该坐标边界。

首次 probe 默认是最早候选；若有 hint，则使用不高于 hint 的最新有效候选。Hint 只改变顺序，不是硬下界。

- guidance/direct PASS：必要时再 probe 最早候选，并在最早候选与该点间定位第一个 PASS guidance；
- guidance/direct REJECTED：以已有直接 PASS 的 current 为高端定位第一个 PASS guidance；
- Probe Indeterminate：立即停止。

显式搜索空间不含 current 时，current 作为虚拟 PASS sentinel；它不加入 CandidateSnapshot、不能被 probe，也不能作为 floor 返回。若只有虚拟 high 而空间内没有 PASS guidance，返回 `NO_PASS_IN_SEARCH_SPACE`。

索引距离不超过 `small_threshold`（默认 8）时升序线性 probe；更大区间使用确定 lower-bound 二分。该步骤允许读取 static-only guidance，但尚未提交结果。

## 8. Promotion 与边界提交

定位得到候选 floor 后，必须对该精确 Proposal 调用 `promote`：

- 直接 PASS：可以继续验证 predecessor；
- 直接 Rejection：把该点改为直接拒绝事实，在当前 Slice 重新定位；
- Indeterminate：立即停止。

若 floor 不是首个候选，其直接前驱也必须 promote：

- predecessor Rejection：形成 `CoordinateBoundary(floor, predecessor, failure_id)`；
- predecessor PASS：先前 floor 不是最低点，重新定位；
- predecessor Indeterminate：立即停止。

因此 boundary 的 floor 与 predecessor 都是直接 runtime observation。Region representative 的结果或 cheap observation 不能代替 promotion。Promotion 与 guidance 相反时，两条 observation 都保留：前者证明旧调度假设，后者是实际兼容性事实。

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
        floor, boundary = find_and_promote_floor(current, dependency)
        if floor < current[dependency]:
            current[dependency] = floor
            changed = true
until not changed
```

后续坐标降低可能改变早先 Slice，因此最终无变化 sweep 必须在最终上下文重新建立所有边界。候选有限且每次提交严格降低一个坐标，所以算法终止。依赖顺序、候选顺序、中点与 threshold 固定后，probe 顺序确定。

## 11. 输出与 validator

成功 `CellSuccess` 保存：

- frozen baseline、baseline direct PASS 与 CandidateSnapshot；
- runtime-backed observations，包括无 disposition 的 static-only observation；
- regions 及其 direct runtime references；
- Rejection/Indeterminate 的 FailureRecord；
- 最终向量、该向量自身的 PassEvaluation、坐标边界和 sweep 数。

若 final 等于 `B`，复用 baseline direct PASS；否则 final vector、Attempt、Proposal、ProbePass、PassEvaluation 必须形成同一精确闭环。Schema 1 以 D014 定义的 refs 保存这些证据，validator 拒绝跨 Slice region、A-B-A 合并、static-only boundary、representative pass 复制和未直接测试的 final。

`observed_upper` 没有独立语义，不进入 Schema 1；final vector 与 final Evaluation 从 `final_proposal_ref` 唯一展开。跨 cell 覆盖、marker 投影和 apply 授权由 D001/D002 的报告模块决定，wire ownership 与规范验证由 D014 拥有。

## 12. 非目标

- 任意非单调空间中的全局最低点或 hole certification；
- static-only floor、region runtime 等价证明或 witness pass；
- 上界搜索、partial tests、progressive budget；
- 单 cell 并行 probe、cost-aware 或 best-first 顺序；
- flaky retry 和跨运行 Evaluation cache。

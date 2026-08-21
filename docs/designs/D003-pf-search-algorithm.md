# PF 单 cell 搜索算法

- **状态：** 现行
- **算法版本：** v1
- **最后核对：** 2026-08-21
- **产品输入与结果：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **静态证据：** [D004](D004-pf-ty-enhancement.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)（待实现）

本文是单个 package/cell 的坐标搜索、probe 顺序、不变量与终止条件的唯一所有者。候选如何进入冻结快照由 D001 定义；静态诊断事实由 D004 定义；`PASS` / `REJECTED` / `INDETERMINATE` 与 FailureRecord 由 D005 定义；`check` 的声明验证不进入坐标搜索。D008 若为 check 引入 Declaration Attempt，仍不进入本文。跨 cell 并发、报告和 apply 不属于本文。

## 1. 模型

受管依赖按规范化名称排序：

```text
D = [d1, d2, ..., dn]
```

每个依赖拥有一个已经按 D001 过滤并冻结的升序精确候选列表：

```text
C[d] = [c0, c1, ..., ck]
```

搜索向量只列受管直接依赖：

```text
V = {d1: version, d2: version, ...}
```

Evaluator 实际观察的是由该向量请求的 Probe Attempt。prepare 成功后才有完整 Proposal；prepare Rejection/Indeterminate 没有 Proposal。相同向量在不同 cell、源码快照、解释器或策略下不是相同 Attempt，成功解析图不同也不是相同 Proposal。

核心向量：

- `B` / `V_hi`：当前声明按最高允许版本解析并完整通过的 baseline；
- `V_static`：增量静态 Evaluator 的坐标不动点；
- `V_final`：完整 Evaluator 的坐标不动点。

`V_static` 和 `V_final` 都是完整向量，不是各依赖彼此独立的版本事实。

## 2. 假设与不变量

v1 假设一个固定一维切片中的确定兼容性结果为：

```text
REJECTED* PASS*
```

其中 `REJECTED` 只包括 D005 允许建立边界的 Probe Rejection；Probe Indeterminate 不属于拒绝侧。

算法维护：

1. `B` 具有直接完整 `PASS` 证据，并携带 D004 冻结的同一静态基线。
2. 候选快照在本次 search 内不变化。
3. 每次提交后的 current 都由当前阶段 Evaluator 直接证明通过。
4. 只有 Probe Rejection 可以移动拒绝边界；Probe Indeterminate 立即停止当前 cell。
5. 提交只严格降低一个坐标，算法不主动升高。
6. 每个 sweep 按规范依赖顺序覆盖全部坐标。
7. static、fast path 和 dynamic 阶段共享本 cell 的同一静态基线。
8. 同一精确 Proposal 在 D002 定义的相同 Evaluation context 中复用 Evaluator 结果。
9. 同切片观测冲突时立即停止，不猜测或跳过 unknown hole。

## 3. SearchCoordinator 状态机

单 cell 顺序固定为：

```text
BASELINE ATTEMPT B = V_hi
  ├── BaselineRejection     -> 终止 cell
  ├── BaselineIndeterminate -> 终止 cell
  └── PASS + D004 STATIC BASELINE
        ↓
FREEZE CANDIDATE SNAPSHOTS
        ↓
STATIC COORDINATE FIXPOINT
        ↓
FULL EVALUATE V_static
  ├── PASS                              -> SUCCESS
  ├── ProbeRejection(TEST_FAILURE)      -> DYNAMIC COORDINATE FIXPOINT FROM B
  ├── ProbeIndeterminate               -> 终止 cell
  └── 与已缓存结果冲突                  -> NONDETERMINISTIC
```

候选发现发生在 baseline 完整通过之后。此时尚无 Probe Attempt；来源故障形成带 `CellFailureScope` 和 FailureRecord 的 Indeterminate cell result，不得虚构 requested vector。过滤后候选为空保存为 `NO_PASS_IN_SEARCH_SPACE`。

用于捕获静态基线的那次检查也是 `B` 的 static pass 事实，不重跑。Baseline 的确定安装/build/harness/test failure 产生 Baseline Rejection；工具、来源或不完整执行产生 Baseline Indeterminate。完整分类由 D005 定义，项目既有静态诊断是否被接受由 D004 定义。

## 4. CoordinateSearch interface

D002 定义的 interface 为：

```text
minimize(start, candidates, evaluator, hints=(), start_is_known_pass=False)
  -> CoordinateOutcome
```

`start_is_known_pass` 为真时把 start 当作已有 PASS，不重新评估。SearchCoordinator 在 static 与 dynamic 阶段都传入真值，因为 `B` 已有直接完整通过证据。

开始时先读取或直接 probe `start`。它必须复用 SearchCoordinator 已建立的 `PASS`；若同 context 得到 Rejection 则与 baseline 冲突并返回 `NONDETERMINISTIC`，得到 Probe Indeterminate 则停止当前 cell。

每个 observation 保存 Attempt、被探测的完整向量、当前坐标（若有）和 `ProbePass | ProbeRejection | ProbeIndeterminate`。缓存按完整规范向量命中；同一向量可能在不同坐标上下文中记录 observation，但不会重复调用 Evaluator。

## 5. 一维定界

在固定 `current` 上为依赖 `d` 搜索时，只改变 `d`：

```text
P(x) = evaluator(current with d = x)
```

旧 observation 只有在其他坐标完全相同的切片中才能用于单调性判断。

### 5.1 有效点与首次 probe

只考虑 `C[d]` 中不高于 `current[d]` 的样本。没有样本时返回 `NO_PASS_IN_SEARCH_SPACE`；若最早样本就是 current，直接得到该坐标边界。

首次 probe：

- 无 hint：最早候选；
- 有 hint：不高于 hint 的最新有效候选；若不存在则仍用最早候选。

Static 阶段不传 hint，因此从最早候选开始。Dynamic 阶段把 `V_static[d]` 作为 hint。Hint 只改变顺序，不是硬下界。

### 5.2 Hint 通过

若 hint `PASS`：

- hint 已是最早候选时直接返回；
- 否则 probe 最早候选；
- 最早也通过则返回最早；
- 最早 Rejected 则在 `[earliest, hint]` 中定位第一个通过样本。

### 5.3 Hint Rejection

若 hint 是 Probe Rejection，以已知通过的 `current[d]` 作为高端，并在 `[hint, current[d]]` 中定位第一个通过样本。若 hint 是 Probe Indeterminate，立即停止当前 cell。

令本切片内不高于 current 的冻结候选为：

```text
C_valid = [c0, ..., c(n-1)]
```

当 current 在 `C_valid` 中时，高端是对应的真实候选索引。当 current 位于显式搜索空间之外时，算法使用索引 `n` 的虚拟 PASS sentinel：

```text
Q(i) = P(ci)       for 0 <= i < n
Q(n) = P(current)  = PASS
```

sentinel 只保存“空间外 current 已知通过”这一边界事实，不加入 CandidateSnapshot、不能被 probe，也永远不能作为 floor 返回。定位结束时若 `high_index == n`，说明搜索空间内没有已知 PASS，结果为 `NO_PASS_IN_SEARCH_SPACE`。

### 5.4 线性与二分

已知索引区间满足 `low = REJECTED`、`high = PASS`；`low` 始终是真实候选，`high` 可以是真实候选或 §5.3 的虚拟索引 `n`。

当两端索引距离不超过 D002 的 `small_threshold` 时，从 `low` 的后继开始升序线性 probe 所有真实候选，返回首个 `PASS`。若只剩虚拟 high 而没有真实 PASS，返回 `NO_PASS_IN_SEARCH_SPACE`。

更大区间使用确定的 lower-bound 二分：

```text
while high_index - low_index > 1:
    middle = floor((low_index + high_index) / 2)
    PASS(middle) -> high_index = middle
    REJECTED(middle) -> low_index = middle

if high_index == n:
    return NO_PASS_IN_SEARCH_SPACE
return C_valid[high_index]
```

当 high 是 sentinel 时，循环计算出的 middle 仍严格小于 `n`，所以只 probe 真实候选。Probe Indeterminate 在 probe 时已经终止，不能被当成二分拒绝侧。

### 5.5 返回边界

返回前必须保存：

- floor 的直接 `PASS`；
- 若 floor 不是首个候选，其直接前驱的 Probe Rejection 及 `failure_id`；
- 全部证据来自相同切片。

若 floor 是首个候选，boundary 的 predecessor 为空。

## 6. 非单调检测

每次加入 observation 后，算法检查相同依赖、相同其他坐标的所有已知点。若存在：

```text
v_low < v_high
PASS(v_low)
REJECTED(v_high)
```

立即返回 `NON_MONOTONIC`，记录依赖和 `(v_low, v_high)` 反例。

稀疏 probe 无法证明没有未观测 hole。v1 的 `FAIL* PASS*` 是明确假设，不是范围认证。

## 7. 坐标不动点

```text
current = start

repeat:
    changed = false
    round_boundaries = {}

    for d in canonical_dependency_order:
        floor, boundary = find_floor(current, d)
        round_boundaries[d] = boundary
        if floor < current[d]:
            current[d] = floor
            changed = true

until changed == false

return current, boundaries from final unchanged sweep
```

后续坐标降低可能让早先坐标出现新的更低通过点，所以一轮不够。最终无变化 sweep 在最终上下文中重新建立全部坐标边界。

### 7.1 终止

候选有限，每次提交严格降低一个坐标。令 `H` 为所有坐标从起始位置最多可下降的候选步数之和，则最多提交 `H` 次、最多执行 `H + 1` 个 sweep。

依赖顺序、候选顺序、中点公式和 threshold 固定后，probe 顺序确定。多个坐标最小点可能存在；PF 返回规范顺序到达的固定点。

## 8. 两阶段搜索

### 8.1 Static fixpoint

```text
V_static = minimize(
    start=B,
    candidates=frozen_candidates,
    evaluator=StaticEvaluator,
)
```

`V_static` 是 D004 增量静态判据在最终上下文中的坐标不动点。Static 创建的同一 Proposal 环境/证据可以被 full 阶段晋升，但 static pass 本身不是完整通过。

### 8.2 联合测试 fast path

对 `V_static` 执行一次完整 Evaluation。若 `PASS`，直接成功。

该 fast path 安全：完整 `PASS` 的必要条件是 D004 static pass；若最终上下文中存在任一更低且完整通过的单坐标版本，它也必须通过 static，和 static fixpoint 矛盾。

在没有 cache conflict 的正常 fast path 中，最多测试两个不同向量：baseline 与 `V_static`；若两者相同则复用 baseline 证据。其余 probe 只做环境解析/安装和静态检查。

### 8.3 Dynamic fixpoint

只有 fast path 得到 cause 为 `TEST_FAILURE` 的 Probe Rejection 才进入 dynamic 搜索：

```text
V_final = minimize(
    start=B,
    candidates=frozen_candidates,
    evaluator=FullEvaluator,
    hints=V_static,
)
```

Dynamic 必须从完整通过的 `B` 开始，不能从被 Rejected 的 `V_static` 提交。每个候选先满足 D004 static 判据，再运行完整测试；prepare/build/harness/static/test Rejection 都进入拒绝侧，Indeterminate 停止；不使用 partial tests。结束时 `V_final` 已具有直接 `PASS`，不额外重跑。

## 9. 输出

成功 `CellSuccess` 保存：

- 冻结静态基线和完整通过 baseline；
- CandidateSnapshot；
- static probe、必要时的 dynamic probe；
- 每个 rejected/indeterminate Attempt 的结构化 disposition、cause 与 FailureRecord；
- 最终向量及完整 Evaluation；
- 最终切片的每个坐标边界；
- sweep 数与候选/策略 identity。

`observed_upper` 始终为 `null`，表示 v1 没有执行上界搜索。

算法成功只证明单 cell 搜索完成。跨 cell 完整覆盖、marker 投影和 apply 授权由 D001/D002 的报告模块决定。

## 10. 非目标

- 任意非单调空间中的真正最低点；
- hole 发现后的 refinement 或安全 `!=`；
- 上界搜索；
- failure attribution、partial tests 或 progressive budget；
- 单 cell 并行 probe、cost-aware 或 best-first 顺序；
- 依赖组合搜索；
- flaky 重试与跨运行 Evaluation cache；
- certification mode（逐一验证范围内所有候选）。

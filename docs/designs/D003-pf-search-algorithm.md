# PF 搜索算法

- **状态：** 草案
- **产品契约：** [D001](D001-pf.md)
- **实现设计：** [D002](D002-pf-implementation.md)

本文定义 PF v1 的下界搜索。算法只处理一个 package/cell；跨 cell 并行和结果投影由外层模块负责。

## 1. 结果承诺

PF 在冻结的候选集合中寻找一个完整通过的坐标最小向量。

“坐标最小”表示：固定最终向量中的其他直接依赖后，没有任何单个受管依赖还能降低到搜索空间中的更早样本并通过完整 Evaluator。

结果不是依赖笛卡尔积的全局最小值，也不证明未探测版本或其他依赖组合兼容。

v1 假设同一一维切片中的结果为：

```text
FAIL* PASS*
```

该假设允许线性或二分定界。若实际观测与它冲突，算法立即停止，不尝试修复非单调区间。

## 2. 模型

### 2.1 Cell

```text
cell = (
    package,
    exact_target_triple,
    cpython_minor,
    extra_surface,
)
```

每个 cell 独立解析、安装、测试和报告。

### 2.2 受管依赖

受管依赖集合为：

```text
D = [d1, d2, ..., dn]
```

依赖按规范化分发包名称排序。该顺序属于策略身份，所有 sweep 都使用同一顺序。

### 2.3 候选

每个依赖 `d` 有一个冻结的升序精确候选列表：

```text
C[d] = [c0, c1, ..., ck]
```

列表已经应用 search-space、granularity、来源、构件、预发布、yanked、已有上界和排除项。

现有 `>=`/`>` 不限制向下搜索。候选不得高于 baseline 中该依赖的精确版本。

### 2.4 向量与 Proposal

```text
V = {
    d1: version,
    d2: version,
    ...
}
```

`V` 只描述受管直接依赖。真正被评估的是 Proposal，它还包含 cell、固定和非受管声明、完整解析图、来源及构件身份。

同一个 `V` 在不同 cell、源码快照或解析图中不是同一个 Proposal。

### 2.5 核心向量

- `B`：当前声明按最高允许版本解析并完整通过的 baseline；
- `V_static`：StaticEvaluator 搜索得到的静态通过不动点；
- `V_final`：FullEvaluator 搜索得到的完整通过不动点。

`V_static` 是完整向量，不是各依赖相互独立的全局硬下界。

## 3. 不变量

搜索始终维护以下不变量：

1. `B` 已由 FullEvaluator 直接验证为 `PASS`。
2. CandidateSnapshot 在搜索期间不可变化。
3. 每次提交后的当前向量都被当前阶段的 Evaluator 直接验证为通过。
4. 只有 `PASS`、`STATIC_FAIL` 和 `TEST_FAIL` 可以建立边界。
5. 每次提交至少严格降低一个坐标，永不主动升高。
6. 每轮扫描全部受管依赖；启发式不能排除依赖。
7. 结果相互矛盾时停止，不用猜测恢复单调性。

这些不变量使搜索可终止、可缓存，并保证最终向量至少具有直接通过证据。

## 4. 候选快照

### 4.1 建立顺序

单个 cell 依次执行：

1. 解析当前声明，得到 baseline Proposal 和精确向量 `B`；
2. 完整验证 `B`；
3. 以 `B` 为 search-space 锚点查询 index；
4. 生成并冻结 CandidateSnapshot。

Baseline 失败时不会建立搜索候选，也不会尝试旧版本恢复。

### 4.2 搜索空间

标量 `search-space`：

```text
all            全部合格历史版本
current-major  与 B[d] 相同 major
current-minor  与 B[d] 相同 minor
```

默认值为 `all`。

列表形式为逐依赖 PEP 508 版本范围。未列出的受管依赖使用 `all`。

列表条目只允许名称和非空 specifier；extras、marker、URL 和来源无效。

显式范围可以不包含 `B[d]`。`B[d]` 仍是已知通过锚点，但不自动成为搜索空间内的 floor。

标量 search-space 与 granularity 的合法组合为：

```text
all            × major | minor | patch
current-major  × minor | patch
current-minor  × patch
```

### 4.3 搜索粒度

`release-granularity` 支持：

- `patch`：每个合格精确 release 都是样本；
- `minor`：每个 minor 系列取冻结时最新的合格稳定 patch；
- `major`：每个 major 系列取冻结时最新的合格稳定 patch。

样本始终保留精确 PEP 440 版本。通过 `1.24.4` 只能生成 `>=1.24.4`。

系列 key 包含 PEP 440 epoch 和 release tuple；缺失分量补零。Pre/dev 默认过滤，post release 归入基础 patch 系列。

### 4.4 冻结

CandidateBuilder 规范化并排序所有样本，保存系列到精确代表版本的映射。

搜索开始后不刷新 index。运行中发布的新版本不进入本次搜索。

来源身份或构件 hash 与快照不一致时返回 `SOURCE_CHANGED`，不得继续使用旧边界。

## 5. Evaluator

### 5.1 StaticEvaluator

```text
Proposal
  ↓ resolve/install
  ↓ ty
STATIC_PASS | STATIC_FAIL | 非证据状态
```

`STATIC_PASS` 环境保留到本次 search 结束，可以被相同 Proposal 的 FullEvaluator 晋升一次。

### 5.2 FullEvaluator

```text
Proposal
  ↓ STATIC_PASS cache?
  ├── no  → StaticEvaluator
  └── yes
  ↓ full test-command
PASS | STATIC_FAIL | TEST_FAIL | 非证据状态
```

FullEvaluator 不运行测试子集。`STATIC_FAIL` 会短路测试，但仍是完整兼容性判据的失败结果。

### 5.3 非证据状态

以下状态立即终止当前 cell：

```text
UNAVAILABLE
BUILD_UNAVAILABLE
UNRESOLVABLE
HARNESS_ERROR
SOURCE_ERROR
TOOL_ERROR
TIMEOUT
```

它们不能被折叠为 `FAIL`，不能推进线性或二分边界，也不能被跳过。

### 5.4 缓存与环境

缓存 key 使用完整 Proposal 和 Evaluator 策略，不只使用受管版本向量。

同一个精确 Proposal 的完整测试只执行一次。测试后的环境视为污染，不再作为干净环境复用。

若显式重跑得到冲突结果，状态为 `NONDETERMINISTIC`，立即停止并禁止 apply。

## 6. Baseline

Baseline 使用当前源码快照、当前声明和正常最高版本解析策略。

它不读取 workspace lock、操作者 `.venv` 或既有安装状态。

```text
FullEvaluator(B)
  ├── PASS         → 继续
  ├── STATIC_FAIL  → BASELINE_FAILED
  ├── TEST_FAIL    → BASELINE_FAILED
  └── 其他状态      → INDETERMINATE
```

`BASELINE_FAILED` 表示仓库在当前声明的正常环境中不能通过。PF 立即停止，不承担版本恢复职责。

## 7. 一维定界

静态和动态阶段共享 `find_floor`。差别只有 Evaluator 和首次 probe hint。

### 7.1 输入

`find_floor(V, d, C[d], hint, evaluator)` 的输入满足：

- `V` 已由当前 Evaluator 验证为通过；
- 除 `d` 外的所有坐标保持不变；
- `C[d]` 按版本升序冻结；
- `hint` 是候选中的优先 probe，可为空。

定义：

```text
P(x) = evaluator(V with d = x)
```

所有 `P(x)` 必须属于同一个精确切片。其他坐标、cell、源码、来源或策略变化后，旧观测不能复用为该切片边界。

### 7.2 首次 probe

静态阶段优先 probe `C[d]` 的最早样本。

动态阶段优先 probe 不高于 `V[d]` 的 `V_static[d]`；若它不在当前有效候选内，则使用最近的更低有效候选。

Hint 只决定 probe 顺序，不是硬下界。

### 7.3 Hint 通过

若 `P(hint) = PASS`，继续检查 hint 以下的候选。

先探测最早样本。若最早样本通过，直接返回它；否则得到：

```text
low = FAIL
high = PASS
```

随后在 `[low, high]` 中定位第一个通过样本。

### 7.4 Hint 失败

若 hint 是兼容性失败，则当前向量提供已知通过高端：

```text
low = hint FAIL
high = V[d] PASS
```

在单一通过区间假设下，hint 以下候选也属于失败侧，因此只需搜索 `[hint, V[d]]`。

若当前通过高端不在显式 search-space 中，搜索可以用它定界，但不能把它报告为该空间内的 floor。

### 7.5 定位

候选数不超过实现常量 `small_threshold` 时，按升序线性查找第一个通过样本。

更大区间使用标准 lower-bound 二分：

```text
while high 与 low 不相邻:
    mid = 中间候选
    PASS(mid) → high = mid
    FAIL(mid) → low = mid

return high
```

返回前必须具有：

- 返回样本的直接通过证据；
- 它是首个样本，或其直接前驱具有兼容性失败证据；
- 所有证据属于同一切片。

### 7.6 空搜索空间

若显式 search-space 中所有样本都兼容性失败，而唯一通过高端位于空间之外，返回 `NO_PASS_IN_SEARCH_SPACE`。

PF 保存失败边界并停止该 cell，不用空间外 baseline 冒充 floor。

### 7.7 非单调检测

每加入一个观测，都检查同一切片中的已知点。

若存在：

```text
v_low < v_high
PASS(v_low)
FAIL(v_high)
```

立即返回 `NON_MONOTONIC`。其中 `FAIL` 只包括当前 Evaluator 的兼容性失败，不包括非证据状态。

稀疏 probe 不能发现所有未观测 hole。v1 将单一通过区间明确作为搜索假设，而不是完整认证结论。

## 8. 坐标不动点

`minimize_coordinates` 对全部依赖反复调用 `find_floor`。

```text
current = start

repeat:
    changed = false

    for d in canonical_dependency_order:
        next = find_floor(current, d, ...)

        if next < current[d]:
            current[d] = next
            assert evaluator(current) == PASS
            changed = true

until changed == false

return current
```

后续坐标降低后，较早坐标可能出现新的更低通过版本。因此一轮扫描不够，必须从头重复直到整轮无变化。

最终无变化的一轮在最终上下文中重新建立每个坐标的边界。

### 8.1 终止

候选集合有限，每次提交都严格降低至少一个坐标，算法从不主动升高坐标。

令：

```text
H = Σ 每个坐标从起始位置最多可下降的样本数
```

最多发生 `H` 次提交，最多有 `H + 1` 轮 sweep。缓存避免在同一 Proposal 上重复执行相同 Evaluator。

### 8.2 确定性

依赖顺序、候选顺序、中点选择和 small threshold 固定后，probe 顺序确定。

多个坐标最小点可能同时存在。PF 返回规范顺序产生的固定点，并在策略身份中记录该顺序。

算法版本、中点规则和 small threshold 也进入策略身份，因为它们会影响实际 probe 集合和可观察到的非单调反例。

## 9. 阶段一：静态定界

Baseline 的完整通过蕴含静态通过，因此可以作为 StaticEvaluator 的起点。

```text
V_static = minimize_coordinates(
    start=B,
    evaluator=StaticEvaluator,
    first_probe=earliest_candidate,
)
```

该阶段覆盖全部受管依赖，不做失败归因。

`V_static` 是 StaticEvaluator 在最终上下文中的坐标不动点。更低版本的 `STATIC_FAIL` 仍是上下文证据，不是独立包版本事实。

StaticEvaluator 创建的环境暂不删除，为下一阶段的精确 Proposal 复用。

## 10. 阶段二：联合测试 fast path

静态搜索结束后，直接评估整个猜测向量：

```text
FullEvaluator(V_static)
```

若结果为 `PASS`，立即返回 `V_static`。

这是安全的 fast path：`V_static` 已静态坐标最小，而完整兼容性要求先静态通过；因此任何单坐标更低的完整通过版本都必须先违反静态不动点。

正常 fast path 只需要两次完整测试：

```text
1 × FullEvaluator(B)
1 × FullEvaluator(V_static)
```

其余 probe 只运行 resolve/install 和 `ty`。

若结果为 `TEST_FAIL`，进入动态坐标搜索。

若同一 Proposal 此前 `STATIC_PASS`、此时却 `STATIC_FAIL`，或出现其他冲突状态，则按 `NONDETERMINISTIC` 或不确定错误停止。

## 11. 阶段三：动态坐标搜索

动态阶段必须从完整通过的 `B` 开始，不能从测试失败的 `V_static` 开始提交。

```text
V_final = minimize_coordinates(
    start=B,
    evaluator=FullEvaluator,
    first_probe=V_static,
)
```

每个动态候选都需要完整兼容性判据：

- 已有 `STATIC_PASS` 缓存时直接运行完整测试；
- 未命中时先按需运行 `ty`；
- 静态通过后运行完整 `test-command`；
- 不使用 partial tests。

`V_static[d]` 只是首次 probe hint。若最终上下文允许更低版本，`find_floor` 会重新打开该坐标。

每次提交后的 current 都有完整 `PASS`。搜索结束时，`V_final` 已经是最终完整通过证据，不再额外重跑同一个 Proposal。

## 12. 高层状态机

```text
SNAPSHOT
  ↓
RESOLVE BASELINE
  ↓
FULL EVALUATE B
  ├── compatibility fail → BASELINE_FAILED
  ├── non-evidence       → INDETERMINATE
  └── PASS
        ↓
FREEZE CANDIDATES
        ↓
STATIC COORDINATE FIXPOINT
  ├── NON_MONOTONIC / NO_PASS_IN_SEARCH_SPACE / INDETERMINATE
  └── V_static
        ↓
FULL EVALUATE V_static
  ├── PASS      → SUCCESS
  ├── TEST_FAIL → DYNAMIC COORDINATE FIXPOINT
  └── other     → STOP
                    ↓
              V_final PASS
                    ↓
                 SUCCESS
```

任何阶段达到总时限，都停止调度、保存不完整报告并禁止 apply。

## 13. 成功证据

成功的 dependency/cell 必须记录：

- baseline Proposal 和完整 `PASS`；
- 冻结 CandidateSnapshot；
- 静态和动态 probe 轨迹；
- 每个 floor 的精确版本；
- 最终完整 `PASS` 向量；
- 最终切片中的坐标边界；
- 搜索空间、粒度和规范依赖顺序；
- 来源、构件、解释器、工具和测试策略身份。

若 floor 是搜索空间中的首个样本，报告记录“没有更早候选”。

若 floor 不是首个样本，报告记录同一最终切片中直接前驱的 `STATIC_FAIL` 或 `TEST_FAIL`。

`observed_upper` 固定为 `null`，含义是 v1 未执行上界搜索。

## 14. Apply 条件

算法成功不自动授权单个 cell apply。外层投影必须确认：

- 所有目标 cell 覆盖完整；
- 每个 cell baseline 和最终向量完整通过；
- 所有搜索均到达不动点；
- 没有 `NON_MONOTONIC`、`NONDETERMINISTIC`、`NO_PASS_IN_SEARCH_SPACE` 或非证据状态；
- 项目、候选、来源、构件和策略身份未漂移；
- 精确 floor 可以由受支持 marker 表示。

Apply 写入实际验证的 `>=exact_version`。它不生成或收紧上界，不修改 `<`、`<=`、`!=`、来源和无关字段。

受限 search-space 可以 apply，但报告必须明确结果只是该空间内的 floor。

## 15. 成本

令 `m_d = |C[d]|`。

单次、上下文固定的一维定界通常需要：

```text
1 次首次 probe + O(log m_d) 次二分 probe
```

小候选集改用 `O(m_d)` 线性探测。

若总下降高度为 `H`，坐标算法最多执行 `H + 1` 轮 sweep。实际成本主要由解析、环境构建和完整测试决定，而不是纯搜索运算。

fast path 将完整测试固定为 baseline 和 `V_static` 两次。动态路径的每个新 Proposal 都执行完整测试，但精确缓存避免重复。

## 16. 后续工作

以下能力不属于 v1：

- 枚举或证明任意非单调空间中的真正最低版本；
- 发现 hole 后继续 refinement；
- 自动生成安全 `!=`；
- 自动搜索不兼容上界；
- failure attribution 和依赖置信度；
- 测试用例关联、partial tests 和 progressive budget；
- cost-aware、best-first 或并行单 cell probe；
- 局部 dependency interaction 和组合搜索；
- static-only 独立模式；
- flaky retry 和重复确认；
- 跨运行持久化 Evaluation 与 Proposal 环境；
- 安全环境克隆、重置和增量依赖变更；
- certification mode，对范围内所有候选逐一验证。

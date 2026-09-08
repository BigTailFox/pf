# C004 — PF 尊重证据的乐观单调搜索

- **状态：** 开放
- **日期：** 2026-09-08
- **性质：** 非规范性 Concept，保存 PF 1.x 对单坐标单调性的设想与待证问题，不授权实施
- **来源：** 将一维 `REJECTED* PASS*` 从正确性假设改为乐观搜索假设的开发构想
- **现行对照：** [D001 §1](../designs/D001-pf.md#1-结果承诺)、[D001 §9](../designs/D001-pf.md#9-v1-非目标)、
  [D003 §9](../designs/D003-pf-search-algorithm.md#9-非单调检测)、
  [D003 §12](../designs/D003-pf-search-algorithm.md#12-非目标)
- **相关 owner：** [D001](../designs/D001-pf.md)、[D002](../designs/D002-pf-implementation.md)、
  [D003](../designs/D003-pf-search-algorithm.md)、[D004](../designs/D004-pf-ty-enhancement.md)、
  [D006](../designs/D006-pf-cli-enhancement.md)、[D014](../designs/D014-pf-report-schema.md)
- **相关构想：** [C001](C001-pf-multi-resolution-coordinate-search.md) 仍按现行立刻
  `NON_MONOTONIC` 终止来写树 refinement；本文讨论的是一维证据模型，二者不互相替代。
  [C005](C005-pf-check-first-lifecycle.md) 把「假设降成本、证据定真值」用到声明下界在开发
  周期中的寿命，不替代本文的单调性 refinement
- **权威先例：** [D038](../archived/designs/D038-pf-static-guidance-authority.md) 已将静态事实降为
  guidance；稳定规则由 D003/D004 拥有。本文把同一原则用到单调性假设
- **实验背景：** [E001](../experiments/E001-pf-self-bootstrap-validation-contract.md)、
  [E006](../experiments/E006-requests-complete-search.md)、
  [E008](../experiments/E008-mkdocs-complete-search.md)、
  [E009](../experiments/E009-mkdocs-static-guidance.md) 显示真实仓库常有较强局部单调性，
  不证明单调性普遍成立，也不构成非单调 refinement 的验收

本文不定义当前或已接受的目标契约。下文的模型、声明编码、报告区分和复杂度目标都是待验证设想。
取得足以改写 D001/D003 的依据后，应另建规范性 Design 并获得接受，再建立 Plan。

下文的 `REJECTED` 是 [Disposition](../../CONTEXT.md) 中的 Probe Rejection；示意图里若出现 `FAIL`，
含义与 `REJECTED` 相同。`INDETERMINATE` 与同点冲突的 `NONDETERMINISTIC` 不在本构想的放宽范围内。

## 1. 构想与现行对照

PF 当前一维坐标搜索依赖：

```text
REJECTED* PASS*
```

固定其他坐标后，某个依赖的有序候选被假定只有一个兼容边界。该假设使 binary / guided search
能把单坐标探测从线性降到近似对数。PF 自身、Requests、MkDocs 的完整 search 都表现出较强局部单调性，
因此它有明确的工程价值。

真实依赖仍可能因上游 regression、后续 bugfix、临时 workaround、API 加入/破坏/恢复，或
Python / platform 特定行为，形成：

```text
old -------------------------------------------------------------- new
REJECTED  PASS  PASS  REJECTED  PASS  PASS
```

现行契约把单调性当作不可违反的正确性假设：

| 现行规则 | 位置 | 后果 |
| --- | --- | --- |
| 每个一维 Slice 假设 `REJECTED* PASS*`；发现反例即以 `NON_MONOTONIC` 停止并禁止 apply | D001 §1 | 有已观察 hole 的坐标拿不到可应用 floor |
| 同 Slice 出现 `PASS(v_low)` 且 `REJECTED(v_high)` 立即终止并保存反例 | D003 §9 | 证据被保留，搜索不继续 |
| 非单调区间细化、version-hole 认证、自动 `!=` 或上界发现列为 v1 非目标 | D001 §9、D003 §12 | 不把 hole 投影进声明，而是直接失败 |

v1 并不丢弃已观察反例：它停止并阻止 apply。缺口是响应方式。一旦出现
`PASS < REJECTED`，即使高端仍有直接 PASS，Cell 也无法提交一个与已观察 hole 不冲突的
`>=floor`。与此同时，未观察 hole 仍可能被乐观二分跳过——这是非穷举搜索的固有代价，不是本构想要消除的对象。

PF 1.x 应放宽单调性的**正确性**地位，但不因此默认退回逐版本枚举。

## 2. 核心原则

把单调性从正确性假设调整为乐观搜索假设：

> 未观察到反例时，PF 可以乐观地假设单调，以减少 oracle 探测；一旦真实执行证据与当前单调模型冲突，证据必须优先，PF 必须细化搜索空间，不得忽略反例以维持单调假设。

写成操作规则：

```text
assumption guides search
evidence determines truth
```

以及：

> 不得为维持单调性而丢弃或覆盖任何已观察的 oracle evidence。

启发式只降低探测成本。错误启发式的后果应是搜索变慢或触发更昂贵的 fallback，而不是写出与已观察
Disposition 冲突的 floor。

## 3. 证据优先

对一个 coordinate，PF 可以根据当前证据推断区间结构，但同一 Slice 的直接 oracle observation
始终高于推断。静态 hint 继续只选择探针，不排除候选，也不更新兼容性边界。

例如已有：

```text
m1 -------------------------- h
PASS                          PASS
```

PF 可以暂时把 `[m1, h]` 视为乐观 PASS segment。若后续 probe 得到：

```text
m1 -------- m2 ------------- h
PASS        REJECTED         PASS
```

则 `PASS < REJECTED < PASS` 已经构成对原单调模型的反例。PF 不得忽略 `m2`、将其解释为噪声、
继续把 `[m1, h]` 当作整体 PASS，或为保持单一 floor 而覆盖该证据。必须使原区间失效并 refinement。

同版本先后得到不同 Disposition 仍是 `NONDETERMINISTIC`；辅助静态协议失败仍只产生 `NO_HINT`。
这两条不因本构想改变。

## 4. 反例驱动的区间分裂

出现单调性反例后，将原乐观区间拆成更小 segment，并优先探测反例两侧。例如：

```text
m1 -------- m2 -------- h
PASS        REJECTED    PASS
```

先拆成：

```text
[m1, m2)    [m2]        (m2, h]
    ?       REJECTED        ?
```

邻域 refinement 之后可以形成：

```text
m1 ------ m2-1  m2  m2+1 ------ h
PASS      PASS   REJECTED PASS       PASS
```

```text
[m1, m2-1]   乐观 PASS segment
[m2]         已观察 REJECTED
[m2+1, h]    乐观 PASS segment
```

即局部的 `PASS* | REJECTED | PASS*`。这些 segment 仍是基于局部单调性的乐观推断，不是对每个
中间版本的穷举证明。任一 segment 内再出现反例，继续 split。

## 5. 搜索模型

坐标状态不再只维护一对 `lower_rejected` / `upper_pass`，而表示为：

```text
observations + inferred segments
```

例如：

```text
v1         v4         v7         v10
REJECTED   PASS       REJECTED   PASS
```

可对应：

```text
segment A: 单调乐观
segment B: 已矛盾并已细化
segment C: 单调乐观
```

循环是 counterexample-driven refinement：

```text
optimistic monotone model
        ↓
oracle probe
        ↓
counterexample?
   no ───────→ continue fast search
   yes
        ↓
split affected segment
        ↓
locally refine neighborhood
        ↓
continue
```

搜索中的 `current` 仍是精确向量：每次提交只改一个坐标的精确版本，并保持该完整向量的直接
PASS。较早 PASS island 不得在未证明与最终其他坐标兼容时充当 `current`；默认仍走与已验证高端
相连、内部无已观察 REJECTED 的 segment。Apply 写回的声明可以比 `current` 更宽，见 §7。

最低候选快路仍可先探测最早样本。若它 PASS，只登记 observation，不立刻提交为 `current`。
定界相对已验证高端（baseline / 当前 `current`）进行。

## 6. 复杂度目标

对现实中保持 `REJECTED* PASS*` 的坐标，成本应接近当前 binary / guided search：

```text
O(log N)
```

对只有少量 hole / regression 的坐标，只增加发生矛盾处的局部 refinement，不重新枚举整个候选系列。

极端交替：

```text
PASS REJECTED PASS REJECTED PASS REJECTED ...
```

每次乐观 segment 都会被新证据打破，自然退化为：

```text
O(N)
```

期望性质：

> 越接近单调，越接近二分；越违反单调，越接近枚举。

不得为维持固定复杂度而牺牲已观察证据的正确性。

## 7. Floor 语义与 apply 投影

搜索提交的仍是精确 `current`；apply 写回的是对该坐标已观察/乐观分段的 **PEP 440 声明**。
二者不必相同。现行 D001 只写精确 `>=version` 并保留用户已有上界与排除项；本构想把 PF 自己
生成的 `!=` 也纳入写回，用来表达 hole，而不是发现不兼容上界。

非单调时，单一“最早 PASS”或单一“最后一段 PASS”都可能不是最短、也不一定是最完整的声明。
例如候选序列（旧 → 新，高端已验证 PASS）：

```text
1  2  3  4  5  6  7  8
F  F  P  P  F  P  P  P
```

即 `FFPPFPPP`（`F` = `REJECTED`，`P` = `PASS`）。

| 写法 | 覆盖 | 问题 |
| --- | --- | --- |
| `>=3` | 3–8 | 包含已观察 hole `5` |
| `>=6` | 6–8 | 合法但丢掉已观察 PASS island `3–4` |
| `>=3, !=5` | 3,4,6–8 | 覆盖全部分段，且不含已知 hole |

提议的投影对象是 **最大可投影集合** `S`，再用尽量短的声明去编码它。

`S` 是满足下列条件的最大版本集合：

1. 包含已验证高端所在的乐观 PASS segment；
2. 不含同一最终 Slice 上任何已观察 REJECTED；
3. 可写成一个下界加有限排除：`>=L, !=h1, !=h2, ...`（再与用户原有上界、排除项、marker 合并）；
4. `L` 与每个 `hi` 都有最终 Slice 的直接证据或由其界定的乐观 segment 支撑。不同 context
   下较早 sweep 的 island PASS 不得进入 `S`。

对上例，`L = 3`，`h = {5}`，`S = {3,4,6,7,8}`。

编码原则：

> 在冻结候选序列上表示恰好集合 `S` 的 PEP 440 声明中，选择最短者。

典型最短形是一个 `>=` 加上若干 `!=`。同一包不得拆成两行 requirement 用并集绕过同名 overlap
规则；`>=3,<5` 会切掉高端，因此不能代替 `>=3, !=5`。连续多个 hole 就列出多个 `!=`，或在
编码长度不可接受时退回更高的 `L`（见下文压缩）。

这比“只取最后一段”更完整，也比“最早 PASS 当 floor”更安全：`>=3` 非法，`>=3, !=5` 合法。
单调 `REJECTED* PASS*` 时 `S` 没有 hole，最短声明退化为现行的单个 `>=floor`。

`current` / final 精确向量仍须自身 PASS。它通常取自高端相连 segment 的下端，作为其他坐标的
context；声明则可以额外纳入最终 Slice 已证明的更早 island。predecessor 证据改为支撑该声明：
`L` 的直接前驱（若存在且 REJECTED）以及每个写入 `!=` 的 hole。

安全网不变：若写出的 specifier 在最终 Slice 上仍允许已观察 REJECTED，则拒绝 apply。

压缩是次要策略，不是默认目标。最短编码针对的是 **恰好表示最大 `S`**，因此 `>=6` 虽更短，
但表示的是真子集，不能赢过 `>=3, !=5`。仅当 `!=` 条数或规范文本超过后续 Design 给出的预算
（例如交替 `PFPFPF…` 接近线性排除）时，才提高 `L`、丢掉更早 island，使可行声明变短。
该预算未定之前，默认不丢 segment。

不在本次生成 `<` / `<=` 上界，也不把未观察版本写成已认证 hole。`!=` 只排除精确已观察
REJECTED（及其在候选序列上经邻域 refinement 闭合的 hole 点），与现行“保留用户上界/排除项”
合并，而不是改写它们。

## 8. 观察与推断

报告应明确区分两类事实。

**已观察证据**来自直接 runtime observation，例如：

```text
v3 PASS
v5 REJECTED
v8 PASS
```

**推断结构**来自当前乐观单调模型，例如：

```text
[v3, v4] assumed PASS
[v6, v8] assumed REJECTED→PASS transition
```

不得把 inferred segment 写成穷举证明，尤其不得声称“specifier 允许的全部版本均已验证 PASS”，
除非实际做了 exhaustive certification。更准确的语义是：

> 当前声明在乐观单调假设下表示最终 Slice 上的最大可投影集合，且不存在与该投影冲突的已观察执行证据。

现行 `NO_PASS_IN_SEARCH_SPACE` 文案夸大已验证范围的问题见
[R010 §2.1](../reviews/R010-pf-engineering-document-audit.md#21-p2-no-pass-文案夸大已验证范围)；
本构想若进入 Design，成功路径同样不得把乐观 segment 说成已逐点认证。

## 9. 与静态 guidance 的同一权威原则

[D038](../archived/designs/D038-pf-static-guidance-authority.md) 把 ty 从可排除候选的权威降为
guidance；现行 D003 写为：静态事实可以改变探测顺序，但不能排除候选或更新兼容性边界。

本构想对单调性使用同一分层：

| 来源 | 角色 | 不能做的事 |
| --- | --- | --- |
| ty / 静态事实 | 选择探针 | 排除候选、更新兼容性边界、覆盖 oracle |
| 单调性假设 | 选择探针、剪枝未观察点 | 丢弃或改写已观察 Disposition、把推断写成穷举证明 |
| 直接 oracle observation | 决定兼容性与声明资格 | — |

共同原则：

> Heuristics and structural assumptions may reduce search cost, but executable evidence has final authority.

## 10. 1.x 范围

本构想只放宽**单 coordinate** 的单调性假设。1.x 可以支持：

- 乐观单调的 binary / guided search；
- 同 Slice 直接 `PASS` / `REJECTED` 矛盾检测；
- 反例驱动的 segment split；
- 矛盾邻域的局部 refinement；
- 多个单调乐观 segment；
- 最坏情况退化为线性探测；
- 报告区分 observed 与 inferred；
- apply 用尽量短的 `>=L, !=h…` 编码最大可投影分段，并拒绝仍包含已知 hole 的声明。

`NON_MONOTONIC` 作为 Cell 失败原因是否退役、缩为“无法形成合法声明”的终态，还是仅保留给
未建模情况，由后续 Design 决定。同点冲突与 `INDETERMINATE` 仍立即停止。

## 11. 暂不处理的依赖交互

本阶段不主动解决多个 dependency coordinate 之间的高阶 interaction。例如：

```text
A_low + B_high = PASS
A_high + B_low = PASS
A_low + B_low = REJECTED
```

仍属后续算法演进。原因：

1. 最终向量仍须自身取得完整 configured verifier PASS，明显失败的组合不能直接作为成功结果；
2. interaction 更常先影响搜索 optimality、completeness 或使 floor 偏保守；
3. 单轴 hole 直接破坏普通 `>=floor`、分段声明与单调搜索本身；
4. 先完成一维 evidence-aware search，可降低后续 interaction search 的复杂度。

未来可以再引入 interaction detection、grouped coordinates、局部多维搜索或 Pareto support region。
这些不属于本文。

## 12. 非目标

本次不要求：

- 穷举验证所有 candidate；
- 静态证明单调性；
- 全局非单调 optimization；
- 任意多维 black-box search；
- dependency interaction discovery；
- 发现或生成不兼容上界；
- 对未观察版本做 hole 完备认证；
- 用第二行 requirement 或 marker 并集绕过同名 overlap 来表示分段；
- symbolic execution；
- 概率正确性声称。

对**已观察** hole 写入 `!=` 属于 §7 的投影，不是非目标。它仍不是穷举认证，也不代替用户原有排除项。

PF 仍然允许把单调性当作工程先验。改变的是：

> 单调性不能覆盖已经观察到的反例。

## 13. 证据缺口与进入 Design 的条件

进入 Design 前至少需要回答下列问题。纯设想或与现行单调 workload 的定性相似不足以改写 D001/D003。

1. **单调对照成本。** 在与当前 `direct-first-coordinate-guidance-v1` 相同的冻结候选、Slice 与
   小窗口阈值上，合成或回放的 `REJECTED* PASS*` 坐标探测次数是否仍接近现行二分，而不是系统性退化到线性。
2. **少量 hole 的声明。** 对 `FFPPFPPP` 这类单 hole，最终 Slice 上是否得到 `>=3rd, !=5th`
   （或等价最短形），而不是更窄的 `>=6th` 或非法的 `>=3rd`。探测次数是否只在 hole 邻域增加。
3. **已观察证据闭合。** 成功声明不得允许同一最终 Slice 的已观察 REJECTED；报告能区分
   observation、inferred segment 与写入的 `>=` / `!=` 条款。
4. **最终 Slice 资格。** 进入 `S` 的 island 是否都在最终其他坐标下重新直接观察；跨 Slice 的
   历史 PASS 不得生成更宽声明。
5. **已提交 current 与后发现的高端 hole。** 若精确 `current` 提交后，在它与已验证高端之间又观察
   到 REJECTED，是允许抬高 `current`、改写声明并再定位，还是将该 Cell 失败。v1 的“每次提交只严格降低
   一个坐标”可能无法原样保留。
6. **最短编码与压缩预算。** 长度按 PEP 440 子句数还是规范文本；连续 hole 的多个 `!=` 是否最短；
   交替最坏情况下预算阈值与提高 `L` 的回退是否要进 1.x。
7. **虚拟 sentinel、最低快路与 predecessor 重验。** 空间外 PASS sentinel、首次最早候选探测、以及
   后续 sweep 的 predecessor 直接重验，如何维持精确 `current` 在高端 segment，同时仍把最终 Slice
   的 island 编进声明。
8. **搜索推导身份。** 现行 `search_derivation_identity` 绑定
   `monotonicity=rejected-prefix-pass-suffix`。规则变更必须成为可区分的 search provenance，
   不能把旧单调 `>=floor` 报告当作带 `!=` 的新语义 apply。
9. **与用户排除项、跨 Cell 投影的合并。** 原声明已有 `!=` / 上界时如何规范合并；不同 Cell 的
   hole 集合不同时，是 canonical marker 分行、不可表示，还是取交集变窄。

建议先用确定性 fake evaluator 做一维合成矩阵（均匀单调、边界靠两端、单 hole 如 `FFPPFPPP`、
多 hole、交替最坏、虚拟 sentinel、Indeterminate、同点冲突），并固定声明编码对照，再决定是否
值得接到真实 evaluator。E001/E006/E008/E009 只支持“近似单调时应对性能接近现状”这一动机，
不能代替 hole 场景的正确性或声明最短性证据。

满足以上闭合后，另建临时 Design，逐项写明将替代 D001/D003 的哪条规则，接受后再 Plan。
若证据表明真实 hole 极少、而抬高 current / 报告模型的代价过高，可以关闭本文或把范围缩到
“观察到反例时失败但文案不再暗示已穷举”，不把 refinement 写成产品义务。

## 14. 可能涉及的 owner 与候选验收方向

若后续推进，新 Design 需要评审的增量（目前不是承诺）：

| Owner | 可能增量 |
| --- | --- |
| D001 | 结果承诺从单调坐标最小向量改为最大可投影集合的最短声明；apply 可写 `>=` 与 PF 生成的 `!=`；保留用户上界/排除项并规范合并；§9 非目标收缩；`NON_MONOTONIC` 与 apply blocker 的新含义 |
| D003 | 以 observations + segments 替换单一 bracket；反例 split 与邻域 refinement；成功路径不再一律 `NON_MONOTONIC` 终止；精确 `current` 与声明编码分离 |
| D002 | `CoordinateSearch` / evaluator seam 是否仍只返回单一 floor+predecessor，或要暴露 segment 与声明条款 |
| D004 | 静态窗口仍只产出 hint；旧区间 split 后作废该坐标的既有 static hint |
| D006 | 成功但存在 hole/segment 时的结论语言；展示 `>=` / `!=` 时避免把推断写成已验证范围 |
| D014 | observation、inferred segment、`L`/`!=` 条款的 wire、identity、reader 复证；不把推断标成 Probe evidence |
| Search provenance | 新的 monotonicity / derivation 规则名，与 execution policy 继续分离 |

候选验收方向（供未来 Design 裁剪，不是本文义务）：

| 方向 | 待验证内容 |
| --- | --- |
| 乐观单调 | 无反例时 floor/predecessor 与现行算法一致，探测次数同阶 |
| 反例 refinement | 每个已观察 `PASS < REJECTED < PASS` 都 split；不丢弃 m2 |
| 声明编码 | `FFPPFPPP` → 最短形 `>=3rd, !=5th`；单调序列仍为单个 `>=floor` |
| 投影安全 | 声明不允许已观察 REJECTED；跨 Slice island 不进入 `S`；否则拒绝 apply |
| 报告 | observed / inferred / 声明条款可区分；不得声称穷举 specifier 内全部版本 |
| 退化 | 交替 oracle 可终止且不输出矛盾 floor；允许接近 O(N) |
| 身份 | 新推导规则与旧报告不可混 apply |
| 产品路径 | focused/full checks、终端、Schema/examples、文档归并 |

核心立场：

```text
optimistic assumptions
evidence-respecting refinement
graceful linear fallback
```

或：

> Assume monotonicity for efficiency; refine on counterexamples; never hide evidence to preserve the assumption.

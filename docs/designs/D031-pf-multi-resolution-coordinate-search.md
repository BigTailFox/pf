# D031 — PF 多分辨率坐标搜索

- **状态：** 草案；本轮重写后的完整契约待评审，未开始实施
- **日期：** 2026-09-05
- **性质：** 临时性搜索算法、配置、候选与报告契约迁移 Design
- **核对基线：** `85e195c`；起草前工作区干净
- **来源：** 本轮 Multi-Resolution Coordinate Search 草案评审与六项修订决定
- **稳定 owner：** [D001](D001-pf.md)、[D002](D002-pf-implementation.md)、
  [D003](D003-pf-search-algorithm.md)、[D006](D006-pf-cli-enhancement.md)、
  [D014](D014-pf-report-schema.md)
- **关联：** [D004](D004-pf-ty-enhancement.md)、[D005](D005-pf-failure-and-diagnose.md)、
  [D008](D008-pf-verification-run.md)、[D013](D013-pf-pytest-observer.md)、
  [R008](../reviews/R008-pf-search-performance-review.md)、
  [D030](../archived/designs/D030-pf-search-space-dsl.md)

本文定义目标方案，不宣称目标行为已经落地。用户已接受本轮六项修订方向；完整 Design 接受后，
生产实施前仍须建立 durable Plan，映射全部验收标准、迁移与证据。本文不创建 Plan。
第一阶段纯算法模拟已完成，证据见 [E005](../experiments/E005-pf-multi-resolution-search-simulation.md)；
尚无真实 evaluator 成本或完整产品性能实测，见 §8.6。

## 1. 目标与取舍

PF 在冻结候选上固定其他 direct coordinates，逐个降低当前依赖，直到完整一轮没有坐标变化。
本方案把一维定位统一为 `major → minor → patch` 的层级 refinement，并增加跨 sweep 的 predecessor
重验。每次提交仍只降低一个坐标，当前完整向量始终有直接 PASS。

主要决定：

1. `search-resolution = major | minor | patch` 替换 `search-step`，默认 `minor`。它决定 refinement
   的终止层；不保留旧配置、CLI 或 wire 别名，也不保留另一套先采样再搜索的产品路径。
2. CandidateBuilder 冻结空间内全部合格精确 release；树及目标层代表从这一份事实派生。
3. 每个桶使用当前 active interval 内的最大精确候选作为代表；只细化首个 passing 桶。
4. 保留空间外的虚拟 PASS sentinel；不能把 sentinel 的结果复制给最高候选或桶代表。
5. 保留最低目标层候选的快速路径；后续 sweep 优先重验当前 floor 的直接 predecessor。
6. 缓存统一在 evaluator 入口命中，搜索流程不维护“是否运行过”的分支，也不为每层维护 PASS cache。
7. 观察到同 Slice 的直接非单调矛盾仍立即终止。动态坐标排序、跨坐标失败记忆与失败用例打分留待独立提案。

本次包含公开配置与候选存储契约的迁移，不能描述为只有 probe 顺序发生变化。默认分辨率仍为 minor，
并保留同系列最高合格精确代表的选择含义；同一冻结发布观测、相同空间与相同层级下，目标层候选集合
应与现行对应 step 的代表集合相同。探测顺序变化仍可能改变未完全单调空间中被观察到的失败或终态。

## 2. 配置与结果语义

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

全局与逐依赖配置都支持 resolution；继承为逐依赖 → 全局 → 默认 minor，root/member 合并沿用 D001。
对应 CLI 入口统一改为 `--search-resolution`。内部策略字段与报告 binding 字段统一为 `resolution`。
`search-space`、条件默认表、prerelease 与 artifact 资格规则沿用 D030 归并后的现行契约。
所有 space × resolution 组合合法；不根据 `0.x`、发布密度或 SemVer 含义自动选择层级。

| Resolution | 搜索层级 | 允许提交的精确版本 |
| --- | --- | --- |
| major | major | 选中 major 桶内最高合格 release |
| minor | major → minor | 选中 minor 桶内最高合格 release |
| patch | major → minor → patch | 选中 patch 桶内最高合格 release |

“到 minor 为止”不等于把结果截断为 `x.y`。例如该 minor 桶的最高合格版本为 `2.3.7`，则输出和 apply
仍使用经过直接验证的 `2.3.7`。PASS 只证明该精确 Proposal，不证明整个系列兼容。

版本分组沿用现行系列规则：不足三段的 release 补零，major/minor/patch key 分别为
`(epoch, major)`、`(epoch, major, minor)`、`(epoch, major, minor, patch)`。
树根直接包含按版本顺序排列的 `(epoch, major)` 桶，无需新增 epoch 配置层。
同一 key 内的 prerelease、post/local 或额外 release 段按完整 Version 顺序选最高合格代表；
patch 不另增“精确 release 穷举”层。由此保留当前 patch 系列选择含义，避免顺带修改特殊版本采样语义。

令 `U[d]` 为依赖 d 的完整合格精确候选序列，`C_r[d]` 为目标 resolution r 的每个非空桶的最高代表序列。
结果是在局部 `REJECTED* PASS*` 假设下，相对于 `C_r` 的 coordinate-minimal passing vector；
对于最终向量，固定其他坐标后，搜索在每个目标层序列上建立 floor 与直接 predecessor 的边界。
这不认证未探测 hole、不证明每个更低原始 release 都失败，也不承诺笛卡尔积全局最小值。

## 3. 候选冻结与结构树

CandidateBuilder 继续使用一次成功 registry query 的 `release_versions + artifacts` 观测。
先以过滤前的完整 release keys 求值 space DSL 的系列位置，再应用 space、Cell 兼容性、声明保留限制、
yanked、prerelease、artifact 与 baseline cap 等公共资格；顺序、anchor 错误与空空间分类均保持。

结果 `U[d]` 为升序、唯一且绑定精确 artifact 的候选序列，冻结后不追加、不按 resolution 删除候选。
构造结构索引不执行 prepare、resolve、ty 或 verifier；只在实际请求 probe 时物化 Proposal。
不据此声称 registry 查询工作集变小，或 uv 的 transitive resolution 搜索范围受到约束。

树由冻结序列的连续区间构成，每个候选恰好属于一个 major、minor 和 patch 桶。树只保存结构，
不保存 PASS/REJECTED；共享纯分组规则供算法与 report reader 使用。不额外落盘整棵树和多份代表列表。

进入坐标 d 后，固定 `context = V_-d`，active interval 为 `U[d] ∩ (-∞, V[d]]`。
每个 active 桶代表为该交集的最大精确候选。若 current 已在 `C_r[d]` 内，向下裁剪不会重选其下方
目标桶的代表；current 所属目标桶的代表就是 current。首次空间外 baseline 仅提供虚拟高端。
一次 refinement 只缩小到完整 child 桶，或缩小到已取得 PASS 的目标层代表，因此不产生临时的
非目标层 floor，也不通过逐轮 clipping 隐式细化超过用户选择的 resolution。

## 4. 统一证据入口与缓存

### 4.1 生命周期与所有权

缓存属于一次 Cell search 的 evaluator 实例。该实例固定 Cell、SourceSnapshot、SEARCH SourcePlan、
evaluation policy、static/harness baseline、resolver profile、release cutoff 与解析上下文。
在这一固定作用域内，规范化的完整 managed vector 可作为 lookup key；不是跨运行或跨 Cell 的持久 key。
同一精确解析请求继续消费现行解析缓存，不在命中旧证据时重新建立另一个解析图。

`_ProposalRunner` 拥有实际 prepare/static/runtime 结果表及 prepared environment 生命周期；
`CoordinateSearch` 只保存算法所需的观测、Slice、区间、边界与历史提示，不再另建一份执行结果缓存。
baseline 的真实 PASS 及其 evidence 引用在创建 evaluator 时预置。普通算法测试通过合法 evaluator
提供同等的已知结果，不以只有 status 的搜索私有对象代替产品证据。

跨层、跨 sweep 的重复请求都走同一入口，不要求调用方先查 cache 或标记“已测”。空间外 sentinel
是一个不可 probe 的已知高端，其特殊性来自候选资格，不来自缓存命中与否。

### 4.2 命中规则

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

### 4.3 失败资格

只有 D005 的直接 `REJECTED` 可以建立拒绝边界，`INDETERMINATE` 立即停止 Cell；非完整执行不能当成 FAIL。
FailedCaseSet 拒绝预言继续归 `_ProposalRunner` / RuntimeEvaluator / ConfiguredVerifier，保持现行主动
坐标作用域与 collection 证明。其命中可拒绝当前 probe；其通过不能建立 PASS。
本次不改 verifier 顺序，不把失败用例集传给树或坐标排序，也不新增 top-K 策略。

## 5. 一维搜索

### 5.1 进入与 predecessor 重验

进入 d 时 current 完整向量已有直接 PASS；只处理不高于 current 的目标层代表。

1. 没有目标候选，返回 `NO_PASS_IN_SEARCH_SPACE`。
2. current 已是目标序列首个候选，直接形成无 predecessor 的边界，复用 current 的真实证据。
3. 有上一轮边界，且其 floor 等于 current、predecessor 仍是目标序列中的直接前驱时，先在当前完整
   context 下请求 predecessor 的直接结果。REJECTED 则返回当前边界；PASS 则把搜索 upper 降到该点；
   INDETERMINATE 则停止。缓存负责相同 context 的复用，旧 context 的结果只作为选点提示。
4. 无可用边界，或 predecessor 已 PASS 后需要继续下降，进入最低候选快速路径。

history 是调度提示，不是跨 context 的拒绝证据。无变化的最终 sweep 必须在最终 context 下建立全部边界。
重验失败仅在局部单调假设下排除更低点，不承诺未观察 hole 不存在。

### 5.2 最低候选快速路径

先请求 active 目标层序列的最低代表。例如 minor resolution 测首个 minor 的最高合格 release，
不测该 minor 内任意更低 patch 来形成超出目标分辨率的 floor。

若得到直接 PASS，则已找到目标序列首个候选；若只有 PASS guidance，先 promotion 再提交。
直接 REJECTED 或拒绝 guidance 进入树搜索；INDETERMINATE 停止。后续树搜索请求同一向量自然命中
统一结果表，不增加第二次 prepare 或验证。该快速路径在 predecessor 重验已证明不变时不再执行。

### 5.3 虚拟 PASS sentinel

若 upper 不属于目标候选序列，它仅为高于所有 active 候选的虚拟 PASS sentinel。
最后一个实际桶代表状态未知，不能因 sentinel PASS 而标为 PASS。

统一 refinement 支持两种高端：实际代表的 passing evidence/guidance，或空间外虚拟 PASS bound。
单 group 只有在其自身已有可用 passing evidence/guidance 时才可直接选择；否则必须请求该代表。
若定位结果落到 sentinel，说明实际空间没有找到 PASS，返回 `NO_PASS_IN_SEARCH_SPACE`，不能返回
sentinel 作为 floor，也不把它加入快照、候选计数、probe 窗口或报告候选。

### 5.4 层级 refinement

每一层都以连续 child 桶的 active max representatives 为有序点列：

```text
refine(groups, upper_bound):
    请求最低 representative 的调度结果
    若方向为 PASS，选择该 group
    否则以拒绝低端和实际/虚拟 passing 高端建立 bracket
    对代表序列作确定的 lower-bound 定位
    若命中虚拟高端，返回 NO_PASS_IN_SEARCH_SPACE
    在选中的 group 内进入下一层；达到 resolution 后返回精确代表
```

只有一个有已知 passing 方向的 group 时跳过定位，直接进入下一层或返回目标代表。
初始 probe、层间重复代表和高端查询全部由统一缓存消除重复执行。
继续采用现行确定小窗口阈值（默认 8）与升序线性/二分策略；本次不同时调参。

实际 representative 的直接 PASS 向下复用时，下一层最后一个代表是同一精确向量。
若搜索使用 static PASS guidance，它只提供调度高端，不满足 current 的直接 PASS 不变量；
必须在 §5.5 promotion 后才能提交。不得把“每层都有直接 upper PASS”当成 static 路径的前提。

### 5.5 Promotion、边界与非单调

定位到目标层代表后，按现行契约取得该 floor 的直接 runtime PASS，并对 `C_r[d]` 中的直接
predecessor 取得直接 Rejection；predecessor 可能在上一 minor 或 major 桶，不能只查当前桶。
首个目标候选没有 predecessor。static guidance 被 promotion 推翻时，以新直接事实重新定位；
每次修正必须增加直接事实，候选有限，无法取得一致证据时沿用现行 `NONDETERMINISTIC` 终止保护。

每次取得或复用直接 observation，检查同 Slice 是否存在 `v_low < v_high` 且
`PASS(v_low), REJECTED(v_high)`；发现即返回 `NON_MONOTONIC`，保留反例，不进行局部或全空间 fallback。
不同 context 和 static guidance 不参与这一判断。

## 6. Static region、窗口与坐标 sweep

Static region 的相邻性继续定义在目标候选序列 `C_r[d]` 上。完整 `U[d]` 中未被目标层选中的 release
不成为额外 region 点；major/minor refinement 的稀疏 representatives 不能因树中相邻而冒充
`C_r[d]` 中已观测连续。每个 probe 仍获取自身静态事实，region 只提供现行资格下的调度 guidance。

`SearchProbeRequest` 的上下界与 candidate_count 始终描述尚未排除的连续目标候选窗口，
不使用 child 桶数冒充候选数；跨层代表只是该窗口内的实际选点。promotion 窗口为目标序列的
floor/predecessor 对。虚拟 sentinel 不进入端点或计数。现有 typed event 消费者无需理解树结构。

```text
current = verified baseline
repeat:
    changed = false
    for d in canonical dependency order:
        floor, boundary = search_coordinate(d, current, tree[d], history[d])
        if floor < current[d]:
            current[d] = floor  # 已直接 PASS 的完整向量
            changed = true
        history[d] = boundary
until not changed
```

依赖顺序固定，完整 sweep 覆盖全部坐标。空间有限、每次提交严格下降，因此 sweep 终止。
最终无变化 sweep 的所有 boundary 属于最终 context；final 自身必须形成精确 PassEvaluation 闭环。
本次不增加单 Cell 并行 probe、动态排序、跨 Cell hints、跨运行缓存或非单调恢复。

## 7. Module、报告与迁移

| Owner | 目标责任 |
| --- | --- |
| ConfigLoader / ProjectLoader / CLI | resolution 的输入、继承、默认值、named policy 与 help |
| CandidateBuilder | 同次 registry 观测、DSL 求值、公共资格和完整 U 冻结；不提前按终止层删除候选 |
| 候选纯分组逻辑 | 三层 key、连续区间与目标代表的共同派生规则，供算法与 reader 使用 |
| CoordinateSearch | 树 refinement、快速路径、重验、Slice observation、promotion、window 与确定 sweep |
| SearchCoordinator / _ProposalRunner | 注入 baseline evidence；统一结果入口、prepare/static/runtime 缓存和原资源清理 |
| RuntimeEvaluator / ConfiguredVerifier | 保留现行 runtime authority 与 FailedCaseSet 拒绝预言 |
| ReportStore / schemas / ApplyAuthorizer | 完整候选、新请求策略、目标代表派生、证据闭合与授权 |
| Explain / terminal | 展示 resolution 与精确 floor，消费 validated projection 与实际候选窗口 |

结构树、缓存结果表与 history 均由现有 owner 隐藏；不新增通用 TreeService、CacheManager 或 HintProvider。
算法的 public minimize seam 继续作为策略测试入口；产品测试沿现有 lower adapters 装配真实图。
在实施 Plan 中明确移除搜索私有 known-pass/cache 与 baseline 注入的 interface 迁移，不保留并行权威来源。

候选与报告的目标形状：

- CandidateSnapshot 的 `candidates` 保存完整 U，每项保留精确 version/artifact；移除仅描述既有
  采样层的 Candidate `series_key` 和 snapshot `series_representatives`，不另存重复目标列表。
- resolution 从唯一 named policy / report binding 取得。目标序列与树均由完整候选派生；
  reader 以共享规则验证每个 probe、floor、predecessor 是对应目标序列中的合法精确代表。
- D030 的过滤前 series inventory 继续仅用于 DSL anchor/位置证明；它与过滤后的 U 不能互相替代。
- snapshot digest 绑定完整候选；`candidate_policy_identity` 将 step 输入替换为 resolution。
  报告 `inputs.search_policy` binding 使用 required `resolution`，完整输入继续进入 generation，
  不因本次实际 floor 恰好相同而忽略策略差异。
- 本迁移把固定 search profile 替换为 `multi-resolution-coordinate-v1`，不更改 runtime evaluation
  policy；Schema 1、现有 v1 identity 前缀与算法版本编号保持 v1，按 pre-release 规则一次替换。
- reader、build/reintern、merge、纯 host-partial、apply 与 explain 一起消费目标形状；不从旧字段
  推断 resolution，不增加 dual reader。apply 在 force waiver 前验证完整 requested search policy，
  保留原声明/projected/no-op 授权及离线幂等行为。
- reader 能从保存的 U 复算其目标代表；仍不声称证明 registry 完备性或不存在未保存的合格 release。

保存完整 U 会增大报告及冻结内存，是统一模型的明确代价；§8 必须测量，不能以“lazy tree”掩盖。
树只需区间索引，无需复制候选对象。源码物化仍按实际 probe 懒执行，与保存版本元数据是不同成本。

## 8. 收益验证：无需先完成产品实施

### 8.1 先定义对照问题

实验把当前 HEAD 的平面搜索作为基线。在完全相同 U、space、resolution、目标序列、baseline 与
规范坐标顺序下比较四组；现行基线以对应 step 接收相同目标代表，仅用作实验对照，不形成产品兼容路径。

| 组 | 一维策略 | 后续 sweep |
| --- | --- | --- |
| A | 现行平面搜索与最低候选快路 | 现行重新定位 |
| B | 同 A | predecessor 重验 |
| C | 分层搜索，保留最低目标候选快路 | 重新定位 |
| D | 同 C | predecessor 重验 |

四组使用等价的直接结果复用与相同小窗口阈值。分别记录逻辑 probe 请求数、唯一精确 vector、
直接 evaluator miss 与 cache hit，避免把重复 lookup 误计为节约了一次当前本就不会执行的 verifier。
已有 FailedCaseSet 与 static region 的成本和收益应作为共同条件记录，不归入树的独立收益。

### 8.2 第一阶段：纯策略模拟，验证选点收益与正确性

在独立临时实验脚本中实现最小 refinement/reconfirm 状态机，通过 deterministic fake evaluator
返回完整向量的结果。无需修改 production search、配置、Schema、uv adapter 或用户项目。
候选形状可使用保存的真实发布观测；兼容性 outcome 若来自合成 oracle，必须标明为合成数据。

覆盖均匀/高度偏斜的桶、单 major/minor、稀疏版本、各 resolution、最低即 PASS、边界居中/靠高端、
空间外 sentinel、全拒绝，以及多坐标 context 变化后 predecessor 仍拒绝/转 PASS。
每条单调合成切片可由穷举 oracle 独立计算 floor；对小型多坐标矩阵按固定顺序计算预期不动点。
覆盖直接非单调矛盾、Indeterminate 与 promotion 反证；允许不同策略观察到不同未探测点，不能把
任意非单调矩阵上“所有算法结果一样”设为虚假验收条件。

输出各场景的 unique vector、cache miss、sweep、boundary 和终态，以及收益分布与退化场景。
这一步可便宜地否证“树总是更快”，也可分离收益究竟来自 B 还是 C；它不证明 runtime wall-clock 收益。
均匀树上 `log M + log m + log p` 与 `log(Mmp)` 同阶，不能仅凭层级数量给出更快结论。

### 8.3 第二阶段：历史 trace 回放与有限缺口测量

旧日志只提供曾实际执行过的向量，新策略通常会请求旧 trace 未覆盖的点。
回放必须报告 exact-vector hit 与 missing；missing 不能补作 PASS/REJECTED，不能免费跳过，
不能以版本距离或历史其他 context 的结果生成权威 outcome。
只有相关策略路径所需证据闭合，才能报告该路径完整 probe 计数。

已有每阶段耗时可用于估算区间和挑选下一批测量点；注明缓存、源码、工具及候选冻结差异。
E002 等历史日志不能直接作为当前性能基线，也不能导入生产结果缓存。
需要时在独立实验运行中固定当前源码和环境，对少量缺口 vector 调用现有 prepare/static/runtime
module，补齐真实成本；此步骤需要最小实验适配，不需要先完成配置和报告迁移。
若无法重建旧 trace 的精确评价上下文，补测只能建立新的当前基线，不能把新 outcome 拼接进旧 trace
冒充同一次搜索的完整证据。trace 的成本估算与第三阶段各策略独立执行的 wall-clock 分开报告。

### 8.4 第三阶段：最小 evaluator 集成对照

若前两阶段显示值得推进，在 isolated checkout/临时 harness 中把候选策略接到现有 evaluator seam，
运行有代表性的少量 Cell/项目。完整产品实施前可以测量真实 probe 与验证成本，但必须真实保留
prepare、static guidance、promotion、FailedCaseSet 和资源隔离，不能以恒定每 probe 耗时代替。

固定 HEAD、source、uv/ty/Python、候选 cutoff、Cell、test-command、space 与目标序列。
不同策略使用独立 invocation-local outcome cache；交替执行顺序，分开冷/热 registry 与 resolution
缓存条件。让后一组复用前一组已运行的测试结果会偏袒后运行算法，不能计作其独立运行耗时。

记录 prepare/resolve/venv-sync/ty、static-only、promotion、原命令与 failed-set 的次数和耗时，
以及每 Cell 和整个 Run 的 wall-clock；并行进程 duration 的总和不是 critical path。
同时记录树构建、完整 U 冻结内存和预期报告体积，避免只测 verifier 收益而漏掉模型成本。
实验结果保存到非规范性 Experiment；本节定义真实集成方法，本阶段尚未执行。

### 8.5 接受收益的条件

在观测闭合的单调对照上，各组必须给出相同目标向量和直接边界资格；错误/非单调场景保持合法终态。
预先选定代表性场景和重复次数后，再比较 wall-clock 分布与波动，不从结果中只挑获益项目。
阈值应由基线成本和测量波动确定，本草案不虚设统一百分比。

第一阶段通过即可支持继续投入实验，不能作为默认切换的性能验收；第三阶段可在完整产品迁移前
决定是否值得实施。正式交付仍须验证配置、report、apply、终端与清理的完整产品路径。
若收益只来自重验而树无净收益，应回到 Design 修订范围，不将重验的收益当作树的验收证据。

### 8.6 第一阶段结果与待决事项

[E005](../experiments/E005-pf-multi-resolution-search-simulation.md) 已完成纯算法矩阵：13,460 次策略运行，
模拟 A 与当前算法 3,362 个 public-seam 差分对照通过，最终产物独立复跑一致。
2,883 个单坐标场景中，B 的 direct oracle miss 比 A 少 36.75%，C 多 2.93%，D 少 23.96%；
D 比 B 多 20.22%。多坐标矩阵同样是 B 更优。数字只代表所列合成输入的探针计数，不代表真实耗时。

该结果支持先验证 predecessor 重验的真实成本，尚不支持将树作为默认性能优化。
本 Design 保留重写方案作为评审对象，实施范围与树策略需依据这些退化证据再决策；
不自动改变用户已接受的 resolution 配置方向，不把第一阶段通过写成 AC10 的完整收益验收。

## 9. 验收标准与 owner 吸收

| AC | 必须取得的证据 |
| --- | --- |
| 1 | global/dep/CLI resolution 的值、minor 默认与继承；space/defaults/prerelease/artifact 规则保持；raw layer 和非法输入分类 |
| 2 | public CandidateBuilder 冻结完整合格 U；同次观测、过滤前 DSL inventory、空间资格及错误时序；resolution 不提前删除 U |
| 3 | 三层连续 partition、epoch、短 release、稀疏系列、pre/post/local/额外 release 段和最高合格代表；所有 space × resolution 组合 |
| 4 | public minimize 在三种 resolution 上返回目标层精确 floor；最低代表快路、单桶、clipping、跨桶 predecessor 与虚拟 sentinel/no-pass |
| 5 | 同 context 跨层/sweep 查询复用；真实 baseline seed；直接 cache hit 不触发 prepare/runtime；不同 invocation 和不同 Proposal 隔离 |
| 6 | static-only 缓存不成为直接 PASS；跨 active dependency guidance 隔离；目标序列相邻 region、promotion 反证与窗口计数正确 |
| 7 | context 改变后的 predecessor 重验、仍拒绝/转 PASS、首个候选无 predecessor；最终无变化 sweep 的边界全部属于最终 context |
| 8 | direct NON_MONOTONIC 立即停止并保留反例；Indeterminate 不裁剪；确定 probe 顺序、有限终止与 final 直接证据闭合 |
| 9 | resolution/profile/full U 进入相应 identity；报告仅派生目标代表；reader/build/merge/host-partial/explain 与 apply policy/force/幂等的 public 行为 |
| 10 | A/B/C/D 策略模拟与真实 evaluator 对照，分别报告树、重验和缓存的增量；missing trace、波动、退化、内存及报告体积边界如实记录 |
| 11 | 正式实施后 focused/full checks、typing/lint/build、Schema/examples 再生成及链接检查；只保留目标 public contract 的长期测试 |
| 12 | README 双语、help、配置/model/schema/fixtures/scripts 全面替换；稳定规则归并 owner，Design 与 Plan 完成审计并同步归档 |

D001 接收 resolution 配置、默认、目标层精确代表与结果范围；D002 接收完整候选模型、缓存入口和
baseline 注入的 ownership；D003 接收树、重验、static window、promotion 与 sweep；D006 接收 help、
窗口和 resolution 展示；D014 接收完整 U、策略字段、identity、派生验证与授权。
D004/D005/D008/D013 的证据和执行规则保持，仅修正必要引用；R008 接收实验结果与剩余开放项。
实施 Plan 必须将上述 AC 映射到有序切片、interface 迁移、生成物和确切验证命令，不以本文的实验方法
代替已执行的证据。

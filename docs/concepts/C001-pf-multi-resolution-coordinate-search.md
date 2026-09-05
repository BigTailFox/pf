# C001 — PF Multi-Resolution Search

- **状态：** 开放想法；树搜索延期，尚无明确真实性能收益证据
- **日期：** 2026-09-05
- **性质：** 非规范性 Concept，保存开发设想、待证假设与实验方向，不授权实施
- **来源：** 原 D031 多分辨率坐标搜索草案中的树搜索部分；原编号 D031 不复用
- **相关 Design：** [D033](../archived/designs/D033-pf-predecessor-revalidate.md) 已独立完成改名、重验、缓存与非单调终止
- **现行 owner：** [D001](../designs/D001-pf.md)、[D002](../designs/D002-pf-implementation.md)、
  [D003](../designs/D003-pf-search-algorithm.md)、[D006](../designs/D006-pf-cli-enhancement.md)、
  [D014](../designs/D014-pf-report-schema.md)
- **实验：** [E005](../experiments/E005-pf-multi-resolution-search-simulation.md)

本文保留树搜索的候选方案与验证方法，供后续探索；不定义当前或已接受的目标契约。
下文的算法、接口、wire 形状和验收项都是待验证的设想。取得明确收益依据后，应重新评审范围，
另建规范性 Design 并获得接受，再建立 Plan；本文不承担 Design 或 Plan 的职责。

## 1. 想法与拆分边界

尝试把一维定位改为 `major → minor → patch` 层级 refinement：冻结完整合格精确 release，
每个桶以当前 active interval 内最大候选作为代表，只细化首个 passing 桶，到用户选择的
resolution 停止。每次提交仍只降低一个坐标，当前完整向量始终有直接 PASS。

原 D031 目标 2、3，以及目标 4 的树内虚拟高端处理留在本文。原目标 1 的 `search-resolution`
改名、目标 5 的最低候选快路和 predecessor 重验、目标 6 的 evaluator 缓存、目标 7 的
直接非单调终止由 D033 独立承担；现行 sentinel 安全规则也继续适用于平面搜索。
这些决定不需要树、完整 U 快照或分层报告验证才能实施。

E005 中树本身的收益不稳定，组合策略也劣于仅重验；目前没有依据把树作为默认性能优化。
需要独立验证树在已具备重验和等价缓存后的增量收益，并计入内存、报告体积及实现复杂度。

## 2. 分辨率与结果设想

配置名称、默认值、继承与系列代表规则由 [D033 §2](../archived/designs/D033-pf-predecessor-revalidate.md#2-resolution-改名与候选语义)
提出。树不新增另一套配置；它只尝试以分层选点实现相同 resolution 的精确代表搜索。

| Resolution | 设想的搜索层级 | 允许提交的精确版本 |
| --- | --- | --- |
| major | major | 选中 major 桶内最高合格 release |
| minor | major → minor | 选中 minor 桶内最高合格 release |
| patch | major → minor → patch | 选中 patch 桶内最高合格 release |

版本分组沿用现行系列规则：不足三段的 release 补零，major/minor/patch key 分别为
`(epoch, major)`、`(epoch, major, minor)`、`(epoch, major, minor, patch)`。
树根直接包含按版本顺序排列的 `(epoch, major)` 桶，无需新增 epoch 配置层。
同一 key 内的 prerelease、post/local 或额外 release 段按完整 Version 顺序选最高合格代表；
patch 不另增“精确 release 穷举”层。由此保留当前 patch 系列选择含义，避免顺带修改特殊版本采样语义。

令 `U[d]` 为依赖 d 的完整合格精确候选序列，`C_r[d]` 为目标 resolution r 的每个非空桶的最高代表序列。
结果是在局部 `REJECTED* PASS*` 假设下，相对于 `C_r` 的 coordinate-minimal passing vector；
对于最终向量，固定其他坐标后，搜索在每个目标层序列上建立 floor 与直接 predecessor 的边界。
这不认证未探测 hole、不证明每个更低原始 release 都失败，也不承诺笛卡尔积全局最小值。

## 3. 候选冻结与结构树设想

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

## 4. 证据入口依赖

若后续推进树方案，复用 D033 定义的 evaluator 入口、baseline evidence 注入、结果表与失败资格，
不另建每层 PASS cache。树的跨层重复代表请求仍走同一入口；直接 cache hit 必须登记当前 Slice
观测并参与非单调检测。static-only 不能跳过 floor/predecessor promotion。

树、缓存与重验的增量收益分别核算。D033 的缓存迁移不以本文为前提。

## 5. 一维搜索设想

### 5.1 进入与 predecessor 重验

复用 D033 的坐标进入和重验顺序，将其平面定位步骤替换为下述树 refinement；旧 context 的
history 仍只提供选点提示。重验已证明边界不变时结束该坐标，否则进入最低目标候选快速路径。

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
对照设想采用现行确定小窗口阈值（默认 8）与升序线性/二分策略，避免同时调参混淆收益。

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
单 Cell 并行 probe、动态排序、跨 Cell hints、跨运行缓存和非单调恢复不在这个想法的范围内。

## 7. 可能涉及的 Module、报告与迁移

本节列出树方案若进入 Design 需要评审的增量，不是 D033 的实施要求。

| Owner | 树方案的可能增量 |
| --- | --- |
| CandidateBuilder | 冻结完整 U，不提前按 resolution 删除候选 |
| 候选纯分组逻辑 | 三层 key、连续区间与目标代表的共同派生规则，供算法与 reader 使用 |
| CoordinateSearch | 在 D033 的重验与证据入口之上接入树 refinement，维护目标序列窗口 |
| ReportStore / schemas / ApplyAuthorizer | 完整候选、目标代表派生、identity 与离线验证 |
| Explain / terminal | 消费实际目标候选窗口，无需理解树结构 |

树拟由现有 owner 隐藏，不预设通用 TreeService。D033 独立提出配置命名和结果表统一，
后续树 Design 应以届时现行 owner 为基线，不重复迁移这些规则。

候选与报告的可能形状：

- CandidateSnapshot 的 `candidates` 保存完整 U，每项保留精确 version/artifact；移除仅描述既有
  采样层的 Candidate `series_key` 和 snapshot `series_representatives`，不另存重复目标列表。
- resolution 从唯一 named policy / report binding 取得。目标序列与树均由完整候选派生；
  reader 以共享规则验证每个 probe、floor、predecessor 是对应目标序列中的合法精确代表。
- D030 的过滤前 series inventory 继续仅用于 DSL anchor/位置证明；它与过滤后的 U 不能互相替代。
- snapshot digest 绑定完整候选；`candidate_policy_identity` 沿用 D033 的 resolution 输入。
  报告 `inputs.search_policy` binding 沿用 required `resolution`，完整输入继续进入 generation，
  不因实际 floor 恰好相同而忽略策略差异。
- 原草案拟把固定 search profile 替换为 `multi-resolution-coordinate-v1`，保持 runtime evaluation
  policy、Schema 1、现有 v1 identity 前缀与算法版本编号；后续 Design 应按届时基线重新评审。
- reader、build/reintern、merge、纯 host-partial、apply 与 explain 一起消费目标形状；不从旧字段
  推断 resolution，不增加 dual reader。apply 在 force waiver 前验证完整 requested search policy，
  保留原声明/projected/no-op 授权及离线幂等行为。
- reader 能从保存的 U 复算其目标代表；仍不声称证明 registry 完备性或不存在未保存的合格 release。

保存完整 U 会增大报告及冻结内存，是统一模型的明确代价；§8 必须测量，不能以“lazy tree”掩盖。
树只需区间索引，无需复制候选对象。源码物化仍按实际 probe 懒执行，与保存版本元数据是不同成本。

## 8. 收益验证：无需先完成产品实施

以下保留原 D031 §8 的实验分阶段方法。A/B/C/D 名称对应 E005 的历史矩阵；后续测量须记录
新的源码基线。D033 可单独做 A/B 验证，树是否推进主要取决于相对 B 的增量，不以 A/D 差异归功于树。

### 8.1 先定义对照问题

实验固定明确源码版本的平面搜索作为基线。在完全相同 U、space、resolution、目标序列、baseline 与
规范坐标顺序下比较四组；E005 的历史基线以对应 step 接收相同目标代表，仅作实验对照。
当前基线已实施 D033，因此 B 对应该基线，A 保留为显式关闭重验的实验对照，不形成产品兼容路径。

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
阈值应由基线成本和测量波动确定，本 Concept 不虚设统一百分比。

第一阶段通过即可支持继续投入实验，不能作为默认切换的性能验收；第三阶段可在完整产品迁移前
决定是否值得实施。正式交付仍须验证配置、report、apply、终端与清理的完整产品路径。
若收益只来自重验而树无净收益，树继续延期，不将重验的收益当作树的验收证据。

### 8.6 第一阶段结果与待决事项

[E005](../experiments/E005-pf-multi-resolution-search-simulation.md) 已完成纯算法矩阵：13,460 次策略运行，
模拟 A 与当前算法 3,362 个 public-seam 差分对照通过，最终产物独立复跑一致。
2,883 个单坐标场景中，B 的 direct oracle miss 比 A 少 36.75%，C 多 2.93%，D 少 23.96%；
D 比 B 多 20.22%。多坐标矩阵同样是 B 更优。数字只代表所列合成输入的探针计数，不代表真实耗时。

该结果支持先验证 predecessor 重验的真实成本，尚不支持将树作为默认性能优化。
树方案因此转入本文，保留待证设想，暂不进入实施；resolution 改名与重验已由 D033 独立完成。
第一阶段通过不表示原 D031 AC10 的真实收益验收完成，也不授权树方案转为默认实现。

## 9. 转入 Design 前的门槛与候选验收项

继续探索应先解释 E005 的退化，在等价缓存与 predecessor 重验条件下建立树的独立收益。
纯模拟只能支持继续实验；默认切换需要真实 evaluator 对照，覆盖代表性输入、波动与退化，
同时衡量完整 U 的内存、树构造和报告体积。尚未满足这些门槛，树搜索继续留作 Concept。

若证据支持推进，新的 Design 可评审下列验收项，并映射至独立 Plan；它们目前不是承诺：

| 候选项 | 待验证内容 |
| --- | --- |
| 完整候选 | 同次 registry 观测、过滤前 DSL inventory、资格与错误时序；resolution 不提前删除 U |
| 结构与代表 | 三层连续 partition、epoch、短 release、稀疏系列、特殊版本、最高合格代表与全部 space × resolution |
| 算法 | 三种 resolution 精确 floor、最低快路、单桶、clipping、跨桶 predecessor、虚拟 sentinel/no-pass |
| 证据整合 | 复用 D033 的缓存、重验、promotion 和非单调契约；跨层重复、目标序列 region 与窗口准确 |
| 报告 | full U/profile identity、派生代表、reader/build/merge/host-partial/explain 和 apply 授权 |
| 成本 | 树相对平面加重验的真实增量、missing trace、波动、退化、内存和报告体积 |
| 产品交付 | focused/full checks、typing/lint/build、Schema/examples、公共路径与链接检查 |
| 文档闭环 | 稳定规则归并 D001/D002/D003/D006/D014；新 Design 与 Plan 同步验收归档 |

原 D031 的统一验收表随范围拆分退役；E005 对原 AC10 的引用是历史证据边界。
D033 拥有其独立验收表。本文将来转入 Design 后保留来源链接和探索结论，不复用 D031 编号。

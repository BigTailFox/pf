# PF Harness Relaxation

- **状态：** 草案
- **日期：** 2026-08-23
- **适用范围：** search probe 与 `check` Declaration Attempt 的环境准备
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)
- **架构接口：** [D010](D010-pf-v1-architecture.md)
- **现行搜索契约：** [D011](D011-pf-runtime-backed-static-search.md)

本文定义 PF 如何在不把 test harness 变成搜索坐标的前提下，防止 harness 自身的最低版本要求错误提高 project dependency floor。本文仍是草案，不取代现行契约，也不能被 CLI、报告或实现描述成已经可用。

## 1. 问题

PF 搜索 package 自身受管直接依赖的最低可用版本。用户配置的 `test-command` 通常还需要 `pytest`、pytest plugin 和 dependency group 中的其他测试工具。这些直接 harness requirement 及其传递依赖可能对 project graph 中的 package 施加更高下限：

```text
Project:  A >= 1.0
Harness:  B >= 2.0
B >= 2.0 requires A >= 1.5
```

若每个 probe 都原样安装 `B>=2.0`，则 `A<1.5` 无法形成测试环境。该结果只证明当前 harness floor 与 probe 冲突，不能证明 package 自身不支持 `A>=1.0`。

PF 必须保留 harness 的直接 package identity、来源和非下限语义，同时允许 resolver 在 baseline 已验证版本以下选择与当前 project graph 兼容的 harness configuration。

## 2. 决策

PF 采用以下规则：

1. search baseline、`pf smoke` 和 `check` 的 declaration-capture 使用用户原始 harness requirements；
2. search 的 exact probe 和 `check` 的 `lowest-direct` Declaration Attempt 使用 relaxed harness requirements；
3. PF 只搜索受管 project dependency vector `P`，不搜索 harness version；
4. 只对用户直接声明的 harness requirements 解除 minimum-version pressure，并以 baseline 中对应直接 distribution 的实际版本作为 ceiling；
5. project graph 在安装 harness 前独立解析，随后逐节点 exact constrain；harness 不得改变其中任何节点的版本；
6. harness-only 间接依赖由 resolver 的默认 highest 策略选择，可以随 `P` 出现、消失或改变版本；它们没有 baseline ceiling，也不是 PF 搜索坐标；
7. baseline 通过后、正式 probe 前冻结 resolver 可见的来源 universe；probe 不从变化中的 index 重新发现候选；
8. 在该冻结 universe 和 relaxation 契约内确定无解的 harness resolution 是 `HARNESS_CONFLICT`，可以拒绝完整 Probe Attempt；
9. 单个已选 harness configuration 的下载、构建或工具失败不证明所有 `H(P)` 都不可安装，在不进行 harness search 的前提下只能是不确定结果。

## 3. 模型

受管 project dependency vector 为：

```text
P = {d1: v1, d2: v2, ...}
```

对一个精确 `P`，PF 先按 project 声明和正常 resolver 策略得到完整 project graph：

```text
G(P) = ResolveProject(P)
```

`P` 只 exact pin 受管直接依赖。固定和非受管直接依赖保持声明语义；project 的间接依赖仍由 resolver 默认选择。`G(P)` 包含安装 project 后实际存在的全部直接和间接节点。

令：

```text
D_H       = 当前 cell 中活跃的直接 harness requirements
U_B       = baseline 对每个可放宽直接 harness distribution 得到的版本 ceiling
R_cell    = baseline 后为当前 cell 冻结的 resolver source universe
Relax(D_H, U_B) = 放宽下限并加入 ceiling 后的直接 harness requirements
```

probe 的 harness configuration 定义为：

```text
H(P) = ResolveCanonicalHighest(
         project_graph = Exact(G(P)),
         harness = Relax(D_H, U_B),
         universe = R_cell,
       )
```

`ResolveCanonicalHighest` 表示固定 resolver 版本、策略和规范输入顺序下的确定结果，不承诺在多包偏序中求一个数学上的逐坐标全局最大值。

最终执行环境为：

```text
E(P) = Install(G(P), H(P))
```

PF 的显式搜索空间仍然只有 `P`：

```text
min P
```

`H(P)` 是环境准备模块隐藏的辅助结果，不进入 `CoordinateSearch` interface。

## 4. Attempt 适用规则

| Verification Role | Resolution | Harness requirements | 是否运行 `test-command` |
| --- | --- | --- | --- |
| search baseline | `highest` | 原始 | 是 |
| smoke baseline | `highest` | 原始 | 是 |
| check declaration-capture | `highest` | 原始 | 否；只捕获静态基线 |
| check declaration | `lowest-direct` | relaxed | 是 |
| search probe | `exact-vector` | relaxed | 由 D003/D011 决定 |

Baseline 不进行 relaxation。若原始 harness 无法安装，search/smoke 的 baseline 或 check 的 declaration-capture 按现行 Verification Role 终止；PF 不使用 relaxed harness 修复用户当前声明的验证锚点。

空 testing dependency group 等价于 `D_H = {}`。此时 relaxation 和 harness ceiling 均为空，环境准备不增加 harness 节点。

## 5. Baseline 与冻结

### 5.1 Baseline harness evidence

Baseline 环境准备顺序为：

```text
resolve and install project
  -> inspect G(B)
  -> install original D_H under Exact(G(B))
  -> inspect E(B)
  -> record baseline direct harness versions U_B
  -> static contract
  -> role 要求时运行 test-command
```

对每个在当前 cell 中活跃、来自 registry 且可排序的直接 harness distribution，`U_B` 保存 baseline 实际安装的规范名称、版本、来源 identity 和 artifact identity。多个 requirement 指向同一规范名称时仍分别保留声明 identity，但共享该 distribution 的实际 ceiling。

URL、Git、path 或 workspace source 不转换成 registry 候选集合；它们保持固定来源和构件 identity。

### 5.2 Resolver source universe

search baseline 完整通过后，或 check declaration-capture 成功后，PF 在任何 relaxed Attempt 前建立一次 `R_cell`。它至少冻结：

- project 受管直接依赖的 CandidateSnapshot；
- 可放宽直接 harness distribution 在 `U_B` 以下的候选版本、artifact locator 和 hash；
- resolver 为这些候选进行传递解析和构建所需的 index response、metadata、artifact 及 build requirement；
- resolver 版本、resolution strategy、prerelease/yanked 规则、cell marker 环境和来源 identity。

冻结阶段可以访问网络。完成后，probe resolution 和安装只消费 `R_cell` 与其中的本地构件，不刷新 index，也不接受运行中新发布或发生变化的 artifact。

`R_cell` 是 resolver 输入快照，不把其中的间接 distribution 变成 PF 的受管依赖或搜索坐标。间接依赖仍由 resolver 在该有限 universe 中按默认策略选择。

无法完整建立 `R_cell` 是 Attempt 前的 `SOURCE_FAILURE / INDETERMINATE`。probe 期间发现快照缺失、hash 不符或必须访问未冻结来源，是 `SOURCE_FAILURE` 或 `INTERNAL_INVARIANT`，不能伪装成 `HARNESS_CONFLICT`。

`R_cell` 具有稳定 digest。相同 cell search 的全部 probe 必须引用同一 digest；不同 digest 下的结果不是相同 Evaluation context。

## 6. Relaxation 变换

Relaxation 对展开 `include-group` 后的每条活跃直接 harness requirement 独立执行，再把同名 requirement 的结果交给 resolver 求交集。

| 原 specifier 语义 | Relaxed 语义 |
| --- | --- |
| 无 specifier | 不变 |
| `>X`、`>=X` | 删除 |
| `==X`，其中 `X` 是可排序的精确版本 | `<=X` |
| `~=X` | 删除其下限，保留 compatible-release 隐含上界 |
| `==X.*` | 删除 prefix 下限，保留该 prefix 的隐含上界 |
| `<X`、`<=X` | 原样保留 |
| `!=X`、`!=X.*` | 原样保留 |
| `===X` | 原样保留；任意相等值不能安全定义“向下” |

完成上述变换后，PF 对每个可放宽直接 distribution 追加：

```text
<= U_B[name]
```

其他 requirement 语义全部保留：

- 规范 package name 与原声明 identity；
- requested extras；
- environment marker；
- named/default index；
- URL、Git、path、workspace source 与 integrity 信息；
- upper bound 和 exclusions。

Prerelease admission 是独立的候选策略，不能因删除一个含 prerelease 的下限而意外改变。PF 必须在变换前确定原 requirement 与 resolver policy 的 prerelease 资格，并把结果写入 `R_cell`；relaxed specifier 只改变版本区间，不重新推断 admission policy。

PF 不增加、删除、替换或重命名直接 harness requirement。若固定来源无法与 `G(P)` 共存，它可以形成 `HARNESS_CONFLICT`，但 PF 不替换为同名 registry package。

该变换必须由一个纯函数实现并具有版本化 identity；不能通过字符串删除符号。`~=`、wildcard equality、epoch、pre/dev/local version 等语义统一由 `packaging` 的 PEP 440 模型解释。

## 7. Project graph 与 harness graph

PF 分开记录：

```text
ProjectGraph       G(P)   安装 harness 前的完整 graph
EnvironmentGraph   E(P)   安装 harness 后的完整 graph
DirectHarness      当前 Attempt 实际选择的直接 harness distribution
```

必须满足：

```text
for every node n in G(P):
    E(P)[n.name].version == n.version
```

因此：

- harness 可以增加 `G(P)` 中不存在的间接节点；
- 某个 baseline harness-only 节点可以在 probe 中消失；
- 不同 `H(P)` 的 harness-only 间接节点可以使用不同版本；
- 新出现的 harness-only 间接节点没有 baseline ceiling；
- 若一个 distribution 已属于 `G(P)`，无论它是否也被 harness 直接或间接引用，其 project version 都不得被 harness 改动；
- 直接 harness ceiling 约束的是用户声明的直接 harness distribution，不扩散到完整 harness 闭包。

集合差 `E(P) - G(P)` 不能完整表达 harness identity：直接 harness distribution 可能已经存在于 `G(P)`。因此证据必须同时保存 DirectHarness selection 和两个完整 graph，不能只保存新增节点集合。

这延续当前 PF 的 project graph 保护语义：PF 只搜索受管直接 project dependency，但安装 harness 时 exact constrain 已解析出的整个 project graph。

## 8. Resolver 与安装语义

relaxed Attempt 的环境准备顺序固定为：

```text
1. 从 R_cell 安装精确 P 与 project
2. inspect 并建立 G(P)
3. 以 Exact(G(P)) + Relax(D_H, U_B) 在 R_cell 中解析 H(P)
4. 安装 resolver 选出的精确 H(P)
5. inspect E(P)，复证 G(P) 未漂移
6. 运行静态检查及本 Attempt 要求的 test-command
```

Resolver 负责在一次正常求解中选择 canonical highest compatible `H(P)`。PF 不：

- 把 harness version 传给 `CoordinateSearch`；
- 为 harness 建立二分、线性或坐标搜索；
- 在 test failure 后尝试其他 harness version；
- 因单个已选 harness artifact 构建失败而枚举更旧 harness configuration；
- 把 `H(P)` 写成独立 lower-bound 结果。

相同 `P`、`R_cell`、relaxation policy 和 baseline ceiling 在一次 Verification Run 内只能解析一次。后续 static、witness、test 或重复 observation 必须复用同一 `H(P)` 和环境 evidence。

## 9. Module interface 与 identity

冻结来源 universe 属于候选/环境准备集群的内部深模块。建议 interface 为：

```text
freeze(package, cell, baseline_environment)
  -> CellResolutionSnapshot | CellFailure
```

`CellResolutionSnapshot` 同时提供现有 project CandidateSnapshot、direct harness candidate snapshot、`R_cell` runtime handle 和 portable digest。SearchCoordinator 与 CompatibilityChecker 只请求一次冻结结果，不自行读取 index、变换 requirement 或拼 resolver constraints。

现有 `PackagePlan.test_requirements: tuple[str, ...]` 不足以表达本设计。ProjectLoader 必须把展开后的每条直接 harness requirement 投影为结构化 `HarnessRequirement`，至少保存：

```text
declaration identity
root/package group provenance
canonical name
requested extras
structured specifier
marker
source identity
original text
```

Relaxation、baseline ceiling、候选冻结和报告都消费该记录；其他模块不得重新解析原始 dependency group 字符串或重新解释 harness source。

现有单一 `ResolutionRequest` interface 保留判别结构，但 relaxed 变体必须携带冻结上下文：

```text
HighestResolution
  -> original harness

LowestDirectResolution(relaxed_harness_context)
  -> check declaration

ExactSelection(selection, relaxed_harness_context)
  -> search probe
```

`EnvironmentFactory.prepare(...)` 仍是唯一外部 method。类型层面不得表达 `highest + relaxed harness`、`exact probe + original harness` 或没有 baseline ceiling/snapshot 的 relaxed Attempt。

Baseline outcome 或 declaration-capture outcome 必须提供建立 relaxed context 所需的 `HarnessBaseline` evidence。`PreparedEnvironment` 必须同时持有：

- project `Proposal`；
- `HarnessResolution`；
- 由二者共同确定的 environment identity。

Harness 仍不是 `Proposal.managed_vector` 的一部分。由于不同 `H(P)` 可以改变静态或动态结果，下列 identity 必须覆盖 relaxation policy、`U_B`、`R_cell.digest`、DirectHarness selection 和 `E(P)` digest：

- Attempt / Evaluation context identity；
- static、witness 和 full Evaluation cache key；
- FailureRecord 与报告中的可移植环境 evidence。

不得只按现有 `proposal_id + S_B digest` 复用在不同 harness environment 中取得的 Evaluation。

## 10. Failure 分类

| 场景 | Cause / disposition | 搜索含义 |
| --- | --- | --- |
| `G(P)` 自身在 `R_cell` 中无解 | `RESOLUTION_CONFLICT / REJECTED` | 拒绝完整 project probe |
| `G(P)` 可解析，但 `Exact(G(P)) + Relax(D_H,U_B)` 被 resolver 确定证明无解 | `HARNESS_CONFLICT / REJECTED` | 拒绝完整 project probe，并继续定界 |
| harness 安装后改变或移除 `G(P)` 节点 | `INTERNAL_INVARIANT / INDETERMINATE` | 环境准备实现违反契约，停止 cell |
| 冻结来源缺失、下载或 hash/source 失败 | `SOURCE_FAILURE / INDETERMINATE` | 未获得兼容性事实，停止 cell |
| probe 选中的 harness configuration 构建失败 | `BUILD_FAILURE / INDETERMINATE` | 未证明其他合法 `H(P)` 都失败，停止 cell |
| test command 以配置失败码正常退出 | `TEST_FAILURE / REJECTED` | 用户动态契约拒绝完整 probe |
| test command 启动失败、timeout、signal 或输出不完整 | 现行 tool cause / `INDETERMINATE` | 停止 cell |

Baseline 继续使用原始 harness：其确定 resolution/build/harness/test failure 按 D005/D008 的 Baseline 规则终止。`check` declaration 上的 `HARNESS_CONFLICT` 是 Declaration Rejection，表示当前声明下界不能形成用户要求的验证环境；它不进入 CoordinateSearch。

`HARNESS_CONFLICT` 的前提是 resolver 对完整 frozen feasible set 给出确定无解，而不是“当前选中的某个 harness version 安装失败”。这使其可以安全成为 Probe Rejection，同时避免引入隐含的 harness search。

Adapter 必须从 resolver 的结构化、完整 resolution outcome 建立该事实。stderr substring、截断输出或普通非零退出不足以证明 frozen feasible set 无解；这些结果按 `TOOL_FAILURE / INDETERMINATE` 处理。

## 11. 搜索流程

```text
BASELINE / DECLARATION CAPTURE
  project + original harness
            |
            v
  resolve, inspect, verify role contract
            |
            v
  capture HarnessBaseline U_B
            |
            v
  freeze CellResolutionSnapshot R_cell
            |
            v
SEARCH PROBE / DECLARATION
  exact P or lowest-direct request
            |
            v
  resolve G(P) from R_cell
            |
            v
  resolve H(P) from Exact(G(P))
      + Relax(D_H,U_B)
      + R_cell
            |
       +----+----------------+
       |                     |
       v                     v
  HARNESS_CONFLICT       prepared E(P)
  REJECTED               static/runtime contract
                             |
                        PASS / TEST_FAILURE /
                        INDETERMINATE
```

Harness Relaxation 不构成新的 search phase。冻结是 Attempt 前的输入准备；`H(P)` resolution 是 `EnvironmentFactory.prepare` implementation；`CoordinateSearch` 仍只观察 project vector 对应的 `ProbePass | ProbeRejection | ProbeIndeterminate`。

D011 已落地；static region、runtime witness 和最终直接测试规则保持不变。`HARNESS_CONFLICT` 是 prepare 阶段对完整 Attempt 的确定 resolution Rejection，不是 static-only boundary。

## 12. 动态测试契约

PF 不区分 full test、smoke test 或其他用户测试形式。`[tool.pf].test-command` 本身就是动态兼容性契约：

```text
environment prepared + command exits 0
  -> PASS

environment prepared + configured failure exit code
  -> TEST_FAILURE
```

PF 不要求 relaxed environment 与 baseline：

- 收集相同数量的 tests；
- 加载相同版本的测试工具；
- 保持相同 harness-only 间接 graph；
- 保持测试工具内部实现一致。

PF 不对正常 test failure 猜测是 package、test code 还是 relaxed harness 行为造成。用户应提供快速、稳定并覆盖关键 dependency-facing behavior 的命令；不同测试规模不改变分类规则。

## 13. Correctness Invariants

### H1 — Project candidate immutable

Harness resolution 不得修改当前受管 project dependency vector。

### H2 — Baseline unchanged

Baseline 和 declaration-capture 始终使用用户原始 harness requirements。

### H3 — Relaxation is direct-harness-only

只有用户直接声明的 harness requirements 可以变换；project requirements 与 harness 间接 requirement 不被 PF 改写。

### H4 — Direct harness downward only

每个可放宽直接 harness distribution 的版本不得高于同 cell baseline 的实际版本。该 ceiling 不扩散到 harness-only 间接闭包。

### H5 — Project graph preserved

安装 harness 后，`G(P)` 的 package 集合和每个版本均保持不变。

### H6 — Exhausted relaxed set is probe evidence

只有在同一冻结 universe 中确定证明 `Exact(G(P)) + Relax(D_H,U_B)` 无解，才可形成 `HARNESS_CONFLICT / REJECTED` 并推动搜索边界。

### H7 — No harness search

Harness 不进入显式搜索空间，不产生独立 lower-bound 结果，也不因 build/test failure 枚举替代版本。

### H8 — Frozen resolver universe

同一 cell 的 baseline 后 probe 共享同一个 `R_cell`；probe 不刷新 index 或接收新构件。

### H9 — Environment-scoped evidence

任何 static、witness 或 test evidence 必须绑定产生它的 `Proposal + HarnessResolution`，不能跨 `H(P)` 复用。

## 14. 非目标

Harness Relaxation 不试图：

- 求直接或间接测试依赖的最低版本；
- 对 harness-only 间接依赖建立 baseline ceiling；
- 证明不同 harness version 行为完全一致；
- 隔离两套 Python dependency namespace；
- 修改用户 dependency group；
- 修复测试或为失败做因果归因；
- 搜索一个能让失败测试通过的 harness version；
- 在 selected harness build failure 后枚举其他 configuration；
- 把 transitive resolver selection 写成 PF floor 结果；
- 允许 probe 从动态 index 获得未冻结候选。

## 15. 所有权

| 规则 | 唯一所有者 |
| --- | --- |
| 用户结果承诺、test group、test command 与退出码 | D001 |
| `EnvironmentFactory`、ResolutionRequest、environment identity 与 cache seam | D002/D010；本文确认后归并 |
| project coordinate search、Probe Rejection 与边界 | D003/D011 |
| cause、disposition、FailureRecord 与 diagnose 文案 | D005 |
| Attempt Role、check/search/smoke 序列与 Journal | D008 |
| relaxation transform、direct ceiling、resolver snapshot 与 graph invariants | 本文；确认后归并 D001/D002/D005/D008 |

## 16. 对现行契约的取代

本文确认并落地时必须同步替换以下现行条款；此前仍以现行文档和代码为准：

| 文档 | 被取代或扩展的规则 |
| --- | --- |
| D001 §5.2 | 所有 Attempt 都安装原始测试支撑 requirement；没有 direct harness relaxation/ceiling/source freeze |
| D002 §7.1、§8.1–§8.2 | PackagePlan 只保存原始 test requirement 字符串；CandidateBuilder 只冻结 project 受管直接候选；EnvironmentFactory 的 ResolutionRequest 不携带 relaxed context，PreparedEnvironment 不记录 HarnessResolution |
| D003 §3 | baseline 后只冻结 project CandidateSnapshot；不冻结 harness/resolver universe |
| D005 §8–§9.2 | harness conflict 没有区分 relaxed feasible-set 无解与单个 selected harness configuration 失败 |
| D008 §5.2 | check 的 `lowest-direct` 使用与 highest 相同的原始 harness requirements |
| D010 §5 | `LowestDirectResolution` / `ExactSelection` 不携带判别的 relaxed harness context |
| D011 §7、§16 | 泛称 harness error 为 UNKNOWN、并整体保留现行 harness rejection 资格；本文改为按 frozen resolution proof 精确区分 |

## 17. 验收标准

1. search/smoke baseline 与 check declaration-capture 的 resolver 输入逐字使用原始展开后 harness requirements；
2. `pytest>=8,<9,!=8.2` 在 baseline `8.4` 下变为等价于 `pytest<9,!=8.2,<=8.4` 的结构化 requirement；
3. `~=1.4.5` 和 `==1.4.*` 的隐含 `<1.5` 上界保留，minimum pressure 被删除；`===`、marker、extras 与来源不变；
4. 删除 prerelease lower bound 不改变该 requirement 已确定的 prerelease admission policy；
5. relaxed resolver 不能选择高于 `U_B` 的直接 harness version；
6. baseline harness-only 间接节点可以在 probe 消失，新间接节点可以出现；两者都不进入 project vector；
7. harness 直接或间接引用 `G(P)` 节点时只能使用 `G(P)` 的精确版本；任何 graph drift 为 Indeterminate；
8. 同一 cell 的所有 probe 使用同一 `R_cell.digest`，冻结后发布的新版本不参与本次运行；
9. probe 期间禁止 index refresh；source snapshot miss 不能分类成 `HARNESS_CONFLICT`；
10. relaxed requirement 在 frozen universe 中得到结构化、完整的确定无解结果时产生 `HARNESS_CONFLICT / ProbeRejection`，CoordinateSearch 可以据此继续提高当前坐标；
11. stderr substring、截断输出或普通 resolver 非零退出不能形成 `HARNESS_CONFLICT`；
12. selected harness configuration 的构建失败产生 Indeterminate，不触发隐含 harness version 枚举；
13. 相同 `P` 只解析一次 `H(P)`；缓存不得跨 HarnessResolution identity 命中；
14. `check` lowest-direct 使用 relaxation，HARNESS_CONFLICT 成为 Declaration Rejection；
15. normal `test-command` failure 仍是 `TEST_FAILURE`，不因 harness 版本变化改写 cause；
16. 报告能复证 direct harness selection、两个 graph、relaxation policy、baseline ceiling 和 resolver snapshot digest 的闭环；
17. D011 的 static-only observation 不能因本设计变成 Rejection，final floor 仍由其自身完整 test pass 授权。

## 18. 决策记录

### D1：Relaxation 同时用于 search probe 与 check declaration

两者都在验证 project dependency lower bound。只修 search 会让 `pf check` 继续把原始 harness floor 误报为声明下界不兼容。

### D2：确定无解的 relaxed harness 是 Probe Rejection

Baseline 已证明原始环境可运行；relaxation 扩大了直接 harness requirement 的可行集合。若 resolver 在同一冻结 universe、精确 `G(P)` 和 direct ceilings 下仍确定无解，则该完整 project vector 不能形成用户要求的开发/测试环境，可以作为局部 Rejection。PF 不把该结论全局归因到某一个 dependency version。

### D3：Ceiling 只约束直接 harness distribution

完整 harness 闭包会随 direct harness version 和 project graph 改变，baseline 中不存在的节点没有可定义的 ceiling。PF 只对用户声明的直接 requirement 承担变换责任；间接节点交给 resolver 默认策略，保持产品模型简洁。

### D4：Project graph 整体 exact constrain

PF 虽然只搜索受管直接 project dependency，但被测试对象是 independently resolved `G(P)`。Harness 可以增加辅助节点，不能改写被测 graph。该规则延续现行 EnvironmentFactory 行为。

### D5：冻结 resolver universe，而不是把 transitive dependency 变成坐标

只冻结直接候选仍会让 transitive resolution 读取变化中的 index，无法使 `H(P)` 确定。`R_cell` 冻结 resolver 输入，但 transitive selection 仍由默认策略决定，不进入 PF 搜索结果。

### D6：Selected harness build failure 不建立边界（待确认）

Resolver 选出的 canonical configuration 构建失败，只否定该具体构件执行；在不枚举其他 `H` 的前提下不能证明 relaxed feasible set 全部失败。把它作为 Rejection 会重新引入错误 floor。

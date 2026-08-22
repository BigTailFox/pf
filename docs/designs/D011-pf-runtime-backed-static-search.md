# PF runtime-backed 静态引导搜索

- **状态：** 现行
- **归并：** 现行条款已归并 D001–D005、D008
- **策略版本：** `static-transition-v1`
- **日期：** 2026-08-23
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **现行搜索算法：** [D003](D003-pf-search-algorithm.md)
- **现行静态证据：** [D004](D004-pf-ty-enhancement.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)

本文记录 PF 不再把新增 `ty` diagnostic 直接解释为 runtime incompatibility 的决策。现行规则已归并到 D003/D004，并同步进入 D001、D002、D005 与 D008；本文保留决策背景、迁移范围和验收依据，不再作为“待实现”覆盖层。

## 1. 问题

落地前的 D004 定义：

```text
increment(P) != ∅ -> STATIC_FAIL
```

当时的 D002/D005/D008 随后把 `StaticFailEvaluation` 直接变成 `STATIC_REGRESSION` Probe Rejection；D003 用该 Rejection 缩窄搜索空间，并以 static coordinate fixpoint 产生 `V_static`。

这条推理过强。新增 diagnostic 可能来自：

- `.pyi`、`py.typed` 缺失或不完整；
- annotation 精度或正确性变化；
- overload、generic、protocol 或 narrowing contract 变化；
- runtime 动态导出、descriptor、C extension 等无法被静态模型完整表达的接口。

因此：

> **新增 `ty` diagnostic 是静态兼容性退化的证据，但不必然是 runtime 不兼容的证据。**

若静态 regression 可以单独成为拒绝边界，PF 可能把 runtime-compatible 版本排除在搜索空间之外并错误提高 dependency floor。

## 2. 决策

PF 使用以下原则：

> **Static analysis discovers compatibility transitions; runtime evidence establishes compatibility boundaries.**

静态分析负责：

- 发现 dependency environment 变化引起的静态状态变化；
- 对 probe 排序并划分 static region；
- 触发 targeted runtime witness 或 `[tool.pf].test-command`；
- 为 runtime rejection 提供相关诊断与归因上下文。

静态分析不再：

- 单独产生 Probe Rejection；
- 单独推进 `REJECTED* PASS*` 的拒绝边界；
- 证明某个 Proposal runtime-compatible；
- 授权 final floor。

本设计不新增“smoke command”配置。文中的动态测试始终指现有 `[tool.pf].test-command`；`pf smoke` 仍是 D001 定义的用户命令。

## 3. Baseline、increment 与 fingerprint

### 3.1 Baseline

对一个 cell 的 highest Proposal `B = V_hi`：

```text
S_B = diagnostics(B)
```

`S_B` 仍是 D004 定义的规范化 `DiagnosticIdentity` 多重集。它只在相同 cell、source snapshot 和 Evaluation policy 内有效；baseline 已存在的 diagnostic 不参与 candidate regression 判定。

捕获 `S_B` 的同一次 `TyCheck` 构成 `B` 的空增量静态事实，不重跑 `ty`。在 `search` / `pf smoke` 中，`B` 是否可作为完整通过的 baseline 仍由其自身 `test-command` 决定；`check` 的 highest 只捕获静态参考状态，不把它冒充搜索锚点。

### 3.2 Increment

对同 scope Proposal `P`：

```text
Δ(P) = diagnostics(P) ⊖ S_B
```

`⊖` 沿用 D004 的 multiset subtraction。baseline diagnostic 消失或重数减少不进入 `Δ(P)`；相同 identity 多出的每个重数分别进入增量。

```text
Δ(P) = ∅  -> STATIC_UNCHANGED
Δ(P) != ∅ -> STATIC_REGRESSION
```

这两个值只描述静态事实，不是 D005 disposition，也不等价于 `PASS` / `REJECTED`。

### 3.3 Canonical fingerprint

PF 对按 D004 规范顺序保存的完整 `Δ(P)` identity 列表计算 fingerprint：

```text
static_fingerprint(P) =
  sha256("pf:ty-static-state:static-transition-v1\0"
         + canonical incremental identity list)
```

重复 identity 必须按实际重数进入列表。因此 `{A}`、`{A×2}`、`{A,B}` 与 `{C}` 是不同 state。Message、severity、GitLab fingerprint、`ty` 退出码和 diagnostic 展示顺序不进入 fingerprint。空增量也有唯一 digest，不能用空值代替。

改变 diagnostic identity、multiset 代数或 fingerprint 输入必须提升策略版本；旧 `increment-v2` Evaluation 不能与本文结果 merge/apply。

## 4. Static region

Static region 不是“全局出现过相同 fingerprint 的所有 Proposal”。它只存在于 D003 的一个固定一维切片中：

```text
Slice = (
  cell,
  source snapshot,
  policy identity,
  S_B digest,
  active dependency,
  exact values of every other coordinate,
)
```

在该切片的冻结候选顺序中，fingerprint 相同且连续的最大区间是一个 static region。以下情形必须视为不同 region：

- fingerprint 相同但中间出现过其他 fingerprint；
- active dependency 或任一其他坐标不同；
- cell、snapshot、baseline digest 或 policy 不同。

Region identity 至少包含 Slice、fingerprint 和已观测连续区间。稀疏 probe 尚未连接的两个同 fingerprint 点不能预先合并成一个 region。

Static region 只是一项调度提示。某个 region 内一个 Proposal 的动态通过不证明该 region 的其他 Proposal 通过；它只允许 D003 在该 region 内继续使用廉价静态 probe。任何未直接运行 `test-command` 的 Proposal 都不得记录为 `COMPATIBLE`、ProbePass 或 verified floor。

## 5. Diagnostic 分类

PF 对 `Δ(P)` 中每条 diagnostic 做确定分类。分类只使用结构化 code、规范路径、源码 AST、Proposal graph 和受管 dependency mapping；不得依赖人类 message 文本、severity 或模糊字符串匹配。

### 5.1 Strong static regression

Strong regression 是能够指向 runtime 名称可达性的 diagnostic，主要包括：

- unresolved import 或 module；
- unresolved imported symbol；
- unresolved attribute 或 member。

一条 diagnostic 只有同时满足以下条件，才有资格进入 strong 路径：

1. code 位于版本化 strong allowlist；
2. PF 能从结构化 diagnostic 和对应源码 AST 唯一恢复被引用的 module/symbol/member；
3. import root 或 owner 能可靠映射到当前 Proposal 中的一个受管 dependency；
4. PF 能生成 §6 定义的无歧义 witness plan。

任一条件不满足时不得猜测归因，按 general regression 处理。特别是，仅凭 `unresolved-attribute` code 或 diagnostic message 不足以生成 witness。

### 5.2 General static regression

以下 diagnostic 属于 general regression：

- argument 或 return type mismatch；
- overload mismatch；
- assignment incompatibility；
- generic、protocol、narrowing 或 inference 变化；
- typing metadata 质量变化；
- 无法可靠归因或无法生成 witness 的 unresolved diagnostic；
- strong allowlist 之外的其他新增 diagnostic。

General regression 不能单独形成拒绝边界。搜索首次进入该 static region 时，必须在继续探测该方向前运行 `test-command`。

一个 `Δ(P)` 可以同时含 strong 与 general diagnostic。只要仍有不能由 confirmed-missing witness 覆盖的增量，witness 未确认缺失时就必须继续运行 `test-command`。

## 6. Targeted runtime witness

Runtime witness 是 PF 的可选内部优化，不是新用户配置，也不是 `test-command` 的替代品。PF 只在 §5.1 四个条件全部成立时生成它；无法可靠生成时直接走 general 路径。

### 6.1 Witness plan

Witness plan 保存：

```text
RuntimeWitnessPlan
  diagnostic identities
  managed dependency
  operation             import-module | import-symbol | has-member
  module
  owner?                 has-member 时必填
  symbol_or_member?
  planner policy version
```

计划必须从当前 source snapshot 和 Proposal graph 重新建立，不跨 Proposal 或运行复用。计划内容进入 Evaluation policy identity；用户不能通过 `ty-args` 注入 witness 代码。

### 6.2 执行与结果

PF 在当前 Proposal 的 prepared environment 中，用所选 interpreter 运行 adapter-owned、无 shell 的结构化 witness harness。Harness 只回答目标 module/symbol/member 是否存在，并产生机器可解析结果：

```text
PRESENT
CONFIRMED_MISSING
NOT_APPLICABLE
ToolFailure
```

- `PRESENT`：目标 runtime 接口存在；strong diagnostic 降级为普通 static regression，并立即运行 `test-command`；
- `CONFIRMED_MISSING`：harness 明确确认计划中的目标不可 import 或不存在；形成 runtime incompatibility evidence，无需再运行 `test-command`；
- `NOT_APPLICABLE`：执行完成但无法对目标作无歧义判断；不形成负向证据，立即运行 `test-command`；
- timeout、signal、启动失败、截断、非法 harness 输出或 interpreter/environment 故障：产生相应 ToolFailure，最终为 `UNKNOWN`，不能退化成 rejection。

任意非零退出、任意 import side-effect exception 或 traceback 文本都不能自动读成 `CONFIRMED_MISSING`。Harness 必须显式核对缺失对象就是 plan 中的目标；否则返回 `NOT_APPLICABLE` 或 ToolFailure。

Witness `PRESENT` 不是 compatibility pass。它只否定“该 runtime 名称缺失”这一假设；当前 Proposal 仍须由 `test-command` 决定。

## 7. Runtime compatibility evidence

本文使用三值语义：

```text
COMPATIBLE
  完整运行当前 Proposal 的 test-command 并通过

INCOMPATIBLE
  runtime witness 得到 CONFIRMED_MISSING
  或 test-command 以配置的 test-failure-exit-codes 退出

UNKNOWN
  timeout、signal、启动失败、resolver/source/harness/tool/environment error，
  输出不完整，或其他无法可靠归属的结果
```

完整测试通过产生 PASS Evaluation；Probe 上的 PASS 再投影为 ProbePass。D005 按 Attempt role 和证据完整性把 `INCOMPATIBLE` / `UNKNOWN` 分别映射为 Rejection 或 Indeterminate。Witness `CONFIRMED_MISSING` 使用新的 runtime cause `RUNTIME_INTERFACE_MISSING`；不得继续用 `STATIC_REGRESSION` 表示该 Rejection。

本节只限制由 static/`ty` 路径引出的边界。D005 已定义的确定 resolution、build 或 harness conflict 仍可拒绝 Attempt；本文不撤销这些 prepare/runtime contract。

最终通过定义为：

```text
VERIFIED(P) iff TEST_COMMAND_PASS(P)
```

因此：

- static unchanged 不能授权 floor；
- static regression + witness `PRESENT` 不能授权 floor；
- region 中其他 Proposal 的动态通过不能授权 `P`；
- witness 只产生负向证据，永远不产生最终正向证据；
- final floor 必须保存其自身 Proposal 上直接、完整的 `test-command` pass。

## 8. Probe 路由

对一个成功 prepare 的 candidate Proposal，顺序固定为：

```text
run ty and compute Δ + fingerprint
  ├── TyCheck unavailable/incomplete
  │     -> UNKNOWN
  ├── first observation of a new static region
  │     ├── eligible strong regression
  │     │     -> run witness
  │     │          ├── CONFIRMED_MISSING -> INCOMPATIBLE
  │     │          ├── PRESENT/NOT_APPLICABLE -> run test-command
  │     │          └── ToolFailure -> UNKNOWN
  │     └── unchanged/general/no reliable witness
  │           -> run test-command
  └── already observed contiguous static region
        -> may remain a cheap static observation for search guidance
```

“First observation” 以 §4 的 region scope 判断，不按全局 fingerprint cache 判断。进入新 region 后必须先取得 runtime result，才能继续越过该 region 探测更低版本；不得先批量跨过多个未验证 region，再补跑动态验证。

同 region 的 cheap observation 没有 disposition。若它后来成为坐标提交点、最终候选或 boundary endpoint，D003 必须先对该精确 Proposal 运行所需动态验证；验证前不能把它序列化为 ProbePass/ProbeRejection。

## 9. 搜索语义

### 9.1 单一 runtime-backed 搜索

本文落地后，D003 不再先求一个可作为拒绝边界的 static coordinate fixpoint，也不再用 `V_static` 的“完整 PASS 必先 static pass”证明 fast path 安全。搜索改为一条 runtime-backed 路径：static state 只提供 transition/region hint，所有边界仍由 D005 disposition 驱动。

搜索必须维持两个层次：

- **committed current**：该精确向量已有直接 `test-command` pass；
- **static frontier**：只有 static state/region 事实，可以继续廉价探索，但不是 current、PASS 或 boundary。

坐标提交前必须把 frontier Proposal 晋升为直接动态结果。晋升通过才可替换 current；晋升拒绝则记录 observed boundary 并按当前切片重新定位；晋升不确定则立即终止 cell。

### 9.2 Region 内复用

同一个已验证 region 内，PF 可以不对每个中间版本重复运行 `test-command`，但必须满足：

1. 每个省略测试的点仍有自己的完整 `TyCheck` 与 fingerprint；
2. 省略只影响 probe 成本，不产生兼容性 disposition；
3. region scope 与连续性已经按 §4 建立；
4. 任何会被提交或返回的点先直接运行 `test-command`；
5. 提交点动态失败时在该 region 内继续定界，不能把先前 region representative 的 pass 当作该点的 pass。

### 9.3 动态边界与非单调

由 static 路径触发且允许形成边界的证据只有：

```text
eligible strong static regression
  + witness CONFIRMED_MISSING
```

或：

```text
candidate static state
  + test-command rejection
```

Static regression 本身、witness `PRESENT`、region representative 的 pass 或 cheap static observation 都不能移动拒绝边界。

动态 Rejection 仍只是在 D003 当前固定切片上的 observed boundary。PF v1 继续采用局部 `REJECTED* PASS*` 假设；若之后直接观察到更低版本 `PASS`、更高版本 `REJECTED`，按 D003 的 `NON_MONOTONIC` fallback 停止，而不是选择其中一个结果。稀疏 probe 仍不认证未观察 hole。

## 10. check、smoke 与 search

### 10.1 `check`

`check` 继续先在 `highest` Declaration-capture Attempt 捕获 `S_B`，再关闭环境并对 `lowest-direct` Declaration Attempt 做完整验证。`lowest-direct` 的 static regression 不再短路：按 §8 运行 witness 或 `test-command`。只有直接 `test-command` pass 才使 declaration compatible；confirmed-missing witness 或 test rejection 使其 incompatible；其他结果 unknown。

### 10.2 `smoke`

`pf smoke` 继续在 highest environment 复用 capture 的同一次 `TyCheck`，然后运行一次 `test-command`。Baseline diagnostic 仍只展示为 warning；本文不增加第二次 `ty`、witness 或候选发现。

### 10.3 `search`

`search` 的 highest baseline 继续要求直接完整 PASS。候选 probe 使用 §8/§9；static state 和 region observation 进入报告证据，但只有 ProbeRejection 可以形成 predecessor boundary。最终 `CellSuccess.final_evaluation` 必须引用 final Proposal 自身的直接 test pass。

## 11. Schema、缓存与报告

公共证据至少保留：

- `S_B`、baseline digest 和 baseline `TyCheck`；
- 每个 candidate 的完整 `TyCheck`、`Δ(P)` 与 `static_fingerprint`；
- 每条增量 diagnostic 的 strong/general 分类及分类策略版本；
- 生成过的 witness plan、结构化 witness result 和机械 ProcessResult；
- static region 的 Slice、fingerprint、已观测连续区间和代表点；
- region runtime result 与具体 Proposal 的引用；
- cheap static observation 尚无 disposition 的事实；
- 每个 ProbePass/ProbeRejection/ProbeIndeterminate 的直接 runtime evidence；
- final Proposal 与其自身 test pass 的闭环。

Schema validator 必须拒绝：

- 把非空 `Δ(P)` 直接编码成 ProbeRejection；
- 把 witness `PRESENT` 或 `NOT_APPLICABLE` 编码成 ProbePass；
- 把 region representative 的 pass 复制给其他 Proposal；
- 把未直接 test-pass 的 Proposal 作为 final floor；
- 跨 Slice 合并 region；
- 缺失、截断或跨 scope 的 static/witness/test 证据。

缓存按实际证据层分离：

```text
TyCheckKey          = proposal_id
StaticStateKey      = (proposal_id, S_B digest, static policy identity)
WitnessKey          = (proposal_id, witness plan identity)
TestEvaluationKey   = (proposal_id, S_B digest, full policy identity)
RegionKey           = (Slice, fingerprint, observed contiguous interval)
```

Region cache 只缓存调度事实，不能伪造 Proposal-level Evaluation。跨运行 Evaluation cache 仍是非目标。

CLI 按 D006 展示新增 static diagnostic、witness 与 test 结果。Static regression 若动态通过，应明确呈现为 warning/transition，而不是 rejection；runtime rejection 的 FailureRecord 可以引用相关 diagnostic，但不能把 `ty` message 当作最终 cause。

## 12. 策略 identity

Evaluation policy identity 新增：

```text
static_policy          = static-transition-v1
comparison             = multiset-subtraction
fingerprint            = ordered-incremental-identity-multiset
region_scope           = fixed-slice-contiguous
strong_classifier      = <version>
witness_planner        = <version>
witness_harness        = <version>
boundary_rule          = runtime-evidence-only
final_verification     = direct-test-command-pass
```

实际 `ty` distribution、有效 `ty` 配置、diagnostic identity 和 output format 继续按 D004 进入 identity。`test-command`、failure exit codes、test harness requirements 和 timeout 继续按 D001/D002 进入 full policy identity。

任何 strong allowlist、AST 恢复规则、dependency attribution、witness output protocol、region scope 或 final verification 规则变化都必须提升对应版本，使不兼容报告不能 merge/apply。

## 13. 所有权

| 规则 | 唯一所有者 |
| --- | --- |
| `ty` argv、GitLab JSON、DiagnosticIdentity 与 `S_B` | D004 / `TyAdapter`、`StaticEvaluator` |
| `Δ(P)`、fingerprint、strong/general 分类 | 本文；落地后归并 D004 |
| witness planning、harness 与结构化结果 | 本文；落地后归并 D004/D002 interface |
| region scope、probe 顺序、frontier、commit 与边界 | 本文；落地后归并 D003 |
| cause、Rejection/Indeterminate 与 FailureRecord | D005 |
| Attempt 序列、Role 与 Journal | D008 |
| `test-command`、命令结果与退出码 | D001 |
| CLI 信息层级与文案组织 | D006 |

## 14. 已完成的契约取代

本次落地已同步替换以下旧条款：

| 文档 | 被取代的现行规则 |
| --- | --- |
| D001 §5.3 | “D004 静态回归”本身可形成 Probe Rejection |
| D002 §8.3 | `FullEvaluator` 只在 `StaticPassEvaluation` 后运行测试 |
| D003 §1–§3 | `V_static`、STATIC COORDINATE FIXPOINT 与现行两阶段状态机 |
| D003 §8 | static fixpoint、`V_static` fast path 及其安全性证明 |
| D003 §9 | 只保存 static/full probe、没有 region/witness/frontier 的报告结构 |
| D004 §3、§6 | `increment != ∅ -> STATIC_FAIL` 与 `StaticFailEvaluation` |
| D004 §6–§8 | `FullEvaluator` 静态短路及 candidate 静态 Rejection |
| D004 §9、§11 | `increment-v2` policy 和 `STATIC_FAIL` 不变量 |
| D005 §6、§8 | `STATIC_REGRESSION` 单独满足 Rejection 资格和分类矩阵 |
| D008 §5.2、§6.2 | Declaration/Probe 的 `StaticFailEvaluation -> STATIC_REGRESSION Rejection` |

## 15. 验收标准

1. Baseline 已有 diagnostic 仍被 `S_B` 抵消；increment 保持 multiset 语义。
2. `{A}`、`{A×2}`、`{A,B}`、`{C}` 与空增量得到稳定且互异的 fingerprint。
3. 相同 fingerprint 在不同 Slice 或被其他 state 隔开时不复用 region。
4. General regression 首次进入 region 时立即运行 `test-command`，不能单独产生 Rejection。
5. Strong diagnostic 无法可靠归因或规划 witness 时运行 `test-command`，不解析 message 猜测目标。
6. Witness `PRESENT` / `NOT_APPLICABLE` 后运行 `test-command`；只有 `CONFIRMED_MISSING` 产生 runtime negative evidence。
7. Witness timeout、坏输出或工具故障为 Indeterminate，不能建立边界。
8. 同 region 的中间 cheap probe 不重复运行测试，但也不生成 Proposal-level PASS。
9. Region 内最终候选必须直接运行 `test-command`；若失败则重新定界，不能沿用 representative pass。
10. Final floor、Proposal、ProbePass 和 TestEvaluation 证明同一个精确向量直接通过。
11. 直接观察到低版本 PASS、高版本 Rejection 时返回 `NON_MONOTONIC`。
12. `check` 的 lowest-direct static regression、`search` candidate 和 `pf smoke` baseline 分别遵守 §10 的动态语义。
13. 报告 validator 拒绝 static-only boundary、跨 Proposal pass 复用和旧新策略证据混合。
14. 没有新增用户 witness/smoke 配置；动态命令仍只有 `[tool.pf].test-command`。

## 16. 非目标

- 要求项目 type-clean；
- 用 static-only floor 或 witness pass 授权 floor；
- 证明一个 static region 内所有 Proposal runtime 等价；
- 为所有 unresolved attribute 自动生成 witness；
- 解析 diagnostic message 做 dependency attribution；
- 新增用户定义的 witness command 或第二个 smoke command；
- 任意非单调搜索、hole certification 或 exhaustive version testing；
- flaky retry、跨运行 Evaluation cache 或 failure attribution 到单个版本的全局结论；
- 改变 deterministic resolution/build/harness rejection 的既有资格；
- 改变 `pf smoke`、`check`、`search`、`apply` 的用户命令表面。

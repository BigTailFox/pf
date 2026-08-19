# PF ty 增量静态检查

- **状态：** 草案
- **产品契约：** [D001](D001-pf.md)
- **实现设计：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)

本文修订 `ty` 在 PF 中的兼容性语义。搜索算法、候选策略和完整测试判据仍以 D001/D003 为准；本文只改变“什么算一次静态通过”。

## 1. 动机

`ty` 会同时报告两类诊断：

1. 依赖接口变化引起的不兼容，这是 PF 要定位的边界；
2. 项目源码本身已有的 typing 错误，与当前直接依赖版本无关。

v1 把 `ty` 退出码 `1` 直接映射为 `STATIC_FAIL`。项目既有诊断因此会：

- 让 `pf check` 在 `lowest-direct` 下失败；
- 让 search 的最高版本 baseline 变成 `BASELINE_FAILED`；
- 让所有更低候选同样 `STATIC_FAIL`，静态定界失去区分力。

这些失败不是依赖下界证据。PF 不负责修复仓库既有的类型错误，但必须能在它们存在时继续做兼容性搜索。

## 2. 新契约

对每个 cell，PF 先在当前声明、当前源快照上按 uv 默认最高版本策略解析，得到精确向量 `V_hi`，并在该环境运行一次 `ty`。这次检查的规范化诊断集合记为 `S_hi`，称为该 cell 的**静态诊断基线**。

之后每一次 `ty`——包括 `pf check` 的 `lowest-direct` 评估，以及 search 的全部 Static/Full probe——都与 `S_hi` 做增量比较：

```text
increment(P) = diagnostics(P) − S_hi

increment(P) = ∅  →  STATIC_PASS
increment(P) ≠ ∅  →  STATIC_FAIL
```

`ty` 退出码 `0` 或 `1` 不再单独决定兼容性状态。退出码 `1` 且增量为空，视为静态通过。

消失的基线诊断不构成失败。PF 只把**新增**诊断当作依赖不兼容。

`pf check` 与 `pf search` 使用同一套增量语义，由同一个 `StaticEvaluator` 判定。禁止 check 走绝对干净、search 走增量的双轨定义。

## 3. 向量与基线

### 3.1 `V_hi`

`V_hi` 是当前声明在该 cell 上按最高允许版本解析出的精确受管向量。它不读取 lock、操作者 `.venv` 或既有安装。

在 search 中，`V_hi` 就是 D003 的 baseline 向量 `B`。

在 check 中，`V_hi` 只用于捕获 `S_hi`。check 的兼容性对象仍是 `lowest-direct` 解析向量，不是 `V_hi`。

### 3.2 `S_hi`

`S_hi` 来自 `V_hi` 上那一次成功的 `ty` 运行，不是项目配置，也不是跨运行缓存。

同一 cell、同一源快照、同一策略内，`S_hi` 冻结后不再刷新。不同 cell 各自拥有自己的 `S_hi`。

无法建立 `S_hi` 时，该 cell 不能产生静态兼容性证据，结果为对应非证据状态，不能假装 `ty` 干净或假装增量通过。

### 3.3 捕获与自比较

用于捕获 `S_hi` 的那次 `TyCheck` 同时就是 `V_hi` 的静态评估。不得对 `V_hi` 再跑一次 `ty` 来“确认”基线，以免两次运行的诊断集合不一致。

因此 `V_hi` 相对 `S_hi` 的增量恒为空。search 的 baseline 不会仅仅因为项目既有 typing 错误而 `STATIC_FAIL`。

## 4. 增量规则

定义诊断多重集差：只比较第 5 节的稳定身份。身份在 `S_hi` 中出现过的诊断，无论消息文本是否微调，都不进入增量。

```text
STATIC_PASS  increment(P) 为空，包括 ty 退出 0，以及退出 1 但无新身份
STATIC_FAIL  至少出现一个 S_hi 中不存在的诊断身份
```

以下情况不是 `STATIC_FAIL`：

- `P` 复现了 `S_hi` 中的全部或部分诊断；
- `S_hi` 中的某些诊断在 `P` 上消失；
- `S_hi` 为空且 `P` 的 `ty` 退出 0。

`S_hi` 为空时，任何新诊断都是增量。这覆盖“项目在最高版本下 `ty` 干净”的情况，行为与旧的绝对检查一致。

完整兼容性判据不变：`PASS` 仍要求静态通过之后完整测试通过。增量静态通过不能代替测试。

## 5. 诊断身份

### 5.1 解析所有权

`TyAdapter` 是 `ty` argv、输出解析和诊断规范化的唯一所有者。它必须固定机器可读格式，不把 `ty` 的人类摘要或退出码交给调用方自行解释。

实现约束：

- 始终传入 `--output-format concise`（若未来 ty 提供稳定 JSON 诊断流，可替换，但身份算法版本必须同步变更）；
- 该标志由 adapter 拥有，放在用户 `ty-args` 之后，覆盖项目 `[tool.ty.terminal]` 与 `ty-args` 中的格式选择；
- `--python`、`--python-version`、`--python-platform`、`--no-progress`、`--color never` 仍由 adapter 固定；
- stdout/stderr 截断、无法解析的 `concise` 行、或退出码既非 `0` 也非 `1`，一律返回 `TOOL_ERROR` 或既有非证据状态，不得丢行后继续比较。

### 5.2 规范化

每条诊断规范化为：

```text
TyDiagnostic
  identity   比较键
  origin     snapshot | external
  path       规范化 POSIX 路径
  line       snapshot 诊断必填；external 为 null
  column     snapshot 诊断必填；external 为 null
  code       ty 规则 id；无法识别时为空串
  message    仅供报告与诊断，不进入 identity
```

路径规则：

- 快照内文件：相对 snapshot root 的 POSIX 路径，`origin = snapshot`；
- 提案虚拟环境、site-packages、typeshed 或其他快照外路径：去掉绝对环境前缀，`origin = external`。

### 5.3 比较键

```text
snapshot:  origin | path | line | column | code
external:  origin | path | code
```

源快照在一次运行内不可变，因此项目文件的行列号稳定，必须纳入身份，以区分同一规则在不同调用点的错误。

外部文件随依赖版本变化，行列号不能比较；只保留规范化路径和规则。外部诊断若在 `V_hi` 上已存在，降低版本后同一规则再次出现不视为增量；若 `V_hi` 上没有而候选上新出现，则是增量。

消息文本不进入身份。依赖类型名常被写进诊断消息，纳入身份会把同一调用点的表述变化误判为新不兼容。

身份按字典序排序后组成不可变 tuple。`S_hi` 的 digest 使用该规范 tuple，进入报告和策略相关证据，不单独成为跨运行缓存 key。

## 6. check 与 search

### 6.1 共用判定

```text
capture_S_hi(V_hi)
  → 成功的 TyCheck 冻结为 S_hi
  → V_hi 的静态结果为 STATIC_PASS

StaticEvaluator(P, S_hi)
  → increment(P) ? STATIC_FAIL : STATIC_PASS
```

`FullEvaluator` 仍然只在 `STATIC_PASS` 后运行完整 `test-command`。`STATIC_FAIL` 短路测试，但是相对 `S_hi` 的增量失败，不是 `ty` 退出码失败。

### 6.2 `pf check`

`check` 验证当前声明，不搜索。每个 cell 的顺序为：

```text
1. 按最高版本策略解析/安装当前声明，得到 V_hi
2. 运行 ty，捕获 S_hi；工具失败则该 cell 为非证据状态
3. 关闭 V_hi 环境；check 不在 V_hi 上跑测试
4. 按 lowest-direct 解析/安装当前声明，得到 V_check
5. FullEvaluator(V_check, S_hi)
```

`V_check` 的静态通过条件是 `increment(V_check) = ∅`。随后仍须完整测试通过。

因此 check 失败的含义变为：

- `COMPATIBILITY_FAILED`：`lowest-direct` 相对 `S_hi` 出现新的 ty 诊断，或完整测试失败；
- `INDETERMINATE`：无法捕获 `S_hi`，或 `V_check` 出现非证据状态。

项目在最高版本下已有的 typing 错误，不再单独使 `pf check` 失败。

`V_hi` 与 `V_check` 是两个 Proposal。check 不为 `V_hi` 建立 floor，也不把最高版本测试结果当作 `lowest-direct` 证据。

### 6.3 `pf search`

search 的 cell 状态机仍是 D003 的 baseline → 静态不动点 → fast path / 动态不动点。变化只有静态证据的定义：

```text
1. 解析最高版本向量 B = V_hi
2. 捕获 S_hi，并得到 STATIC_PASS(B)
3. 完整测试 B；测试失败仍是 BASELINE_FAILED
4. 冻结候选
5. 之后所有 StaticEvaluator / FullEvaluator 都携带同一 S_hi
```

`BASELINE_FAILED` 不再包含“`ty` 报告了项目既有诊断”。它只表示：

- `B` 的完整测试失败；或
- 实现错误导致 `B` 在自比较后仍得到 `STATIC_FAIL`（不应发生）。

基础设施错误、超时和不可解析状态仍按 D003 停止该 cell。

静态坐标搜索、联合测试 fast path 和动态搜索的 probe 顺序、不动点与非单调检测不变。`STATIC_FAIL` 仍是兼容性失败边界，只是它现在表示“相对 `S_hi` 的增量诊断”，而不是“`ty` 非零退出”。

fast path 论证仍然成立：完整兼容性要求先静态通过；任何单坐标更低且完整通过的向量，必须先通过增量静态检查。若它能通过，静态不动点已经包含它。

## 7. Schema 与报告

### 7.1 原始检查与判定分离

`TyAdapter` 不再用退出码直接构造评估级 `STATIC_PASS` / `STATIC_FAIL`。它返回：

```text
TyCheck | ToolFailure
```

`TyCheck` 包含 `ProcessResult` 和规范化诊断 tuple。退出码 `0` 时诊断必须为空；退出码 `1` 时诊断必须非空且全部可解析。违反这些不变量视为 `TOOL_ERROR`。

`StaticEvaluator` 消费 `TyCheck` 与 `S_hi`，构造：

```text
StaticPassEvaluation.ty      TyCheck
StaticPassEvaluation.baseline_digest
StaticPassEvaluation.incremental   空 tuple

StaticFailEvaluation.ty      TyCheck
StaticFailEvaluation.baseline_digest
StaticFailEvaluation.incremental   非空增量诊断
```

`TyPass` / `TyFail` 若仍保留，只能作为评估结果的别名，不能再表示“`ty` 进程退出码”。公共报告必须能看出：原始 `ty` 是否报错、基线有哪些身份、增量是什么。

### 7.2 cell 证据

每个成功或带 baseline 的失败 cell 记录：

- `V_hi` 的 Proposal 身份；
- `S_hi` 的规范诊断 tuple 与 digest；
- 捕获 `S_hi` 的 `TyCheck` 进程摘要。

`STATIC_FAIL` 证据必须包含非空增量，而不能只存 `ty` 退出码。`explain` 展示基线诊断计数和增量诊断，不把基线错误说成本次不兼容。

缺少 `S_hi` 的旧报告与本策略身份不一致，不能与新报告 merge，也不能按新语义 apply。

## 8. 模块所有权

| 职责 | 所有者 | 禁止 |
| --- | --- | --- |
| 运行 `ty`、固定输出格式、解析并规范化诊断 | `TyAdapter` | 比较基线、决定 `STATIC_PASS`、创建环境 |
| 冻结 `S_hi`、计算增量、构造静态评估 | `StaticEvaluator` | 解析 `ty` 文本、选择 `lowest-direct` / highest |
| 为 check 捕获 `S_hi` 再评估 `V_check` | `CompatibilityChecker` | 复制增量规则或自行解释退出码 |
| 为 search 捕获 `S_hi` 并注入后续 probe | `SearchCoordinator` | 在 CoordinateSearch 内解析诊断 |
| 记录 `S_hi` 与增量 | Evaluation / Report Schema | 把诊断差写成自由文本 |

`CoordinateSearch` 仍然只看见 `PASS` / `STATIC_FAIL` / `TEST_FAIL` / 非证据状态。诊断比较不泄漏进搜索模块。

`EvaluationCache` 的 static/full 分离不变。一次 search/check 进程内 `S_hi` 固定，缓存 key 仍是 Proposal 身份；不得把一个 cell 的 `S_hi` 用到另一个 cell。

check 捕获 `S_hi` 后关闭 `V_hi` 环境。该环境未运行测试，也不晋升为 `V_check` 或后续 search Proposal。不同 Proposal 仍禁止原地升降依赖。

## 9. 策略身份

增量比较是证据语义，必须进入策略身份。至少包括：

```text
ty-diagnostic-policy = increment-v1
identity-rule        = snapshot-path-line-column-code + external-path-code
output-format        = concise
```

`ty-args`、`ty-timeout` 和工具版本仍进入策略身份。jobs、日志样式和总时限不进入。

变更身份算法或输出格式时，必须提升 `ty-diagnostic-policy`，使新旧证据无法 merge。

## 10. 不变量

1. 每个进入 Static/Full 评估的 cell 都先有冻结的 `S_hi`，或因无法捕获而成为非证据状态。
2. `V_hi` 的静态结果在成功捕获后必为 `STATIC_PASS`。
3. `STATIC_FAIL` 当且仅当增量诊断非空。
4. 消失的基线诊断不推进失败边界。
5. `pf check` 与 search probe 使用同一 `StaticEvaluator` 规则。
6. 完整 `PASS` 仍要求测试通过；既有 typing 错误不豁免测试。
7. 截断或不可解析的 `ty` 输出不能生成 `S_hi`，也不能生成兼容性失败边界。
8. `CoordinateSearch` 不读取诊断列表。

## 11. 非目标

本增强不包含：

- 把 PF 变成项目 typecheck CI，或要求仓库 `ty` 干净；
- 按规则白名单忽略诊断；
- failure attribution（增量诊断不自动映射到某个依赖）；
- static-only floor；
- 跨运行持久化 `S_hi`；
- 将消息文本或完整 `ty` 输出纳入比较键；
- 对 `S_hi` 做测试，或用最高版本测试结果代替 `lowest-direct` 测试。

## 12. 文档修订

| 文档 | 修订 |
| --- | --- |
| D001 | 静态通过改为相对 `S_hi` 的增量；baseline 不再因既有 ty 诊断失败；`pf check` 先捕获 `S_hi` 再评估 `lowest-direct` |
| D002 | `TyAdapter` 返回 `TyCheck`；`StaticEvaluator` 拥有增量比较；`CompatibilityChecker` 增加 `V_hi` 捕获步骤 |
| D003 | `B = V_hi` 时先冻结 `S_hi`；`STATIC_FAIL` 定义为增量；`BASELINE_FAILED` 收窄为测试失败或非证据 |

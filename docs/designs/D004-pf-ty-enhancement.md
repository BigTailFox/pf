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

对每个 cell，PF 先在当前声明、当前源快照上按 uv 默认最高版本策略解析，得到精确向量 `V_hi`，并在该环境运行一次 `ty`。这次检查的规范化诊断多重集记为 `S_hi`，称为该 cell 的**静态诊断基线**。

之后每一次 `ty`——包括 `pf check` 的 `lowest-direct` 评估，以及 search 的全部 Static/Full probe——都与 `S_hi` 做多重集增量比较：

```text
S_hi, diagnostics(P) : multiset[DiagnosticIdentity]

increment(P) = diagnostics(P) ⊖ S_hi

increment(P) = ∅  →  STATIC_PASS
increment(P) ≠ ∅  →  STATIC_FAIL
```

`⊖` 是 bag / multiset subtraction，不是集合差。同一身份出现 `n` 次只抵消基线中的 `n` 次；多出来的次数进入增量。

诊断多重集是静态兼容性的唯一事实源。`ty` 退出码不承担 compatibility semantics，也不与诊断条数互相约束。

消失的基线诊断不构成失败。PF 只把**新增**诊断当作依赖不兼容。

`S_hi` 定义当前最高版本环境被接受的静态状态。PF 检测相对该状态的静态回归；它不要求参考环境本身 type-clean。因此本契约不是“依赖 API 必须通过类型检查”，而是：

> candidate 依赖环境相对最高版本参考环境，不得引入新的静态诊断。

例如 `V_hi` 已有 `foo.py:42 unresolved-attribute`，即使它本身就是某个依赖 API 的使用问题，它也会进入 `S_hi`，之后不再作为 floor failure。PF 寻找的是向下移动依赖版本引入的兼容性退化，而不是重新判定仓库当前所有 type errors。

`pf check` 与 `pf search` 使用同一套增量语义，由同一个 `StaticEvaluator` 判定。禁止 check 走绝对干净、search 走增量的双轨定义。

## 3. 向量与基线

### 3.1 `V_hi`

`V_hi` 是当前声明在该 cell 上按最高允许版本解析出的精确受管向量。它不读取 lock、操作者 `.venv` 或既有安装。

在 search 中，`V_hi` 就是 D003 的 baseline 向量 `B`。

在 check 中，`V_hi` 只用于捕获 `S_hi`。check 的兼容性对象仍是 `lowest-direct` 解析向量，不是 `V_hi`。

### 3.2 `S_hi`

`S_hi` 是 `V_hi` 上那一次成功 `TyCheck` 的诊断多重集，不是项目配置，也不是跨运行缓存。

同一 cell、同一源快照、同一策略内，`S_hi` 冻结后不再刷新。不同 cell 各自拥有自己的 `S_hi`。

无法建立 `S_hi` 时，该 cell 不能产生静态兼容性证据，结果为对应非证据状态，不能假装 `ty` 干净或假装增量通过。

### 3.3 捕获与自比较

用于捕获 `S_hi` 的那次 `TyCheck` 同时就是 `V_hi` 的静态评估。不得对 `V_hi` 再跑一次 `ty` 来“确认”基线，以免两次运行的诊断多重集不一致。

因此 `V_hi` 相对 `S_hi` 的增量恒为空。search 的 baseline 不会仅仅因为项目既有 typing 错误而 `STATIC_FAIL`。

## 4. 增量规则

只比较第 5 节的稳定身份。消息、severity 和 ty 提供的 fingerprint 不参与差。身份在 `S_hi` 中按重数抵消后仍剩余的诊断，无论消息文本是否微调，都进入增量。

```text
STATIC_PASS  increment(P) = ∅
STATIC_FAIL  increment(P) ≠ ∅
```

以下情况不是 `STATIC_FAIL`：

- `P` 的诊断多重集是 `S_hi` 的子多重集；
- `S_hi` 中的某些诊断在 `P` 上消失或次数减少；
- `S_hi` 与 `P` 均为空多重集。

`S_hi` 为空多重集时，`P` 上的任何诊断都是增量。这覆盖“项目在最高版本下没有任何 ty 诊断”的情况，行为与旧的绝对检查一致。

完整兼容性判据不变：`PASS` 仍要求静态通过之后完整测试通过。增量静态通过不能代替测试。

## 5. 诊断身份

### 5.1 解析所有权

`TyAdapter` 是结构化 diagnostic collector：它运行 `ty`、固定机器可读输出、解析 GitLab JSON，并规范化为 `TyCheck`。它不是“typecheck 命令包装器”，不把人类摘要或退出码交给调用方解释兼容性。

Adapter 拥有并注入的 argv：

```text
--output-format gitlab
--python
--python-version
--python-platform
--no-progress
--color never
```

以及这些选项在 ty 中的别名（例如 `--venv`、`--target-version`、`--platform`）。

用户 `ty-args` 或项目 `[tool.ty.terminal]` 不得包含任何 adapter 拥有的选项或其别名。出现冲突时为配置错误，在构造 argv 时失败。禁止把 adapter 标志追加到用户参数后面并依赖“最后一个参数赢”；ty 的部分 CLI 选项本身互斥，覆盖策略不安全。

`--output-format gitlab` 是契约。PF 解析 JSON 数组，忽略每条记录的 `fingerprint`，只消费：

```text
check_name
location
description
severity
```

JSON 无法解析、stdout 截断、或必需字段缺失，一律 `TOOL_ERROR`。不得丢弃残缺记录后继续比较。

### 5.2 退出码

退出码只区分“这次 `ty` 是否作为工具成功跑完”，不与诊断条数互相证明：

```text
exit 0 或 1
    结构化输出完整且可解析 → TyCheck
    诊断多重集以 JSON 为准，可以为空，也可以非空

exit 2 或 101
    → TOOL_ERROR

timeout / 启动失败 / 其他非 0、1 退出码
    → 既有非证据状态
```

`--exit-zero`、`--exit-zero-on-warning` 和 `[tool.ty.terminal] error-on-warning=false` 都会改变退出码语义。因此禁止以下不变量：

```text
exit 0  ⇒  diagnostics = ∅
exit 1  ⇒  diagnostics ≠ ∅
```

### 5.3 规范化

每条诊断规范化为：

```text
TyDiagnostic
  identity   比较键
  origin     snapshot | external
  path       规范化 POSIX 路径
  line       snapshot 诊断必填；external 为 null
  column     snapshot 在 JSON 提供时必填；external 为 null
  code       ty check_name，非空
  severity   仅供报告，不进入 identity
  message    description，仅供报告，不进入 identity
```

`check_name` 缺失或为空、`location.path` 缺失或为空，使整次检查成为 `TOOL_ERROR`。不得用空串充当规则 id。稳定身份不能在未知规则上退化合并。

路径规则：

- 快照内文件：相对 snapshot root 的 POSIX 路径，`origin = snapshot`；
- 提案虚拟环境、site-packages、typeshed 或其他快照外路径：去掉绝对环境前缀，`origin = external`。

GitLab `location` 必须提供 path 和 begin line。snapshot 诊断在 JSON 提供 column 时纳入身份；若官方 gitlab 对象只有 `lines.begin`，snapshot 身份使用 `origin | path | line | code`，不发明空 column。

### 5.4 比较键

```text
snapshot:  origin | path | line | column? | code
external:  origin | path | code
```

源快照在一次运行内不可变，因此项目文件的位置稳定，必须纳入身份，以区分同一规则在不同调用点的错误。

`external` 身份有意粗糙。PF 主要把快照内、位置稳定的诊断当作兼容性证据，避免解释依赖环境内部不稳定的行列号。同一 external 文件、同一 `code` 只要重数不变，即使位置和含义已变，v1 也视为基线已覆盖。这是保守策略：降低对依赖 stub 内部漂移的敏感度，减少 false positive。若日后发现 external 诊断本身很重要，再设计 `external-v2`，不在 v1 复杂化。

消息和 severity 不进入身份。依赖类型名常被写进 `description`，纳入身份会把同一调用点的表述变化误判为新不兼容。

身份按字典序排列、保留重数后组成不可变 tuple。`S_hi` 的 digest 使用该规范多重集，进入报告和策略相关证据，不单独成为跨运行缓存 key。

## 6. check 与 search

### 6.1 共用判定

```text
capture_S_hi(V_hi)
  → 成功的 TyCheck 冻结为诊断多重集 S_hi
  → V_hi 的静态结果为 STATIC_PASS

StaticEvaluator(P, S_hi)
  → increment(P) = ∅ ? STATIC_PASS : STATIC_FAIL
```

`FullEvaluator` 仍然只在 `STATIC_PASS` 后运行完整 `test-command`。`STATIC_FAIL` 短路测试，但是相对 `S_hi` 的多重集增量失败，不是 `ty` 退出码失败。

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

静态坐标搜索、联合测试 fast path 和动态搜索的 probe 顺序、不动点与非单调检测不变。`STATIC_FAIL` 仍是兼容性失败边界，只是它现在表示“相对 `S_hi` 的多重集增量”，而不是“`ty` 非零退出”。

fast path 论证仍然成立：完整兼容性要求先静态通过；任何单坐标更低且完整通过的向量，必须先通过增量静态检查。若它能通过，静态不动点已经包含它。

## 7. Schema 与报告

### 7.1 原始检查与判定分离

`TyAdapter` 返回：

```text
TyCheck | ToolFailure | ConfigurationError
```

`TyCheck` 包含 `ProcessResult` 和规范化诊断 tuple（多重集的确定序列）。诊断条数与退出码无关。

`StaticEvaluator` 消费 `TyCheck` 与 `S_hi`，构造：

```text
StaticPassEvaluation.ty      TyCheck
StaticPassEvaluation.baseline_digest
StaticPassEvaluation.incremental   空多重集

StaticFailEvaluation.ty      TyCheck
StaticFailEvaluation.baseline_digest
StaticFailEvaluation.incremental   非空增量多重集
```

`TyPass` / `TyFail` 若仍保留，只能作为评估结果的别名，不能再表示“`ty` 进程退出码”。公共报告必须能看出：原始诊断多重集、`S_hi` 身份与重数、增量是什么。报告可以附带退出码作为工具事实，但 explain 不得把它说成兼容性原因。

### 7.2 cell 证据

每个成功或带 baseline 的失败 cell 记录：

- `V_hi` 的 Proposal 身份；
- `S_hi` 的规范诊断多重集与 digest；
- 捕获 `S_hi` 的 `TyCheck` 进程摘要。

`STATIC_FAIL` 证据必须包含非空增量多重集，而不能只存 `ty` 退出码。`explain` 展示基线诊断计数和增量诊断，不把基线错误说成本次不兼容。

缺少 `S_hi` 的旧报告与本策略身份不一致，不能与新报告 merge，也不能按新语义 apply。

## 8. 模块所有权

| 职责 | 所有者 | 禁止 |
| --- | --- | --- |
| 运行 `ty`、固定 gitlab JSON、拒绝冲突 argv、解析并规范化诊断 | `TyAdapter` | 比较基线、决定 `STATIC_PASS`、创建环境、用退出码推断诊断 |
| 冻结 `S_hi`、计算多重集增量、构造静态评估 | `StaticEvaluator` | 解析 `ty` 文本、选择 `lowest-direct` / highest |
| 为 check 捕获 `S_hi` 再评估 `V_check` | `CompatibilityChecker` | 复制增量规则或自行解释退出码 |
| 为 search 捕获 `S_hi` 并注入后续 probe | `SearchCoordinator` | 在 CoordinateSearch 内解析诊断 |
| 记录 `S_hi` 与增量 | Evaluation / Report Schema | 把诊断差写成自由文本 |

`CoordinateSearch` 仍然只看见 `PASS` / `STATIC_FAIL` / `TEST_FAIL` / 非证据状态。诊断比较不泄漏进搜索模块。搜索算法本身不因本增强而改变。

`EvaluationCache` 的 static/full 分离不变。一次 search/check 进程内 `S_hi` 固定，缓存 key 仍是 Proposal 身份；不得把一个 cell 的 `S_hi` 用到另一个 cell。

check 捕获 `S_hi` 后关闭 `V_hi` 环境。该环境未运行测试，也不晋升为 `V_check` 或后续 search Proposal。不同 Proposal 仍禁止原地升降依赖。

## 9. 策略身份

增量比较是证据语义，必须进入策略身份。至少包括：

```text
ty-diagnostic-policy = increment-v1
identity-rule        = snapshot-path-line-column-code + external-path-code
output-format        = gitlab
comparison           = multiset-subtraction
```

`ty-args`、`ty-timeout` 和工具版本仍进入策略身份。jobs、日志样式和总时限不进入。

变更身份算法、输出格式或比较代数时，必须提升 `ty-diagnostic-policy`，使新旧证据无法 merge。

## 10. 不变量

1. 每个进入 Static/Full 评估的 cell 都先有冻结的 `S_hi`，或因无法捕获而成为非证据状态。
2. `V_hi` 的静态结果在成功捕获后必为 `STATIC_PASS`。
3. `STATIC_FAIL` 当且仅当多重集增量非空。
4. 消失或重数减少的基线诊断不推进失败边界。
5. `pf check` 与 search probe 使用同一 `StaticEvaluator` 规则。
6. 完整 `PASS` 仍要求测试通过；既有 typing 错误不豁免测试。
7. 截断、非 JSON、缺失 `check_name` / path，或用户 `ty-args` 与 adapter 选项冲突，不能生成 `S_hi`，也不能生成兼容性失败边界。
8. `CoordinateSearch` 不读取诊断多重集。
9. 退出码不是诊断条数的证明，也不是 `STATIC_PASS` / `STATIC_FAIL` 的证明。

## 11. 非目标

本增强不包含：

- 把 PF 变成项目 typecheck CI，或要求仓库 `ty` 干净；
- 按规则白名单忽略诊断；
- failure attribution（增量诊断不自动映射到某个依赖）；
- static-only floor；
- 跨运行持久化 `S_hi`；
- 将消息、severity、fingerprint 或完整 `ty` 输出纳入比较键；
- 细化 `external-v2` 身份；
- 对 `S_hi` 做测试，或用最高版本测试结果代替 `lowest-direct` 测试。

## 12. 文档修订

| 文档 | 修订 |
| --- | --- |
| D001 | 静态通过改为相对 `S_hi` 的多重集增量；baseline 不再因既有 ty 诊断失败；`pf check` 先捕获 `S_hi` 再评估 `lowest-direct` |
| D002 | `TyAdapter` 解析 gitlab JSON、拒绝冲突 argv、返回 `TyCheck`；`StaticEvaluator` 拥有多重集增量比较 |
| D003 | `B = V_hi` 时先冻结 `S_hi`；`STATIC_FAIL` 定义为多重集增量；搜索算法不变 |

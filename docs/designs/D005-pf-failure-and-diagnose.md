# PF failure 语义与 diagnose

- **状态：** 现行契约
- **策略版本：** `failure-v1`
- **最后核对：** 2026-08-21
- **产品与命令：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **静态证据：** [D004](D004-pf-ty-enhancement.md)
- **CLI 交互与展示：** [D006](D006-pf-cli-enhancement.md)

本文是 PF 中失败分类、搜索处置、失败证据保真、failure 用户文案和 `diagnose` 行为的唯一契约。D001 只定义产品结果与命令，D002 只定义模块位置，D003 只消费本文定义的 disposition，D004 只定义静态诊断事实，D006 只组织本文文案在普通命令和 `explain` 中的信息层级。

## 1. 问题

当前已实现的开发期 Schema 把工具原因直接暴露为搜索状态：

```text
BUILD_UNAVAILABLE
UNRESOLVABLE
HARNESS_ERROR
SOURCE_ERROR
TOOL_ERROR
TIMEOUT
```

D001/D003 又把这些状态全部定义为非证据，因此 candidate probe 在项目或 harness 安装失败时立即终止整个 cell。这个模型有三个问题：

1. 同一机械失败在 baseline 和 candidate probe 中具有不同搜索含义，却被同一个 status 决定；
2. candidate 的确定性安装失败无法作为 `FAIL* PASS*` 中的失败点，搜索被不必要地中止；
3. prepare 失败发生在 Proposal 建立之前，现行实现既会虚构 `prepare:<status>` Proposal ID，也会丢失底层 `ToolFailure`，使报告不能解释失败。

新的模型必须把以下三个问题分开：

```text
发生了什么？       cause
搜索可以怎样使用？ disposition
用户如何调查？     diagnosis
```

## 2. 目标

- candidate probe 的确定性项目/harness 安装失败形成负向兼容性证据，而不是强行停止 cell；
- baseline 没有完整 `PASS` 时不启动坐标搜索；
- timeout、来源故障、工具崩溃和内部不变量错误绝不伪装成兼容性失败；
- 每个可报告的 Rejection/Indeterminate 保留完整 scope、stage、脱敏机械事实和可用日志引用；Attempt 已建立时还必须保留 requested resolution/vector；
- `CoordinateSearch` 只消费搜索处置，不解释 uv 文本、stage 或退出码；
- `pf diagnose` 能在不重新执行项目代码的情况下解释报告中的失败；
- CLI 先说明用户能理解的现象、影响和下一步，再把 cause Enum 作为次级技术信息；
- 直接把首发 Schema 1 塑造成最终失败模型，不为开发期报告保留兼容层。

## 3. 术语

### 3.1 Attempt

**Attempt** 是 PF 对一个已知 cell、源码快照、策略和请求解析方式或精确受管向量进行验证的尝试。Attempt 在环境创建和解析之前已经存在。

Attempt 分为：

- **Baseline Attempt**：按当前声明解析 `highest`，目标是建立完整通过的搜索锚点；
- **Probe Attempt**：物化一个精确 requested vector，目标是在当前 Evaluation context 中获得搜索证据。

### 3.2 Proposal

**Proposal** 是成功解析并检查实际依赖图后建立的不可变对象。失败的 prepare attempt 没有 Proposal；不能为它虚构 Proposal ID。

### 3.3 Rejection

**Rejection** 表示完整且确定的事实证明当前 Attempt 不满足 PF 的验证契约。它是搜索可以消费的负向兼容性证据。

Rejection 只拒绝当前完整 Attempt/Proposal。即使 Probe Attempt 只改变了一个坐标，也不能把结果描述成“这个 distribution version 在所有上下文中不兼容”。PF v1 仍不进行 failure attribution。

### 3.4 Indeterminate

**Indeterminate** 表示 PF 未能获得完整、可靠、可归属到当前 Attempt 的兼容性结果。它不能推进边界。

候选发现或调度 deadline 也可能在 Attempt 建立前终止 cell。这类结果是 cell-scoped Indeterminate，不得为了统一形状虚构 Attempt。

### 3.5 Diagnosis

**Diagnosis** 是对 Rejection 或 Indeterminate 的结构化解释和机械事实。Diagnosis 帮助用户调查，但不能反向改变已经记录的 disposition。

本文避免单独使用含义不明的“失败”。必须明确说 attempt rejection、indeterminate attempt、tool failure 或 cell terminal result。

## 4. 三层结果模型

### 4.1 机械事实

`ProcessRunner` 只产生 `ProcessResult`：argv、cwd、环境变量名、exit/signal/start error、timeout、duration、截断标志和脱敏输出。它不知道兼容性。

### 4.2 操作原因

Adapter 把机械事实分类为操作层 cause，例如：

```text
RESOLUTION_CONFLICT
BUILD_FAILURE
HARNESS_CONFLICT
STATIC_REGRESSION
TEST_FAILURE
SOURCE_FAILURE
ENVIRONMENT_FAILURE
TOOL_FAILURE
TIMEOUT
INTERNAL_INVARIANT
NONDETERMINISTIC
```

Cause 回答“发生了什么”，不回答搜索是否继续。Adapter 不知道一个操作属于 baseline 还是 probe。

### 4.3 搜索处置

失败策略根据 Attempt role、stage、cause 和证据完整性产生：

```text
PASS
REJECTED
INDETERMINATE
```

`CoordinateSearch` 的二元兼容性关系为：

```text
PASS     -> PASS
REJECTED -> FAIL
```

`INDETERMINATE` 不属于该关系，出现时立即结束当前 cell。

## 5. Attempt identity

prepare 失败时没有实际解析图，因此 Attempt identity 与 Proposal identity 必须分离。

Attempt identity 覆盖：

```text
AttemptIdentity
  source_snapshot_digest
  cell
  requested_resolution       highest | exact-vector
  requested_managed_vector   exact-vector 时必填
  active declaration IDs
  source plan identity
  evaluation policy identity
```

```text
attempt_id = sha256("pf:attempt:v1\0" + canonical AttemptIdentity)
```

Proposal 成功建立后同时保存 `attempt_id` 和现有 `proposal_id`。失败 prepare observation 只引用 `attempt_id`。

Attempt identity 不包含进程时长、输出文本、本地路径或 run ID；相同请求在同一 frozen context 中应得到相同 identity。

## 6. 分类原则

一次失败只有同时满足以下条件，才可以成为 Rejection：

1. scope 完整：cell、snapshot、policy 和 requested vector/解析方式明确；
2. 结果完整：没有 timeout、signal、启动失败、关键输出截断或解析歧义；
3. 失败是 Attempt 局部且确定的，而不是 index、网络、缓存、磁盘或 PF 自身故障；
4. cause 属于验证契约，例如不可解析、不可构建、harness 冲突、静态回归或测试正常失败；
5. 若为 Probe Attempt，它与同一 Evaluation context 中已经完整通过的 baseline 共享 scope。

不满足任一条件时必须是 Indeterminate。

Retryability 不参与分类。一个问题可以由用户修复后重跑，但这不代表 PF 可以在当前证据上自动重试或猜测结果。

## 7. Baseline 与 probe 的不同处置

### 7.1 Baseline Attempt

Baseline 的目标是建立已知完整通过的 `B = V_hi`。Baseline 被 Rejected 或变成 Indeterminate 时都终止当前 cell，因为 D003 没有 PASS 锚点。

两者必须保留区别：

- baseline Rejection 表示当前 highest fresh install 确定不满足验证契约，属于兼容性失败，退出码为 `1`；
- baseline Indeterminate 表示无法作出兼容性判断，退出码为 `4`。

Schema 使用 `BaselineRejection | BaselineIndeterminate` discriminator union 表达两类终态，不保留含义宽泛的 `BASELINE_FAILED` Schema status。CLI 的人类文案仍可显示 “baseline failed”，但不能据此折叠结构化 disposition 和 cause。

### 7.2 Probe Attempt

在同 scope baseline 已经完整 `PASS` 后，Probe Attempt 的 Rejection 是合法 `FAIL` 点：

- 记录 observation 与完整 Diagnosis；
- 不终止 cell；
- 按 D003 在当前切片继续定位更高的 `PASS`；
- 可以作为最终 floor 的 predecessor rejection；
- 参与非单调检测。

“继续”不等于无条件探测相邻版本。线性或二分 probe 顺序仍由 D003 唯一决定。

Probe Attempt 的 Indeterminate 仍立即终止当前 cell。PF 不跳过 unknown hole，也不把它当成 rejected version。

## 8. v1 分类矩阵

| 场景 | Cause | Baseline | Probe | 搜索含义 |
| --- | --- | --- | --- | --- |
| 精确依赖图无解 | `RESOLUTION_CONFLICT` | Rejected，终止 | Rejected，继续 | 当前 attempt 不可安装 |
| wheel/build backend 确定失败 | `BUILD_FAILURE` | Rejected，终止 | Rejected，继续 | 当前 attempt 不可构建 |
| harness 在目标图约束下无解 | `HARNESS_CONFLICT` | Rejected，终止 | Rejected，继续 | 当前 attempt 无法满足测试契约 |
| harness 安装改变目标依赖图 | `HARNESS_CONFLICT` | Rejected，终止 | Rejected，继续 | 当前 attempt 无法保持被测图 |
| D004 增量诊断非空 | `STATIC_REGRESSION` | 不适用 | Rejected，继续 | 静态兼容性失败 |
| 测试以配置的失败码退出 | `TEST_FAILURE` | Rejected，终止 | Rejected，继续 | 动态兼容性失败 |
| index/DNS/凭据/远端来源不可用 | `SOURCE_FAILURE` | Indeterminate，终止 | Indeterminate，终止 | 没有候选事实 |
| 进程 timeout、signal 或启动失败 | `TIMEOUT` / `TOOL_FAILURE` | Indeterminate，终止 | Indeterminate，终止 | 执行事实不完整 |
| uv/ty/test 输出无法可靠解析 | `TOOL_FAILURE` | Indeterminate，终止 | Indeterminate，终止 | 分类不可靠 |
| Python/venv/解释器不满足 cell | `ENVIRONMENT_FAILURE` | Indeterminate，终止 | Indeterminate，终止 | 执行环境错误 |
| 实际受管向量偏离 requested vector | `INTERNAL_INVARIANT` | Indeterminate，终止 | Indeterminate，终止 | PF/adapter 契约被违反 |
| 同 context 结果冲突 | `NONDETERMINISTIC` | Indeterminate，终止 | Indeterminate，终止 | 不允许选择一个结果 |

矩阵中的 `BUILD_FAILURE` 作为 Rejection 是已确认决策。前提是 adapter 已经获得完整、可归属到当前 attempt 的构建失败；含糊的非零退出仍是 `TOOL_FAILURE`。

## 9. 项目安装与 harness 安装

### 9.1 项目安装

项目 editable install、受管精确向量、固定声明和传递解析共同定义 Attempt 的可安装性。确定的 resolution/build failure 拒绝整个 Attempt，而不是归因到当前被移动的 dependency。

Baseline 已通过只能证明相同 scope 的 highest Attempt 可安装；它不把 candidate 的任意异常自动变成 Rejection。分类仍必须满足 §6。

### 9.2 Harness 安装

Harness 是 PF 完整验证契约的一部分，但不是产品运行时依赖图的一部分。安装前后的目标图必须完全一致。

以下结果是 `HARNESS_CONFLICT`：

- harness requirements 在冻结目标图约束下无解；
- harness 安装成功但新增解析改变目标图中的任一既有版本；
- harness 的要求与 candidate 的精确受管向量冲突。

以下结果不是 `HARNESS_CONFLICT`：

- uv 进程 crash 或输出无法分类；
- index、网络、凭据或 artifact 下载故障；
- timeout；
- graph inspection 本身失败；
- PF 检测到 requested vector 与实际图不一致。

现行 `HARNESS_ERROR` 混合了上述两类情况，必须拆分后才能安全推进搜索。

## 10. Probe 与边界 Schema

`ProbeEvidence.status` 不再同时承担 cause 和 disposition。概念结构为：

```text
ProbePass
  attempt
  proposal
  evaluation

ProbeRejection
  attempt
  proposal?       prepare rejection 时为空
  cause
  diagnosis
  evaluation?     static/test rejection 时存在

ProbeIndeterminate
  attempt
  proposal?
  cause
  diagnosis

BaselineRejection
  attempt
  proposal?
  cause
  diagnosis
  evaluation?

BaselineIndeterminate
  attempt
  proposal?
  cause
  diagnosis

CellIndeterminate
  cell_scope      Attempt 建立前的 candidate discovery / scheduling 失败
  cause
  diagnosis
```

`CoordinateBoundary` 的 predecessor 保存对 `ProbeRejection` observation 的引用或稳定 `failure_id`，而不是只保存 `STATIC_FAIL | TEST_FAIL` 字符串。

最终报告必须能够表达：

- search 成功，但某些更低 candidate 因 install/harness rejection 建立了边界；
- search 失败，因为某个 attempt indeterminate；
- baseline 被确定拒绝；
- prepare 失败且从未建立 Proposal。
- Attempt 建立前，candidate discovery 或调度未能获得结果。

## 11. FailureRecord 与诊断保真

每个 Rejection/Indeterminate 保存一个脱敏、有界的 `FailureRecord`。失败 scope 是 discriminator union：

```text
AttemptFailureScope
  attempt

CellFailureScope
  package
  cell
  source_snapshot_digest
  evaluation_policy_identity
```

`CellFailureScope` 只用于 Attempt 建立前的 cell orchestration failure。Rejection 必须使用 `AttemptFailureScope`；两种 scope 都可以产生 Indeterminate。

记录结构为：

```text
FailureRecord
  failure_id
  scope             AttemptFailureScope | CellFailureScope
  disposition       REJECTED | INDETERMINATE
  cause
  stage
  process?          ProcessResult
  summary_code?     adapter 提供的稳定细分类
  detail?           无进程时的结构化说明
```

要求：

- candidate prepare failure 保留原始 `ToolFailure`/`ProcessResult`，不能只保留 status；
- candidate discovery 或未启动 deadline 保留 cell scope，不能虚构 requested vector 或 Attempt ID；
- `failure_id` 在一份报告内唯一且稳定，用于 boundary 和 `diagnose` 引用；
- 人类摘要不进入 Schema；Presenter 根据结构化 scope、disposition、cause 和稳定细分类生成；
- 公共报告不保存 secret、本地绝对日志路径或未脱敏环境值；
- 输出截断标志必须保留，不能把截断内容冒充完整诊断；
- 日志不可用不改变 disposition，但会降低可展示的细节。

## 12. `pf diagnose` v1 interface

v1 interface：

```text
pf diagnose [package] [--failure FAILURE_ID]
```

默认读取所选 package 的 `package-floor.json`：

- 未指定 `--failure` 时，按 cell、attempt 顺序列出全部 Rejection 和 Indeterminate；
- 指定后展示一个 FailureRecord 的完整可移植诊断；
- 先展示用户可读的结果、原因、影响和下一步，再展示 disposition、cause、cell、phase/stage、requested vector、Proposal、边界作用、进程终态和脱敏摘要；
- 没有 Attempt 时明确展示 cell-scoped operation，requested vector 与 Proposal 均为“不适用”；
- 若当前机器仍有对应本地 run log，则额外显示日志链接；
- 不修改报告或项目。

`diagnose` 与 `explain` 的职责不同：

| 命令 | 回答的问题 |
| --- | --- |
| `explain` | floor 是什么、投影为何可/不可 apply |
| `diagnose` | 哪次 attempt 为什么被拒绝或无法判断、有哪些机械事实 |

首版 `diagnose` 已确认严格离线：不访问网络、不创建环境、不启动工具、不自动重试。精确重放需要验证源码快照仍一致并重新获得外部来源，属于单独的 `--reproduce` 设计，不应由“查看诊断”隐式触发。

成功读取并展示诊断时 `diagnose` 返回 `0`，不继承被诊断运行的退出码。报告缺失/非法、failure ID 不存在和本地日志读取失败按 D001 的命令错误映射处理。

### 12.1 默认展示层级

`UNRESOLVABLE`、`TOOL_ERROR` 等旧 status，以及 `RESOLUTION_CONFLICT` 等新 cause，都是机器词汇。CLI 不得把 Enum 名单独作为标题、原因或最终结论。

用户首先看到四类信息：

1. **发生了什么**：面向项目使用者的一句话，不要求理解 PF 内部阶段；
2. **有什么影响**：搜索继续、baseline 未建立，或当前 cell 已停止；
3. **下一步做什么**：一条保守、与 cause 对应的调查建议；
4. **如何深入调查**：`failure_id`、`pf diagnose` 命令和可用日志链接。

稳定 Enum 仍保留在报告和 `diagnose` 的“Technical details”中，便于脚本、测试和问题报告使用。它不能取代自然语言主文案。

Presenter 使用内部展示模型，不把文案写回公共 Schema：

```text
FailurePresentation
  title
  impact
  next_step?
  failure_id
  technical_code
```

`title` 由 cause 决定，`impact` 由 scope、Attempt role 和 disposition 决定。已识别的 `summary_code` 可以细化标题；未知细分类必须退回 cause 的通用文案，不能回退为裸 Enum 或原始 stderr。

实时 `check` / `smoke` / `search` 只显示 title、impact、failure ID 和诊断入口。`explain` 使用同一 title。`diagnose` 再展开 context、next step 和 technical details，避免普通失败输出被内部字段淹没。

### 12.2 Cause 的默认用户文案

默认英文主文案如下。未来本地化可以替换语言，但不能改变对应的 cause、disposition 或影响语义。

| Cause | 默认主文案 | 默认下一步 |
| --- | --- | --- |
| `RESOLUTION_CONFLICT` | This version combination has conflicting dependency requirements and cannot be installed. | Review the conflicting requirements, adjust project constraints if needed, then rerun PF. |
| `BUILD_FAILURE` | This version combination could not be built. | Inspect the build details and log; check build requirements, Python support, and available artifacts. |
| `HARNESS_CONFLICT` | The test dependencies cannot be installed without changing the versions being checked. | Adjust the configured test dependencies so they preserve the dependency graph under test. |
| `STATIC_REGRESSION` | This version combination introduces new type-checking diagnostics. | Review the new diagnostics and decide whether to fix the code or keep a higher dependency floor. |
| `TEST_FAILURE` | The full test command failed for this version combination. | Review the failing test summary and log before changing code or dependency constraints. |
| `SOURCE_FAILURE` | PF could not reach or read a configured package source. | Check the index URL, network, credentials, and source availability, then rerun PF. |
| `ENVIRONMENT_FAILURE` | The current Python or system environment cannot run this check. | Verify the interpreter, platform support, permissions, and required system tools. |
| `TOOL_FAILURE` | PF could not complete a verification tool operation reliably. | Inspect the technical details and log; verify that the named tool can run in this environment. |
| `TIMEOUT` | The operation timed out, so compatibility is unknown. | Inspect the log and increase the relevant timeout only if the operation is expected to finish. |
| `INTERNAL_INVARIANT` | PF detected an inconsistent verification result. | Keep the failure ID and technical details when reporting the problem; do not trust this cell result. |
| `NONDETERMINISTIC` | The same version combination produced conflicting results. | Stabilize flaky tests or external inputs, then rerun the full search. |

下一步是调查建议，不是自动修复承诺。Presenter 不得根据 stderr 自由生成建议，也不得暗示用户仅靠重试就能把 Indeterminate 变成 Rejection 或 PASS。

### 12.3 Disposition 与 scope 的影响文案

| 结果 | 默认 impact |
| --- | --- |
| Probe Rejection | This candidate did not pass the required checks. PF will continue searching. |
| Baseline Rejection | The highest-version baseline did not pass, so PF did not start the floor search for this cell. |
| Attempt Indeterminate | PF could not determine whether this candidate works, so it stopped this cell. |
| Cell-scoped Indeterminate | PF could not obtain the information needed to start or continue this cell. |

文案不得把完整 Attempt 的 Rejection 简化成“某个 dependency version 全局不兼容”。Harness conflict 也不得简化成“项目运行时依赖冲突”，因为它只说明 PF 的完整测试契约无法满足。

### 12.4 输出示例

以下示例的 cell 标题和段落布局遵循 D006；failure title、impact 和 next step 仍由本文定义。

candidate resolution conflict 的实时摘要：

```text
✗ demo [py3.11][x86_64-unknown-linux-gnu][no-extra]: This version combination has conflicting dependency requirements and cannot be installed.
  This candidate did not pass the required checks. PF will continue searching.
  Diagnose: pf diagnose demo --failure failure-7f2c
```

来源故障的实时摘要：

```text
! demo [py3.11][x86_64-unknown-linux-gnu][no-extra]: PF could not reach or read a configured package source.
  PF could not obtain the information needed to start or continue this cell.
  Diagnose: pf diagnose demo --failure failure-a19d
```

单条 `diagnose` 的信息顺序：

```text
Failure: failure-a19d
Outcome: Compatibility is unknown
What happened: PF could not reach or read a configured package source.
Impact: PF stopped this cell before a candidate attempt was available.
Next step: Check the index URL, network, credentials, and source availability, then rerun PF.

Context:
  package: demo
  cell: py3.11 / x86_64-unknown-linux-gnu / no-extra
  stage: candidate discovery

Technical details:
  disposition: INDETERMINATE
  cause: SOURCE_FAILURE
  attempt: not available
  process: exited 2
  log: .pf/logs/<run-id>/process-4.log
```

若没有本地日志，省略 `log` 或明确显示 “Detailed local log is unavailable.”。不得把日志缺失呈现为新的兼容性失败。

## 13. 本地详细日志

`RunLogStore` 继续保存脱敏的完整进程日志，但公共报告不保存 `run_id`、绝对路径或其他本机定位信息。后续 `diagnose` 通过项目本地 locator index 查找日志：

```text
.pf/logs/diagnosis-index.json
  (report_generation_id, failure_id)
    -> <run-id>/process-<id>.log
```

`report_generation_id` 由 generator/algorithm、package、source snapshot、policy、声明和 target cells 的规范 identity 计算，不吸收 cell result 或本地日志信息。

该 locator index：

- 只使用 `.pf/logs` 内相对路径；
- 与日志共享私有权限、脱敏和原子写规则；
- 不是公共证据，不参与 Proposal/policy identity、report equality、merge 或 apply；
- 缺失时 `diagnose` 仍能使用报告内的有界 `ProcessResult`；
- 报告被同 generation 新结果更新时，同步替换对应 `failure_id` 的本地映射；
- 不得通过扫描 run 目录、任意路径或模糊匹配输出内容寻找日志。

其他宿主 merge 进来的 FailureRecord 在本机可以没有 locator；这不影响公共报告的证据或可移植诊断。

## 14. 模块所有权

| 规则 | 唯一所有者 |
| --- | --- |
| 进程终态、截断与脱敏机械事实 | `ProcessRunner` |
| uv/ty/test 操作 cause | 对应 Adapter |
| Attempt 构造与 prepare stage | `EnvironmentFactory` / highest verifier |
| scope + cause -> disposition | 新的 failure policy module |
| baseline/probe 生命周期 | `SearchCoordinator` |
| `PASS/REJECTED` 边界与 `INDETERMINATE` 停止 | D003 / `CoordinateSearch` |
| FailureScope、FailureRecord 结构与交叉引用 | Evaluation/Report Schema |
| 本地详细日志与 diagnosis index | `RunLogStore` |
| `diagnose` 读取与组织 | `DiagnoseCommandWorkflow` |
| 人类摘要、日志链接与 remediation 文案 | `TerminalPresenter` |

Failure policy 应是一个深 module：调用方只提交结构化 FailureScope 和操作失败，module 隐藏分类矩阵。Adapter、搜索和 Presenter 都不得复制矩阵或使用 stderr substring 决定 disposition。

## 15. 首发 Schema 与开发期报告

本设计发生在 PF 首次发布之前。现有 Schema 1 和 algorithm v1 是开发期结构，不构成需要兼容的已发布契约。

实施时：

- 直接用本文结构替换现有 `schema_version = 1` 模型；
- 首发 generator/search algorithm identity 保持 `v1`；
- Evaluation policy identity 加入 `failure-v1`；
- 不实现 Schema 2、dual reader、迁移器或旧字段兼容分支；
- 所有开发期 `package-floor.json` 删除后重新 search；
- 旧结构即使也声明 `schema_version = 1`，仍因缺少 Attempt/FailureScope/FailureRecord 和新 union 字段而严格验证失败；
- 不从旧 status 猜测 Rejection，因为旧 candidate prepare 记录缺少完整 ToolFailure 和 Attempt identity。

## 16. 示例

### 16.1 Candidate resolution conflict

```text
baseline a=5 -> PASS
probe    a=1 -> RESOLUTION_CONFLICT / REJECTED
probe    a=3 -> PASS
probe    a=2 -> PASS
```

搜索得到 `a>=2`。`a=1` 是当前切片中的 predecessor rejection；cell 不因安装失败而提前终止。

### 16.2 Candidate harness conflict

```text
baseline packaging=24 -> PASS
probe    packaging=20 -> harness requires packaging>=22
```

该 Attempt 为 `HARNESS_CONFLICT / REJECTED`。PF 只声明完整 Proposal 在该 test contract 下失败，不声明 `packaging==20` 对所有项目不兼容。

### 16.3 Candidate timeout

```text
probe a=1 -> uv timeout
```

结果为 `TIMEOUT / INDETERMINATE`。搜索立即停止当前 cell；不能跳过 `a=1` 或把它放在 FAIL 侧。

### 16.4 Baseline build failure

```text
baseline highest -> BUILD_FAILURE / REJECTED
```

当前 highest fresh install 已被确定拒绝，但没有 PASS 锚点，不能开始坐标搜索。cell 终态为 baseline rejection。

### 16.5 Index unavailable

```text
probe a=1 -> registry DNS failure
```

结果为 `SOURCE_FAILURE / INDETERMINATE`。即使 baseline 刚刚通过，也不能从网络故障推断 candidate 不兼容。

### 16.6 Candidate discovery unavailable

```text
baseline highest -> PASS
candidate query  -> registry DNS failure
```

candidate query 发生在精确 Probe Attempt 建立前。结果使用 `CellFailureScope`，为 `SOURCE_FAILURE / INDETERMINATE` 并终止 cell；PF 不虚构 candidate vector 或 Attempt ID。

## 17. 不变量

1. 没有 Attempt identity 就没有 Rejection；Attempt 建立前的失败只能是 cell-scoped Indeterminate。
2. prepare 失败没有 Proposal，不得虚构 Proposal ID。
3. 只有 `ProbeRejection` 可以推进 FAIL 边界；`ProbeIndeterminate` 必须停止当前 cell。
4. Baseline 必须完整 `PASS` 后才能进入 candidate discovery 和 CoordinateSearch。
5. Rejection 只作用于完整 Attempt/Proposal，不进行 dependency failure attribution。
6. Cause 与 disposition 正交；Adapter cause 不能直接充当搜索状态。
7. Retryability、remediation 和日志可用性不改变证据分类。
8. 每个边界 rejection 都能解析到报告内的 FailureRecord 或结构化 Evaluation。
9. `diagnose` 默认只读且离线，不隐式重放失败。
10. Failure policy 进入 policy identity；开发期旧报告不迁移，必须重新 search。
11. CLI 不得用裸 Enum 代替用户可读的 title、impact 和 next step；展示文案不得反向改变结构化证据。

## 18. 决策记录

### D1：Candidate `BUILD_FAILURE` 形成 Rejection（已确认）

完整、明确归属到当前 Attempt 的 build backend/wheel 构建失败形成 Rejection。generic exit、截断输出、signal 和启动失败仍为 Indeterminate。

因此，旧版本只能从源码构建且构建失败时可以建立 FAIL 点；搜索不因该 candidate 提前终止。

### D2：Harness conflict 属于 PF 验证兼容性（已确认）

Harness conflict 属于 PF 的验证兼容性，因为 floor 只有在配置的完整 test contract 下才有意义。文案必须说“该 Proposal 不满足 harness contract”，不能说运行时项目本身必然坏。

Candidate harness conflict 形成 Rejection 并继续搜索；baseline harness conflict 形成 baseline Rejection 并终止，因为没有 PASS 锚点。

### D3：`diagnose` 首版严格离线（已确认）

首版只读取报告和本地脱敏日志，不重新执行。未来单独设计显式 `--reproduce`，并要求源码 snapshot、policy 和来源 identity 全部复验。

### D4：公共报告不保存 `run_id`（已确认）

公共报告只保存可移植 FailureRecord。项目本地 locator index 使用 `(report_generation_id, failure_id)` 关联相对日志路径；它不进入 report equality、merge 或 apply。

### D5：Schema 不保留 `BASELINE_FAILED`（已确认）

Schema 1 直接使用 disposition 明确的 `BaselineRejection | BaselineIndeterminate` union。CLI 可继续使用熟悉的人类文案，避免一个 Schema status 同时表示测试失败、安装拒绝和工具不确定。

### D6：不兼容开发期 Schema（已确认）

PF 尚未发布，直接重塑 Schema 1 与 algorithm v1；不实现 v1→v2 迁移或任何旧报告兼容层。

### D7：CLI 以用户文案为主、Enum 为技术细节（已确认）

实时命令、`explain` 和 `diagnose` 共享由 Presenter 生成的友好文案。Cause/disposition Enum 保留为稳定机器接口，但只能出现在次级技术详情中，不能单独作为面向用户的错误说明。

## 19. 非目标

- 自动修复 dependency、build backend 或 harness 配置；
- 把失败归因到某个依赖或测试用例；
- partial tests、suspect sets 或 progressive budget；
- 自动重试 flaky、timeout、网络或 build failure；
- 从 stderr 自由文本生成未经结构化验证的兼容性结论；
- 从 stderr 自由生成 remediation，或承诺自动修复用户项目；
- 跨运行 Evaluation cache；
- 在公共报告中嵌入完整原始日志、secret 或本地绝对路径。

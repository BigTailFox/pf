# PF 进程输出、磁盘日志与内存投影

- **状态：** 现行
- **最后核对：** 2026-08-21
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **CLI 交互与展示：** [D006](D006-pf-cli-enhancement.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)

本文是 PF 中外部进程输出如何捕获、如何写入本机日志、如何进入内存投影，以及这些材料如何（不）构成兼容性证据的唯一契约。D001 只承诺报告不含输出文本、日志不进入 identity，以及终端给日志链接；D002 只定义 `ProcessRunner` / `RunLogStore` 的模块位置、安全写和 Schema 形状；D005 只消费本文定义的“磁盘日志是否完整”，不解释字节上限；D006 只规定 live 卡片展示末几行，不规定这几行从哪读；D008 的 Verification Journal 不含 stdout/stderr 正文。

## 1. 问题

现行实现对每个 stdout/stderr 流施加捕获上限，并把“上限之内的字节”同时当作：

1. 进入 `ProcessResult` 的内存材料；
2. `.pf/logs` 里的日志正文；
3. D005 的证据完整性信号（`stdout_truncated` → 不能 Rejection，测试路径甚至变成 `TOOL_FAILURE`）。

这三件事需求不同。报告已经不再保存输出文本之后，磁盘日志仍被 32 MiB 截断，内存投影仍被当成原文，缓存里没有全文会被当成验证工具不可靠。结果是：pytest 进度条超过历史上的 4 KiB 默认值时，484 个测试通过也会中止 baseline。

真正需要分开的是：

```text
进程排出了什么？     磁盘日志（原文）
这次运行 RAM 里留什么？ 输出缓存（投影）
搜索可以怎样使用？   可移植终态 + 日志是否完整
```

## 2. 目标与非目标

### 2.1 目标

- 每个外部进程在 `.pf/logs/<run-id>/` 下有一份脱敏后的完整 stdout/stderr 原文，产品层不对正文设字节上限；
- 每个外部进程在内存中最多保留 16 MiB 输出投影，视为对磁盘日志的缓存：供 live CLI 和加速读取；
- 缓存未覆盖全文不得改变 cause、disposition 或 `failure_id`；
- 需要解析输出正文的 adapter 在缓存不够时从磁盘日志读取，不得因为缓存上限把完整工具输出判成 `TOOL_FAILURE`；
- `package-floor.json` 只保存进程终态，不保存 stdout/stderr 文本；
- 流式写入日志，不得把完整输出装进 `ProcessResult` 再切片。

### 2.2 非目标

- 不改变 D001 的命令、退出码、报告 Schema 版本或 apply 语义；
- 不改变 D003 搜索顺序或 D004 诊断身份；
- 不把日志路径、run ID 或输出哈希编进 Proposal / report identity；
- 不为 v1 增加用户可见的日志配额、跨进程内存总预算、pager、`--verbose` 或自动清理；
- 不规定 Windows/POSIX 安全写细节（仍由 D002 拥有）；
- 不规定 live 卡片展示几行（仍由 D006 拥有）。

## 3. 术语

**Process Log**（进程日志）：
一次外部进程结束后，写入项目 `.pf/logs/<run-id>/process-*.log` 的脱敏记录。它包含可移植终态和 stdout/stderr 原文。它不是公共证据。
_Avoid_: ProcessResult 输出字段, report log, summary/tail

**Output Cache**（输出缓存）：
当前 CLI 进程内对某一个外部进程 Process Log 正文的有界内存投影。它可以加速 live CLI 和 adapter 读取；未覆盖全文时必须仍能从磁盘日志读回。它不是证据。
_Avoid_: summary_limit, 捕获上限, 有界 ProcessResult, 跨进程总预算

**Portable Process Facts**（可移植进程终态）：
进入 `FailureRecord` / `package-floor.json` 的进程事实：退出码、signal、启动错误、timeout、耗时，以及每个流的磁盘日志是否完整写完。不含输出文本，不含日志路径。
_Avoid_: 有界 head/tail, truncated, 截断摘要

本文沿用 D005 的 Attempt、Rejection、Indeterminate、Cause、Disposition、FailureRecord、Diagnosis Index。

## 4. 三层模型

```text
外部进程
  → 接收缓冲（匿名 tempfile 或等价管道，不是产品日志）
  → 流式脱敏写入 Process Log          【原文，磁盘】
  → 可选填入 Output Cache              【投影，内存，每进程 ≤ 16 MiB】
  → Portable Process Facts 进入报告    【终态，可移植】
```

不变量：

1. 磁盘日志是输出正文的来源。缓存是派生数据。
2. 报告是终态的来源。缓存和日志都不进入 report identity。
3. 每个外部进程的 Output Cache 投影合计最多 16 MiB；Process Log 正文没有产品字节上限。
4. 从缓存读到的字节集合必须是对应磁盘日志的前缀、后缀或子区间；不得出现磁盘上没有的文本。

匿名接收缓冲允许在进程仍运行时增长。这不是对用户可见的日志配额；进程结束后应删除接收缓冲，只保留项目下的 Process Log。

## 5. 磁盘日志

### 5.1 内容

每个外部进程一份日志，格式实现可演进，但必须能还原：

- argv、cwd、仅环境变量名、timeout、redaction policy identity；
- 退出码 / signal / 启动错误 / timeout / 耗时；
- 每个流是否完整写入（`stdout_complete` / `stderr_complete`）；
- 脱敏后的 stdout 原文和 stderr 原文，各为一块连续正文，不再拆成 summary/tail 冒充原文。

argv、cwd、环境变量名等元数据仍可有独立硬上限（实现细节，防止把超长 `-c` 脚本写进日志头）。元数据截断只影响日志头，不得把对应流标成不完整，也不得改变终态分类。

环境变量值、已知凭据、URL userinfo 不得写入日志。脱敏在写入项目日志之前完成。`.pf` / `.pf/logs` 的权限、symlink fail-closed、原子写和 Windows ACL 由 D002 拥有。

### 5.2 无产品正文上限

v1 不对 Process Log 的 stdout/stderr 正文设置产品字节上限，也不提供 `[tool.pf]` 配额项。

进程仍受 D001 的 timeout 约束。磁盘耗尽、权限失败、写入中断导致日志没写完时，对应流的 `*_complete` 为 false，cause 按 D005 走基础设施/工具失败，不得伪装成 `TEST_FAILURE` 或 `STATIC_REGRESSION`。

PF v1 不自动删除运行日志。

### 5.3 写入时机

Process Runner 必须在进程输出产生后把脱敏正文流式写入 Process Log，使用固定大小的读写缓冲。禁止：先把全文读成 Python `str` 再按缓存上限切片后当作原文。

`RunLogStore.record` 可以在进程结束后把终态字段补进同一份日志（exit_code、耗时、完整性标志）。不得因为补终态而丢掉已经写入的正文。

## 6. 输出缓存

### 6.1 角色

Output Cache 是本次 CLI 进程内的缓存，不是第二份证据库。合法用途只有：

- live CLI 按 D006 取末几行；
- adapter 或 diagnose 在同一次运行中加速读取已经在缓存里的正文。

缓存未覆盖全文时，读取方必须打开对应 Process Log。不得把“缓存里没有全文”当成工具崩溃或磁盘日志不完整。

### 6.2 每进程 16 MiB

v1 不使用跨外部进程的内存总预算。每个外部进程的 Output Cache 投影（该进程 stdout 与 stderr 合计）最多 **16 MiB**。16 MiB 足够一次工具调用的常规长文本；超出部分只存在于磁盘日志。

当该进程输出超过 16 MiB 时，缓存只保留投影，优先保留各流末尾，以便 D006 的末 3 行不读盘也能显示。实现不得为了“装得下 JSON”再为单个 adapter 另设上限。

并行 cell 各自持有自己的最多 16 MiB 投影。分类完成后，实现应释放该进程除 live 卡片仍需要的投影之外的缓存。

缓存未覆盖全文时必须满足：

- 不得修改已写入的 Process Log；
- 不得修改 Portable Process Facts；
- 不得把 `stdout_complete` / `stderr_complete` 设为 false。

### 6.3 与 `ProcessResult` 的关系

可移植 `ProcessResult` 不携带 stdout/stderr 文本。运行期若仍把投影挂在同一 Python 对象上，那些字段不得进入 `model_dump` / `package-floor.json` / `failure_id`。推荐把缓存做成 runner/log store 的内部状态，按 `ProcessResult` 对象 identity 查找，避免 Schema 再次长出 `summary_limit`。

## 7. 可移植终态与完整性

Portable Process Facts 包括：

```text
exit_code | signal | start_error   （恰好一个终态）
timed_out
duration_seconds
stdout_complete
stderr_complete
```

`stdout_complete` / `stderr_complete` 为 true 表示：**磁盘上的 Process Log 完整保存了该流的脱敏原文**。默认为 true。false 只用于写入失败、磁盘耗尽、在写完之前被中止。下列情况必须保持 true：

- Output Cache 只保留了 16 MiB 投影或从未缓存全文；
- live CLI 只展示末几行；
- 日志头元数据被截断；
- 输出很长但已经完整写入磁盘。

现行 Schema 的 `stdout_truncated` / `stderr_truncated` 在落地本文时删除，不得作为别名保留。

`failure_id` 只哈希 Portable Process Facts 及 D005 规定的其它结构化字段，不吸收输出文本。

D005 §6 的“结果完整”解释为：需要完整工具输出才能分类时，对应流的 `*_complete` 为 true。缓存是否持有全文不是该条件的输入。

## 8. Adapter 如何使用输出

`ProcessRunner` 仍然不知道 uv、ty 或测试退出码语义。

- **只看退出码的操作**（完整 `test-command`）：分类只使用 Portable Process Facts。不得因为缓存里没有全文返回 `TOOL_FAILURE`。
- **需要解析正文的操作**（`ty` GitLab JSON、`uv python list` JSON、环境图 JSON、git 清单等）：先查 Output Cache；若投影不含完整文档，必须从 Process Log 读取再解析。解析失败（JSON 非法、结构不符）才是 `TOOL_FAILURE`。`stdout_complete` 为 false 导致无法解析时，同样是不完整结果，走 D005 的 Indeterminate / `TOOL_FAILURE`，而不是假装解析了不完整日志。
- **用输出短语辅助分类的操作**（uv 把 stderr 映射到 `RESOLUTION_CONFLICT` 等）：允许读缓存或日志。短语匹配不得在 `stderr_complete` 为 false 时给出确定性 Rejection；日志不完整的安装失败是 Indeterminate。

禁止 adapter 再为“装得下 JSON”设置 `ProcessSpec.summary_limit`。该字段若仍存在于 Schema，只允许测试注入小型 runner，不得出现在生产 adapter 调用中。

Registry HTTP 响应不是进程日志，其读取上限仍由 `UvAdapter` 拥有，不适用本文的磁盘无上限规则。

## 9. Live CLI 与 diagnose

D006 规定失败卡片最多展示进程输出末 3 行，并给 `-> see PATH`。这 3 行的数据源按顺序为：

1. Output Cache 中该进程的投影末尾；
2. 否则读取对应 Process Log 的末尾；
3. 否则省略输出行，仍展示日志链接（若有）。

成功 cell 不展示输出正文。

`pf diagnose` 展示 D005 规定的 title/impact/技术终态。完整 stdout/stderr 只通过本地 Process Log 查看。Diagnosis Index 缺失时，仍可展示报告内的 Portable Process Facts，并声明本地日志不可用；不得把缺失日志呈现为新的兼容性失败。其他宿主 merge 进来的 FailureRecord 可以没有本机日志。

## 10. 模块所有权

| 规则 | 唯一所有者 |
| --- | --- |
| 磁盘日志是原文、缓存是投影、报告只含终态 | 本文 |
| `stdout_complete` / `stderr_complete` 只描述磁盘日志完整性 | 本文 |
| 每外部进程 16 MiB Output Cache | 本文 |
| timeout、安全写、dir_fd/Windows ACL、diagnosis index 定位规则 | D002 / `RunLogStore` |
| cause → disposition | D005 |
| live 卡片行数、颜色、diagnose 文案层级 | D006 |
| 生产 `SubprocessRunner` 流式捕获与脱敏 | `ProcessRunner` |
| 把投影当缓存持有 | `ProcessRunner` + `RunLogStore`（内部状态，不新建公共模块，除非出现第二个真实实现） |
| uv/ty/test 如何解释正文 | 对应 Adapter |

## 11. 对现行实现的取代

落地本文后，下列现行行为作废：

- 默认 32 MiB（以及历史上的 4 KiB）捕获上限把全文切进 `ProcessResult`；
- `RunLogStore` 对输出正文再施加小于原文的第二截断；
- `TestAdapter` / `TyAdapter` 因 `stdout_truncated` 或内存投影不完整返回 `TOOL_FAILURE`；
- 生产路径上的 `ProcessSpec.summary_limit`；
- Schema 字段 `stdout_truncated` / `stderr_truncated`；
- D001 §5.4 把有界 head/tail 与捕获上限写成产品承诺；
- D004 §4.1 / D005 §6 把输出截断当作证据不完整。

P003 中“有界 head/tail 即日志”的历史证据仍然有效，但不再描述落地后的目标。

## 12. 被拒绝的方案

- **继续用一个字节上限同时服务报告、内存和证据完整性。** 报告已不存输出，该上限只制造假 `TOOL_FAILURE`。
- **磁盘日志完全进入 RAM 再按缓存上限裁切后写盘。** 这会让“磁盘无上限”在实现上先变成内存无上限。
- **每个 adapter 自选 summary_limit。** 浅接口；调用方必须知道默认值才会分类正确。
- **缓存未覆盖全文视为 TOOL_FAILURE。** 把 16 MiB 投影做成了兼容性结论。
- **v1 提供用户配置的日志配额。** 超时和 ENOSPC 已能停止 runaway；配额会再次把存储预算写成证据规则。
- **v1 使用跨外部进程的内存总预算。** 首发用每进程固定 16 MiB 更简单，也足够常规长文本；若以后并行 cell 的峰值内存成为问题，再另开契约，不得在本文含糊其辞。

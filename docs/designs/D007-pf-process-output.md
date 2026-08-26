# PF 进程输出、磁盘日志与内存投影

- **状态：** 现行
- **日志格式：** `pf-process-log-v2`
- **最后核对：** 2026-08-26
- **Failure 消费：** [D005](D005-pf-failure-and-diagnose.md)
- **CLI 展示：** [D006](D006-pf-cli-enhancement.md)
- **Journal 与 Index：** [D008](D008-pf-verification-run.md)

本文是 Process Log、Output Cache、portable process facts、输出完整性、日志保密与安全读取的唯一所有者。模块位置由 D002 描述；cause/disposition、展示和 Journal 不在这里重复定义。

## 1. 三层模型

```text
外部进程
  -> 匿名接收缓冲
  -> 脱敏正文写入 Process Log       磁盘原文
  -> Output Cache                  当前进程内投影，每 process <= 16 MiB
  -> Portable Process Facts        可移植终态，可进入 FailureRecord
```

- Process Log 是脱敏输出正文的来源；Output Cache 是可丢弃派生数据。
- 报告只保存 portable terminal facts；日志、cache 与 locator 不进入 report/Proposal/failure identity。
- Process Log 正文没有产品字节上限；每个 process 的 stdout+stderr cache 合计最多 16 MiB，优先保留尾部。
- Cache 中的文本必须来自相应日志，不得合成日志不存在的输出。
- 匿名缓冲可以随运行增长，进程结束后删除；它不是产品日志或配额。

## 2. ProcessResult

Portable facts 是：

```text
exit_code | signal | start_error   恰好一种 terminal fact
timed_out
duration_seconds
stdout_complete
stderr_complete
```

`ProcessResult.stdout/stderr` 仅是运行期 excluded cache projection；`model_dump`、FailureRecord 和 Schema 2 不包含它们。`failure_id` 只吸收 portable facts 与 D005 的其他结构化字段。

`stdout_complete` / `stderr_complete` 表示磁盘 Process Log 完整保存了该流的脱敏正文。Cache 未覆盖全文、CLI 只展示 tail、日志 header 元数据被截断都不得把它们设为 false。只有日志正文写入失败、磁盘耗尽或完成前中止才为 false。

需要完整结构化输出才能判断 outcome 的 adapter 只能在相应 stream complete 时给出确定结论；Output Cache 是否完整不是 classification 输入。

## 3. Process Log

每个外部 process 写入 `.pf/logs/<run-id>/process-NNNN.log`。V2 header 至少记录：

- process ID、redacted argv/cwd、environment names、timeout、session 与 redaction policy；
- exit/signal/start-error/timeout/duration；
- stdout/stderr completeness 与字符长度。

随后以字符长度 framing 写连续 stdout 与 stderr 正文。正文中出现 section marker 不得改变分流；生产 text handle 禁用平台换行转换，使 POSIX/Windows framing 一致。

历史 V1 只作保守读取：每个 section marker 恰好一次且顺序明确时可读；任何歧义、缺失或附加结构都 fail closed。V1 读取兼容不恢复旧写格式。

Header 元数据可有防滥用硬上限；截断不得影响正文、stream completeness 或 disposition。正文无 `[tool.pf]` 配额，PF v1 也不自动删除日志；process timeout 仍由 D001 配置控制。

## 4. 脱敏与安全边界

`SecretRedactor` 在任何数据进入项目日志、cache consumer 或 terminal event 前流式处理：

- 替换已知 secret literals；
- 删除 URL userinfo；
- 在 chunk 边界保留可能是 secret/URL 前缀的 suffix，直到能安全判定。

环境只记录变量名，不记录值。argv、cwd、failure detail 和 locator 在各自持久化边界再次验证/脱敏；不能假定上游已经安全。

`RunLogStore` 初始化时一次选择私有 `SecureLogDirectory`：

- POSIX 使用逐级 directory fd、no-follow 与 run-directory inode identity；
- Windows 使用 native directory handle、reparse guard、volume/ACL capability 与 protected DACL；
- `.pf`/`.pf/logs` symlink、目录替换、越界 locator 或无法提供等价安全原语时 fail closed；
- process log、run manifest、Journal 与 Diagnosis Index 使用私有权限和原子 replace。

产品层不包含平台条件；平台 adapter 只实现同一个 private secure-directory seam。

## 5. Capture 与 cache

`SubprocessRunner` 以 `shell=False`、独立 process group 和匿名 tempfile 接收 stdout/stderr。Process 结束后按固定 chunk 读取、跨 chunk 脱敏，并写入 Process Log；不得先把全文变成一个 Python string 再按 cache limit 截断后冒充原文。

Cache 合计预算 16 MiB。若两流都存在，预算在两流间分配并尽量保留各自尾部；单流可使用全部预算。并行 processes 各有独立预算，不建立跨 process 全局配额。

读取完整 output 的统一顺序是：

1. cache 覆盖全文则直接返回；
2. 有安全 Process Log locator 则从日志读取；
3. 否则只能返回已有 projection，调用方不得假装完整。

Production adapter 不设置更小的 `ProcessSpec.summary_limit`；该字段只允许聚焦 runner tests 注入预算。Registry HTTP response 不是 Process Log，由 UvAdapter 自行限制。

## 6. Adapter 使用规则

- 不解析 stdout/stderr 的 test command 只使用 portable facts，以及 D013 独立的 bounded pytest protocol；cache 不完整不能制造 `TOOL_FAILURE`。
- 解析 JSON/pylock/git output 的 adapter 必须取得完整 stream；语法/结构错误才是 tool failure。
- uv diagnostic classifier 只有在完整 output 与 D012 qualification profile 都成立时才能产生 conflict；不完整 stderr 保持 Indeterminate。
- ProcessRunner 不解释 uv、ty、pytest 或 test exit；adapter 不从 tail/summary 推断未观察的 facts。

## 7. Diagnose tail

普通 Cell card 不读取 process output，也不显示常规 Process Log link；精确 diagnose 入口不可用时，D006 才允许 link fallback。

`RunLogStore.read_tail` 通过 secure-directory adapter 流式读取合法日志：stderr 有非空行时返回其最后 3 行，否则返回 stdout 最后 3 行。读取时移除 ANSI/OSC、C0/C1 control（tab 除外）；Presenter 用 literal `Text`，不得解释 Rich markup。

Locator 缺失时仍可展示 FailureRecord portable facts；其他 host merge 的 Failure 通常没有本机日志。Locator 存在但日志 framing、encoding 或安全读取失败是本地诊断读取错误，不能伪装成新的 compatibility result。

## 8. 所有权与不变量

| 规则 | Owner |
| --- | --- |
| Process execution 与 cache implementation | `SubprocessRunner` |
| Log format、completeness、redaction、安全读写、16 MiB cache contract | D007 |
| Cause/disposition | D005 |
| Tail/link 展示 | D006 |
| Journal/Diagnosis Index association | D008 |

必须保持：日志正文与 cache projection 分离；cache 缺失不改变 portable facts；本地日志不可提升报告 authority；任何持久化面都不泄漏 secret；所有读取都通过安全 locator；输出正文不进入公共报告。

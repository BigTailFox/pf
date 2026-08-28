# P005 — 进程输出、磁盘日志与 Output Cache 实施记录

- **状态：** 已归档（已完成）
- **开始日期：** 2026-08-21
- **完成日期：** 2026-08-21
- **性质：** 非规范性实施记录
- **设计来源：** [D007](../../designs/D007-pf-process-output.md)
- **依赖：** D001–D005 已落地；本计划不实施 D006/D008
- **后继：** [P006](P006-pf-verification-run.md) 可与本计划并行于模块边界，但 Presenter 末 3 行数据源以本计划为前提

本文记录 D007 的落地过程与可核验证据，不复制契约。

## 1. 范围

本轮实现：

- 磁盘 Process Log 保存脱敏后的 stdout/stderr 原文，不对正文设产品字节上限；
- 每外部进程 Output Cache ≤ 16 MiB，优先保留各流末尾；缓存未覆盖全文不得改变 cause/disposition/`failure_id`；
- `ProcessResult` 只携带 Portable Process Facts：`stdout_complete` / `stderr_complete` 描述磁盘完整性；删除 `stdout_truncated` / `stderr_truncated`；
- 需要解析正文的 adapter 在缓存不够时从 Process Log 读取；不得因缓存上限返回 `TOOL_FAILURE`；
- 生产 adapter 不再设置 `ProcessSpec.summary_limit`；该字段仅保留给测试注入小型 runner。

## 2. 落地顺序

| 切片 | 可观察行为 | 主要测试 | 状态 |
| --- | --- | --- | --- |
| 001 | Schema：可移植终态用 `*_complete`；输出文本不进入 dump / `failure_id` | `tests/test_schemas.py` | 已完成 |
| 002 | Runner 以固定大小缓冲流式脱敏写盘；禁止先拼全文 `str` 再当原文；超缓存上限时投影为尾部且 `*_complete` 仍为 true | `tests/test_process.py` | 已完成 |
| 003 | `TestAdapter` / `TyAdapter` / `UvAdapter` / snapshot 不因 cache 上限判 `TOOL_FAILURE` | adapter 与 snapshot 测试 | 已完成 |
| 004 | `FailurePolicy` 以磁盘完整性而非 truncated 判定 Rejection 资格 | `tests/test_failure.py` | 已完成 |
| 005 | 相关门禁 | pytest / ty | 已完成 |

## 3. 过程与证据

### 关键产物

- `src/pf/adapters/process.py`：`OUTPUT_CACHE_LIMIT = 16 MiB`、`project_output_cache`、`_redact_stream` 分块回调（不再 `"".join`）、`_OutputCacheBuilder` 滚动尾部、`read_process_output`
- `src/pf/runlog.py`：`begin_record` / `_ProcessLogWriter` 流式正文；`finish` 补终态后按固定缓冲拷入 Process Log 并原子 replace
- `src/pf/schemas/evaluation.py`：`ProcessResult.stdout`/`stderr` `exclude=True`；删除 truncated 字段
- 生产 adapter（`ty.py` / `uv.py` / `test_command.py`）不设 `summary_limit`；HTTP registry 上限仍归 `UvAdapter`

### 可核验测试

```text
uv run --no-sync pytest --no-testmon -q --tb=line
  tests/test_process.py
  tests/test_schemas.py::test_process_result_omits_captured_output_from_portable_facts
  tests/test_failure.py
  tests/test_uv_adapter.py
  tests/test_ty_adapter.py
  tests/test_report.py::test_report_store_omits_captured_process_output
```

代表断言：

- `test_subprocess_runner_honors_a_larger_process_spec_cache_limit`：cache 为投影，`stdout_complete is True`
- `test_subprocess_runner_output_without_logs_stays_within_cache_limit`：无日志时 `output()` 仍不超过 cache_limit
- `test_process_result_omits_captured_output_from_portable_facts`：dump 不含 stdout/stderr 文本
- `tests/test_uv_adapter.py`：`runner.spec.summary_limit is None`
- `tests/test_failure.py`：`stdout_complete=False` 不得形成 Rejection
- `test_subprocess_runner_streams_redacted_chunks_instead_of_joining_a_str`：每次 `write_stdout` 块 ≤ 64 KiB 且块数 ≥ 3；`record(stdout=全文)` 路径会失败
- `test_run_log_store_copies_process_body_in_fixed_size_buffers`：spy 每次 `stream.write`，完整 payload 不得作为单次写入参数
- `test_run_log_store_patches_terminal_facts_without_dropping_streamed_body`：`finish` 补 `exit_code` 等终态且正文仍在

### 决策

- `ProcessSpec.summary_limit` 只给测试注入小型 cache；生产路径一律 16 MiB。
- `*_complete` 只描述磁盘是否写完，不描述 cache 是否装满。
- Registry HTTP 读取上限不属于进程日志，仍由 `UvAdapter` 拥有。
- 生产 runner 经 `begin_record` / `write_*` / `finish` 写盘。`RunLogStore.record(stdout=str)` 仍是测试/空 body 便利入口，不是 runner 写原文的路径。

### 审计补完：无日志时 Output Cache 也不得超过 16 MiB

独立审计发现 `SubprocessRunner._outputs` 曾保存脱敏后的全文，而不是 16 MiB 投影。无 `RunLogStore` 时 `output()` 会把全文交回调用方，违反 D007 §6.2。投影必须写入内部缓存；全文只存在于 Process Log。

### 审计补完：D007 §5.3 流式写入（不得先拼全文 `str`）

D007 §5.3 原文要点：

> Process Runner 必须在进程输出产生后把脱敏正文流式写入 Process Log，使用固定大小的读写缓冲。禁止：先把全文读成 Python `str` 再按缓存上限切片后当作原文。
>
> `RunLogStore.record` 可以在进程结束后把终态字段补进同一份日志……不得因为补终态而丢掉已经写入的正文。

D007 §12 被拒绝方案同等措辞：

> 磁盘日志完全进入 RAM 再按缓存上限裁切后写盘。

**改前实现：** 匿名 tempfile 收齐 → `_redact_stream` 把分块 `"".join` 成一份 Python `str` → `project_output_cache` 按 16 MiB 切片 → `RunLogStore.record(stdout=全文)` → `_render` 再拼成一份 `content: str` → `_write_private_at` 一次原子写盘。磁盘最终内容可以是对的，但写路径先把「磁盘无上限」变成「内存无上限」，与 §5.3 / §12 矛盾。只断言「磁盘上有全文」不能证明已经流式写入。

**改后实现：**

1. 匿名 tempfile 仍是 §4 接收缓冲；进程结束后由 `TemporaryFile` 上下文删除。
2. `_redact_stream` 按 65536 字节读、跨块 overlap 脱敏，对每个块回调，不再 `join`。
3. 同一回调写入 `_OutputCacheBuilder`（滚动各流尾部，合计 ≤ 16 MiB）和 `ProcessLogWriter.write_stdout` / `write_stderr`。
4. Writer 把分块落到匿名 body tempfile；`finish(result)` 用固定缓冲拷贝 header（含终态）+ 已写入正文，再原子 replace。这落实 §5.3「可以补终态」且不丢正文。

宽度匹配测试见上列三条新测试名；它们拦截的是「每次写入的 Python `str` 块」，不是「文件读回来等于原文」。

### 裁决：分类后释放 cache（§6.2「应」）

D007 §6.2 原文要点：

> 分类完成后，实现应释放该进程除 live 卡片仍需要的投影之外的缓存。

**本轮不实现，不把现状写成已满足该条。** 理由：

- 条款用语是「应」，不是 §5.3 的「必须 / 禁止」。
- 分类所有权在 adapter / `FailurePolicy`（D007 §8、§10）；`ProcessRunner` 不知道何时分类完成。为释放 cache 新增跨模块钩子会扩大本轮范围。
- live 卡片已挂在 `ProcessResult.stdout`/`stderr` 上的 ≤16 MiB 尾部投影（`exclude=True`）；D007 §9 允许回退读 Process Log。

§4「进程结束后应删除接收缓冲，只保留项目下的 Process Log」随 §5.3 路径落地：接收缓冲是匿名 `TemporaryFile`，流式脱敏结束后关闭，产品日志只留 `.pf/logs/<run-id>/process-*.log`。

### 仍非目标 / 不假装已实现

- Adapter 在缓存不够时 `read_output` 把磁盘日志读成 `str` 再解析：这是 D007 §8 的读路径，不是 §5.3 对「写原文」的禁令。
- Windows `write_private_stream` 的流式证明不在 Linux Fake 上成立；POSIX `_write_private_stream` 上的 spy 才是写路径证据。
- 不刷 P001 `fail_under=90` 覆盖率；不重做 D001–D005。

## 4. 完成门禁

```text
uv run --no-sync pytest --no-testmon -q
  -> 513 passed in 108.24s
uv run --no-sync ty check src tests
  -> All checks passed
```

全量套件在 §5.3 流式写入补完后重跑。未 git commit。

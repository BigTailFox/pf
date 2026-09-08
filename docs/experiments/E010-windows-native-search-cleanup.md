# E010 — 非 WSL Windows 上 PF 自搜索与 Cell 结束环境清理

- **状态：** 已完成
- **日期：** 2026-09-08（Asia/Shanghai；run-id 使用 UTC）
- **性质：** 非规范性 dogfood 实验事实，不定义新契约
- **前序：** [E001](E001-pf-self-bootstrap-validation-contract.md)、[E002](E002-pf-search-performance.md)
- **规范：** [D001](../designs/D001-pf.md)、[D003](../designs/D003-pf-search-algorithm.md)、[D007](../designs/D007-pf-process-output.md)、[D008](../designs/D008-pf-verification-run.md)、[D014](../designs/D014-pf-report-schema.md)
- **开放对照：** [R008](../reviews/R008-pf-search-performance-review.md)、[R010](../reviews/R010-pf-engineering-document-audit.md) 的 Windows host 资格缺口
- **证据位置：** [data/E010/](data/E010/)（测量脚本 JSONL / 宿主事实 / 终端抄本）；可变根 `package-floor.json` 不作历史证据链接
- **目标：** 在当前 PF 仓库、原生 Windows（非 WSL）上复现 `pf search` 报错，完成一轮自搜索，并测量 Cell 结束时 `PreparedEnvironment` 清理墙钟

本文只记录此次宿主上的命令、结果与局限。它不授权把 Windows 清理耗时写成产品 SLA，也不把本次 dirty worktree 的 floor 当作发布资格。

## 1. 结论

未改生产代码时，当前仓库在非 WSL Windows 上执行 `pf search` 会在进程超时 / 中断路径上以未捕获异常退出：`AttributeError: module 'os' has no attribute 'killpg'`。这不是 verifier Rejection，search 写不出权威报告。

在 dirty worktree 上补了 Windows 进程树停止、子进程 PATH 解析、解释器 ABI 回退和临时目录 cleanup 重试之后，本轮公开 `pf search` **能跑完并写出报告**。三个 Cell 都在 `[baseline][highest][testing]` 被 configured verifier 拒绝，报告 `result.status=incomplete`，reasons 为 `BASELINE_REJECTION` 与 `UNREPRESENTABLE_PROJECTION`。CLI 退出码 1。没有进入 coordinate descent，因此 **没有** `finish_coordinate` 批次清理样本。

Cell 结束时的环境清理（HighestVersionVerifier 在 baseline 完整 verifier 之后的 `PreparedEnvironment.close`）是亚秒级删除，不是 hang：

| 轮次 | Cell | 文件数 | 字节 | duration_s | exists_after |
| --- | --- | ---: | ---: | ---: | --- |
| 2（主证据） | 3.10 | 2850 | 149,070,750 | **0.453** | false |
| 2 | 3.11 | 2831 | 155,735,991 | **0.391** | false |
| 2 | 3.12 | 2831 | 154,442,200 | **0.438** | false |
| 1 | 3.10 | 2714 | 148,131,294 | 0.188 | false |
| 1 | 3.11 | 2695 | 154,555,395 | 0.171 | false |
| 1 | 3.12 | 2695 | 153,312,326 | 0.172 | false |

源码 snapshot 关闭约 0.031–0.047 s（约 390 个条目 / 8.3 MiB）。同一量级 proposal 树在两轮之间有约 2× 墙钟差（171–188 ms vs 391–453 ms），符合 NTFS / Defender 抖动，仍远小于默认 30 分钟 `test-timeout` 和单 Cell baseline pytest 墙钟。

## 2. 宿主与输入

| 项 | 值 |
| --- | --- |
| OS | `platform.platform()=Windows-10-10.0.26100-SP0`；`os.name=nt`；`sys.platform=win32` |
| WSL | `WSL_DISTRO_NAME` / `WSL_INTEROP` 均为空；原生 Win32 |
| `os.killpg` | 不存在（[host.json](data/E010/host.json)） |
| PF 解释器 | `.venv` CPython 3.10.11 |
| CPU | 28 |
| 仓库 HEAD | `afbf3a57c6703eca6acd4e9b1a158bd69ae837ed` |
| 工作树 | dirty：Windows 进程终止、PATH 解析、ABI 回退、临时目录 cleanup、测量脚本与本实验文档 |
| 配置 | `[tool.pf] test-command = ["pytest", "--no-testmon", "--no-cov", "--maxfail=1", "-m", "not qualification"]` |
| 测量命令 | `scripts/measure_windows_search_cleanup.py` 包装公开 `pf search --max-cells 1 --ty-jobs 1 --test-jobs 1` |

`--max-cells 1` 限制 Cell Scheduler 并发为 1，使三次 Cell 串行，cleanup 墙钟不重叠。它不是「只搜一个 Cell」：本轮仍选了 Python 3.10/3.11/3.12 三个 Cell。该限制只是测量配置，不是产品默认。

测量脚本在调用公开 CLI 前 monkeypatch `PreparedEnvironment.close`、`SourceSnapshot.close`、`_ProposalRunner.finish_coordinate` 与 `_ProposalRunner.close`。探针必须先 `from pf.cli import main`，不能 `sys.path.insert(src)` 再直接 import `pf.environment`（会循环导入）。

## 3. 报错排查

按实际阻断顺序：

### 3.1 `os.killpg`（原先把整轮 search 打成未捕获异常）

`ProcessSpec.start_new_session` 默认 `True`。超时与 CLI interrupt 走 `SubprocessRunner._terminate` → `os.killpg`。Windows 上该符号不存在。CPython 3.10 还会把 `Popen(start_new_session=True)` 标为 unused；产品默认仍把该标志当作 POSIX process group。

默认 `test-timeout` 为 1800 s。Windows 上一次完整 pytest 探针很容易接近或超过该上限；一旦超时，search 不是记 `TimedOut` / Indeterminate，而是进程 runner 崩溃。

修补：POSIX 仍 `killpg` SIGTERM → grace → SIGKILL。Windows 用 `taskkill /T`（先非 `/F`，grace 后再 `/F`，再 `process.kill()`）。`Popen` 的 `start_new_session` 在 `os.name == "nt"` 时不传入，避免把 POSIX session 语义塞进 Win32。停止路径仍按 spec 的 process-group 意图做进程树 terminate。不要再给 Windows child 加 `CREATE_NEW_PROCESS_GROUP`：加上之后本机 pytest 出现 `WinError 2`。

`taskkill` 必须用模块导入时捕获的 `subprocess.Popen`，不能再走运行期 `subprocess.run` / `subprocess.Popen`：configured verifier 里 `test_cancel_during_spawn_reaps_the_child` 会 monkeypatch `Popen`，否则内部 `taskkill` 被当成第二个 child，baseline 在第一个测试失败。

定向测试（修补后通过）：`test_subprocess_runner_times_out_default_process_group_without_posix_killpg`、`test_subprocess_runner_timeout_stops_a_grandchild_process`、`test_subprocess_runner_interrupt_stops_an_inflight_process_group`、`test_cancel_during_spawn_reaps_the_child`。

### 3.2 Windows `CreateProcess` 不按子进程 PATH 解析 `argv[0]`

未激活 `.venv` 时，父进程 PATH 上没有 `pytest`。PF 把 proposal `Scripts` 写进**子进程** PATH，但 Win32 `CreateProcess` 用**父进程** PATH 解析无目录的 `argv[0]`。对照：全路径 `pytest.exe` 可运行；`shutil.which("pytest", path=child_PATH)` 能找到；`Popen(["pytest"], env={PATH: child_PATH})` 报 `WinError 2`。

修补：`_launch_argv()` 在 Windows 上对无目录的 `argv[0]` 用子进程 PATH 做 `which`，再把绝对路径交给 `Popen`。POSIX 为 no-op。

### 3.3 空 `SOABI` 导致报告校验失败

Windows 上 `sysconfig.get_config_var("SOABI")` 为 `None`，interpreter `abi=""`。D014 reader 要求 `cpython-310` / `cp310` 一类 ABI，写出报告时报 Proposal interpreter 与 Cell 不匹配。回退到 `sys.implementation.cache_tag`（本机 `cpython-310`）。

### 3.4 临时目录 cleanup 的 sharing violation

`PreparedEnvironment.close` / `SourceSnapshot.close` 原先直接 `TemporaryDirectory.cleanup()`。Windows 上文件仍被占用时会把 search 打成未捕获异常。`cleanup_temporary_directory()` 在 Windows 上按 `0 / 0.2 / 0.5 / 1.0 / 2.0` s 重试，最后 `shutil.rmtree(..., ignore_errors=True)`。本轮主证据三次 proposal 关闭均 `error=null` 且 `exists_after=false`，没有走到需要记录的残留目录。

### 3.5 控制台 GBK 与 `✓`

默认系统代码页下，Rich 往控制台写 `✓` 会 `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'`，`pf diagnose` 同样会炸。本轮测量与 search 使用 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`。这是实验绕过，不是产品改了终端编码契约。

### 3.6 测量输出写进仓库会导致 snapshot drift

第一轮把 `--output-dir` 指到 `docs/experiments/data/E010/`。search 过程中写入 JSONL，结束时 `project source snapshot drifted during search`，CLI 退出码 3，报告未写成。第二轮输出到 `%TEMP%\pf-e010`，结束后复制证据；search 本身不再改仓库源码树。

## 4. Search 运行

环境：

```text
PYTHONUNBUFFERED=1
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
```

第一轮（漂移，不作为报告终态）：

```text
.venv\Scripts\python.exe -u scripts\measure_windows_search_cleanup.py --output-dir docs\experiments\data\E010 -- search --max-cells 1 --ty-jobs 1 --test-jobs 1
```

| 项 | 值 |
| --- | --- |
| run-id | `20260908T100525.883689Z-8340-b27ab2d4` |
| 墙钟 | 41.921 s |
| 退出码 | 3 |
| 终态 | snapshot drifted；baseline 已在 `test_cancel_during_spawn_reaps_the_child` 失败 |
| 证据 | [round-1-repo-write-drift/](data/E010/round-1-repo-write-drift/) |

第二轮（主证据）：

```text
.venv\Scripts\python.exe -u scripts\measure_windows_search_cleanup.py --output-dir %TEMP%\pf-e010 -- search --max-cells 1 --ty-jobs 1 --test-jobs 1
```

| 项 | 值 |
| --- | --- |
| run-id | `20260908T100831.101809Z-9560-71e586b5` |
| 墙钟 | 65.141 s |
| 退出码 | 1 |
| 报告 | `package-floor.json` 已写；`report_generation_id=35503bb92eaa1cf1277e56c363befad8e9493624c6b4441a09f32bb17665480c` |
| 报告 status | `incomplete`；`BASELINE_REJECTION` + `UNREPRESENTABLE_PROJECTION` |
| 三个 Cell | 均为 `BASELINE_REJECTION`，停在 `[baseline][highest][testing]` |
| 失败用例 | `tests/test_diagnose.py::TestDiagnoseWorkflow::test_diagnose_resolves_a_successful_floor_predecessor_and_local_log` |
| 证据 | [host.json](data/E010/host.json)、[command.json](data/E010/command.json)、[summary.json](data/E010/summary.json)、[cleanup.jsonl](data/E010/cleanup.jsonl)、[search.txt](data/E010/search.txt) |

`--maxfail=1` 下 baseline 在第一个失败测试停止。本机复现该 diagnose 测试时，断言失败点是渲染文本里找不到完整 `proposal {id}` 子串；`✗` / `✓` 在 `PYTHONUTF8=1` 下已经出现。这是 configured full-suite verifier 在 Windows 上的后续资格缺口，不是进程 runner 崩溃。未为了让 search 进入坐标下降而继续逐项改测试。

观察重点与实际命中：

| 探针事件 | 是否出现 | 含义 |
| --- | --- | --- |
| `prepared-environment-close` / `baseline_finally` | 每 Cell 一次 | HighestVersionVerifier 在 baseline 完整 verifier 之后关闭那一个环境；本轮这就是 Cell 结束时的环境释放 |
| `finish-coordinate` | 无 | 未离开 baseline，没有坐标结束批次 |
| `proposal-runner-close` | 无 | 未进入 Cell search context 的剩余关闭路径（或 close 时 `_prepared` 已空且探针未单独记一条零计数事件） |
| `release_prepared` | 无 | 无静态窗口溢出提前释放 |
| `snapshot-close` | 每轮两次 | 源码 snapshot 与 drift 对照 snapshot |

## 5. Cell 结束清理用时

主证据 JSONL：[cleanup.jsonl](data/E010/cleanup.jsonl)。`duration_s` 覆盖 `PreparedEnvironment.close` / `SourceSnapshot.close` 的墙钟，含 Windows retry 睡眠（若发生）。本轮三次 proposal 关闭均一次成功，未见 retry 带来的整秒级停顿。

`file_count` 是 `Path.rglob("*")` 的条目数（含目录），`byte_count` 只累加普通文件大小。

没有 `finish_coordinate` 样本。若把「Cell 结束」理解成坐标下降中最后一次坐标的环境释放，本轮没有该数据；若理解成该 Cell 搜索停住后释放其唯一物化环境，则 `baseline_finally` 就是该事件，墙钟为上表 0.391–0.453 s。

## 6. 局限

- 本轮是 dirty worktree 上的 dogfood，source snapshot 含本次 Windows 修补、测量脚本与实验文档，不能与干净 HEAD 或 E001/E002 的 Linux 计数互换。
- 串行 `max-cells=1` 拉长墙钟，不能外推为 `auto` 并行的 wall-clock critical path。
- Windows Defender / Indexer 对 `%TEMP%\pf-proposal-*` 的扫描不在 PF 控制范围内，清理墙钟包含宿主杀毒与 NTFS 删除成本。
- 最后一次 `ignore_errors` 可能留下残留临时目录；实验记录 `exists_after`，不把「磁盘一定清空」写成不变量。本轮三次主证据关闭均为 `exists_after=false`。
- `PYTHONUTF8=1` 是实验绕过；未改产品终端编码。
- baseline 被 `--maxfail=1` 的完整仓库测试套件拒绝，因此没有坐标下降中的环境清理样本，也不能把本次 floor 写成发布资格。
- 第一轮把测量输出写进仓库，额外引入 snapshot drift；该失败模式属于实验装置，不是产品 search 算法。
- `test_process` 里仍有与本轮无关的既有 Windows 缺口（例如 CRLF 与 `\n` 对照、stubborn `SIGTERM` 在 Windows 上 `signal is None`、`os.mkfifo` 不存在）。未把整扇 Windows 测试门改成绿当作本实验目标。

# E006 — requests 双阶段完整搜索实验

- **状态：** 已完成
- **日期：** 2026-09-05–06（Asia/Shanghai）
- **性质：** 非规范性 dogfood 实验报告；记录运行事实，不定义产品契约
- **历史对照：** [E003](E003-requests-dependency-validation.md)、[E004](E004-requests-validation-surfaces.md)
- **目标：** `experiments/requests`，commit `dae7ef63b4df6eded86637f251fc4e3a06c3b479`
- **PF：** `0.1.0`；第一阶段 search 的生产代码为 `325e43d`，第二阶段 refine 使用 `4a9238d`
- **实现口径：** 第一阶段后的 smoke/check 重跑 HEAD 为 `40b34cd`；第二阶段在 D033 修复提交后运行。search 报告自身只记录 generator 版本，不记录 PF Git commit
- **Source snapshots：** 第一阶段 `75b5d4f7dd959f3f54a5fea2bb46aa8937d81fcdcd4d3f496040ce91bb6329b3`；第二阶段 `0d625d940fbec6c9b68eb8cb650e03308eab43385651fb4dfe6be354300c46ef`
- **Evaluation policy：** `aa654eb96a8614885202f36d8e4158e077ecfc7ed2e1f5b3dda2485760b851d0`
- **Search generations：** 第一阶段 `8611ccc7f3da74ae11fef4544367e856ea363c4c3258b0f28775e73c0c20b282`；第二阶段 `0aff1d79fe9851ce263b4a9bc8ef2fd93ad46810ad723309e28d9b305da6dc85`

最新完整 smoke 复测 **10/10 PASS**；原始声明下界 check **10/10 REJECTED**；用户完成的 search
分为 minor 定位与 patch refine 两个阶段，两份报告均为 **10/10 SUCCESS、complete、六个依赖投影
全部可表示**。refine 后 `charset-normalizer`、`urllib3` 与 `PySocks` 分别落到 `1.3.1`、`1.26.5`
与 `1.7.0`。首次 smoke 重跑出现一次本地 HTTP 连接重置，完整复测未复现，两次结果均保留。
不能把 search 成功解释为原始声明下界已经通过。

## 1. 输入、验证契约与实际时序

三种命令均覆盖 CPython **3.10–3.14**、`x86_64-unknown-linux-gnu`、两个实际 extra surface：
`socks` 与 `socks+use_chardet_on_py3`，共 **10 Cells、6 个受管直接依赖、0 pinned**。
`requests[socks]` 的 harness 自引用已吸收为 required extras；空 `security` 不再创建额外 Cell。

目标配置保留：

```toml
[tool.pf]
test-command = ["pytest"]
resolve-artifact = "any"
```

验证契约是完整 repository pytest，包含仓库既有 doctest、HTTP/httpbin、证书、SOCKS 与本地
testserver 测试。未缩小测试路径或删除测试。正常失败可 early-exit；搜索中的 failed-set 子运行只能
提供拒绝证据，PASS 仍须来自完整 configured verifier。静态诊断计数不等于 verifier 结果。

两次 search 都使用报告冻结的 `registry-series-slice-v1` 策略，不含 prerelease。双阶段由两次
独立 PF invocation 组成，不是一次 invocation 内隐含的 `major → minor → patch` 树搜索：

| 阶段 | declaration 输入 | search space | resolution | 目的 |
| --- | --- | --- | --- | --- |
| 第一阶段 | requests 原始依赖声明 | 省略显式 space，命中 `majors[declaration-1:]` 条件默认 | `minor` | 在声明前一个 major 起的范围内定位通过的 minor 系列代表 |
| 第二阶段 | 第一阶段 projection 写入后的中间声明 | `minors[declaration]` | `patch` | 只在已定位 minor 内进一步确定 patch floor |

第一阶段报告中的字段名仍为当时契约的 `search-step = "minor"`；现行契约已改名为
`search-resolution`。第一阶段 `requested_space = null`，实际使用的条件默认为：

```toml
with-lower-bound = "majors[declaration-1:]"
without-lower-bound = "majors[baseline-2:]"
```

六个依赖均有声明下界，使用第一项。每个 minor 系列取最高合格精确 release 作为代表。用于第二阶段的
中间工作树包含第一阶段六条 projection，并增加：

```toml
search-space = "minors[declaration]"
search-resolution = "patch"
```

因此第一阶段 floor 是冻结 major 范围内的 minor 代表结果，第二阶段 floor 是对应 declaration minor
内的 patch 代表结果；两者都不是全发布历史或全部依赖组合的穷举证明。第二阶段有自己的 SourceSnapshot、
CandidateSnapshots、Attempts 与 runtime evidence，不把第一份报告当作跨运行 evaluation cache。

本文按 `smoke → check → 第一阶段 search → 第二阶段 refine` 解释结果，但实际运行时序为：用户先完成
9 月 5 日 21:47 开始的第一阶段 search，再要求补跑 smoke/check；本次在 22:18、22:19 执行
smoke/check，22:20 再完整复测 smoke。上述四次运行的 snapshot、evaluation policy 相同；search 使用
自己取得的 baseline，不引用后来 smoke 的 PASS。第一阶段 projection 随后进入中间工作树，9 月 6 日
01:05 在 D033 修复后开始第二阶段。它保持相同 evaluation policy，但因声明与搜索配置改变而使用新的
snapshot。E003/E004 的旧 policy、旧矩阵不混入本轮证据。

本次由 PF 仓库根目录启动沙箱外进程，子进程 cwd 为目标仓库；核心调用为：

```python
import os
import subprocess

env = dict(os.environ)
env["PATH"] = "/home/llh/pf/.venv/bin:" + env["PATH"]
env["UV_CACHE_DIR"] = "/tmp/pf-uv-cache"
for command in ("smoke", "check"):
    subprocess.run(
        ["/home/llh/pf/.venv/bin/pf", command],
        cwd="/home/llh/pf/experiments/requests", env=env, timeout=900,
    )
```

实际执行同时保存 stdout/stderr、开始结束时间和退出码；第二次 smoke 使用相同环境及参数。
两次 search 的命令与终端输出由用户提供，本次整理未重新执行 search。

## 2. 运行总表

| 阶段 | run-id | PF 退出码 | 结果 | 耗时口径 |
| --- | --- | --- | --- | --- |
| smoke 首次重跑 | `20260905T141808.653261Z-783898-a1b8d058` | 1 | 9 PASS、1 REJECTED | 实测墙钟 86.53s |
| check 重跑 | `20260905T141935.195322Z-788444-6d7e586c` | 1 | 10 REJECTED | 实测墙钟 25.03s |
| smoke 完整复测 | `20260905T142054.067994Z-793128-eb5cfb77` | 0 | 10 PASS | 实测墙钟 85.42s |
| 第一阶段 search（minor） | `20260905T134722.724441Z-686839-efa6c438` | 未独立捕获 shell 退出码 | 10 SUCCESS；`result.status = complete` | 终端最长 Cell 24m11s |
| 第二阶段 refine（patch） | `20260905T170534.659642Z-1081079-e82a45d6` | 未独立捕获 shell 退出码 | 10 SUCCESS；`result.status = complete` | 终端最长 Cell 14m37s |

两次 search 的 `Search complete` 均与各自机器报告相符；按对应命令契约代表成功，但不把推定退出码冒充
独立捕获结果。Cell 并发耗时不能相加当作命令墙钟，也不能用最长 Cell 替代完整命令计时。

## 3. smoke：最高版本通过，另有一次未复现的连接重置

首次重跑只有 Python 3.13 / `socks+use_chardet_on_py3` 失败：

- 测试：`tests/test_requests.py::TestRequests::test_HTTP_307_ALLOW_REDIRECT_POST`。
- 异常：`requests.exceptions.ConnectionError`，底层 `ConnectionResetError(104, 'Connection reset by peer')`。
- 本地 httpbin 已返回一次 HTTP 307；随后连接重置。pytest 在 64 passed 后停止，正常退出 1。
- Failure ID：`failure-2edfd9499a0bb6c9`；日志为该 run 的 `process-0083.log`。
- Journal：`REJECTED / VERIFIER_EXITED_NONZERO / test`，authority 为 configured verifier。

相同配置的第二次完整 smoke 为 10/10 PASS；10 个 pytest 进程全部正常退出 0，每个均报告
`619 passed, 15 skipped, 1 xpassed, 18 warnings`。Journal 无失败条目，终端也明确输出
`Smoke passed · 10 cells`；成功数由三者交叉核对，不单凭空 Journal 推断。

本轮证据说明该连接重置**未在一次完整复测中复现**，尚不足以确定触发根因或发生概率。
保留首次 REJECTED，不改写为 INDETERMINATE，也不由一次成功复测推断环境永远稳定。

## 4. check：原声明下界仍不能满足验证契约

10 个 Cell 全部进入 declaration / `lowest-direct` 的 configured pytest，并正常非零退出，
没有 declaration-capture 或 environment projection 的 INDETERMINATE。

本次 lowest-direct project lock 选出：`certifi=2023.5.7`、`charset-normalizer=2.0.0`、
`idna=2.5`、`pysocks=1.5.6`、`urllib3=1.26.0`；chardet surface 另有 `chardet=3.0.2`。

| Python / Cells | pytest 退出码 | 本次直接可见的失败 | 代表证据 |
| --- | --- | --- | --- |
| 3.10–3.11 / 4 | 1 | `test_use_proxy_from_environment[http_proxy-http]`；`InvalidSchema: Missing dependencies for SOCKS support.` | `failure-ba7fa029fca4cb1e`；`process-0110.log` |
| 3.12–3.14 / 6 | 4 | conftest 导入失败；`ModuleNotFoundError: No module named 'urllib3.packages.six.moves'` | `failure-d2e1c29553a0afcc`；`process-0101.log` |

这与 [E004 §8](E004-requests-validation-surfaces.md#8-后续诊断check-的声明下界失败) 记录的低版本
导入问题一致。表中错误文本是本次日志事实；PySocks 旧导入方式的进一步归因见该历史调查。
四个 SOCKS 失败环境实际已安装 PySocks，不能将错误提示直接理解成 PF 漏装依赖。

check 验证的是当时原始声明生成的下界组合；search 可以抬高不通过的坐标、降低其他坐标并取得 PASS。
两者结果因此并不矛盾。smoke/check 复测本身未执行 apply，也未改动 requests 的依赖声明；后续为衔接
第二阶段而写入第一阶段 projection 的中间工作树见 §6。

## 5. 第一阶段 search：minor floor 与拒绝边界

第一阶段完成时，`ReportStore.read` 已完整验证当时的 `experiments/requests/package-floor.json`；其摘要在
原始文件被第二阶段覆盖前已冻结到 §7。
10 个 Cell 均为 `SUCCESS`、各完成 2 sweeps；每个 baseline 和 final Proposal 都有 PASS Evaluation。
六个 projection 全为 `representable = true`；同一依赖在全部活跃 Cell 上的 floor 一致。

| 依赖 | 原声明 | 最终 floor | 代表候选的直接前驱 | 前驱证据 | 活跃 Cells |
| --- | --- | --- | --- | --- | ---: |
| certifi | `>=2023.5.7` | **2022.5.18.1** | 无 | 本次空间最低候选，无更低拒绝证据 | 10 |
| charset-normalizer | `>=2,<4` | **1.3.9** | 1.2.0 | RUNTIME_INTERFACE_MISSING | 10 |
| idna | `>=2.5,<4` | **2.0** | 1.1 | VERIFIER_EXITED_NONZERO | 10 |
| PySocks | `>=1.5.6,!=1.5.7` | **1.7.1** | 1.6.8 | VERIFIER_EXITED_NONZERO | 10 |
| urllib3 | `>=1.26,<3` | **1.26.20** | 1.25.11 | VERIFIER_EXITED_NONZERO | 10 |
| chardet | `>=3.0.2,<8` | **2.2.1** | 2.1.1 | VERIFIER_EXITED_NONZERO | 5 |

报告产生的依赖投影为：

```text
certifi>=2022.5.18.1
charset_normalizer<4,>=1.3.9
idna<4,>=2.0
PySocks!=1.5.7,>=1.7.1
urllib3<3,>=1.26.20
chardet<8,>=2.2.1
```

这些在第一阶段完成时只是报告投影，随后作为第二阶段的中间声明写入目标工作树。certifi 到达搜索
范围底部，不证明更早版本不兼容；其他前驱指本次 minor 代表候选序列中的前驱，不表示相邻的每个
patch 都测过。

代表 Failure ID（Python 3.10 / socks；chardet 使用同 Python 的 chardet surface）：

| 依赖 | 前驱 Failure ID |
| --- | --- |
| charset-normalizer | `failure-d8c45e911bffb67d` |
| idna | `failure-d95c1c589be02dca` |
| PySocks | `failure-a8b4c16b3f093cb0` |
| urllib3 | `failure-d4678ac31411de4b` |
| chardet | `failure-cec0aca6218b8e4c` |

全部 Cell 的 final vector、baseline、边界、前驱 FailureRecord 及其 requested vector 保存于
[search-summary.json](data/E006/search-summary.json)，可核对每条边界的实际评价上下文。

### 5.1 终端的 1.24.3 不是 urllib3 floor

用户输出中的 `search completed at [urllib3=1.24.3][1.22~1.26.20#5]` 保留的是搜索过程位置。
本次报告中 `urllib3=1.24.3` 的 10 条 runtime Evaluation **全部为 VERIFIER_REJECTED**。
最终 Proposal、boundary 与 projection 一致指向 **1.26.20**，不能把终端过程值抄为下界。

类似地，终端完成卡片上的 `[baseline]` 标签不能用来还原 initial highest vector。报告中真正的
highest baseline 为 `certifi=2026.7.22`、`charset-normalizer=3.5.1`、`idna=3.19`、
`pysocks=1.7.1`、`urllib3=2.7.0`，chardet surface 另有 `chardet=7.6.0`。

### 5.2 运行规模与时间

报告包含 370 Attempts、370 Proposals、370 static evaluations、330 runtime evaluations、
222 个去重 resolution graphs、55 个 candidate snapshots。Runtime evaluations 分为
**130 PASS、160 VERIFIER_REJECTED、40 RUNTIME_INTERFACE_MISSING**；200 个 FailureRecords
全部是 Rejection，和 search Journal 的 200 entries 对应，没有 INDETERMINATE。
运行留下 3308 份 process logs。过程中的拒绝是搜索证据，不是失败 Cell 数。

| Python | socks | socks+use_chardet_on_py3 |
| --- | ---: | ---: |
| 3.10 | 17m58s | 24m11s |
| 3.11 | 17m57s | 24m00s |
| 3.12 | 17m33s | 23m37s |
| 3.13 | 17m29s | 23m32s |
| 3.14 | 17m16s | 23m53s |

时间来自用户终端输出。Chardet surface 本次约多 6 分钟，但没有配对成本实验，不能把差值全归因于
某个依赖、阶段或某项优化；本报告也不作为 D033 或 E005 性能方案的实施验收。

## 6. 第二阶段 refine：同一 minor 内定位 patch

修复后的 PF HEAD `4a9238d` 对中间声明执行 `minors[declaration]` × `patch`。当前
`experiments/requests/package-floor.json` 已由 `ReportStore.read` 完整验证：10 个 Cell 全部
`SUCCESS`、各完成 2 sweeps，六个 projection 全部可表示；同一依赖在全部活跃 Cell 上仍得到一致 floor。

| 依赖 | 第一阶段 minor floor | 第二阶段候选 | refine 后 floor | 第二阶段直接前驱 / 证据 | 活跃 Cells |
| --- | --- | --- | --- | --- | ---: |
| certifi | 2022.5.18.1 | `2022.5.18.1`（1） | **2022.5.18.1** | 无；本次空间唯一候选 | 10 |
| charset-normalizer | 1.3.9 | `1.3.0`–`1.3.9`（10） | **1.3.1** | 1.3.0 / RUNTIME_INTERFACE_MISSING | 10 |
| idna | 2.0 | `2.0`（1） | **2.0** | 无；本次空间唯一候选 | 10 |
| PySocks | 1.7.1 | `1.7.0`、`1.7.1`（2） | **1.7.0** | 无；本次空间最低候选 | 10 |
| urllib3 | 1.26.20 | `1.26.0`–`1.26.20`（21） | **1.26.5** | 1.26.4 / VERIFIER_EXITED_NONZERO | 10 |
| chardet | 2.2.1 | `2.2.1`（1） | **2.2.1** | 无；本次空间唯一候选 | 5 |

第二阶段报告产生的依赖投影为：

```text
certifi>=2022.5.18.1
charset_normalizer<4,>=1.3.1
idna<4,>=2.0
PySocks!=1.5.7,>=1.7.0
urllib3<3,>=1.26.5
chardet<8,>=2.2.1
```

这是第二阶段新 snapshot 与完整 runtime evidence 下的结果，不是把两个报告的边界引用拼成一份报告。
第一阶段负责选择 minor，第二阶段只在该 minor 内 refine；最终仍是所列策略和 configured verifier 下的
coordinate-minimal passing vector，不证明未执行的组合或选定 minor 之外的版本。

### 6.1 两条 patch 级拒绝边界

`charset-normalizer=1.3.0` 在 10 个 Cell 的最终 context 中都由 runtime witness 确认为缺少
`charset_normalizer.__version__`；`1.3.1` 则有直接完整 PASS。因此完成卡片中的
`search completed at [charset-normalizer=1.3.0][1.3.0~1.3.1#2]` 展示的是最后验证的 predecessor
与两候选窗口，floor 是 **1.3.1**。Python 3.10 / socks 的代表 Failure ID 为
`failure-b3136bd059fbd3e7`。

`urllib3=1.26.4` 在 10 个 Cell 的最终 context 中都被 configured pytest 拒绝，`1.26.5` 直接完整
通过。代表失败 `failure-2a4e7afbebbdfe06` 落在
`tests/test_requests.py::TestRequests::test_https_warnings`：实际 warning categories 比预期的
`SubjectAltNameWarning` 多出 `DeprecationWarning`，pytest 正常退出 1。这是测试 oracle 建立的行为边界，
不是 resolver、安装或基础设施失败。

### 6.2 运行规模与时间

| 指标 | 第一阶段 minor | 第二阶段 patch refine |
| --- | ---: | ---: |
| Attempts / Proposals | 370 / 370 | 155 / 155 |
| Runtime evaluations | 330 | 145 |
| PASS | 130 | 95 |
| Rejection | 200 | 50 |
| Resolution graphs | 222 | 93 |
| Candidate snapshots | 55 | 55 |
| Process logs | 3308 | 1242 |
| 最长 Cell | 24m11s | 14m37s |

第二阶段 50 个 Rejection 由 20 个 `RUNTIME_INTERFACE_MISSING` 与 30 个
`VERIFIER_EXITED_NONZERO` 组成，Journal 也恰有 50 entries，没有 INDETERMINATE。第一、第二阶段的
源码 snapshot、搜索空间和 PF 实现不同，且这里只捕获了每 Cell elapsed；这些数值说明本次 refine
工作量较小，但不能单独归因为 predecessor 重验或推导命令墙钟加速比例。

### 6.3 本次 dogfood 的有价值发现

1. 两次平面搜索可以组成实用的 coarse-to-fine 工作流：第一阶段用
   `majors[declaration-1:]` × minor 定位系列，第二阶段用 `minors[declaration]` × patch 在该系列内
   收紧精确版本。当前 PF 不自动执行这种层级搜索；中间 projection、配置变化和第二份报告都是显式步骤。
2. Requests 的真实多坐标 refine 现场暴露了 [D033](../archived/designs/D033-pf-predecessor-revalidate.md)
   纳入修复的窄空间缺口：正在下降的 coordinate 属于
   `C[d]`，其他尚未下降 coordinate 的 highest baseline 可能位于各自 `C[d]` 之外，exact probe 仍需其
   冻结 artifact。`4a9238d` 引入 baseline selection 后，本次 10-Cell 搜索完整结束，为该修复提供了
   第三方仓库端到端证据。
3. patch refine 把宽泛的系列代表转成了可解释的接口/行为边界：Requests 在
   `charset-normalizer=1.3.0` 缺少实际导入的 `__version__`，从 `1.3.1` 起通过；其完整 pytest 在
   `urllib3=1.26.4` 的 warning 精确断言失败，从 `1.26.5` 起通过；PySocks 的同 minor 最低候选
   `1.7.0` 也通过全部 10 个 Cell。
4. 这些结果同时说明“测试可通过的 floor”和“上游声明的支持范围”不是同一件事。refine vector 低于
   requests 原始 metadata 的多条下界，代表 urllib3 predecessor 的运行日志还出现了
   `RequestsDependencyWarning`。E006 只记录当前源码、矩阵和 pytest oracle 下的兼容证据，不据此建议
   requests 上游放宽依赖声明；若 support policy 也要成为门禁，需要在验证契约中明确表达。

## 7. 固定证据与复核

终端文本去除了行尾布局填充空格，其余内容保留。

- [smoke.txt](data/E006/smoke.txt)：首次重跑终端输出，保留一次失败。
- [check.txt](data/E006/check.txt)：check 重跑终端输出。
- [smoke-repeat.txt](data/E006/smoke-repeat.txt)：相同配置完整复测输出。
- [rerun.json](data/E006/rerun.json)：上述三次进程的 UTC 起止时间和实际退出码。
- [rerun-evidence.json](data/E006/rerun-evidence.json)：Journal 身份、失败事实与 pytest 日志摘要。
- [search-summary.json](data/E006/search-summary.json)：从第一阶段原始报告提取的身份、计数、投影与逐 Cell 证据。
- [refining-search/search.txt](data/E006/refining-search/search.txt)：第二阶段用户终端输出，去除边框与布局填充。
- [refining-search/search-summary.json](data/E006/refining-search/search-summary.json)：第二阶段 report 身份、策略、候选 inventory、计数、投影、边界 Failure IDs 与 Cell 耗时摘要。
- [refining-search/diagnostics.txt](data/E006/refining-search/diagnostics.txt)：两个代表 predecessor 的 `pf diagnose` 与相关 process-log 语义摘录。

原始运行目录为 `experiments/requests/.pf/logs/<run-id>/`。这些目录及原始 `package-floor.json`
是本地运行产物，后续可能清理或覆盖；本文随仓库保存上述摘要，摘要不替代完整报告的离线授权。
第一阶段原始 report 已被第二阶段输出覆盖，其冻结摘要记录 SHA-256
`040e20a1fa9681502303fae3ee6fdb2f18fcdcf8614f590ce5984d755811a40d`。当前第二阶段原始 report 的
SHA-256 为：

```text
236f8cb19cb2448031c686b00ce9cf80a10d2760b6d019e6736284d9d52efd30
```

本次执行的完整 reader 复核命令（PF 仓库根目录；只读，无 registry 查询）：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from pf.report import ReportStore
r = ReportStore().read(Path("experiments/requests/package-floor.json"))
print(r.report_generation_id, r.result.status, len(r.cell_results))
PY
```

第二阶段结果为 `0aff1d79fe9851ce263b4a9bc8ef2fd93ad46810ad723309e28d9b305da6dc85`、
`complete`、`10`。第一阶段 reader 复核结果已冻结在原 [search-summary.json](data/E006/search-summary.json)。
另核对两份摘要、第二阶段全部 final roots、六个投影与逐 Cell 边界一致、runtime/FailureRecord 计数、
两条代表诊断及三次 smoke/check 重跑日志；文档相对链接与 `git diff --check` 通过。

两阶段实验只证明所列 Linux target、Python minors、两个 required surfaces、各自源码快照、验证契约及
冻结候选策略上的结果；不外推其他平台、未探测 patch、依赖任意组合或完整区间的普遍兼容性。

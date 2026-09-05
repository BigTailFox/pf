# E006 — requests 最新 smoke / check / search 实验

- **状态：** 已完成
- **日期：** 2026-09-05（Asia/Shanghai）
- **性质：** 非规范性 dogfood 实验报告；记录运行事实，不定义产品契约
- **历史对照：** [E003](E003-requests-dependency-validation.md)、[E004](E004-requests-validation-surfaces.md)
- **目标：** `experiments/requests`，commit `dae7ef63b4df6eded86637f251fc4e3a06c3b479`
- **PF：** `0.1.0`；本次 smoke/check 重跑时 HEAD 为 `40b34cd6054b24e33905c78b7f69c923b41904e8`
- **实现口径：** 生产代码与 `325e43d` 相同；后续提交为文档及独立模拟脚本。search 报告自身只记录 generator 版本，不记录 PF Git commit
- **Source snapshot：** `75b5d4f7dd959f3f54a5fea2bb46aa8937d81fcdcd4d3f496040ce91bb6329b3`
- **Evaluation policy：** `aa654eb96a8614885202f36d8e4158e077ecfc7ed2e1f5b3dda2485760b851d0`
- **Search generation：** `8611ccc7f3da74ae11fef4544367e856ea363c4c3258b0f28775e73c0c20b282`

最新完整 smoke 复测 **10/10 PASS**；当前声明下界 check **10/10 REJECTED**；用户完成的 search
为 **10/10 SUCCESS、报告 complete、六个依赖投影全部可表示**。首次 smoke 重跑出现一次本地 HTTP
连接重置，完整复测未复现，两次结果均保留。不能把 search 成功解释为当前声明下界已经通过。

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

搜索使用报告冻结的 `registry-series-slice-v1` 策略：`search-step = "minor"`、不含 prerelease、
`requested_space = null`，条件默认如下：

```toml
with-lower-bound = "majors[declaration-1:]"
without-lower-bound = "majors[baseline-2:]"
```

六个依赖均有声明下界，使用第一项。每个 minor 系列取最高合格精确 release 作为代表。
因此本次 floor 是**冻结范围及代表候选上的结果**，不是全发布历史逐 patch 穷举的绝对最低版本。

本文按 `smoke → check → search` 的工作流解释结果，但实际运行时序为：用户先完成 21:47 开始的
search，再要求补跑 smoke/check；本次在 22:18、22:19 执行 smoke/check，22:20 再完整复测 smoke。
各次 Journal 与 search report 的 snapshot、policy 均相同；search 使用自己取得的 baseline，
不引用后来 smoke 的 PASS。E003/E004 的旧 policy、旧矩阵不混入本轮证据。

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
search 的命令为用户提供的 `pf search`，本次整理未重新执行 search。

## 2. 运行总表

| 阶段 | run-id | PF 退出码 | 结果 | 耗时口径 |
| --- | --- | --- | --- | --- |
| smoke 首次重跑 | `20260905T141808.653261Z-783898-a1b8d058` | 1 | 9 PASS、1 REJECTED | 实测墙钟 86.53s |
| check 重跑 | `20260905T141935.195322Z-788444-6d7e586c` | 1 | 10 REJECTED | 实测墙钟 25.03s |
| smoke 完整复测 | `20260905T142054.067994Z-793128-eb5cfb77` | 0 | 10 PASS | 实测墙钟 85.42s |
| 用户 search | `20260905T134722.724441Z-686839-efa6c438` | 未独立捕获 shell 退出码 | 10 SUCCESS；`result.status = complete` | 终端最长 Cell 24m11s |

search 的 `Search complete` 与机器报告相符；按当前命令契约对应成功，但不把推定的退出码冒充
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

check 验证的是当前声明生成的下界组合；search 可以抬高不通过的坐标、降低其他坐标并取得 PASS。
两者结果因此并不矛盾。本轮未执行 apply，也未改动 requests 的依赖声明。

## 5. search：最终 floor 与拒绝边界

`ReportStore.read` 已完整验证原始 `experiments/requests/package-floor.json`。
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

这些是报告投影，不是已应用到源码的修改。certifi 到达搜索范围底部，不证明更早版本不兼容；
其他前驱指本次 minor 代表候选序列中的前驱，不表示相邻的每个 patch 都测过。

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

## 6. 固定证据与复核

终端文本去除了行尾布局填充空格，其余内容保留。

- [smoke.txt](data/E006/smoke.txt)：首次重跑终端输出，保留一次失败。
- [check.txt](data/E006/check.txt)：check 重跑终端输出。
- [smoke-repeat.txt](data/E006/smoke-repeat.txt)：相同配置完整复测输出。
- [rerun.json](data/E006/rerun.json)：上述三次进程的 UTC 起止时间和实际退出码。
- [rerun-evidence.json](data/E006/rerun-evidence.json)：Journal 身份、失败事实与 pytest 日志摘要。
- [search-summary.json](data/E006/search-summary.json)：从用户此次原始报告提取的身份、计数、投影与逐 Cell 证据。

原始运行目录为 `experiments/requests/.pf/logs/<run-id>/`。这些目录及原始 `package-floor.json`
是本地运行产物，后续可能清理或覆盖；本文随仓库保存上述摘要，摘要不替代完整报告的离线授权。
本次原始 report 的 SHA-256 为：

```text
040e20a1fa9681502303fae3ee6fdb2f18fcdcf8614f590ce5984d755811a40d
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

结果为上述 generation、`complete`、`10`。另核对全部 baseline/final 的 PASS、六个投影与逐 Cell
边界一致、runtime/FailureRecord 计数及三次重跑日志；文档相对链接与 `git diff --check` 通过。

本次只证明所列 Linux target、Python minors、两个 required surfaces、源码快照、验证契约及冻结
候选策略上的结果；不外推其他平台、未测 patch、依赖任意组合或完整区间的普遍兼容性。

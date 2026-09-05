# E003 — requests 依赖验证与搜索实验

- **状态：** 已完成
- **日期：** 2026-09-05
- **性质：** 非规范性 dogfood 实验报告；不定义命令、算法、Schema 或 module interface
- **目标仓库：** [`expirements/requests`](../../expirements/requests)（psf/requests `v2.34.2-28-gdae7ef63`，commit `dae7ef63b4df6eded86637f251fc4e3a06c3b479`）
- **PF 版本：** `0.1.0`；工作树 `684df373070fab081e8db195d434c722c731cc0b`
- **宿主：** `x86_64-unknown-linux-gnu`；本机可用 CPython `3.10`–`3.14`
- **源码快照：** `75b5d4f7dd959f3f54a5fea2bb46aa8937d81fcdcd4d3f496040ce91bb6329b3`
- **评价策略：** `configured-verifier-terminal-v1`；policy identity `3cc886658c6e4df455f0333142e88cf02aabbc5736065888b08d937137b6d805`
- **验证契约：** full-repository contract；`test-command = ["pytest"]`；`resolve-artifact = "any"`
- **搜索产物：** [`expirements/requests/package-floor.json`](../../expirements/requests/package-floor.json)
- **报告实体：** generation `07053f8be671cf542d256b956f16dd3cc0a007d1b7f72413cb47afbce04ca25d`
- **契约所有者：** [D001](../designs/D001-pf.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、[D012](../designs/D012-pf-harness-relaxation.md)、[D014](../designs/D014-pf-report-schema.md)

本文记录在隔离的 requests 工作树中依次执行 `pf smoke`、`pf check`、`pf search` 的结果。三次命令都选出 20 个 Cell、6 个受管直接依赖，并在 baseline 的 `resolve-environment` 阶段以同一 `TOOL_FAILURE` / `resolution-plan-invalid` 终止。没有 Cell 进入 ty、configured verifier 或 floor 搜索。因此本次实验**没有**得到 requests 依赖的经过验证的 floor。

## 1. 问题

requests 是 PF 仓库外的第一个真实第三方 dogfood 目标。实验要回答：

1. 当前 PF 能否在该仓库的声明与 `test` group 上完成 highest fresh install（`smoke`）；
2. 当前声明下界能否通过同一验证契约（`check`）；
3. 受管直接依赖的 verified floor 是什么（`search`）。

按 E001 §7 的记录口径，本次 validation contract 是 requests 仓库完整 `pytest` suite，包含 HTTP/httpbin、证书、socks extra 与本地 testserver。它不是 targeted-runtime contract。未执行的 contract 不推断 floor。

## 2. 目标与配置

`expirements/requests/pyproject.toml` 在实验时包含：

```toml
[tool.pf]
test-command = ["pytest"]
resolve-artifact = "any"
```

其余 `[tool.pf]` 字段使用默认值：`extra-policy = "each"`、`search-space = "all"`、`search-step = "minor"`、`test-group = "test"`。`requires-python = ">=3.10"`，本机推断出 CPython `3.10`–`3.14`。

受管直接依赖（6 个，0 pinned）：

| 位置 | 声明 | extra |
| --- | --- | --- |
| base | `charset_normalizer>=2,<4` | — |
| base | `idna>=2.5,<4` | — |
| base | `urllib3>=1.26,<3` | — |
| base | `certifi>=2023.5.7` | — |
| optional | `PySocks>=1.5.6, !=1.5.7` | `socks` |
| optional | `chardet>=3.0.2,<8` | `use_chardet_on_py3` |

`security` extra 为空数组，仍形成独立 extra surface。Cell 矩阵是 5 Python minor × 4 extra surface = 20。

`test` group 为：

```toml
test = [
    "requests[socks]",
    "pytest-httpbin==2.1.0",
    "httpbin~=0.10.0",
    "pytest-cov",
    "pytest-mock",
    "pytest-xdist",
    "pytest>=3",
    "trustme",
]
```

项目自身使用 `dynamic = ["version"]`，静态版本写在 `src/requests/__version__.py`（`2.34.2`），不在 `project.version`。

运行方式：在 `expirements/requests` 下执行 `uv run --project /home/llh/pf pf <command>`，使 cwd 成为 PF 的 project root。

## 3. 三次命令结果

三次运行共享同一 source snapshot。uv `pip compile` 均在约 20ms 内以 exit 0 完成；PF 随后无法把 environment pylock 投影为 ResolutionPlan。

| 命令 | 运行 ID | 角色 | 退出码 | 终态 | 墙钟 |
| --- | --- | ---: | ---: | --- | ---: |
| `pf smoke` | `20260905T044714.798844Z-18798-7b99814e` | `baseline` | 4 | 20/20 `INDETERMINATE` | ~14s |
| `pf check` | `20260905T044853.106859Z-22019-47a4dfdc` | `declaration-capture` | 4 | 20/20 `INDETERMINATE` | ~14s |
| `pf search` | `20260905T044919.626956Z-23542-3846882a` | `baseline` | 4 | 20/20 `BASELINE_INDETERMINATE`；写出 incomplete report | ~14s |

每个 Cell 的 FailureRecord 形状相同：

| 字段 | 值 |
| --- | --- |
| disposition | `INDETERMINATE` |
| cause | `TOOL_FAILURE` |
| stage | `resolve-environment` |
| summary_code | `resolution-plan-invalid` |
| process exit | 0（stdout/stderr 完整，未超时） |
| environment_plan_digest | `null` |
| project_plan_digest | 非空（project resolve 已成功） |

代表诊断（smoke / py3.10 / no-extra）：

```text
pf diagnose failure-4c6d75a95e9f59fe --package requests
```

诊断摘要：compatibility unknown；impact 为 highest-version resolution 未知；建议检查 named tool 能否在本环境运行。Process Log 为 `.pf/logs/20260905T044714.798844Z-18798-7b99814e/process-0025.log`，stderr 仅 `Resolved 36 packages in 4ms`。

`pf search` 写出的报告：

```text
schema_version = 1
status = incomplete
reasons = INDETERMINATE, UNREPRESENTABLE_PROJECTION
cell_results = 20 × BASELINE_INDETERMINATE
evaluations = 0
projections = 6 × representable=false, floors=[]
```

没有 floor、predecessor、FailureRecord 以外的 runtime Rejection，也没有 apply 授权。

## 4. 因果链

Journal 只权威记录 `resolution-plan-invalid`：uv 已给出 exit 0 的 environment lock，PF 无法把它投影成证据。下面把该 summary_code 接到当前实现，区分为 journal 事实与代码路径归因。

### 4.1 Journal 与 lock 事实

1. project resolve 成功。每个 FailureRecord 都带 `project_plan_digest`，失败发生在随后的 environment resolve。
2. environment `uv pip compile --format pylock.toml --resolution highest` 成功。py3.10 / no-extra 的 lock 含 36 个包，包括 path 源 `requests`：

   ```toml
   [[packages]]
   name = "requests"
   directory = { path = "source", editable = true }
   ```

   该条目没有 `version`。离线 `parse_uv_pylock`（不传 source root）得到 `requests.version is None`，其余 35 个 registry 包都有版本。
3. SourcePlan 把 `requests` 列为 registry 路由。这不是 base 依赖，而是 `test` group 的 `requests[socks]` 被收成 harness 声明后登记的。
4. 八个 harness declaration id 与 `test` group 的八条声明一一对应。

### 4.2 代码路径归因

`UvAdapter._resolve` 在 compile 成功后：

1. 从 pylock 去掉“当前 package 的 path 源”条目；
2. 若仍有 `version is None` 的包，抛出 `ValueError("resolution plan omitted a package version")`；
3. 对 environment plan 调用 `_direct_harness`：每个 harness 名必须在剩余包中且带版本，否则 `ValueError("resolved harness package is missing: {name}")`；
4. 上述 `ValueError` / `UvLockError` 一律变成 `ResolutionIndeterminate(cause=TOOL_FAILURE, summary_code=resolution-plan-invalid)`。

`dynamic = ["version"]` 不是这次失败的原因。它只表示 requests 自己的发行版本不写在 `project.version`；PF 搜索的是它的直接依赖，不搜索被测包自身的 floor。project resolve 已经成功并留下 `project_plan_digest`，说明 path 源 `requests` 被正确滤掉后，其余依赖都有版本，依赖图投影成立。

environment resolve 比 project resolve 多一步 `_direct_harness`。`test` group 含 `requests[socks]`，被收成名为 `requests` 的 harness 声明。过滤掉项目 path 源后，harness 名 `requests` 不在剩余包中，`_direct_harness` 抛出 `ValueError("resolved harness package is missing: requests")`。

因此 environment plan 无法成为 PF 证据。失败发生在 harness 自引用的 lock 投影，不在 uv 解析、安装、ty 或 pytest，也不在被测包的动态版本声明。`pf diagnose` 只展示 process exit 0 与 `TOOL_FAILURE`，不展示被吞掉的 `ValueError` 文本。

### 4.3 为何三次命令同一终态

`smoke`、`check` 与 `search` 都要先为每个 Cell 准备 baseline environment。`check` 的 journal 角色是 `declaration-capture`，但本次 requested resolution 仍是 `highest`，并在同一投影步骤失败。没有任何命令越过 environment resolve，因此也没有任何命令开始搜索空间。

## 5. 结论

### 5.1 本次没有 verified floor

在当前 PF `0.1.0` 与该 requests 快照上：

- 不能声称 highest fresh install 通过；
- 不能声称已声明下界通过；
- 不能给出 `charset-normalizer`、`idna`、`urllib3`、`certifi`、`PySocks` 或 `chardet` 的 floor。

incomplete report 只证明：20 个 Cell 的 baseline environment 投影失败，projection 不可表示。

### 5.2 PF 在进入 verifier 之前就 fail-closed

行为符合 D005：无法可靠完成工具操作时记 `INDETERMINATE`，不发明 PASS/REJECTED 边界。`search` 按 D001/D014 写出 incomplete `package-floor.json` 并退出 4，也符合“无 PASS anchor 则终止 Cell”。

本案例暴露的是 environment-plan 投影对一种真实仓库形状不完整：test group 自引用被测 distribution（`requests[socks]`）。被测包的 `dynamic = ["version"]` 不在这条因果链上。

这是 dogfood 缺口，不是 requests 测试失败，也不是声明下界已被证伪。

### 5.3 终端诊断偏弱

`pf diagnose` 把 `resolution-plan-invalid` 说成“verification tool operation”不可靠。实际 named tool（uv）成功；失败在 PF 自己的 pylock 投影。后续若要降低误导，应在 FailureRecord 中保留被吞掉的 `ValueError` 文本或更具体的 summary_code。本实验不授权该改动。

## 6. 非结论

本实验不证明：

- requests 的当前声明下界或 highest 解析不能通过其 pytest suite；环境从未安装，测试从未运行；
- `charset-normalizer` / `idna` / `urllib3` / `certifi` / `PySocks` / `chardet` 的兼容范围；
- `security` extra 或 Python 3.13/3.14 与 requests 不兼容；这些 Cell 只是同一投影失败的重复；
- 应删除 `test` group 中的 `requests[socks]`；那是目标仓库的既有形状，PF 若要支持此类项目，应在 harness 投影层处理自引用；
- 动态版本声明挡住了依赖搜索；project resolve 已证明它没有；
- PF 核心搜索算法、ty 基线或 configured verifier authority 需要因本案例修改。

## 7. 后续实验记录要求

若在修复投影之后重跑本仓库，至少记录：

- 同一 snapshot / policy / Cell 矩阵是否仍被选中；
- `requests[socks]` 是否被吸收为 required extras，Cell 矩阵是否变为 Python × 3 个可执行 surface，且不再进入 `_direct_harness`；
- 每个命令是否越过 resolve-environment，进入 ty 与 configured `pytest`；
- 若得到 floor：每个受管依赖的 floor、predecessor、Failure ID，以及 contract 仍是 full-repository `pytest`。

在投影修复之前重跑这三条命令，预期仍是同一 `resolution-plan-invalid`，不增加新的兼容性证据。

本案例作为第三方 dogfood 的 environment-plan 投影边界证据保留。产品判断与推荐投影规则由
[R009](../archived/reviews/R009-requests-harness-self-reference.md) 接收；R009 不授权实施，也不把本文的
incomplete 终态改写成 floor。

# P047 — D042 宿主能力局部化实施计划

- **状态：** 已完成
- **日期：** 2026-09-10
- **对应 Design：** [D042](../designs/D042-pf-host-capability-locality.md)
- **目标 owner：** [D002](../../designs/D002-pf-implementation.md) §1、§5、§8、§11；[D007](../../designs/D007-pf-process-output.md) §4–§5、§8；[tests/README.md](../../../tests/README.md)；[CONTEXT.md](../../../CONTEXT.md)
- **起点：** `9f8e4cba7d2ebbda9ea874f4b64bf243b0b01385`
- **流程与测试：** [AGENTS.md](../../../AGENTS.md)、[测试说明](../../../tests/README.md)

本 Plan 记录切片、决定与验收证据；目标行为只由 D042 规定。不另立契约。未开切片时不改 `src/` 或 `tests/`。S1 只改文档。

## 1. 切片与验收映射

依赖：**S1 → S2 → S3 → S4**。不可颠倒。S1 未完成不得改生产探测或静态配置。S2 的 fail-closed 与 Resolver 搬家未落地不得开 S3 测试迁移；S1–S3 未逐项取得证据不得开 S4 吸收审计与归档。

| 顺序 | 接口、实现、文档与测试工作 | D042 AC | 完成标准 | 证据槽 |
| --- | --- | --- | --- | --- |
| S1 | 按 D042 §2 分家吸收：D002 §1/§5/§8/§11、D007 链接、CONTEXT **Host**、`tests/README.md` 独占种类 / 覆盖率并集 / Host/Cell 展开。D002 保留 `## 5. Application boundary` 锚点，新增 §5.1/§5.2，既有 composition 正文归入 §5.3；删除「每 invocation 最多一次」且不建立替代计数规则。D002 §11 仅迁出种类表与覆盖率算法，其余 seam / 真实性规则留存并链接测试 owner | AC1–AC6、AC9、AC10 | owner 正文与 D042 §2 一致；D002 §5 既有 application boundary 规则未丢失，只记录合法调用位置而不规定次数；D002 无种类定义、无覆盖率并集算法；`tests/README.md` 不再把种类指回 D002；D001/D006/D008 产品句未改写；R010 仍开放 | E1 |
| S2 | `host_target()` 按 D042 §3 先校验 OS、再校验 machine、仅 Linux 读取 libc并 fail-closed；补 §3.1 测试矩阵；`os.name` 从 `static_request.py` 移入 `TyConfigurationResolver`（产品省略 `platform=`，参数可显式传入）；删除 `ProjectLoader._host_target`；按 §2.2 完成全量命中对照 | AC7、AC11 | 未知 OS 时不读 machine/libc；未知 machine/Linux libc 升 `ConfigurationError`；darwin/win32 不读取 libc；`static_request.py` 无 `os.name`；ty 配置根命中只在 `static_configuration.py`；§2.2 每个最终命中都有 owner/豁免结论；无 `pf.platform` / HostFacts | E2 |
| S3 | 为 marker、projection、apply、admission 各记录跨三类 Cell family 的代表测试 node，并审计本机 OS 改道。`tests/test_windows_runlog.py` 标 `infra`；把 `TestRunLogStoreProcessOutput` 从 `tests/test_process.py` 迁入既有 `tests/test_runlog.py`，以手工 `ProcessObservation` 代替仅为造结果而执行的生产 `SubprocessRunner.run`；Windows guard 用例直接经 `pf.runlog.secure_log_directory` 注入测试内假 adapter，不进口 `test_secure_runlog` / `_secure_runlog`。`test_process.py` 保留模块级 `process`，真实 runner/redact/停止/超时/PATH 用例留在该文件 | AC8 | 四个 Cell seam 均有三类 family 代表 node；扫描中的 Host 改道逐项归类；产品测试（含 `process`）不直接或经 helper 进口 `_secure_runlog`；RunLogStore portable 契约在未标记收集且不进入生产 runner；真实 runner 测试仍只在 `process` 收集 | E3 |
| S4 | 审计 AC1–AC11 与 owner 吸收完整性，回填 §5；运行 PR 回归、文档与生成投影检查；将 D042/P047 状态改为完成态并同变更移入归档，更新现行/归档索引及全部入链 | AC1–AC11 | 每个 AC 有「命令 → 结果」；D042 稳定规则已完整进入目标 owner；`docs/README.md` §7 删除 D042/P047 开放行，§3/§8 历史入口与 `docs/archived/README.md` 纳入 D042/P047；现行目录不留跳转页；归档后链接和生成检查通过 | E4 |

## 2. 接口与所有权迁移

| 现行 | 目标 | 吸收后 owner |
| --- | --- | --- |
| D002 §5「`host_target()` 每 invocation 最多一次」 | 删除且不建立替代计数规则。CLI composition 可调用并向 Runner 注入；Loader 省略 `platforms` 时可调用 | D002 §5 |
| `host_target()` 未知 machine 透传、非 musl 默认为 `gnu` | 只返回 D042 §3 表内 exact triple；不确定输入 `ConfigurationError` | D002 §5 |
| `static_request.py` 读 `os.name` 再传 `platform=` | 产品装配省略 `platform=`；Resolver 内部读 `os.name`；owner 测试仍可显式传入 | 静态 module implementation |
| `ProjectLoader._host_target` | 删除；Loader 直接调模块级 `host_target()` | — |
| D002 §11 种类 / 车道列表与覆盖率并集 | 只迁出现行种类列表、PR/自举收集关系与覆盖率并集算法，并与 `tests/README.md` 现行正文去重合并 | `tests/README.md` |
| D002 §11 公开 seam、Search/static 账本验证、不进口 `_secure_runlog`、未授权车道不进入生产 `SubprocessRunner.run` | 全部留在 D002，加测试 owner 链接；直接驱动 `_secure_runlog` / `windows_runlog` 协议标 `infra` | D002 §11 |
| D007 结构禁令若新写一份 | 只链接 D002；保留现行 POSIX/Windows 行为句 | D007 行为；D002 结构 |
| CONTEXT 无 Host | 词条 + `_Avoid_`，无行为 | CONTEXT |

### 2.1 S1 写入位置

| 文件 | 做 |
| --- | --- |
| D002 §1 | 在「不建立 `utils.py`、通用 filesystem/repository…」上补：受支持 Host 上 OS 差异留在能力 owner 内部；禁止通用平台独立层 / `pf.platform` / HostFacts；内部 seam 必要条件为同一能力两个真实 adapter；Darwin 默认随 POSIX。不复制测试展开表 |
| D002 §5 | 保留 `## 5. Application boundary` 标题/锚点；依次建立 `### 5.1 Host OS 读取`、`### 5.2 Cell target 投影`、`### 5.3 Composition 与 invocation lifecycle`。§5.3 承接现有 §5 全部正文，删除「每 invocation 最多一次」，只写两个合法调用位置与 Runner 注入关系 |
| D002 §8 | 一句：进程组停止与 PATH `which` 是 `SubprocessRunner` 内部差异，不升为 public ProcessPlatform。不复制 D007 正文 |
| D002 §11 | 只删除种类 / 车道定义列表、PR/自举收集关系与覆盖率并集句。保留公开 seam、Search/static 账本与真实性规则（含不进口 `_secure_runlog`、未授权车道不得进入生产 `run`）。种类与覆盖率并集指向 `tests/README.md` |
| D007 §8 | 加链接：无 public `ProcessPlatform`；跨能力平台层见 D002。不复制 D042 §1/§5 |
| `CONTEXT.md` | **Host** 紧挨 **Cell**：运行本次 PF invocation 的本机。它不是 Cell。`_Avoid_: Cell, platform, Environment` |
| `tests/README.md` | 独占种类表；删除「种类 / 消费者见 D002 §11」。保留「Adapter 真实性见 D002 §11」。增加 Host/Cell 展开（D042 §6） |

### 2.2 Host OS 命中基线与交付对照

审查时 `src/pf` 中 `os.name` / `sys.platform` / `platform.machine()` / `platform.libc_ver()` 的产品命中如下。S2 完成时在最后一列逐行写实际路径与结论；最终 `rg` 每个命中必须落在表中，且还要人工核对相关 import、调用方与公开 seam，不能只保存命令计数。

| 路径 | 基线 | S2 目标 | S2 实际结果 |
| --- | --- | --- | --- |
| `project.py` `host_target()` | Linux 非 musl 默认为 `gnu`；machine 未校验 | 按 D042 §3 的顺序与 fail-closed 规则实现 | `src/pf/project.py` `host_target()`：先校验 `sys.platform`，未知 OS 立即 `ConfigurationError`；再规范化 machine；仅 Linux 读 `libc_ver`。表内 8 支 + `linux2` + 未知 OS/machine/libc 由 `tests/test_project.py` 覆盖 |
| `static_request.py:215` | `os.name == "nt"` 再传 `platform=` | 删除；装配省略 `platform=` | 已删除。`StaticRequestFactory._assemble` 调用 `TyConfigurationResolver.resolve` 时不传 `platform=`；`tests/test_static_request.py::TestStaticRequestAssembly::test_product_assembly_omits_platform_argument` 证明 |
| `static_configuration.py` | 已 `import os`（`fstat`）；`platform` 现为必填 | 省略时读 `os.name`；显式 owner-test 参数保留 | `platform=` 可省略。省略时经 `_host_os_name()` 读 `os.name`（避免 owner 测试污染 stdlib `os` / pathlib）。显式 `platform=` 用例保留；`test_omitted_platform_selects_host_user_config_root` 覆盖 posix/nt |
| `adapters/process.py` | 进程组 / 停止 / PATH | 留在 §5.1 | 仍仅 `os.name` 于 `start_new_session`、停止路径、Windows PATH `which`。公开表面仍是 `ProcessRunner.run` |
| `_secure_runlog.py` / `windows_runlog.py` | POSIX vs Windows 目录 | 留在 §5.1 | 仍仅 adapter 内 `os.name`。产品测试不进口 `_secure_runlog` |
| `snapshot.py` `cleanup_temporary_directory` | Windows 重试 | 留在 §5.1 | 仍仅 `cleanup_temporary_directory` 内 `os.name == "nt"` |
| `environment.py` `_interpreter` | `Scripts` vs `bin` | 留在 §5.1 | 仍仅 `_interpreter` 内 `os.name == "nt"` |
| `adapters/uv.py` / `adapters/static_inputs.py` | probe 脚本字符串含 `platform`/`sys`/`os` | 豁免（目标侧 ABI） | 仍只在发给 prepared venv 的 probe 字符串中；不是 Host 身份读取 |
| `markers.py` contextual marker | `packaging` 为五字段外变量补 context/Host default，无直接身份读取 | D001 既有 contextual 语义；不生成 host target，不从 AC7 删除 | 无 `os.name` / `sys.platform` / `platform.machine` / `libc_ver` 直接读取 |
| `cli.py` `configure_utf8_stdio()` | `os.environ`，不读 `os.name` | 不进入 §5.1 | 仍只写 UTF-8 环境，不读 `os.name` / `sys.platform` |

D042 §5.1 白名单外的产品编排（含 `static_request.py`）在 S2 后不得再有 Host OS 身份命中。

## 3. 决策（实施前锁定）

1. **唯一目标契约。** 实施期间只实现 D042。禁止兼容层、`pf.platform`、HostFacts、假 Host。
2. **种类独占。** `tests/README.md` 是种类、覆盖率并集与 Host/Cell 展开的唯一正文。D002 §11 不得保留平行种类列表。
3. **真实停止只属 `process`。** 进入生产 `SubprocessRunner.run` / 停止路径的测试不得标未标记。安全目录与 `close()` 的 portable 契约走未标记，且不得调用生产 `SubprocessRunner.run`。
4. **两个合法的 `host_target` 调用位置。** CLI composition 可探测并向 Runner 注入；Loader 省略 `platforms` 时可探测。不改 `ProjectLoader.load` 签名，不规定调用次数，也不把探测扩成服务定位器。OS 先于 machine：未知 OS 仍报 `unsupported host platform`（与现行 `emscripten` 测试一致），且测试证明 machine/libc 未读取；已知 OS 但 machine/Linux libc 不在表内另报 `ConfigurationError`，不得把坏字符串写进 triple。
5. **libc。** 只在 `startswith("linux")` 上读 `platform.libc_ver()[0]`；只在识别为 `musl` 或 `gnu`/`glibc` 时发出对应 triple；空串与其它名字 fail-closed。darwin/win32 不读 libc。本 Plan 不发明第二套 musl 探测。
6. **machine。** 别名 `amd64`→`x86_64`、`arm64`→`aarch64`；规范化后仅 `x86_64` / `aarch64`。
7. **UTF-8。** 不改 `configure_utf8_stdio()`；不把它写入 §5.1；不把入口强制写成已关闭 R010。
8. **Resolver 参数。** `platform=` 可省略，不删除。`tests/test_static_configuration.py` 现行显式 `platform=` 保留。补一条省略时按 `os.name` 选择配置根的 owner 测试。
9. **不覆盖。** 不扩 CI `matrix.os`；不改 host-partial；不复活宿主 ignore；不改 E010 清理墙钟。
10. **S3 去向（现行偏差）。** `tests/test_windows_runlog.py` 现无模块标记，S3 标 `infra`。将 `TestRunLogStoreProcessOutput` 整体迁入既有 `tests/test_runlog.py`；删除 `from test_secure_runlog import windows_log_adapter` 与对 `pf._secure_runlog.WindowsRunDirectory` 的 patch，改为经 `pf.runlog.secure_log_directory` 直接返回测试内假 adapter。仅为记录日志而跑生产 runner 的结果改为手工 `ProcessResult` / `ProcessTerminalUnavailable`；类迁出后 `tests/test_process.py` 保留模块级 `process`，真实 runner、停止/超时/PATH/redact 用例不改道。
11. **E 回填。** §5 必须写成「证明：命令 → 结果摘要」；AC7 另回填 §2.2 最后一列，AC8 另列四个 Cell seam 的代表 node 与 Host 改道 allowlist。S4 完成前不得归档。
12. **失败先记。** 写入 §6。
13. **未开切片不改代码。** S1 未记录完成前不得开 S2。
14. **归档原子性。** S4 在同一变更中完成 owner 吸收审计、D042/P047 归档、两个索引更新与现行入链重定位；不保留现行跳转页。

### 3.1 `host_target` owner 测试矩阵（S2）

现行 `TestProjectLoader` 旁的两个测试只覆盖 linux+musl、darwin、win32 成功，以及 `emscripten` 失败。S2 必须覆盖：

| 输入 | 期望 |
| --- | --- |
| `linux` + `AMD64` + `("glibc", "2.39")` | `x86_64-unknown-linux-gnu` |
| `linux` + `aarch64` + `("gnu", "")` | `aarch64-unknown-linux-gnu` |
| `linux` + `x86_64` + `("musl", "1.2")` | `x86_64-unknown-linux-musl` |
| `linux` + `ARM64` + `("MUSL", "1.2")` | `aarch64-unknown-linux-musl` |
| `linux2` + `x86_64` + `("glibc", "2")` | `x86_64-unknown-linux-gnu`（`startswith("linux")`） |
| `darwin` + `AMD64` + libc callback 若调用即失败 | `x86_64-apple-darwin` |
| `darwin` + `arm64` + libc callback 若调用即失败 | `aarch64-apple-darwin` |
| `win32` + `AMD64` + libc callback 若调用即失败 | `x86_64-pc-windows-msvc` |
| `win32` + `arm64` + libc callback 若调用即失败 | `aarch64-pc-windows-msvc` |
| `emscripten` + machine/libc callback 若调用即失败 | `ConfigurationError("unsupported host platform: emscripten")` |
| `linux` + `i686` + `("glibc", "2")` | `ConfigurationError`，triple 不得含 `i686` |
| `linux` + `x86_64` + `("", "")` | `ConfigurationError` |
| `linux` + `x86_64` + `("unknown", "")` | `ConfigurationError` |

另在 `tests/test_static_configuration.py` 增加一条参数化 owner 测试：省略 `platform=` 时分别 patch Resolver owner 读取的 `os.name` 为 `posix` / `nt`，证明选择 XDG/HOME 与 APPDATA 配置根；现有显式 `platform=` 用例继续证明可控的 owner seam。`tests/test_static_request.py` 只证明产品装配不再传 `platform=`，不 patch 全局 Host。

## 4. 证据槽

| 槽 | 切片 | 预定命令（实施时回填结果） |
| --- | --- | --- |
| E1 | S1 | 2026-09-10：`.venv/bin/python scripts/check_docs.py` → `documentation checks passed`；`.venv/bin/python scripts/generate_report_schema.py --check` 退出 0；`src/pf` 无 `platform.py` / `platform/` / `host.py` / `host/`；`HostFacts` / `ProcessPlatform` 在 `src/pf` 无命中。D002 §1/§5.1–§5.3/§8/§11、D007 §8、CONTEXT **Host**、`tests/README.md` 种类与 § Host/Cell 展开已与 D042 §2 对照 |
| E2 | S2 | 2026-09-10：身份命中与 §2.2 逐行核对完毕。`ProjectLoader._host_target` 已删；`src/pf` / `tests` 无该方法。`.venv/bin/python -m pytest --no-testmon -q tests/test_project.py -k host_target` → `13 passed, 83 deselected`；`.venv/bin/python -m pytest --no-testmon -q tests/test_static_configuration.py tests/test_static_request.py` → `12 passed, 3 deselected` |
| E3 | S3 | 2026-09-10：四 seam 代表 node 见下。Host 改道 allowlist：`tests/test_process.py::_pid_is_running`（本机 pid 探测）、`TestSubprocessRunner.test_subprocess_runner_resolves_unqualified_windows_executables_against_child_path`（本机无 Windows PATH `which` 则 skip）、`tests/test_windows_runlog.py::test_windows_run_directory_rejects_non_windows`（本机已是 Windows 则 skip 非 Windows 拒绝）。`host_target` owner 测试 patch `sys.platform` 属 AC11，不计入产品 Cell 改道。`_secure_runlog` / `from test_secure_runlog` 只余 `tests/test_secure_runlog.py`。`.venv/bin/python -m pytest --no-testmon -q tests/test_runlog.py tests/test_secure_runlog.py tests/test_windows_runlog.py` 与四 seam 代表项一并 `62 passed`；`.venv/bin/python -m pytest --no-testmon -q -m process tests/test_process.py` → `39 passed, 1 skipped`（Windows PATH） |

S3 Cell seam 代表 node（每 seam 跨 linux / darwin / win32 正向断言）：

| Seam | 代表 node | 三类 family |
| --- | --- | --- |
| marker | `tests/test_markers.py::TestPortableMarkers::test_complete_cell_facts_are_host_independent` | linux-gnu / linux-musl / apple-darwin / windows-msvc 注入 target，五字段不读本机 |
| projection | `tests/test_projection.py::TestReportProjection::test_group_projection_covers_linux_darwin_and_win32_selectors` | 三类 target 各有独立 floor，投影后按 Cell 求值命中对应版本 |
| apply | `tests/test_authorization.py::TestApplyAuthorizer::test_complete_three_family_report_uses_declared_matrix` | linux + darwin + win32 完整报告 → `DECLARED_MATRIX`，`selected_selectors` 含三 family |
| admission | `tests/test_verification.py::TestVerificationRunnerAdmission::test_admission_executes_only_the_injected_host_family` | 分别注入三 family `host_target`，只执行匹配 Cell |
| E4 | S4 | 2026-09-10：`PATH="/home/llh/pf/.venv/bin:$PATH" .venv/bin/python -m pytest --no-testmon -q -m "not qualification"` → `2532 passed, 1 skipped, 8 deselected in 35.93s`。终审通过后同变更归档。归档后 `.venv/bin/python scripts/check_docs.py` → `documentation checks passed`；`.venv/bin/python scripts/generate_report_schema.py --check` 退出 0。`docs/README.md` §7 已无 D042/P047；§3 编号为 D038–D043；`docs/archived/README.md` 已收录；现行 `docs/designs/` / `docs/plans/` 无 D042/P047 跳转页 |

切片内先运行上表 scoped 证据；最终 PR 回归只在 S4 运行，不代替各 AC 的语义断言：

```sh
.venv/bin/python -m pytest --no-testmon -q -m "not qualification"
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/generate_report_schema.py --check
```

## 5. 逐 AC 回填

实施后按「证明：命令 → 结果」填写。全量 pytest 只作回归网。

| AC | 切片 | 证明 |
| --- | --- | --- |
| AC1 | S1 | 证明：读 D002 §1 → 已写能力 owner 内消化 OS 差异；禁止通用平台独立层 / `pf.platform` / HostFacts；内部 seam 必要条件为同一能力两个真实 adapter；Darwin 与 Linux 同属 POSIX。无「三条件同时成立」句 |
| AC2 | S1 | 证明：D002 保留 `## 5. Application boundary`；新增 §5.1/§5.2/§5.3。§5.3 写明 CLI composition 与 Loader 两个合法调用位置，Runner 只收注入。`rg` D002 无「每 invocation 最多一次」 |
| AC3 | S1 | 证明：D007 §4–§5 POSIX/Windows 行为句保留；§8 仅加「无 public ProcessPlatform；跨能力平台层见 D002」，不复制 D002 §1/§5 |
| AC4 | S1 | 证明：CONTEXT **Host** 紧挨 **Cell**，Avoid `Cell, platform, Environment`；无 admission / 五字段 / 支持矩阵 |
| AC5 | S1 | 证明：D002 §11 无种类列表、无覆盖率并集算法，只留公开 seam / 真实性并链接 `tests/README.md`。`tests/README.md` 不再把种类指回 D002 |
| AC6 | S1 / S2 | 证明：E1 包布局检查通过；stdlib `platform` 仅 `host_target()` 使用 |
| AC7 | S2 | 证明：§2.2 最后一列已填；`static_request.py` 无 `os.name`；ty 配置根命中只在 `static_configuration.py`；白名单外无身份读取 |
| AC8 | S3 | 证明：上表四 seam 代表 node；Host 改道 allowlist 见 E3；`TestRunLogStoreProcessOutput` 已在未标记 `tests/test_runlog.py` 且不进入生产 runner；`tests/test_windows_runlog.py` 标 `infra`；`tests/test_process.py` 仍模块级 `process` |
| AC9 | S1 | 证明：未改 D001 / D006 / D008 产品句；R010 仍开放（`docs/reviews/R010-pf-engineering-document-audit.md` 状态：开放） |
| AC10 | S1 | 证明：`tests/README.md` 独占种类表、覆盖率并集与 Host/Cell 展开；种类定义不再指回 D002 |
| AC11 | S2 | 证明：E2 `13 passed` 覆盖 §3.1 矩阵：未知 OS 不读 machine/libc；darwin/win32 libc callback 若调用即失败；未知 machine 错误文案不含 `i686` triple；空/未知 libc `ConfigurationError` |

## 6. 决定与偏差

- 2026-09-10：接受 D042；锁定本 Plan。未改 `src/` / `tests/`。
- 2026-09-10：审查后小改 D042（第五次）：§5 拆成 Host OS 读取 / Cell 投影；CLI UTF-8 不进 §5.1；libc 只约束 Linux；S3 不再扛 AC11；点名 musl 空串缺口与 `_secure_runlog` 产品进口。本 Plan 同步锁定 §3 测试矩阵与 S3 测试去向。
- 2026-09-10：交付前审查补强 D042（第六次）与本 Plan：明确 OS→machine→Linux libc 探测顺序、D002 §5.1–§5.3 吸收形状、Cell seam 代表证据、RunLogStore 迁入既有测试文件、分离 E2 pytest 过滤，并增加 S4 原子归档与生成投影检查；目标契约与已接受的 fail-closed 决定不变。
- 2026-09-10：简化 D042（第七次）：Design 删除文件级编排名单、测试文件迁移和异常文案，§7 只指向本 Plan；彻底删除 `host_target()` 调用次数不变量。P047 保留 inventory、迁移步骤、现行错误文案与证据命令，验收目标不变。
- 2026-09-10：开 S1。将 D042 状态改为「实施中」，并按 §2 吸收 D002 / D007 / CONTEXT / `tests/README.md`。未改 Design 契约正文。
- 2026-09-10：S1 完成。E1 文档与生成投影检查通过。
- 2026-09-10：S2 按 §3.1 一次写完 `host_target()` 探测顺序；删除 `ProjectLoader._host_target`；`os.name` 从 `static_request.py` 移入 Resolver。owner 测试 patch `_host_os_name` 而非 stdlib `os.name`，避免 pathlib 在 `nt` 下实例化 `WindowsPath`。
- 2026-09-10：S2 完成。E2 pytest 与 §2.2 对照通过。
- 2026-09-10：S3 不为「只记账」：marker 沿用既有跨 family 正向断言；projection / apply / admission 缺代表项，当场补测三 node。`TestRunLogStoreProcessOutput` 迁入 `tests/test_runlog.py`，手工 `ProcessResult`，Windows guard 经 `pf.runlog.secure_log_directory` 注入测试内假 adapter。
- 2026-09-10：S3 完成。E3 四 seam + RunLog / process 证据通过。
- 2026-09-10：GPT 5.6 sol high 盲审（agent `4adf903f-48e2-49e3-b933-1331957d6927`）对照 D042 AC1–AC11 与开工三条约束，结论：无偏差项，ready-for-archive。不改 Design。
- 2026-09-10：S4 首次 PR 回归用 `.venv/bin/python -m pytest` 但未把仓库 `.venv/bin` 列入 `PATH`，得到 `84 failed` / `1 error`，典型 `JournalHighestUncollected(detail='invalid-layout')`。根因是 `StaticRequestFactory` 经 `shutil.which("ty")` 发现工具，与 D042 Resolver 搬家无关；`tests/README.md` 已要求 `PATH` 含 `.venv/bin`。不改生产代码。补测：`PATH=".venv/bin:$PATH" .venv/bin/python -m pytest --no-testmon -q tests/test_check.py::TestCheckWorkflow::test_check_preserves_configured_verifier_outcomes` → `12 passed`。按同一 PATH 重跑 E4。
- 2026-09-10：GPT 5.6 sol high 终审（同一 agent `4adf903f-48e2-49e3-b933-1331957d6927`）独立核验 AC1–AC11、三条开工约束与停止条件，结论：终审通过，可以归档。PATH/`invalid-layout` 为工具发现环境错误，未掩盖 D042 偏差。无剩余偏差项。
- 2026-09-10：S4 归档。D042/P047 改为完成态并移入 `docs/archived/`；`docs/README.md` §7 删除开放行，§3 编号纳入 D038–D043；`docs/archived/README.md` 收录；现行目录不留 D042/P047 跳转页。空的 `docs/plans/` 无法进 git，§3 将「进行中 Plan」改为代码路径 `docs/plans/`，避免 `check_docs.py` 报 `missing plans/`。

## 7. 停止条件

与 D042 §8 停止条件相同。任一成立则本 Plan 未完成。

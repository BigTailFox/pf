# D042 — PF 宿主能力局部化

- **状态：** 已完成并归档；2026-09-10 通过 AC1–AC11 验收，稳定规则已由现行 owner 接管；实施与证据见 [P047](../plans/P047-pf-host-capability-locality.md)
- **日期：** 2026-09-09
- **修订：** 2026-09-10（第七次：删除探测次数不变量；文件级 inventory 与测试迁移细节下沉 P047）
- **性质：** 已归档临时结构 Design；不再承担现行规范
- **目标 owner：** [D002](../../designs/D002-pf-implementation.md) §1、§5、§8、§11；[D007](../../designs/D007-pf-process-output.md) §4–§5、§8；[tests/README.md](../../../tests/README.md)；[CONTEXT.md](../../../CONTEXT.md)
- **验收标准：** [§8](#8-验收标准)
- **实施计划：** [P047](../plans/P047-pf-host-capability-locality.md)
- **来源：** 三端（Linux / Windows / macOS）支持是否应建通用平台独立层的结构判断；证据见 [E010](../../experiments/E010-windows-native-search-cleanup.md)、[R002 §6](../reviews/R002-pf-v1-architecture-review.md#6-p2runlogstore-的平台-implementation-应进入私有-seam)、[R010 §4](../../reviews/R010-pf-engineering-document-audit.md#4-r007-开放项交接) 的真实 host 资格缺口
- **关联：** [D001](../../designs/D001-pf.md) 保留 Cell target → PEP 508 五字段，不读 Host；[D006](../../designs/D006-pf-cli-enhancement.md) 已拥有 CLI UTF-8 入口句；[D008](../../designs/D008-pf-verification-run.md) 保留 host Cell admission。已归档 [D039](D039-pf-static-evaluation-module.md) / [D041](D041-pf-repository-test-conformance.md) 的稳定规则在 D002 / D004 / `tests/README.md`；本文只补 Host / Cell 结构句，不覆盖种类表或静态公开表面

本文保存已完成的宿主能力局部化。稳定规则已归并 [D002](../../designs/D002-pf-implementation.md) §1/§5/§8/§11、[D007](../../designs/D007-pf-process-output.md)、[CONTEXT](../../../CONTEXT.md) 与 `tests/README.md`。正文保留迁移时的目标与理由，不再承担现行规范。

本文把「三端 Host OS 差异」写成结构规则：差异消化在拥有该能力的 module 内部；禁止通用平台独立层。命令、失败资格、Cell 语义与 wire 不变；Host 身份探测资格按 §3 收窄，`host_target()` 对未知 machine/libc 改为 fail-closed。

D002 §5 已写 composition 向 `VerificationRunner` 注入 `host_target()`，但仍有无效的全 invocation 探测计数句；吸收时删除，不建立替代计数规则。D002 §11 现仍与 `tests/README.md` 并列定义种类；吸收后种类与覆盖率并集只留在 `tests/README.md`，D002 §11 只保留公开 seam / 真实性约束与链接。D007 §4–§5 已写安全目录与进程停止的 POSIX/Windows 分流。D006 已写 CLI 入口强制 UTF-8。吸收按 §2 分家，不把同一禁令、种类表或展开表抄进两份 owner。

## 1. 结论

规划 Linux / Darwin / Windows 三类 **Cell target**，与在某个 **Host** 上运行，不是同一条轴：

- **Cell target** 是产品要推理的兼容性环境。规划、marker、uv、apply、报告必须看见它。
- **Host** 是本次 invocation 所在的本机。搜索、评价、失败、报告、授权不得靠 `os.name` / `sys.platform` 分支。

本文的结构目标是：在**任一受支持 Host** 上，Host OS 差异留在已有能力 owner（进程、安全目录、快照清理、venv 布局、ty 配置根、CLI UTF-8）内部，而不是再做一个所有上层都依赖的 `pf.platform`。受支持 Host 集合与真实 macOS/Windows 发布资格由 [R010 §4](../../reviews/R010-pf-engineering-document-audit.md#4-r007-开放项交接) 跟踪，不是本文的验收，也不因吸收本文而闭合。

通用平台独立层通不过删除测试：删掉它之后，调用方仍要学习进程组、临时目录、安全日志各自的语义，复杂度只是换了名字。R002 已经对 RunLog 做过一次正确的窄手术——POSIX / Windows 两个真实 adapter 只服务安全目录——并明确不要扩成通用 filesystem。本文件把同一尺子写进 D002，覆盖其余宿主能力。

## 2. 范围与非目标

**接受并吸收后替换：**

| 目标 | 替换内容 |
| --- | --- |
| D002 §1 | 在现有「不建立通用 filesystem/repository」之上，写明：受支持 Host 上的 OS 差异留在能力 owner 内部；禁止通用平台独立层、`pf.platform`、跨能力 HostFacts；内部 seam 的必要条件是同一能力已有两个真实 adapter；Darwin 与 Linux 同属 POSIX，不因「三端」单独成层。不在此复制测试展开表 |
| D002 §5 | 保留 `## 5. Application boundary` 锚点；写入 §5.1 Host OS 读取表、§5.2 Cell 投影表（含实现路径），现有 composition / invocation lifecycle 正文归入 §5.3，不覆盖或搬离。固定 `host_target()` 是唯一 Host→Cell 身份探测函数，只返回 §3 的 exact triple，未知 machine/libc fail-closed。CLI composition 可调用并向 Runner 注入；`ProjectLoader` 在省略 `platforms` 时可调用。**删除**「`host_target()` 每 invocation 最多一次」旧句，不建立调用次数不变量 |
| D002 §8 | 进程组停止与子进程 PATH 解析是 `SubprocessRunner` 内部差异，不升为 public ProcessPlatform。不复制 D007 的 POSIX/Windows 行为正文 |
| D002 §11 | 删除种类 / 车道定义列表与覆盖率并集算法。只保留公开 seam、真实性约束（不替换 evaluator、产品测试不进口能力私有 adapter、未授权车道不得进入生产 process seam 等）以及指向 `tests/README.md` 的链接。不写入 Host/Cell 展开表 |
| D007 §4–§5、§8 | 保留现行分流正文；结构禁令（无 `ProcessPlatform` / 无跨能力平台层）只链接 D002，不在 D007 另写一份 |
| `tests/README.md` | **独占**测试种类定义、覆盖率并集算法与 Host / Cell 展开（§6）。不再把种类定义指回 D002 |
| `CONTEXT.md` | 增加 **Host** 词条与 `_Avoid_`；不写入 admission / 五字段 / 支持矩阵 |

**保持不变：** D001 命令、退出码、五字段映射与「四平台字段不取 Host 默认值」、省略 `platforms` 时用宿主 target；D003 搜索；D005 分类；D006 selector 展示与 CLI UTF-8 入口句；D008 host Cell 选择与 host-partial（Runner 仍只收注入、自己不探测）；D012/D013 资格矩阵；D014 wire；自举 `C`；coverage `fail_under = 90` 的 CI OS 并集算法。这里的“不变”不包含 Host 探测资格：§3 的 fail-closed 会让无法可靠识别的 Host 从错误规划 Cell 改为配置失败，但不改变 D001 退出码或 D008 admission 规则。

**本文件不覆盖：** 关闭 R010 的真实 macOS/Windows 发布资格或把 CI `matrix.os` 扩成验收；把 E010 清理墙钟写成产品 SLA；改 host-partial 退出码；复活宿主 ignore / 用户 ty 配置发现；把 planning 默认值改成 `ProjectLoader.load(..., host_target=...)`（那会改 D002 §6 签名）。

## 3. Host 与 Cell

吸收进 CONTEXT 的词汇（只含词条与 `_Avoid_`；行为仍只在 owner）：

**Host**

运行本次 PF invocation 的本机。它不是 Cell。

_Avoid_: Cell, platform, Environment

以上词条进 CONTEXT。以下 `host_target()` 表与 fail-closed 吸收进 D002 §5，不进 CONTEXT。

一次 invocation 的 **host target** 是该 Host 对应的精确 uv target triple，由 `host_target()` 产生。这是身份探测，不是第三份进程 adapter。

`host_target()` 只许返回下列 exact triple。先读并校验 `sys.platform`；未知 OS 立即以 `ConfigurationError` 失败，不再读取 machine/libc。已知 OS 再把 machine 转为小写并规范化别名（`amd64`→`x86_64`，`arm64`→`aarch64`），校验结果只属于 `x86_64` / `aarch64`；只有 Linux 随后读取 libc：

| OS | machine | libc | triple |
| --- | --- | --- | --- |
| `sys.platform.startswith("linux")` | `x86_64` / `aarch64` | 只在 Linux 上读 `platform.libc_ver()[0]`（大小写不敏感）：已识别的 `gnu` / `glibc` → `gnu`；已识别的 `musl` → `musl` | `{machine}-unknown-linux-{gnu\|musl}` |
| `darwin` | `x86_64` / `aarch64` | 不读、不校验 | `{machine}-apple-darwin` |
| `win32` | `x86_64` / `aarch64` | 不读、不校验 | `{machine}-pc-windows-msvc` |

规范化后不在表内的 machine、Linux 上无法识别为 `musl` 或 `gnu`/`glibc` 的 libc（含空串），同样一律 `ConfigurationError`。返回值不得把原 machine/libc 字符串写进 triple，也不得把「非 musl」默认为 `gnu`。`darwin` / `win32` 不读取 libc，不能因 `libc_ver` 为空或不可调用而失败。这是对现行 `project.host_target()` 的目标契约；吸收后由 D002 拥有。owner 测试覆盖表内 8 个 OS×machine×libc 输出分支、别名/大小写规范化，以及未知 OS、machine、空 libc 与未知 libc 的代表性失败输入。

行为仍只在既有 owner：

- D001：省略 `platforms` 时用宿主 target 规划 Cell；portable 四平台字段只由 Cell 的 exact target 得出。Linux Host 规划 Windows Cell 时，`platform_system` 必须是 `Windows`，不得读本机 `sys.platform`。`python_version` 是第五字段，等于 Cell minor。
- D008：`VerificationRunner` 只用注入的 host target 过滤 `cell.target == host_target` 的可执行 Cell。非本机 family 的 Cell 仍参与规划、marker 求值、报告 coverage 与跨 Host merge，本机不执行它们。宽松或错误的 host target 会选错 Cell 或制造错误 `MISSING_CELL`，因此 §3 的 fail-closed 是 admission 前置。

## 4. 能力局部化

深模块以小 interface 隐藏完整行为。Host OS 是部分能力的 **implementation 分叉**，不是新的产品轴。

### 4.1 正规则

1. 某项 Host 差异属于**已经拥有该能力的 module**。调用方继续只学习该 module 的现有公开表面。
2. 同一能力上已经存在两个真实 adapter（典型为 POSIX 与 Windows）时，可以在该 module **内部**建立 seam。产品流程不出现 `os.name` 条件。
3. Darwin 与 Linux 共用 POSIX adapter，除非出现与 POSIX **行为不同**的第三份真实实现。三端品牌不自动产生三个 adapter。`host_target()` 区分 `apple-darwin` / `linux-gnu` / `linux-musl` / `windows-msvc` 是 Cell 身份，不是 Darwin 进程实现。
4. `host_target()` 是唯一允许把 `sys.platform` / `platform.machine()` / libc 读成 Cell 身份的函数，且必须满足 §3。CLI composition 可调用并向 `VerificationRunner` 注入字符串；`ProjectLoader`（与探测函数同属 `project.py`）在应用 D001 默认 `platforms` 时可调用。其它编排 module 只接收 target，不探测 Host，也不对调用次数建立产品不变量。
5. 能力 owner 需要「posix 或 windows」这类单一事实时，在**该 owner 的实现路径**内读 `os.name`，或接收该能力专用参数。ty 配置根的探测**移入** `TyConfigurationResolver`：产品装配省略 `platform=`，由 Resolver 读 `os.name`；参数仍可显式传入，供 owner 测试覆盖两支，避免产品测试 patch `os.name`。`static_request.py` 不再读 `os.name`。不把这些事实收成跨模块 HostFacts / Platform 服务。

开内部 seam 的**必要条件**只有一条：该能力已有两个真实实现，不是「将来可能有」。产品流程里反复出现同一组 OS 条件、以及删除测试（新接口比直接调用 OS 更小），是选择 seam 形状的**设计证据**，不是与「两份 adapter」并列的机械门槛。单点 dispatch、内部高度分化的深 module（如已有的 `SecureLogDirectory`）只要满足必要条件即可保留内部 seam。

### 4.2 禁止的形状

不建立：名为 platform / host / PAL 的包；通用 filesystem 或路径抽象；把 `kill_process_group`、`cleanup_temp`、`secure_mkdir`、`config_home`、`which` 堆在同一 Protocol 上的浅层；为了测试而伪造整个 Host、并宣称已证明真实三端。

现有禁令保留并收窄：不建立 `utils.py`、通用 filesystem/repository、DI framework、event bus 或 daemon。通用平台独立层属于同一类。

## 5. 能力表

吸收后 D002 §5 用 **§5.1** 标明允许读取本机 OS 身份的 owner 及实现路径。命中指 `os.name`、`sys.platform`，以及把 `platform.machine()` / `libc_ver` 读成产品身份。`import os`、`os.environ`、`os.fstat` 不是命中。**§5.2** 是 Cell target 投影：只读注入或规划得到的 target。未列入 §5.1 的产品编排不得新增宿主身份分支。

CLI UTF-8 是入口固定行为：`configure_utf8_stdio()` 不读 `os.name` / `sys.platform`，不跟随宿主代码页。它不进入 §5.1。禁止的是 `src/pf/platform/`、`src/pf/host/` 包（及同名 module），不是 stdlib `platform` 在 `host_target()` 内的使用。

### 5.1 Host OS 读取

| 能力 | Owner | 实现路径 | 调用方学习的表面 |
| --- | --- | --- | --- |
| Host→exact target | `host_target()` | `project.py`（`host_target`；删除无调用的 `ProjectLoader._host_target`） | `str` exact triple。Runner 由 composition 注入；Loader 在省略 `platforms` 时可调用 |
| 进程组、超时/中断停止、Windows 子进程 PATH | `SubprocessRunner` | `adapters/process.py` | `ProcessRunner.run(ProcessSpec) → ProcessObservation` |
| 安全日志目录 | 私有 `SecureLogDirectory` | `_secure_runlog.py`、`windows_runlog.py` | `RunLogStore` |
| 临时目录关闭 | `cleanup_temporary_directory` | `snapshot.py` | `SnapshotBuilder` / `EnvironmentFactory` 的 `close()` 调用该函数；消费方不是第二 owner |
| venv 解释器路径 | `EnvironmentFactory` | `environment.py`（`_interpreter`） | `PreparedEnvironment.interpreter` |
| ty 用户配置根 | `TyConfigurationResolver` | `static_configuration.py` | 产品装配省略 `platform`；`static_request.py` 不读 `os.name`。owner 测试可显式传 `platform=` |

### 5.2 Cell target 投影

| 能力 | Owner | 实现路径 | 调用方学习的表面 |
| --- | --- | --- | --- |
| PEP 508 四平台字段 | `pf.markers` | `markers.py` | `platform_marker_facts(target)`，只读 target |
| wheel / uv platform tag | `UvAdapter` | `adapters/uv.py` | candidate / install 按 Cell target 匹配 |
| apply selector 展示 | `pf.terminal` | `terminal/` | D006 标签；`win32`/`darwin` 是 Cell 别名 |

不列入 §5.1：

- Cell 解释器 ABI：`UvAdapter` / `static_inputs` 发给 prepared venv 的 probe 脚本读 `SOABI` / `cache_tag`，是目标侧观察。脚本字符串里的 `os` / `platform` / `sys` 不是 Host 读取。
- D001 preserved / external harness 的 contextual marker：五字段仍由 Cell 覆盖，剩余字段沿用 `packaging` 的 context/Host default。它不生成 host target，也不是 portable marker 或 AC7 可移除的 Host 身份分支。
- 宿主 ignore / 用户 ty 配置：静态请求以 `environment_mode="explicit"` 只传 `LANG` / `LC_*` / `TY_CONFIG_FILE`，三类 Host 都没有全局 ignore 输入。D004 只承认快照内 ty 配置。

E010 已把 `killpg`、PATH `which`、临时目录重试放进 §5.1 对应 owner。CLI UTF-8 已由 `configure_utf8_stdio()` 产品化（`tests/test_cli.py` 用 GBK stream 证明可写 `✓`），不是 E010 实验绕过。

§5.1 是 Host OS 身份读取的完整 owner 白名单；除此之外的产品编排只接收 target 或能力结果，不读取 Host 身份。`configure_utf8_stdio()` 与 composition 调用 `host_target()` 是入口装配，不是业务分支。实施期文件级 inventory、基线命中与豁免只见 P047，不进入长期 owner。

## 6. 测试

测试种类、车道与覆盖率并集的定义吸收后只在 `tests/README.md`。本文件只规定 Host / Cell **如何展开**；不改 marker 名字、自举 `C` 或 `fail_under = 90`。

| 要证明的事 | 怎么测 | 车道 | 不做什么 |
| --- | --- | --- | --- |
| Cell 契约（marker、projection、apply、admission） | 进程内对 linux / darwin / win32 family **注入 target** | 未标记 | 不为「当前是 Linux」跳过 Windows Cell 断言 |
| 本机可执行哪些 Cell | 注入 `host_target`；D008 过滤 | 未标记 | 产品测试改全局 `sys.platform` 冒充 Host |
| `host_target()` 规范化与 fail-closed | owner 测试可 patch `sys.platform` / `machine` / `libc_ver`；覆盖 §3 支持组合与未知输入 | 未标记 | 把该例外扩到产品 Cell 契约 |
| 安全目录、临时目录 `close()` 的 portable 契约 | 经 `RunLogStore` / `close()` 公开表面；**不得**进入生产 `SubprocessRunner.run` | 未标记 | 每个 workflow 复制三套 OS 用例；把真实进程停止写进未标记 |
| 能力 owner 的私有 Host adapter 协议 | 直接打内部 adapter | **仅** `infra` | 把其它 adapter 协议标进 `infra`；产品测试进口私有 adapter |
| 真实 `SubprocessRunner` 进程组停止、超时/中断、Windows PATH `which` | 进入生产 `SubprocessRunner.run` / 停止路径 | **仅** `process` | 标未标记、`infra` 或 `qualification`；当作 Host 发布资格 |
| 产品命令在该 OS 上的路径 | 与 Linux 相同的收集；命令路径标 `e2e` | `e2e`（且 `process`） | 用 PAL / 本机 Linux 冒充 Windows/macOS e2e |
| 工具协议 / 版本凭据 | `qualify_*.py` 与 committed manifest | `qualification` | 用资格矩阵证明产品 Host 行为或关闭 R010 |

Cell 契约展开要求 marker、projection、apply、admission 每个公开 seam 都有跨 linux / darwin / win32 family 的代表性正向断言；不要求把每条测试与三类 target 做笛卡尔积。S3 证据逐 seam 列出代表测试 node。

因本机 OS 跳过或改道，只用于**本 Host 不具备被测能力**的用例（symlink、FIFO、Windows DACL）。形式包括 `skipif`、运行时 `pytest.skip`、`if os.name` 早退。禁止用它们跳过「某个 Cell target 的产品规则」。

`host_target()` owner 测试控制该函数的 OS/machine/libc 输入；adapter 协议测试可控制该 adapter 的 OS 输入。这两种手法不得用到产品 Cell 契约。能力 owner 的 portable 契约只走其公开 interface；内部 dependency replacement 不成为新的产品 interface。具体测试迁移、文件与注入点只见 P047。

增加 Windows / macOS CI job 时：产品测试文件不乘三；该 job 执行同一收集，并补上本机才能走到的宿主私有分支。这只扩大覆盖率并集，**不是**本文交付的发布资格。资格矩阵按工具×Host 扩展属于 R010。

## 7. 实施

切片、文件迁移、测试命令与证据回填只见 [P047](../plans/P047-pf-host-capability-locality.md)，本文不保留第二份实施计划。

## 8. 验收标准

| AC | 必须成立的目标 | 公开 seam / 证据 |
| --- | --- | --- |
| AC1 | D002 §1 写明：受支持 Host 上 OS 差异留在能力 owner 内部；禁止通用平台独立层 / `pf.platform` / HostFacts；内部 seam **必要条件**为同一能力两个真实 adapter；Darwin 默认随 POSIX。不把「三条件同时成立」写进 D002 | D002 §1 |
| AC2 | D002 保留 `## 5. Application boundary` 锚点；§5 含 §5.1 Host OS 读取表、§5.2 Cell 投影表与承接既有正文的 §5.3 composition / invocation lifecycle。写明 CLI composition 与 Loader 是两个合法调用位置，Runner 只收注入；**删除**「每 invocation 最多一次」旧句且不建立替代调用次数不变量 | D002 §5 与吸收 diff |
| AC3 | D007 仍以 `ProcessRunner` / `RunLogStore` 为公开表面，保留现行 POSIX/Windows 行为句；无 public `ProcessPlatform`；结构禁令只链接 D002，不复制 §1/§5 | D007 正文 |
| AC4 | CONTEXT 有 **Host** 词条与 `_Avoid_`，无 admission / 五字段 / 支持矩阵；Cell 仍 Avoid `platform`；Host Avoid `Cell` / `platform` / `Environment` | CONTEXT.md |
| AC5 | 测试种类、覆盖率并集与 Host/Cell 展开**只**在 `tests/README.md`。D002 §11 无种类 / 车道定义列表、无覆盖率并集算法，只保留公开 seam、真实性约束与指向 `tests/README.md` 的链接 | 两份正文 |
| AC6 | `src/pf` 无 `platform/` / `host/` 包或同名 module，无通用 Filesystem/PAL。不禁止 stdlib `platform` 仅由 `host_target()` 使用 | 包布局 |
| AC7 | §5.1 是 Host OS 身份读取的完整 owner 白名单；白名单外的产品编排不读取 Host 身份。ty 配置根读取只在 `TyConfigurationResolver` implementation。目标侧 probe、D001 contextual marker 与非身份 `os` 使用不误报；实施 inventory 还要核对 import、调用方与公开 seam | P047 inventory 对照 |
| AC8 | marker、projection、apply、admission 各有跨三类 Cell family 的代表测试；产品 Cell 契约不按本机 OS 改道。能力 owner 的 portable 契约走公开 interface 且不进入未授权真实进程；私有 Host adapter 协议标 `infra`，真实 process 行为标 `process`，产品命令路径标 `e2e` 且 `process`，工具资格标 `qualification`。替身不能证明真实 Host 资格 | `tests/README.md` 与 P047 证据 |
| AC9 | D001 五字段与省略 `platforms`、D008 admission、D006 selector / UTF-8 入口句不因本文改写产品通道；§3 fail-closed 只收窄 Host 探测资格；R010 真实 host 资格仍开放 | 范围内 diff |
| AC10 | `tests/README.md` 独占完整种类定义（吸收 D002 §11 迁出的种类句，与现行表合并去重）并增加 §6 展开；不再把种类定义指回 D002 | `tests/README.md` |
| AC11 | `host_target()` 先拒绝未知 OS，再读取 machine，并只在 Linux 读取 libc；只返回 §3 表内 exact triple。未知 OS / machine / libc 升 `ConfigurationError`；owner 测试覆盖 8 个表内输出分支、规范化与代表性不确定输入 | `project.host_target` 与 `tests/test_project.py` |

停止条件（任一成立则未交付）：新增 `pf.platform`、HostFacts 或等价浅层并让产品编排依赖它；marker / wheel 改读本机 OS；为三端复制三套产品测试；用替身、`qualification` 或本文 AC 宣称真实 Host 已资格化；无独立实现却把 Darwin 拆成第三 adapter；把 E010 清理耗时写成 D001 产品承诺；保留或新建 `host_target()` 调用次数不变量；未知 machine/Linux libc 仍默认为 `gnu` 或透传；`darwin` / `win32` 读取 libc；白名单外的产品编排仍读 Host 身份；D002 §11 仍完整定义测试种类；未按车道真实性收集测试；只改文档不完成 P047 inventory 却声称结构已落地。

## 9. 明确不做与已知缺口

- 改变可执行 Cell 集合、host-partial merge 或 apply 授权。
- 复活宿主 ignore / 用户 ty 配置发现，或把 D004 现行 fail-closed 改写成「已支持」。
- 把 `host_target()` 扩成 Host 能力服务定位器，或把「全进程只探测一次」写成不变量。
- 把 Cell 解释器 ABI（`SOABI` / `cache_tag`）收进 §5.1 Host OS 读取表。
- 用本文件关闭 R006/R008/R010 的其它开放项，或把 UTF-8 入口强制写成已关闭 R010 Windows 资格。
- 要求 macOS 在 POSIX adapter 之外再有一份仅因品牌不同的实现。

已知缺口（本文不闭合）：

- **真实 Host 发布资格：** CI 仍是 Ubuntu；macOS/Windows 资格留在 R010。§1 的结构目标不得读成三端已交付。
- **musl 识别：** 只承认 `platform.libc_ver()` 已识别的 `musl` / `gnu` / `glibc`。空串 fail-closed。不发明 `ldd` / `ld-musl` 探测。当前把空串默认为 `gnu` 的 Host（常见 musl）会从「错误的 gnu Cell」变为无法启动。
- **Git 模式 snapshot 的宿主配置：** `git ls-files --exclude-standard` 会读本机用户的 global `core.excludesFile`，从而改变 `SourceSnapshot` 内容。这是宿主**配置**通道，不是 Host OS 分支；AC7 的身份扫描照不到。不在本文改发现语义。

## 10. 接受状态

已完成并归档。稳定规则已由 D002 §1/§5/§8/§11、D007 §8、CONTEXT **Host** 与 `tests/README.md` 接管。实施与证据见 [P047](../plans/P047-pf-host-capability-locality.md)。

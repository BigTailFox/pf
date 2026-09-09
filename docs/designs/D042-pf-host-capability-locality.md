# D042 — PF 宿主能力局部化

- **状态：** 草案
- **日期：** 2026-09-09
- **性质：** 临时结构 Design；未接受、不授权实施、不冒充已交付行为
- **目标 owner：** [D002](D002-pf-implementation.md) §1、§8、§11；[D007](D007-pf-process-output.md) §4–§5、§8；[tests/README.md](../../tests/README.md)；[CONTEXT.md](../../CONTEXT.md)
- **验收标准：** [§8](#8-验收标准)
- **来源：** 三端（Linux / Windows / macOS）支持是否应建通用平台独立层的结构判断；证据见 [E010](../experiments/E010-windows-native-search-cleanup.md)、[R002 §6](../archived/reviews/R002-pf-v1-architecture-review.md#6-p2runlogstore-的平台-implementation-应进入私有-seam)、[R010 §4](../reviews/R010-pf-engineering-document-audit.md#4-r007-开放项交接) 的真实 host 资格缺口
- **关联：** [D001](D001-pf.md) 保留 Cell target → PEP 508 五字段，不读 Host；[D008](D008-pf-verification-run.md) 保留 host Cell admission；[D039](../archived/designs/D039-pf-static-evaluation-module.md) / [D041](../archived/designs/D041-pf-repository-test-conformance.md) 正交，吸收时并存不互相覆盖

本文把「三端支持」写成现行结构规则：Host OS 差异消化在拥有该能力的 module 内部；禁止通用平台独立层。产品语义、命令、失败资格与 wire 不变。接受前不是现行契约。

## 1. 结论

PF 必须能在 Linux、Windows、macOS **Host** 上运行，也必须能规划这三类 **Cell target**。这两件事共用日常口语里的「平台」，但不是同一条轴：

- **Cell target** 是产品要推理的兼容性环境。规划、marker、uv、apply、报告必须看见它。
- **Host** 是本次 invocation 所在的本机。搜索、评价、失败、报告、授权不得靠 `os.name` / `sys.platform` 分支。

挡住实现和测试随三端膨胀的办法，是把 Host OS 差异收进已有能力 owner（进程、安全目录、快照清理、venv 布局、ty 配置根），而不是再做一个所有上层都依赖的 `pf.platform`。

通用平台独立层通不过删除测试：删掉它之后，调用方仍要学习进程组、临时目录、安全日志各自的语义，复杂度只是换了名字。R002 已经对 RunLog 做过一次正确的窄手术——POSIX / Windows 两个真实 adapter 只服务安全目录——并明确不要扩成通用 filesystem。本文件把同一尺子写进 D002，覆盖其余宿主能力。

## 2. 范围与非目标

**接受并吸收后替换：**

| 目标 | 替换内容 |
| --- | --- |
| D002 §1 | 在现有「不建立通用 filesystem/repository」之上，写明：Host OS 差异留在能力 owner 内部；禁止通用平台独立层、`pf.platform`、跨能力 HostFacts；内部 seam 只在同一能力已有两个真实 adapter 时出现；Darwin 与 Linux 同属 POSIX，不因「三端」单独成层 |
| D002 §8 | `host_target()` 是 Host→exact target 的唯一身份探测，composition root 每 invocation 最多一次并注入；进程组停止与子进程 PATH 解析是 `SubprocessRunner` 内部差异，不升为 public ProcessPlatform |
| D002 §11 | 产品测试按注入的 Cell target / `host_target` 展开，不按本机 OS 乘三；宿主能力测 owner 的 portable 契约，adapter 协议标 `infra`；真实 Host 资格走 CI OS 并集与 qualification，不能用替身代替 |
| D007 §4–§5、§8 | 安全目录与进程停止路径保持现有 POSIX/Windows 分流；补一句：这些是 `RunLogStore` / `SubprocessRunner` 的内部 adapter，不另建跨能力平台层 |
| `tests/README.md` | 增加 Host / Cell 测试句（§6）；覆盖率并集与 marker 集合不变 |
| `CONTEXT.md` | 增加 **Host**；**Cell** 仍 `_Avoid_: platform` |

**保持不变：** D001 命令、退出码、五字段映射与「不取 Host 默认值」；D003 搜索；D005 分类；D006 selector 展示（`win32`→`windows` 是 Cell 标签，不是 Host 探测）；D008 host Cell 选择与 host-partial；D012/D013 资格矩阵；D014 wire；自举 `C`；coverage `fail_under = 90` 的 CI OS 并集算法。

**本文件不覆盖：** 关闭 R010 的真实 macOS/Windows 发布资格；把 E010 的清理墙钟或控制台 GBK 写成产品 SLA；改 host-partial 退出码；为 ty/git ignore 在 Windows 上发明新的产品发现语义；D039 的静态公开表面搬家。

## 3. Host 与 Cell

吸收进 CONTEXT 的词汇（行为仍只在 owner）：

**Host**

运行本次 PF invocation 的本机。它决定本机可执行哪些 Cell，以及进程、文件系统、控制台等 OS 原语如何实现。它不是 Cell。

_Avoid_: Cell, platform, Environment

一次 invocation 的 **host target** 是该 Host 对应的精确 uv target triple，由 `host_target()` 产生（Linux gnu/musl、Darwin、Windows MSVC）。D008 只用它过滤 `cell.target == host_target` 的可执行 Cell。非本机 family 的 Cell 仍参与规划、marker 求值、报告 coverage 与跨 Host merge，本机不执行它们。

D001 的 portable 五字段只由 Cell 的 exact target 得出。Linux Host 规划 Windows Cell 时，`platform_system` 必须是 `Windows`，不得读本机 `sys.platform`。

## 4. 能力局部化

深模块以小 interface 隐藏完整行为。Host OS 是部分能力的 **implementation 分叉**，不是新的产品轴。

### 4.1 正规则

1. 某项 Host 差异属于**已经拥有该能力的 module**。调用方继续只学习该 module 的现有公开表面。
2. 同一能力上已经存在两个真实 adapter（典型为 POSIX 与 Windows）时，可以在该 module **内部**建立 seam。产品流程不出现 `os.name` 条件。
3. Darwin 与 Linux 共用 POSIX adapter，除非出现与 POSIX **行为不同**的第三份真实实现。三端产品承诺不自动产生三个 adapter。
4. `host_target()` 是唯一允许把 `sys.platform` / `platform.machine()` / libc 读成 Cell 身份的函数。其余模块要 target 时接收注入的字符串。
5. 能力 owner 需要「posix 或 windows」这类单一事实时，可以自己读 `os.name`，或接收该能力专用的参数（如 `TyConfigurationResolver.resolve(..., platform=)`）。不把这些事实收成跨模块 HostFacts / Platform 服务。

开内部 seam 的三条同时成立才拆：

- 该能力已有两个真实实现，不是「将来可能有」；
- 产品流程里反复出现同一组 OS 条件，而不是一次性探测；
- 新接口比直接调用 OS 更小：删掉它之后，复杂度会散回多个调用方。

### 4.2 禁止的形状

不建立：名为 platform / host / PAL 的包；通用 filesystem 或路径抽象；把 `kill_process_group`、`cleanup_temp`、`secure_mkdir`、`config_home`、`which` 堆在同一 Protocol 上的浅层；为了测试而伪造整个 Host、并宣称已证明真实三端。

现有禁令保留并收窄：不建立 `utils.py`、通用 filesystem/repository、DI framework、event bus 或 daemon。通用平台独立层属于同一类。

## 5. 能力表

吸收后 D002 用本表标明「允许出现 `os.name` / `sys.platform` 的 owner」。未列入的产品编排不得新增宿主分支。

| 能力 | Owner | 调用方学习的表面 | Host 差异停在哪里 |
| --- | --- | --- | --- |
| Host→exact target | `host_target()`（`project.py`） | `str` target；cli 每 invocation 最多一次 | `sys.platform` / machine / libc |
| 进程组、超时/中断停止、Windows 子进程 PATH | `SubprocessRunner` | `ProcessRunner.run(ProcessSpec) → ProcessObservation` | POSIX `setsid`+`killpg`；Windows 进程树 terminate；`CreateProcess` 不读子进程 PATH 时的 `which` |
| 安全日志目录 | 私有 `SecureLogDirectory` | `RunLogStore` | POSIX dir_fd；Windows handle/DACL（已有） |
| 临时目录关闭 | `SnapshotBuilder` / `EnvironmentFactory` 的 `close()` | `close()` | NTFS sharing violation 重试 |
| venv 解释器路径 | `EnvironmentFactory` | `PreparedEnvironment.interpreter` | `bin/python` 与 `Scripts/python.exe` |
| ty 用户配置根 | 静态 module 内 `TyConfigurationResolver` | 公开 Evaluator 表面不增加 platform 参数 | `APPDATA` vs `XDG_CONFIG_HOME`/`HOME`；`platform=` 是静态 implementation |
| git global ignore 发现 | 静态 module 内 ignore 采集 | 同上，经 Evaluator | 非 POSIX 上现行 fail-closed（不在本文改成新发现语义） |
| PEP 508 四平台字段 | `pf.markers` | `platform_marker_facts(target)` | **只读 target**，不读 Host |
| wheel / uv platform tag | `UvAdapter` | candidate / install 公开结果 | 按 Cell target 匹配，不按 Host |
| apply selector 展示 | `pf.terminal` | D006 标签 | `win32`/`darwin` 的显示别名，不是 Host 探测 |

E010 已把 `killpg`、PATH `which`、临时目录重试放进上表对应 owner。吸收后用 §8 AC7 核对：编排模块零命中，其余命中都能指回本表。

产品编排（不得出现宿主分支）：`cli.py` 的 handler 逻辑、`workflow.py`、`verification.py`、`search.py`、`coordinate_search.py`、`evaluation.py`、`check.py`、`baseline.py`、`failure.py`、`report.py`、`authorization.py`、`editor.py`、`scheduling.py`、`candidates.py`、`search_space.py`、`markers.py`、`harness.py`。`cli.py` 只调用 `host_target()` 并向下注入，自身不为进程组或路径布局分支。

静态叶子在 D039 吸收前仍是静态 implementation；它们可以读 `os.name`，但不得把 platform 参数加到 Check/Highest/Search 的公开构造器上。

## 6. 测试

种类、车道与 D041 正交。本文件只增加 Host / Cell 如何展开，不改 marker 集合、自举 `C` 或覆盖率门槛。

| 要证明的事 | 怎么测 | 不做什么 |
| --- | --- | --- |
| Cell 契约（marker、projection、apply、admission） | 进程内对 linux / darwin / win32 family **注入 target**，本机 OS 不参与 | 不为「当前是 Linux」skip 掉 Windows Cell 断言 |
| 本机可执行哪些 Cell | 注入 `host_target`；D008 过滤 | 测试里改全局 `sys.platform` 冒充 Host |
| 进程停止、安全目录、清理 | owner 公开表面的 **一份** portable 契约（组被停掉、目录 fail-closed、`close()` 成功或规定的残留策略） | 每个 workflow 复制三套 OS 用例 |
| POSIX vs Windows adapter 协议 | 直接打内部 adapter，标 `infra`（与 D041 `_secure_runlog` 句一致） | 产品测试进口 `_secure_runlog` |
| 真实 Host 行为 | `process` / `e2e` / `qualification` 跑在真实 OS 上；CI 增加 `matrix.os` 只扩大覆盖率并集 | 用 PAL / fake Host 关闭 R010 |

`skipif(os.name == ...)` 只用于**本 Host 不具备被测能力**的用例（symlink、FIFO、Windows DACL）。禁止用它跳过「某个 Cell target 的产品规则」。

增加 Windows / macOS CI job 时：产品测试文件不乘三；该 job 执行同一收集，并补上本机才能走到的宿主私有分支。资格矩阵按工具×Host 扩展属于 R010，不是本文件的交付。

## 7. 建议切片

接受后另写 Plan。建议顺序（不授权现在实施）：

| 顺序 | 工作 | AC |
| --- | --- | --- |
| S1 | 吸收 §3–§6 进 D002 / D007 / CONTEXT / `tests/README.md`；索引与 D041 种类句并存 | AC1–AC6、AC9、AC10 |
| S2 | `os.name` / `sys.platform` 对照 §5；编排模块清零；错位分支移回 owner，不新建包 | AC7 |
| S3 | 测试：Cell 矩阵确认注入 target；禁止清单上的 `skipif`；adapter 协议在 `infra` | AC8 |

S2 若核对后已全部落在 §5，则不改生产行为，只保留扫描证据。E010 未产品化的控制台编码不在本 Plan 处理。

## 8. 验收标准

| AC | 必须成立的目标 | 公开 seam / 证据 |
| --- | --- | --- |
| AC1 | D002 §1 写明：Host OS 差异留在能力 owner 内部；禁止通用平台独立层 / `pf.platform` / 跨能力 HostFacts；内部 seam 仅当同一能力已有两个真实 adapter；Darwin 默认随 POSIX | D002 正文 |
| AC2 | D002 含 §5 能力表（或与之等价的 owner 表）及编排模块禁分支名单 | D002 §8 / §1 |
| AC3 | D007 仍以 `ProcessRunner` / `RunLogStore` 为公开表面；POSIX/Windows 停止路径与安全目录是内部差异；无 public `ProcessPlatform` | D007 正文；`src/pf` 包布局 |
| AC4 | CONTEXT 有 **Host**；Cell 仍 Avoid `platform`；Host Avoid `Cell` / `platform` / `Environment` | CONTEXT.md |
| AC5 | D002 §11 与 `tests/README.md` 含 §6 的 Host/Cell 展开句；覆盖率仍为 canonical Python 全量收集在各 CI OS 上的并集，`fail_under = 90` 不变 | 两份正文；`tests/README.md` 覆盖率段 |
| AC6 | `src/pf` 无 `platform` / `host` 包，无通用 Filesystem/PAL module | 包布局 |
| AC7 | §5 编排名单中的生产文件不含 `os.name` / `sys.platform`；`src/pf` 其余命中均能指回 §5 某一 owner | `rg` 对照表 |
| AC8 | 产品 Cell 契约测试不因本机 OS `skipif`；现存 symlink/FIFO 类 skip 仍只绑在能力缺失上 | `tests/` 扫描 |
| AC9 | D001 五字段与「不取 Host 默认值」、D008 host Cell admission、D006 selector 展示正文不因本文改写；R010 真实 host 资格仍开放 | 范围内 diff |
| AC10 | 若 D041 已吸收或仍为草案，`tests/README.md` 同时保有 D041 种类句（或其现行等价）与本文件 §6；不以本文件覆盖种类表 | `tests/README.md` |

停止条件（任一成立则未交付）：新增 `pf.platform`、HostFacts 或等价浅层并让 search/evaluation/workflow 依赖它；marker / wheel 改读本机 OS；为三端复制三套产品测试；用 PAL 替身宣称真实 Host 已资格化；无独立实现却把 Darwin 拆成第三 adapter；把 E010 清理耗时或 GBK 绕过写成 D001/D006 产品承诺；只改文档不跑 AC7 扫描却声称结构已落地。

## 9. 明确不做

- 改变可执行 Cell 集合、host-partial merge 或 apply 授权。
- 为 Windows 补齐 git global-ignore / POSIX-only 静态发现，或把现行 fail-closed 改写成「已支持」。
- 把 `host_target()` 扩成 Host 能力服务定位器。
- 用本文件关闭 R006/R008/R010 的其它开放项。
- 要求 macOS 在 POSIX adapter 之外再有一份仅因品牌不同的实现。

## 10. 接受状态

草案。接受后状态改为「已接受待实施」，再写覆盖 AC1–AC10 的 Plan；Plan 完成并吸收进 D002、D007、CONTEXT 与 `tests/README.md` 后，本文与 Plan 一并归档。本文不授权改生产代码或测试。

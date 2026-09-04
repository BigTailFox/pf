# D026 — 按命令装配 capability graph 与 Ctrl+C 稳定终态

- **状态：** 已完成、已归档
- **日期：** 2026-09-04
- **最后修订：** 2026-09-04
- **性质：** 临时迁移 Design；稳定规则已归并到现行 owner，本文不再承担规范性
- **评审来源：** [R006](../../reviews/R006-pf-cli-system-review.md) §4、§6，
  [R007](../../reviews/R007-pf-current-improvement-priorities.md) §4、§5
- **产品边界：** [D001](../../designs/D001-pf.md)
- **实现结构：** [D002](../../designs/D002-pf-implementation.md)
- **CLI 展示：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **进程生命周期：** [D007](../../designs/D007-pf-process-output.md)
- **实施计划：** [P032](../plans/P032-pf-command-composition-and-interrupt.md)

本文曾拥有两件独立验收事项，稳定规则已归并 D001/D002/D006/D007：

1. 在唯一 `cli.py` root 内按命令装配 capability graph；
2. 用户中断的数值退出、唯一 final、child 停止与报告落盘规则。

不得把第 2 项借第 1 项的重构暗中加入。D008 聚合、D014 wire、apply authority 与命令语义不变。

## 1. 问题与目标

`main()` 在 Cyclopts 解析前调用 `build_context()`，为 `--help`、`--version`、`explain`、
`diagnose`、`merge` 也构造 RegistryAccess、UvAdapter、评价图、SearchCoordinator 与七个
workflow。离线路径必须认识完整在线验证图。composition 期间的预期 `PfError` 位于
`with build_context()` 之外，不进入 `TerminalPresenter.render_error()`。

`main()` 只捕获 `PfError` 与 `CycloptsError`。`KeyboardInterrupt` 没有 PF 退出码、stderr
final、in-flight process-group 停止规则或 report 落盘规则，也可能展示 traceback。D001 现行
退出码只有 `0..4`；用户中断不能映射为基础设施退出 `4`。

目标：

- help/version 与离线命令不再构造它们不消费的能力；
- composition-time 预期失败与运行期预期失败走同一 typed 结果表面；
- Ctrl+C 得到稳定退出 `130`、唯一 stderr final、无 traceback，并停止 in-flight children。

## 2. Composition 形状

比较两种形状：

| 形状 | 调用方必须知道的 | 删除测试 |
| --- | --- | --- |
| 最小 bootstrap + 同一 root 内按命令装配 | handler 只请求本命令的 typed workflow | 删除后，UvAdapter/评价图知识回到每个命令路径 |
| 七槽 `CliContext` 上的惰性 provider | 每个调用方仍看到七个 workflow 槽位 | 七个 workflow 仍然同时存在于 interface 上，没有 depth |

选择第一行。拒绝 DI framework、service locator、`get(name)`、module bundle，以及作为生产
interface 的七个 optional workflow 字段。

### 2.1 Bootstrap

`build_context()` 只建立 `RunLogStore` 与 `TerminalPresenter`。parser 由随后的
`create_app(context)` 注册；help/version 只使用 parser 与 presenter，不请求任何 workflow。
装配失败时关闭已创建资源。`cli.py` 仍是唯一生产 composition root。

### 2.2 命令 graph

同一 invocation 内，root 用 private 缓存共享子图；handler 只调用本命令的装配入口。

| 命令 | 装配 | 不得构造 |
| --- | --- | --- |
| help / version | parser + presenter | UvAdapter、host target、评价图、SearchCoordinator、任一 workflow |
| explain | `ProjectDiscovery` + `ReportStore` | UvAdapter、host target、评价图、SearchCoordinator |
| diagnose | discovery + reports + 已有 `RunLogStore` | 同上 |
| merge | `ReportStore` | 同上 |
| apply | process runtime、UvAdapter、`ProjectLoader`、`SnapshotBuilder`、reports、Authorizer、Editor | static/runtime evaluator、SearchCoordinator、`host_target`、VerificationRunner |
| check / smoke | apply 的 planning 子图 + 评价图 + VerificationRunner（`host_target` 一次） | SearchCoordinator（smoke/check 不需要） |
| search | planning + 评价图 + `SearchCoordinator` + VerificationRunner + reports + builder；association 直接使用 `RunLogStore` | — |
| minimize | 顺序复用 search 与 apply 的已缓存子图 | 不装配第二份 uv/评价图 |

`host_target()` 只在第一次装配 VerificationRunner 时探测一次。`RegistryAccess` /
`SubprocessRunner` / `UvAdapter` 只在 apply 或在线验证命令首次需要 process runtime 时建立。

生产 `CliContext` 构造时不要求七个 workflow 存在。测试可向同一类型注入已构造的 command
workflow，以免 CLI 单测触发真实装配；这不是生产 interface。

### 2.3 错误边界

`main()` 在 bootstrap 之后、命令 handler 之内捕获预期失败。handler 在解析后才请求 graph，
因此 composition-time `PfError` 与 workflow `PfError` 都进入
`presenter.render_error()`。已建立资源只关闭一次。内部 module 仍不调用 `sys.exit()`。

### 2.4 FailureLogAssociations

`FailureLogAssociations` 只有 `RunLogStore` 一个生产 adapter，Search workflow tests 已使用
真实 `RunLogStore`。删除该 Protocol，由 `SearchCommandWorkflow` 直接接收可选
`RunLogStore`。`DiagnosisLogLocator` 保留：它另有 recording test adapter。

## 3. 中断语义

用户中断（SIGINT / `KeyboardInterrupt`）退出 `130`。它不是 D008 命令聚合结果，不是
CellResult，也不是基础设施退出 `4`。

### 3.1 阶段终态

| 阶段 | 退出 | final | 持久化 |
| --- | --- | --- | --- |
| 解析前 / bootstrap，尚无 presenter | `130` | 无 | 关闭已创建 logs |
| 装配中 | `130` | stderr `⚠ Interrupted` | 不写 `package-floor.json` |
| 运行中 | `130` | 同上 | 不合成 search/apply 产物；已落盘的 Journal/Process Log 保留 |
| 持久化未提交 | `130` | 同上 | 保留提交前字节；不删除已有合法报告 |
| 持久化已提交或命令 final 已发出 | 见下 | 不发出第二个 final | 已提交 artifact 保留 |

若唯一命令 final 尚未发出，Presenter 发出 interrupt final 后退出 `130`。若命令 final 已发出，
不再打印 `Interrupted`；若中断发生在 handler 返回之后的 close 中，保留该命令退出码并无
traceback。运行中或未提交持久化阶段的中断仍是 `130`。

### 3.2 Child / process-group

`SubprocessRunner` 跟踪 in-flight `Popen`。中断时对每个 tracked process 复用 timeout 的
停止路径：process group 则 `SIGTERM` → grace → `SIGKILL` 并等待回收。被中断的 child 不向
search/failure owner 提供作为证据的 `ProcessObservation`；CLI abort 不是 Rejection 或
Indeterminate Cell。已写入的 Process Log 片段保留，不回溯删除。

`CliContext.interrupt_processes()` 在 runner 尚未装配时为空操作。中断标志置位后，新的
`run()` 立即再抛 `KeyboardInterrupt`，避免 shutdown 中启动新 child。

### 3.3 再次中断

`close()` 与 `interrupt()` 必须在嵌套 `KeyboardInterrupt` 下仍完成资源关闭，不展示
traceback，不把退出改成 `4`。不安装忽略 SIGINT 的永久 handler。

### 3.4 展示

D006：interrupt final 走 stderr，warning 标记 `⚠`，正文 `Interrupted`。TTY 与非 TTY 同一句，
非 TTY 无控制序列。不发卡，不输出 exception type 或 traceback。每个顶层命令仍然只有一个
final，且它是最后一条结果信息。

## 4. 验收标准

Composition（独立）：

1. `cli.py` 是唯一生产 composition root；不引入第二个 root、DI framework、service locator
   或七个生产 optional workflow 槽位。
2. `--help` / `--version` 不构造 UvAdapter、不探测 host target、不构造评价图或
   SearchCoordinator。
3. `explain`、`diagnose`、`merge` 不构造 UvAdapter、host target、评价图或 SearchCoordinator。
4. `apply` 不构造 static/runtime evaluator 与 SearchCoordinator，不探测 host target。
5. `check`/`smoke`/`search`/`minimize` 按 §2.2 装配；`host_target()` 每 invocation 最多一次；
   minimize 共享已缓存子图。
6. composition-time 预期 `PfError` 进入统一 typed error 表面，无 traceback；已建立资源只关闭一次。
7. 删除 `FailureLogAssociations`；Search association 仍从 workflow public behavior 覆盖。

Interrupt（独立）：

8. 解析前、装配中、运行中、未提交持久化的用户中断退出 `130`，不得映射为 `4`。
9. 运行中中断停止并等待 in-flight process groups；不合成 `package-floor.json`；已提交
   artifact 与已有 Process Log/Journal 保留。
10. 若命令 final 尚未发出，stderr 恰好一个 `⚠ Interrupted`；TTY/non-TTY 无 traceback；非 TTY
    无控制序列。
11. 嵌套中断仍关闭资源且无 traceback。
12. 公共 presenter/CLI/process tests 覆盖 §4.8–11，不断言 private helper 名称。

归并：

13. 稳定规则归并 D001（退出 `130` 与落盘）、D002（composition）、D006（interrupt final）、
    D007（in-flight 停止）；关闭 R006/R007 对应项；本文与 P032 在同一完成变更中归档。

## 5. 非目标

- 报告路径 owner、非 TTY 搜索活动、ResultCardEmitter、`--json`/`--quiet`/`--root`；
- 改变 D008 聚合、deadline/`--max-duration` 的 `INDETERMINATE`、host-partial 退出 `0`；
- 把 interrupt 写成 CellResult、FailureRecord 或 report reason；
- 删除 `DiagnosisLogLocator` 或其他有两个真实用途的 Protocol；
- 为 help 省略 `RunLogStore`（presenter 仍按现行方式持有 logs）。

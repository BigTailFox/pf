# D027 — 报告路径规则单一 owner

- **状态：** 已完成、已归档
- **日期：** 2026-09-04
- **最后修订：** 2026-09-04
- **性质：** 临时迁移 Design；稳定规则已归并到现行 owner，本文不再承担规范性
- **评审来源：** [R007](../../reviews/R007-pf-current-improvement-priorities.md) §6
- **实现结构：** [D002](../../designs/D002-pf-implementation.md)
- **CLI 展示：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **报告 wire：** [D014](../../designs/D014-pf-report-schema.md)
- **实施计划：** [P033](../plans/P033-pf-report-path-ownership.md)

本文曾拥有一件验收事项：包默认 `package-floor.json` 位置规则只有一个 owner，已解析路径值沿现有
planning 与 command-result seam 传递。Search/Apply/Explain/Diagnose/Terminal 只消费路径值。

不得建立 public path module、repository 或 locator registry。绝对 filesystem path、当前
checkout root 与 display path 不进入 Schema 1、report identity、Journal 或 merge。

## 1. 问题与目标

`ProjectDiscovery` 已在 `PackageLocation.report_path` 建立 `package_root / "package-floor.json"`。
Search 与 Apply 仍从 `root + pyproject_path.parent + 文件名` 重算读写路径；`TerminalPresenter` 与
explain 视图再从 `ValidatedReport.package.pyproject_path` 重算展示路径。四处调用方都学习了文件名、
package-relative location 与 root-relative display 的关系。

在线 workflow 只持有 `ProjectPlan`；Presenter 只持有报告；Explain 成功路径也不携带读取路径。
因此不能只要求调用方去读 `PackageLocation.report_path`。目标是让已解析路径值成为 planning result
与 command result 的一部分。

## 2. 路径值流

报告位置规则的唯一 owner 是 `ProjectDiscovery`。catalog 对每个 installable package 物化

```text
PackageLocation.report_path = package_root / "package-floor.json"
```

这是绝对 filesystem `Path`，只用于本机 I/O，不是 Schema 字段。

| 消费者 | 取得的值 | 用途 |
| --- | --- | --- |
| `ProjectLoader` | `inventory.target.report_path` | 写成 `ProjectPlan.report_path`（root-relative posix） |
| `ExplainCommandWorkflow` | `location.report_path` | I/O；成功/失败 command result 携带同一 root-relative display |
| `DiagnoseCommandWorkflow` | `location.report_path` | I/O；report 来源的 `FailureDiagnosis.source_path` 为同一 display |
| `SearchCommandWorkflow` | `project.report_path` | `root / report_path` 写入；`SearchCommandResult.report_path` 原样携带 |
| `ApplyCommandWorkflow` | `project.report_path` | `root / report_path` 读取 |
| `TerminalPresenter` | Search/Explain command result 的 `report_path`，Diagnose 的 `source_path`，Merge 的显式 input/output | 展示；不从 `pyproject_path` 重建 |

`ProjectPlan.report_path` 是 invocation-local planning 值，与 `owned_pyproject_paths` 同类，不进入
`PackagePlan`、Schema 1 或 report identity。Merge 继续只消费命令 request 的显式路径，不被 package
默认路径覆盖。

### 2.1 Command result

```text
SearchCommandResult  = report + report_path
ExplainCommandResult = report + report_path
```

两者与现有 `MergeCommandResult` 一样是 workflow 的 frozen command result，不是 wire record。
`report_path` 是 root-relative posix display，与 Explain 失败的 `ExplainReportError.report_path`
同一类值。`ApplyCommandResult` 不增加路径字段：Apply 只消费 planning 路径做读取，展示不包含
报告路径。`render_minimize` 仍消费 search report 的验证结论做 host-partial remainder，不重建
报告位置。

### 2.2 Presenter

`render_search` / `render_explain` 接收对应 command result。search summary 与 explain overview
的 artifact 路径等于 result 携带的值。删除从 `ValidatedReport.package.pyproject_path` 拼接
`package-floor.json` 的重建。Diagnose 在 `source == "report"` 时只渲染 `source_path`，不回退到
文件名。

## 3. 验收标准

1. `package-floor.json` 相对所选 package root 的位置规则只出现在 `ProjectDiscovery` 对
   `PackageLocation.report_path` 的物化；不存在第二个 public 公式或 path module。
2. `ProjectLoader.load` 把已解析的 root-relative 报告路径写入 `ProjectPlan.report_path`；
   Search/Apply 只用该值拼接 `request.root` 做读写。
3. `SearchCommandWorkflow.run` 返回 `SearchCommandResult`；`ExplainCommandWorkflow.run` 返回
   `ExplainCommandResult`；两者的 `report_path` 来自 discovery/planning，不来自 report identity。
4. `TerminalPresenter.render_search` / `render_explain` 展示 command result 的 `report_path`；
   即使该值与从 `report.package.pyproject_path` 推导的位置不同，仍显示 result 携带的值。
5. Explain 失败、Diagnose 的 report 来源继续携带同一 display 值；Presenter 不从文件名或
   `pyproject_path` 补全缺失路径。
6. Schema 1、report identity、Journal、merge 输入输出与 snapshot 排除名单不增加绝对路径、
   checkout root 或 display path 字段。Merge 显式 request/result 路径不变。
7. workspace root/member 选择与离线 `ProjectDiscovery.select` seam 不变。
8. 公共 planning/workflow/presenter tests 覆盖 root 与 workspace member 的路径值传递，以及
   Presenter 消费携带路径；不断言 private helper 名称。
9. 稳定规则归并 D002（planning/command result/Presenter 不得反推路径）与 D006（展示消费
   command-result 路径）；关闭 R007 §6；本文与 P033 在同一完成变更中归档。

## 4. 非目标

- 新的 public path module、repository、locator registry 或 helper-only 补丁；
- 改变报告文件名的产品含义、Schema 1、apply authority 或 merge identity；
- 为 Search/Explain 增加 `--output` / `--report`，或让 Merge 改走 package 默认路径；
- 报告预检、非 TTY 搜索活动、ResultCardEmitter。

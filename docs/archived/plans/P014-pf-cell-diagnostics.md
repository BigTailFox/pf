# P014 — Cell 诊断降噪与可扩展结构化详情

- **状态：** 已归档（已完成）
- **日期：** 2026-08-25
- **展示契约：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **进程日志：** [D007](../../designs/D007-pf-process-output.md)
- **验证运行：** [D008](../../designs/D008-pf-verification-run.md)
- **pytest witness：** [D013](../../designs/D013-pf-pytest-observer.md)

## 1. 目标

`pf smoke` / `pf check` / `pf search` 的每个 cell 只展示调查入口所需的信息：失败阶段、D005 failure title、至多一个结构化典型详情、剩余数量和可用的 `pf diagnose` 命令。普通 cell 卡片不展示 baseline ty warning、role impact、stdout/stderr tail 或 Process Log 链接，也不跨 cell 聚合诊断。

结构化详情是运行时、非权威、非持久化的 discriminated union。首批 variant 为 pytest failure 与因 runtime witness 确认缺失而导致拒绝的静态 issue；以后新增 stage 典型错误时扩展 variant，不让 Presenter 解析工具输出或认识 adapter 私有协议。

`pf diagnose` 保留 title、impact、next step、context 和 technical facts，并从安全 `RunLogStore` 读取 stderr（若非空）否则 stdout 的最后 3 个非空行，再显示完整 Process Log 链接。

## 2. 非目标

- 不改变 TestOutcome、FailureRecord、disposition、report、cache 或 policy identity；
- 不把 pytest nodeid 或静态详情写入 Verification Journal / `package-floor.json`；
- 不建立跨 cell 聚合、命令级诊断模型或通用终端预算；
- 不展示普通 static regression、baseline ty warning、ty tool failure 或与最终 runtime witness 无关的 static increment；
- 不改变 `explain` 既有的诊断折叠与条数上限。

## 3. 运行时详情接口

`CellResultDetail` 是 typed discriminated union：

```text
PytestFailureDetail(kind="pytest-failure", first=(nodeid, phase), total)
StaticIssueDetail(kind="static-issue", first=TyDiagnostic, total)
```

`TestFail` 可以携带 `PytestFailureDetail`，但该字段必须从 model dump 排除。`completion_outcome` 是 Evaluation/command result 到 `CellResultDetail` 的唯一投影所有者；`CellFailed` 与 `CellPresentation` 只传递详情，Presenter 只按 variant 渲染。

pytest detail 缺失、非法、超限、写入失败或 cleanup 失败只能省略详情，不能改变 `TestPass | TestFail | ToolFailure`。static detail 只从 `RuntimeInterfaceMissingEvaluation` 的最终 `CONFIRMED_MISSING` witness plan 选择其 `diagnostic_identities` 覆盖的 incremental issue；同一 cell 显示第一条及剩余数量。

## 4. pytest UI detail protocol

standalone plugin 在现有 authoritative witness 之外写独立 `pf-pytest-failure-details-v1` canonical artifact。artifact 只包含其 run nonce、第一个失败 nodeid/phase 与 distinct failed nodeid 总数；同一 nodeid 在 setup/call/teardown 多次失败只计一个并保留首次 phase。adapter 有界枚举并严格验证目录，只采用唯一匹配当前 nonce 的 artifact，忽略嵌套 pytest 的合法其他 nonce artifact；同 nonce 重复、非法或超限一律省略详情。目录、文件数、文件大小、nodeid 和计数都有界；原子提交失败静默降级。

该协议属于 Diagnostic Metadata，不参与 D013 outcome classifier。`TestAdapter.run(...) -> TestOutcome` 仍是唯一外部 interface；RuntimeEvaluator、FailurePolicy 与 CoordinateSearch 不学习 pytest detail protocol。

## 5. 普通 cell 与 diagnose 输出

普通非成功 cell 的稳定顺序为：

```text
cell title + failed at stage + elapsed
failure title
first structured detail
... and N more
pf diagnose entry
```

没有结构化详情时省略对应两行。Journal/index 写入成功时显示精确 diagnose 命令；写入失败时不得显示悬空 failure ID，只显示可用的 Process Log 链接；若连日志也不可用，显示 `Detailed diagnosis unavailable.`。

`pf diagnose` 从已由 Diagnosis Index 安全定位的 length-framed Process Log 流式读取输出，优先 stderr，否则 stdout，只在内存保留最后 3 个非空行，移除 C0/C1 与 ANSI/OSC 终端控制序列并以纯文本展示，随后给出完整日志链接。Presenter 不直接打开路径；已定位日志损坏、v1 marker 歧义或读取失败时显式报错，不伪装成合法空输出。

## 6. 自适应终端

默认 CLI 外层 Console 最多 120 列，窄于该值时跟随实际终端；TTY 卡片充满这张画布。内部 Panel、Table、detail、列宽、bar width、摘要字符宽度和 breakpoint 不硬编码，继续由 Rich 自行换行与测量。测试可以构造固定宽度 Console 验证 56/80/120 列以及超宽终端的 120 列上限。

## 7. TDD 顺序

1. pytest detail：公开 `TestAdapter.run` 覆盖 collection/setup/call/teardown、多 phase 去重、缺失/非法/超限/写入失败及序列化排除；
2. completion detail：pytest first+count、confirmed-missing causal static issue、TestFail+static regression 不错误归因、成功 baseline warning 隐藏；
3. terminal：TTY/non-TTY 每 cell 一块、无 impact/tail/普通日志链接、有 detail/count/diagnose、Journal 失败才回退日志；
4. diagnose：安全日志读取、stderr 优先、最后 3 个非空行、完整日志链接；
5. layout：56/80/120 列与生产代码固定 width 审计；
6. 聚焦 pytest、Ruff、ty、Python 3.10–3.12 全量 pytest、build/wheel/plugin smoke；把结果记录回本文。

## 8. 实施记录

- **运行时接口：** 新增可扩展 `CellResultDetail` discriminated union，首批承载 pytest failure 与 confirmed-missing witness 的 causal static issue；`TestFail`、`CellFailed` 和 search event 保留精确 failure 关联，所有 detail 字段都从 model dump 排除，不进入 Journal、报告或 cache。
- **pytest detail：** standalone plugin 写 canonical `pf-pytest-failure-details-v1`；adapter 在 authoritative `TestFail` 之后 best-effort 读取。真实 PF 自测发现嵌套 pytest 会留下其他 nonce 的合法 artifact，因此 reader 最终采用惰性 `os.scandir`、1024 文件上限、全目录严格验证、唯一当前 nonce 选择；同 nonce 重复、非法或超限只省略详情。plugin 与 runtime schema 同时拒绝 C0/C1、DEL 和未配对 surrogate，避免可选 nodeid 破坏终端。
- **终端与 diagnose：** 普通 cell 只展示失败阶段、D005 title、至多一个结构化详情/count 和 diagnose 入口；移除 baseline ty warning、impact、tail 与普通 Process Log 链接。`diagnose` 通过安全 RunLog seam 流式读取 stderr（否则 stdout）最后 3 个非空行，以 literal `Text` 展示并链接完整日志。Process Log v2 用字符长度 framing；v1 兼容 reader 对 stdout/stderr marker 歧义 fail-closed。
- **布局：** 默认生产 Console 的外层画布最多 120 列，内部 renderable 不固定 width、height、列宽、bar width 或 breakpoint。spinner/title 与 determinate `dynamic tests`/bar 都固定一个空格；detail/stage 对齐 cell title 的 `[`，identity 长度变化不移动 elapsed，最后一个结构化 identity 以相同 version 样式保留在 TTY 冻结块。Reason 使用与 cell 结果相同的标准色且不 bold/bright；diagnose 入口使用默认前景色的 dim italic。整体 cell 进度按 matrix 顺序显示可换行的 `□`/`■`，每个完成方块使用对应结果色。
- **验证：** P014 聚焦回归 `349 passed`；全仓 Ruff 与 ty 通过；最终联网 `pf smoke` 的 CPython 3.10、3.11、3.12 各 `1 failed, 861 passed`，三张 cell 均展示同一真实首条 report qualification failure，没有 ty issue 或输出 tail。唯一全量失败是并行 D014 工作尚未提供 `tests/fixtures/report-schema/pf-self-search-inline.json`，不属于 P014。
- **终端 follow-up 验证：** terminal/CLI 无 testmon 聚焦回归 `113 passed`，Ruff 规则检查与 ty 通过。全量 pytest 为 `893 passed, 3 failed, 1 skipped`：3 项均因沙箱禁止联网而 indeterminate；没有 terminal/CLI 回归。公开 TTY 测试覆盖 120 列外层上限、稳定 elapsed、单空格与左对齐、冻结 identity、Reason 结果色、dim italic diagnose hint，以及方块位置、四类结果色和窄屏多行折叠。
- **构建与审查：** sdist/wheel 构建与 standalone plugin wheel smoke 通过；最终 Standards/Spec 复审均无 Blocker 或 Important。
- **已知外部边界：** 当时根目录未跟踪的开发期旧内联 `package-floor.json` 会让 `pf diagnose` 在读取最新 Journal 前被并行 D014 reader 拒绝；P014 的 journal/process-log diagnose 路径已由聚焦测试覆盖，本计划不修改 D014 owner。P019 在首次发布前把现行引用图定名为 Schema 1；这条历史记录不代表兼容版本。tail reader 不物化整份日志，但在没有通用终端预算的契约下仍需在内存保留一个完整逻辑行。

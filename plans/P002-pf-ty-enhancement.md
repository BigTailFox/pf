# P002 — PF ty 增量静态检查实施计划

- **状态：** 已完成
- **完成日期：** 2026-08-20
- **设计来源：** [D004](../docs/designs/D004-pf-ty-enhancement.md)
- **起始提交：** `36bcd2f80626204fbc56204a388d4c5b6f9876ac`
- **方法：** 纵向 TDD（每次一个公开行为：RED → GREEN → 必要时重构）

## 1. 目标与验收口径

本计划只实现 D004 对静态检查语义的修订，不改变 D001/D003 的候选策略、坐标搜索顺序或完整测试判据。

完成时必须同时满足：

1. `TyAdapter` 固定 GitLab JSON 输出，拒绝 adapter 所有选项的用户配置冲突，并把一次成功运行规范化为 `TyCheck`；退出码不再代表静态兼容性。
2. `StaticEvaluator` 冻结每个 cell 的最高版本诊断基线 `S_hi`，以稳定身份执行多重集相减，并产生带 baseline digest 与增量证据的静态评估。
3. `pf check` 先建立最高版本 `V_hi`/`S_hi`，关闭该环境，再用同一 `StaticEvaluator` 评估 `lowest-direct` 的 `V_check` 并运行完整测试。
4. `pf search` 捕获一次 `S_hi`，复用该次 `TyCheck` 作为 baseline 的静态通过证据，并把同一冻结基线注入所有 static/full probe。
5. 公共报告保存 `V_hi`、原始规范化诊断、基线 digest、捕获进程摘要以及 `STATIC_FAIL` 的非空增量；新策略身份能阻止旧语义证据 merge/apply。
6. `PASS` 仍要求完整测试通过；解析失败、截断输出、字段缺失、冲突参数和工具失败全部 fail closed 为配置错误或非证据状态。

## 2. 模块与接口决策

### 2.1 `TyAdapter` 深模块

接口只暴露一次 `check(...) -> TyCheck | ToolFailure`。实现内部拥有 argv、项目 ty terminal 配置冲突检查、GitLab JSON 解析、路径归属判断和稳定身份规范化。调用方不读取 stdout、fingerprint 或退出码来决定兼容性。

### 2.2 `StaticEvaluator` 深模块

接口提供两个生命周期动作：

- 捕获最高版本环境，返回冻结的 `StaticBaseline` 和它的自比较 `STATIC_PASS` 证据；
- 使用显式 `StaticBaseline` 评估任意 proposal，返回静态通过、静态失败或非证据状态。

这条 seam 是 check 与 search 的共同测试面。多重集比较不进入 workflow、search 算法或 adapter。

### 2.3 编排所有权

- `CompatibilityChecker`：`highest` 捕获 → 关闭 → `lowest-direct` 完整评估。
- `SearchCoordinator`：`highest` 捕获一次 → 复用自比较证据跑 baseline 测试 → 将同一基线交给 proposal runner。
- `FullEvaluator`：只在相对基线静态通过后运行测试，不选择或刷新基线。
- `CoordinateSearch`：继续只消费状态和 proposal id。

## 3. 纵向 TDD 切片

| 切片 | 公开行为 | RED | GREEN / 完成条件 | 状态 |
| --- | --- | --- | --- | --- |
| 001 | `TyAdapter` 固定 GitLab JSON argv，并拒绝 argv / `[tool.ty.terminal]` 所有权冲突 | argv 缺少 `--output-format gitlab`；两类冲突均未抛错 | 3 个 focused tests 通过；冲突在 runner 调用前失败 | 完成 |
| 002 | `TyAdapter` 把 exit 0/1 的完整 JSON 规范化为确定 `TyCheck` | JSON、路径、字段和退出码矩阵测试 RED | snapshot/external 身份正确；消息、severity、fingerprint 不进入比较键 | 完成 |
| 003 | adapter 对截断、非法 JSON、缺字段与工具退出 fail closed | malformed fixture RED | 只允许 exit 0/1 + 完整合法 JSON 生成 `TyCheck` | 完成 |
| 004 | `StaticEvaluator` 捕获基线并执行多重集增量语义 | 重数、子多重集、空基线测试 RED | `STATIC_FAIL` 当且仅当增量非空；证据带 digest/增量 | 完成 |
| 005 | `FullEvaluator` 只在增量静态通过后运行完整测试 | evaluator 集成测试 RED | 静态失败短路；通过后保留 ty/baseline/test 三类机械证据 | 完成 |
| 006 | `CompatibilityChecker` 使用分离的 `V_hi` 与 `V_check` | workflow 顺序与资源关闭测试 RED | highest 只捕获基线且不测试；lowest-direct 使用同一 `S_hi` 完整评估 | 完成 |
| 007 | `SearchCoordinator` 捕获一次并向全部 probe 注入同一 `S_hi` | 既有诊断 baseline / probe 测试 RED | baseline 自比较不重跑 ty；既有诊断不造成 `BASELINE_FAILED` | 完成 |
| 008 | 报告与策略身份完整记录增量证据 | schema/report/merge/explain 测试 RED | cell 证据含 `V_hi`、`S_hi`、digest、进程摘要与增量；策略为 increment-v1 | 完成 |
| 009 | 文档、兼容性迁移与全量门禁 | README/design consistency 与全套命令 | focused tests、全套 pytest、`ty check src tests`、构建均通过 | 完成 |

## 4. 验证策略

每个切片只先写一个描述公开行为的测试，确认失败后写最小实现，再继续下一个行为。外部进程只在 `ProcessRunner` seam 使用 fake；不 mock PF 内部的诊断比较实现。

常规验证命令：

```text
uv run pytest <focused-test> -q --no-testmon
uv run ty check src tests
```

最终门禁：

```text
uv run pytest -q --no-testmon
uv run pytest -q --no-testmon --cov=pf --cov-report=term-missing
uv run ty check src tests
uv build
```

## 5. 实施日志

| 时间 / 切片 | RED 证据 | GREEN / 验证证据 | 结论 |
| --- | --- | --- | --- |
| 计划创建 | 不适用 | 已把 D004 拆成 9 个纵向切片 | 接口与行为以 D004 为批准依据；实现从 adapter collector tracer bullet 开始 |
| 切片 001 | 固定 argv 断言显示 index 2 缺少 `--output-format`；用户参数与 terminal 配置冲突均 `DID NOT RAISE` | `tests/test_ty_adapter.py` 对应 3 个 focused tests：3 passed | `TyAdapter` 成为 GitLab 输出与相关 CLI/config 选项的唯一所有者，冲突不启动进程 |
| 切片 002 | 新测试导入 `TyCheck` 时失败；旧 adapter 只能返回按退出码分类的 `TyPass` / `TyFail` | exit 0/1、snapshot/external、`positions.begin` / `lines.begin`、fingerprint/消息变化矩阵通过 | 退出码只判断工具是否完成；规范诊断 tuple 是静态比较的唯一事实源 |
| 切片 003 | `lines.begin`、非法 JSON、非数组、缺字段、截断与非对象记录最初不能形成预期的 fail-closed 结果 | adapter focused tests 覆盖退出 0/1/2/101/timeout、截断与损坏输出；外部路径按环境前缀/标准 marker/稳定 basename 归一 | 任一残缺记录使整次检查为 `TOOL_ERROR`；不静默丢诊断；project/config override 的下划线别名也被拒绝 |
| 切片 004 | `StaticBaselineCapture` 不存在，旧 evaluator 直接把 exit 1 当 `STATIC_FAIL` | 重复身份、消息变化、诊断消失、空基线、新增重数与 digest 测试通过 | `Counter` 多重集相减只保留新增次数；捕获时同一 `TyCheck` 同时成为 `V_hi` 自比较通过证据；跨 cell/snapshot/policy 基线先于 ty 执行被拒绝 |
| 切片 005 | `FullEvaluator` 没有 baseline 参数，无法表达相对检查；静态失败短路测试 RED | 静态失败不运行测试；静态通过后的 PASS / TEST_FAIL / TIMEOUT 与阶段事件测试通过 | FullEvaluator 不选择基线；只把同一静态通过 Proposal 晋升为完整测试证据 |
| 切片 006 | checker 只构建一次 `lowest-direct`，constructor 也没有独立 static capture seam | 顺序测试确认 `highest → close → lowest-direct`；真实最小本地包通过完整 check | `V_hi` 只捕获 `S_hi` 且不测试，`V_check` 才执行增量静态检查与完整测试 |
| 切片 007 | Search 的 Static/Full 协议没有 baseline，既有 typing 诊断令 baseline 静态失败 | coordinator 测试确认每 cell 只 capture 一次、baseline ty 不重跑、所有 probe digest 相同、既有诊断仍可搜索 | `SearchCoordinator` 冻结一次 `S_hi`；CoordinateSearch 仍只读取状态，不接触诊断比较 |
| 切片 008 | `CellSuccess` 无 `S_hi`，probe 只保存状态/proposal id，explain 无法展示增量；策略 hash 不含增量算法常量 | schema 拒绝缺少结构化 `STATIC_FAIL` 证据；explain 展示 baseline 计数与增量；策略含 `increment-v1` 四项常量且 jobs 不改变 identity | 报告保存原始 `TyCheck`、digest 与增量；旧成功报告因缺 `S_hi` 无法按新 Schema 读取，新旧策略 hash 不能混用 |
| 覆盖门禁修正 | 首次全量覆盖率为 89.17%，低于 90% 门槛 | 补充 D004 fail-closed / schema 不变量测试后：300 passed，coverage 90.55% | 未降低门槛；补的是用户可观察失败语义与证据一致性，不是实现细节占位测试 |
| 双轴 review 修正 | review 构造出 nested package 路径错位、dotted config 绕过、工具版本未入策略、跨基线/状态矛盾报告等反例 | 每项先补回归测试确认 RED，再修至 GREEN；两轮复核最终均为“无未解决 finding” | 相对路径以 ty 的 package cwd 解析；实际 ty 版本进入策略；报告把 baseline/final/probe 的 Proposal、TyCheck、digest、cell、snapshot、policy 锁为同一证据链 |
| 切片 009 | 最终门禁前曾有 5 个旧 fixture 使用伪造 snapshot digest，新的 fail-closed 报告校验正确拒绝 | fixture 改为真实 snapshot identity；`pytest --cov`：315 passed，coverage 90.43%；`ty check src tests`：All checks passed；`uv build` 成功 | D001/D002/D003/README 已与 D004 一致，无需重复修改；实现、迁移、解释输出和发布构建均完成 |

## 6. 风险与偏差记录

| 风险 / 偏差 | 处理 |
| --- | --- |
| ty 的 GitLab JSON 路径可能是相对 package 或绝对 proposal 路径 | 用真实本地 ty 输出确认格式；相对路径先以子进程 package cwd 解析，再相对 snapshot root 规范化；不凭消息文本猜身份 |
| 当前 `ProcessRunner` 默认只保留有限 stdout summary | adapter 显式请求足够且可检测的捕获；任何实际截断都返回 `TOOL_ERROR` |
| schema 变化会触及大量既有 fixture | 保持公共证据强类型且 fail closed；按切片机械迁移，不保留新旧双语义 |
| Rich 15 在非 TTY `StringIO` 测试中启动 live renderer 会产生控制序列竞态 | 仅在真实 `isatty()` 流启动 live renderer；非 TTY 继续消费同一事件并输出稳定最终文本 |
| baseline 被错误复用于其他 cell、源码快照或策略 | `StaticEvaluator` 在运行 ty 前校验三项 scope，错误基线无法产生证据 |
| jobs 是调度参数，不应污染策略身份 | 策略文档显式排除 `jobs`；测试确认 auto 与串行得到相同 identity |
| dotted config override 可绕过裸键冲突检查 | 对 override 的规范化末级键判定所有权，覆盖 `terminal.output_format` 与 `environment.python-version` |
| 报告中局部证据可能来自另一个 baseline 或与状态矛盾 | Schema 双向校验 probe 状态/结构类型，并将所有 baseline、final 和 search evidence 绑定到捕获的 `V_hi` / `S_hi` |
| 未跟踪的 `package-floor.json` 属于用户工作树 | 全程不修改、不暂存、不提交该文件 |

## 7. 完成检查

- [x] 9 个 TDD 切片均有 RED/GREEN 证据和结论。
- [x] D004 第 10 节全部不变量拥有实现或公开接口测试。
- [x] focused / full / coverage / ty / build 门禁通过。
- [x] 双轴 review 无未解决的阻断问题。
- [x] 提交只包含 D004、P002、实现与测试的相关改动。

## 8. 最终结论

D004 已完整落地为一条共享的增量静态证据链：每个 cell 只捕获一次 `V_hi` / `S_hi`，check 与 search 都由同一 `StaticEvaluator` 做诊断多重集相减，完整 `PASS` 仍必须经过测试。ty 退出码、消息、severity 和 fingerprint 均不承担兼容性语义；所有解析、配置、scope 与报告不一致均保守失败。新报告携带可解释、可校验的 baseline 与增量证据，并通过包含实际 ty 版本和 `increment-v1` 算法常量的策略身份与旧语义隔离。

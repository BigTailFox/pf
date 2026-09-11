# P048 — D044 check 稳态与最小验证序列实施计划

- **状态：** 已完成
- **日期：** 2026-09-11
- **对应 Design：** [D044](../designs/D044-pf-check-first-minimal-verification.md)
- **阶段：** S1–S3 已完成；稳定规则已吸收进现行 owner，本文件与 D044 同变更归档
- **修订基准：** `bd8e9ae2243e9c161871d1923f361cb0db774128`
- **流程与验证：** [AGENTS.md](../../../AGENTS.md)、[tests/README.md](../../../tests/README.md)、[文档索引](../../README.md)

本 Plan 只拥有切片、执行决定与证据；目标行为及非目标由 D044 唯一规定。2026-09-11 将回执、
专属 identity、报告参与 apply 事务及其跨命令协调撤出本轮。旧 S2–S4 和事务专属强杀/竞争
验收未实施，现移除；相应产品设想转回 C005，不保留为本 Plan 的隐藏后续阶段。

## 1. 当前状态与执行顺序

已完成：D044 收缩到 check 稳态与 Smoke/Check 最小序列；保留前几轮闭合的聚合、Role、
HarnessBaseline 命令分支、CLI 配置和阶段间中断规则；现有 apply 安全契约不变。

实施顺序：**S1 → S2 → S3**。三个切片均已完成。稳定规则已吸收；本文件与 D044 已归档。

| 切片 | 依赖 | 工作与完成边界 | AC | 证据槽 | 状态 |
| --- | --- | --- | --- | --- | --- |
| S1 验证命令改造 | 实施授权 | Smoke/Check 删除 static 依赖，Check 两分支、Role/context/聚合、CLI/request 与 Journal 一起迁移；Search 保持现行行为 | AC1–AC7 | E1 | 已完成 |
| S2 真实流程与既有 apply 回归 | S1 | 无报告的真实 check、重复验证、无 Git 生命周期和 minimize 集成；既有 apply 授权/NOOP/写入安全回归 | AC1、AC2、AC5、AC8、AC9 | E2 | 已完成 |
| S3 完整验收与吸收 | S1、S2 | 必需完整车道、逐项 AC 审计、双语摘要及 owner 吸收、Design/Plan 同变更归档 | AC1–AC10 | E3 | 已完成 |

## 2. 切片工作与公开接口

### S1 — 验证命令改造

涉及 `src/pf/harness.py`、`baseline.py`、`check.py`、`verification.py`、`workflow.py`、
`cli.py`，相关 `schemas/config.py`、`schemas/evaluation.py`、`schemas/journal.py`，
RunLog/terminal 消费者和 fixtures。按 D044 §3–§4 一次迁移接口与调用方：

- Harness owner 提供纯分支决定；Runner 算一次并传给 Check，不持久化该 enum。
- 新增 SmokeVersionVerifier 并分开 Smoke 的 typed 成功结果；Smoke 不要求保留供后续
  relaxation 使用的 HarnessBaseline。Search 继续使用 HighestVersionVerifier 和完整 evidence。
- Check 两分支移除 static capture/compare/runtime ledger；保留真实 prepare/verifier failure、
  snapshot/environment 清理和 Run-live/final 事实一致性。
  DEGENERATE 返回实际 declaration outcome，不保留空的 harness-prepare 字段或跳过占位。
- 更换 `harness-prepare` Role 的所有生产者与消费者，保留空 ty-cache sidecar 的提交顺序。
- 删除两命令的 ty_jobs override；同步 CLI help、request validation、composition 与公开测试。
  共享配置仍按目标合同校验，不为了删除选项改变其他命令或 source/policy identity。

公开验证入口：CompatibilityChecker、SmokeVersionVerifier、VerificationRunner、命令 workflow、
`create_app`、RunLogStore 与 TerminalPresenter。主要扩展 `tests/test_check.py`、`test_smoke.py`、
`test_harness.py`、`test_verification.py`、`test_cli.py`、`test_execution_run.py`、`test_runlog.py`、
`test_diagnose.py`；Search 回归覆盖 `test_baseline.py` 及现有 search/static lifecycle 测试。
移除仅证明旧 static capture/Role 的测试，以目标行为用例替换，不保留旧语法兼容层。

关键边界必须有公开证据：

- 无 active harness、全部 fixed、存在 ceiling-eligible（含适用的 ~= / wildcard）三类路径；
  非空 active IDs 仍复证 environment plan；Search 全 fixed 仍执行完整最高环境验证。
- REQUIRED 的最高环境成功并关闭后，在 declaration 启动前取消或发生基础设施异常；断言
  原异常/中断出口、已取得日志事实及资源清理，不产生 Cell PASS 或 declaration 结果。
  该边界可用进程内公开 seam 注入验证，本轮不要求真实子进程强杀。
- `max_cells=1` 的两分支及 typed failure 混合 Cell 正常完成全部调度；排队或中断不伪造
  Attempt/outcome。Search 的 Cell-scoped deadline 单独保持。

### S2 — 真实流程与既有 apply 回归

优先扩展 `tests/test_end_to_end.py` 的连续生命周期；真实产品 CLI 路径标 `process` + `e2e`。
验证每次运行与跨命令使用，不能用重复返回 PASS 的 recording adapter 替代真实 verifier。

| 场景 | 直接证据 | AC |
| --- | --- | --- |
| 手写声明、无报告的连续两次 check | 首次 search/apply 前，同快照两次真实 check 均正常完成且有新 prepare/完整 verifier 执行记录；每次开始及结束时 package-floor.json 均不存在；不能只测 help/request validation 或退出码 | AC1、AC5 |
| 已有报告时修改源码/测试后再 check | 本次新 prepare 与完整 verifier 执行记录；已有报告 bytes 不变 | AC5 |
| Smoke 重复 invocation | 每次一轮 highest prepare/full verifier；实际执行且没有 ty，不能只断言退出 0 | AC2 |
| 无 Git 生命周期 | 真实 smoke→search→explain→apply→重复 apply→check→修改源码/测试→check；重复 apply 为既有 NOOP，apply/check 不改报告 | AC8 |
| minimize 集成 | 既有真实 search/apply 流程及结果保持；与独立命令使用同一现行授权/编辑入口 | AC8 |
| 现有 apply 安全 | 通过 ApplyAuthorizer、ProjectEditor 和 workflow 回归严格准入、drift/force、NOOP/重入、raw CAS、写后验证与恢复 | AC9 |

无报告 check 放在同一连续 fixture 的首次 search 之前，之后接入已有报告的生命周期；不能
先 search/apply 再以“报告未变化”关闭 AC1。无 Git 场景使用仓库外、从未创建 `.git` 文件或目录
的临时项目，并保留此初始条件证据；不能在 PF 仓库工作树内完成命令就算通过。
保留公开调用记录和产物，不能只检查最终状态。
现有 apply 测试主要在 `test_authorization.py`、`test_editor.py`、`test_report_workflows.py`、
`test_snapshot.py`，按影响复用与回归，不为已撤下的回执添加 Schema 或事务故障矩阵。
涉及 marker/selector 的公开 seam 沿用 linux/darwin/win32 Cell family 代表用例；与实际 Host
资格分开，不因本机 OS 跳过其他 Cell 语义。

本切片不重构 apply、ReportStore 或恢复协议；如发现独立缺陷，记录真实证据并按现行 owner
处理范围，不将回执事务重新带回 D044。使用固定小项目和受控依赖，显式记录网络或额外
Python minor 要求；不在自举契约中收集真实 CLI，也不以资格回放替代产品链路。

### S3 — 完整验收与 owner 吸收

按 D044 §2/§7 吸收 D001/D002/D004/D006/D008/D012；特别核对 D002 §5 的 Smoke workflow、
D001 §7 的验证命令限定、D008 的阶段间中断与聚合、D012 的命令分支。双语 README、help、
相关验证术语、Journal fixtures 与现行引用同步；不增加回执词条或迁移报告 Schema 1。
吸收时逐项核对：D001 §5 按命令区分两个/三个 scheduling 选项；D006 附录 A.1 明确
`--ty-jobs` 仅属 search/minimize，其余选项按 D001 适用范围分组；A.6 补入 D044 §4 已规定的
`Check failed · harness preparation did not pass` 示例。AC9 仅回归原合同，不向 apply owner
吸收一套新的授权或持久化规则。

完成必需完整车道后，逐项将 §3 的待验改为结论及精确证据。仅文档通过或仅进程内调用次数
正确不能关闭 D044。全部 AC 完成后，在同一完成变更内归档 D044/P048，更新现行/归档索引
与入链，再跑 docs。已有归档事实不回写；C005 的缓存、增量 apply 和可选应用记录仍开放。

## 3. 逐项验收矩阵

本表定位证据，不替代 [D044 §6](../designs/D044-pf-check-first-minimal-verification.md#6-验收标准)。
AC1–AC7 保留原验证范围；本轮 AC8/AC9 收拢现有流程与 apply 回归，AC10 对应最终交付。
旧版回执 AC 不再是本轮门禁；范围修订不代表这些能力已实现或通过验收。

| AC | 切片 / 证据槽 | 直接证据入口与核对重点 | 当前结论 |
| --- | --- | --- | --- |
| AC1 | S1–S3 / E1–E3 | CLI/workflow 当前声明准入、选项与配置；E2 必须含手写声明、无 package-floor.json 时真实 check 完成及 verifier 记录；D001/help/双语 README | 通过。E1 CLI/request；E2 `test_cli_checks_handwritten_project_only_declaration`；D001 §5/§7、README/README.zh、D006 附录 A.1 |
| AC2 | S1、S2 / E1、E2 | Smoke runtime-only 调用与真实重复 invocation | 通过。E1 `test_smoke`；E2 e2e 重复 smoke |
| AC3 | S1 / E1 | Check 两类 DEGENERATE、active IDs、environment plan 复证 | 通过。E1 `test_check` / `test_harness` |
| AC4 | S1 / E1 | REQUIRED 的 eligible harness、prepare/verifier 顺序和最高环境失败 | 通过。E1 `test_check` REQUIRED / 失败路径 |
| AC5 | S1、S2 / E1、E2 | 连续 check 与源码变化；真实 verifier 记录、报告无读写 | 通过。E2 无报告两次 check、改源码后再 check |
| AC6 | S1 / E1 | DI 边界；全 fixed Search 的完整 highest/static 路径 | 通过。E1 `test_baseline` / `test_verification`；资格脚本改走 Search |
| AC7 | S1 / E1 | typed outcome/events/Journal/diagnose、聚合、排队、阶段间中断、空 sidecar | 通过。E1 verification/runlog/diagnose/check 中断与混合 Cell |
| AC8 | S2 / E2 | 无 Git 的完整生命周期、重复 apply NOOP、报告不改写、minimize | 通过。`test_no_git_onboarding_and_repeated_check`、`test_minimize_keeps_search_and_apply_flow` |
| AC9 | S2 / E2 | 现行授权/编辑/恢复回归及三类 Cell family 代表用例 | 通过。E2 apply 回归 81 passed |
| AC10 | S3 / E3 | 完整车道、逐项审计、owner/摘要/Journal/生成一致性与归档闭合 | 通过。daily/PR/coverage 已绿；owner 吸收后归档，docs 见本槽 |

## 4. 验证命令与证据保存

所有 PF/uv 测试按 AGENTS.md 在仓库根目录的沙箱外执行。进程内断言使用公开 seam，同类
输入参数化；真实场景覆盖无法由进程内证据证明的风险。具体文件随切片归属调整，必须
回填实际命令，不以预期命令代替执行记录。

| 范围 | 计划命令 | 执行时机 |
| --- | --- | --- |
| 验证命令聚焦 | `uv run pytest --no-testmon -q tests/test_check.py tests/test_smoke.py tests/test_harness.py tests/test_verification.py tests/test_cli.py tests/test_execution_run.py tests/test_runlog.py tests/test_diagnose.py tests/test_baseline.py` | S1；按实际变更补 search/static 用例 |
| 现有 apply 回归 | `uv run pytest --no-testmon -q tests/test_authorization.py tests/test_editor.py tests/test_report_workflows.py tests/test_snapshot.py` | S2 |
| 真实流程 | `uv run pytest --no-testmon -q -m e2e tests/test_end_to_end.py` | S2；记录实际新增/复用的 nodes |
| 日常完整 | `uv run python scripts/validate.py daily` | 切片稳定及 S3；适用时由更宽车道覆盖 |
| PR | `uv run python scripts/validate.py pr` | S3，按 CI Python 3.11/3.12 矩阵 |
| 覆盖率 | `uv run python scripts/validate.py coverage` | S3，canonical Python 3.10 / CI OS 覆盖率并集及门禁 |
| 文档与生成一致性 | `uv run python scripts/validate.py docs` | 本轮文档、最终 owner 吸收和归档后 |

不安排回执 Schema 生成迁移；现有 schema/example 一致性仍由 docs 车道检查。工具协议或版本
凭据未变化时，不额外刷新 qualification；完整 coverage 仍按既定车道收集。
验证脚本原始输出与环境/源码状态保存在 `tests/.cache/validation/<run>/`；聚焦命令另存 stdout/
stderr、退出码与源码状态，长期验收证据归入稳定产物位置。每个证据槽填写实际命令、计数、
日志/manifest、支持的 AC 和未覆盖范围；不得将环境障碍记为产品 PASS。

## 5. 执行记录

旧范围的记录保留为历史，不能证明本次修订后的文档或行为。以下 E1–E3 对应本轮三个切片。

| 日期 / 槽 | 行动与结论 | 命令 / 原始证据 | 尚未完成 |
| --- | --- | --- | --- |
| 2026-09-11 / E0（收缩前） | 完成最后两项评审修订及三项说明，D044 已接受；起草 P048、同步导航；独立复核确认全部 AC 有证据槽 | `uv run python scripts/validate.py docs` → PASS，2 steps，exit 0；`tests/.cache/validation/docs-uap_9vsi/manifest.json`（本地文档检查，非行为证据） | 未授权生产实现，所有行为 AC 待验 |
| 2026-09-11 / E0a | 按用户决定撤下回执/事务扩展，重写为三切片、AC1–AC10；保留现有 apply 安全；同步 C005 和导航；独立复核无遗漏 | `uv run python scripts/validate.py docs` → PASS，2 steps，exit 0；`tests/.cache/validation/docs-8son7f4v/manifest.json`（本地文档检查，非行为证据） | 未授权生产实现，所有行为 AC 待验 |
| 2026-09-11 / E0b | 终审补齐 AC1 的无报告真实 check 证据槽；明确无 Git fixture、typed outcome 及 owner 吸收核对项；Design 接受状态和范围不变 | `uv run python scripts/validate.py docs` → PASS，2 steps，exit 0；`tests/.cache/validation/docs-mlzp5rlk/manifest.json`（本地文档检查，非行为证据） | 未授权生产实现，所有行为 AC 待验 |
| 2026-09-11 / E1 | S1 聚焦验证命令与 Search 回归 PASS | `uv run pytest --no-testmon -q tests/test_check.py tests/test_smoke.py tests/test_harness.py tests/test_verification.py tests/test_cli.py tests/test_execution_run.py tests/test_runlog.py tests/test_diagnose.py tests/test_baseline.py` → 260 passed, 3 deselected, exit 0；`tests/.cache/validation/p048-e1-20260911T034324Z/` | 真实 CLI 生命周期见 E2 |
| 2026-09-11 / E2 | 无报告真实 check、无 Git 生命周期、minimize、原 apply 回归 PASS | apply：`uv run pytest --no-testmon -q tests/test_authorization.py tests/test_editor.py tests/test_report_workflows.py tests/test_snapshot.py` → 81 passed, 6 deselected, exit 0；`tests/.cache/validation/p048-e2-apply-20260911T034325Z/`。e2e：`uv run pytest --no-testmon -q -m e2e tests/test_end_to_end.py` → 6 passed, exit 0；`tests/.cache/validation/p048-e2-e2e-20260911T034337Z/`。nodes：`test_cli_checks_handwritten_project_only_declaration`、`test_no_git_onboarding_and_repeated_check`、`test_minimize_keeps_search_and_apply_flow` | 完整车道见 E3 |
| 2026-09-11 / E3 | daily / PR / coverage PASS；owner 吸收后归档 | daily：`uv run python scripts/validate.py daily` → PASS，5 steps，exit 0；2474 passed, 118 deselected；`tests/.cache/validation/daily-rk54bdjz/manifest.json`。PR：同命令 `pr` → PASS，6 steps；2583 passed, 1 skipped, 8 deselected；`tests/.cache/validation/pr-q8mnp_eu/manifest.json`。coverage：同命令 `coverage` → PASS，6 steps；2591 passed, 1 skipped，TOTAL 88%；`tests/.cache/validation/coverage-xnk7e_c0/manifest.json`。资格 `qualify_static_guidance.py` 的 controlled 模式改为 Search，不再用 Check 采集 ty。归档后 `uv run python scripts/validate.py docs` → PASS，2 steps，exit 0；`tests/.cache/validation/docs-jkvzz3p0/manifest.json` | 无 |
| 2026-09-11 / E3b | 独立评审补记：混合 Cell 改为真实 CompatibilityChecker 两分支；Runner 对 Check 只算一次 `harness_baseline_requirement` 并按 Cell 复用；D002 Smoke 返回类型改为 `SmokeCellOutcome`；CLI 组合测试去掉 `hasattr(..., "_static")`。AC10：本地 `validate.py coverage` 按 `tests/README.md` 只采集（`--cov-fail-under=0`），单宿主 88% 不是本轮回归，也不是采集车道失败；90% 是 CI 各 OS 并集门禁，本轮不改 `fail_under`、不补覆盖率数字 | 聚焦：`uv run pytest --no-testmon -q tests/test_check.py tests/test_verification.py tests/test_cli.py tests/test_harness.py` → 160 passed, 3 deselected, exit 0；`tests/.cache/validation/p048-e3b-20260911T040632Z/` | 未授权降低 CI 90% 并集门槛；本机未另跑 3.11/3.12 PR 矩阵 |

切片结束时更新 §1 状态、§3 AC 结论和本表。S1–S3 行为证据已齐；AC10 的 docs 步骤在归档入链更新后执行。

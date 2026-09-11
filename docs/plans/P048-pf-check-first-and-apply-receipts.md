# P048 — D044 check 稳态与 apply 回执实施计划

- **状态：** 进行中
- **日期：** 2026-09-11
- **对应 Design：** [D044](../designs/D044-pf-check-first-and-apply-receipts.md)（已接受待实施）
- **阶段：** 计划已起草；生产实现未授权、未开始
- **起点：** `b2217f997ce1b9fef6586b1d0f0127367f2590d9`，加当前 D044/C005/R008/文档索引的未提交修订
- **流程与验证：** [AGENTS.md](../../AGENTS.md)、[tests/README.md](../../tests/README.md)、[文档索引](../README.md)

本 Plan 只拥有切片、执行决定与证据；目标行为及非目标由 D044 唯一规定。2026-09-11 的授权
仅覆盖 Design 修订与 Plan 起草。当前不修改生产代码、测试或生成 schema/examples，不执行产品
行为验证；下列实施与验证命令均待生产实施授权后执行。

## 1. 当前状态与执行顺序

已完成：核对最后两项评审，限定验证命令范围，补齐 highest prepare 成功后的阶段间中断语义；
同步 D002 §5 映射、Smoke evidence 保留边界及 ApplyTarget 序列化说明。D044 已接受，尚未实施。

默认顺序：**S1 → S2 → S3 → S4 → S5 → S6**。S1 是命令轴；S2–S4 是回执/事务轴；
S5 验证真实进程与两轴流程；S6 完成回归、验收及 owner 吸收。S1 与 S2 没有产品依赖，可在
实施授权后按 disjoint ownership 调整执行顺序；S3 之前须完成 S2 的持久化可行性结论。
不得以命令轴通过宣称 D044 完成。下一步是取得生产实施授权后，从 S1 开始。

| 切片 | 依赖 | 交付与完成边界 | AC | 证据槽 | 状态 |
| --- | --- | --- | --- | --- | --- |
| S1 命令序列 | 实施授权 | Smoke/Check 删除 static 依赖，Check 两分支、Role/context/聚合、CLI/request 与 Journal 一起迁移；Search 行为回归 | AC1–AC7 | E1 | 未开始 |
| S2 持久化协调 | 实施授权 | 选择并证明跨进程 guard、只读 reader、pending、锁顺序及恢复入口；不得把不可行性留到最后的强杀测试 | AC12、AC13、AC16 | E2 | 未开始 |
| S3 回执与授权绑定 | S2 | strict wire/identity、完整 ApplyTarget、报告读取 revision 与 grant 绑定、不可变 replacement；所有生产者/reader/fixtures/生成投影同步 | AC8、AC9、AC11、AC14 | E3 | 未开始 |
| S4 两文件事务与命令集成 | S2、S3 | ProjectEditor 提交/恢复，apply/minimize 前置恢复，NOOP/重入，结果字段与离线展示；完成进程内故障和授权矩阵 | AC10–AC16 | E4 | 未开始 |
| S5 真实进程及完整流程 | S1、S4 | 同快照重复验证、事务外部强杀与恢复、竞争写入、只读报告、无 Git 生命周期；记录真实进程证据 | AC2、AC5、AC12、AC13、AC15、AC16 | E5 | 未开始 |
| S6 完整验收与吸收 | S1–S5 | 必需完整车道、逐项 AC 审计、双语摘要及 owner 吸收、Design/Plan 同变更归档 | AC1–AC17 | E6 | 未开始 |

## 2. 切片工作与公开接口

### S1 — 命令轴

涉及 `src/pf/harness.py`、`baseline.py`、`check.py`、`verification.py`、`workflow.py`、
`cli.py`，相关 `schemas/config.py`、`schemas/evaluation.py`、`schemas/journal.py`，
RunLog/terminal 消费者和 fixtures。按 D044 §3–§4 一次迁移接口与调用方：

- Harness owner 提供纯分支决定；Runner 算一次并传给 Check，不持久化该 enum。
- 新增 SmokeVersionVerifier 并分开 Smoke 的 typed 成功结果；Smoke 不要求保留供后续
  relaxation 使用的 HarnessBaseline。Search 继续使用 HighestVersionVerifier 和完整 evidence。
- Check 两分支移除 static capture/compare/runtime ledger；保留真实 prepare/verifier failure、
  snapshot/environment 清理和 Run-live/final 事实一致性。
- 更换 `harness-prepare` Role 的所有生产者与消费者，保留空 ty-cache sidecar 的提交顺序。
- 删除两命令的 ty_jobs override；同步 CLI help、request validation、composition 与公开测试。
  共享配置仍按目标合同校验，不为了删除选项改变其他命令或 source/policy identity。

公开验证入口：CompatibilityChecker、SmokeVersionVerifier、VerificationRunner、命令 workflow、
`create_app`、RunLogStore 与 TerminalPresenter。主要扩展 `tests/test_check.py`、`test_smoke.py`、
`test_harness.py`、`test_verification.py`、`test_cli.py`、`test_execution_run.py`、`test_runlog.py`、
`test_diagnose.py`；Search 回归覆盖 `test_baseline.py` 及现有 search/static lifecycle 测试。

本轮最后一项评审必须成为公开行为用例：REQUIRED 的最高环境成功并关闭后，在 declaration
启动前取消或发生基础设施异常；断言原异常/中断出口、已取得日志事实和资源清理，不产生
Cell PASS 或 declaration 结果。另覆盖 `max_cells=1` 的两分支及 typed failure 混合 Cell。

### S2 — 先证明协调与恢复可行

涉及 `src/pf/editor.py`、`report.py` 及各自 private 持久化实现；不新增通用 filesystem/transaction
服务，不让 Search/Merge 注入 Editor。按 D044 §6，先确定并记录以下执行决定：

| 执行决定 | 需要取得的证据 | 当前记录 |
| --- | --- | --- |
| workspace/report guard 的 Host 实现、路径归一与固定锁顺序 | 两个真实进程互斥；取消/进程死亡释放锁；任意 merge 输出路径参与同一保护 | 待 S2 选择并验证 |
| 无 pending 的独立只读 report 如何取得读保护 | 实际不可写的 report 及父目录仍可经 ReportStore/read/explain 读取，不依赖先创建旁路文件 | 待 S2 选择并验证 |
| durable pending 与 recovery 定位、journal/backup 的可信校验 | 进程死亡后 pending 仍阻止读写；非法报告 replacement 分支不能吞掉协调失败 | 待 S2 选择并验证 |
| Editor 的公开恢复/保护作用域 | apply 在 load/read/snapshot/authorize 前恢复；minimize 在 search planning 前恢复；保护持续到提交或失败收尾 | 待 S2 固定调用顺序 |
| 事务测试的可等待同步与外部强杀方法 | 明确停在真实 I/O 边界；不靠固定 sleep、替换业务结果或 patch PF 私有函数 | 待 S2 记录测试驱动方式 |

使用 ReportStore 与 ProjectEditor 的公开表面验证目标行为；只有能力 owner 的私有 Host adapter
协议可按 tests/README.md 单独列为 infra。跨进程证据进入 process；真实产品命令路径同时标 e2e。
具体锁原语、内部 journal schema 和临时路径在本切片确定，不另立合同。若某方案不能满足
D044，先在本切片更换实现方案；若必须改变目标合同，记录阻点并重新取得 Design 决定。

### S3 — 回执、编码与授权

涉及 `src/pf/schemas/apply.py`、`schemas/report.py`、`authorization.py`、`report.py` 及 schema
exports、报告 fixture builders、`scripts/generate_report_schema.py` 和其生成产物。

- 按 D044 §5.1 实现同一 ApplyTarget wire 投影、floor/apply identity 与严格复算；保持现行
  generation、evidence/Attempt/Proposal/Failure 算法，摘要与 raw revision 分开。
- Authorizer 冻结包含 NOOP groups 的完整目标及 floor identity；ReportStore 的读取句柄将
  ValidatedReport 与实际读取 revision 绑定，准备 replacement 时复核来自同一 floor 的 grant。
- ReportStore 只准备已验证 replacement，不提前发布回执；Editor 不导入 report wire。
- search/build/update/merge 的新输出显式清空回执；read/write 原报告保留回执。同步全部
  readers/consumers/fixtures/schema/examples，不保留旧 Schema 1 或旧 Role 的兼容层。

公开验证入口：ReportStore、PackageReportBuilder、ApplyAuthorizer、wire reader；主要扩展
`tests/test_report.py`、`test_report_schema.py`、`test_authorization.py`、`test_report_workflows.py`、
`test_report_artifacts.py`。摘要用固定非 ASCII/required-nullable 向量验证，不能只拿同一
helper 的两次输出互相比。投影按 linux/darwin/win32 Cell family 展开代表性声明/selector。

### S4 — 两文件提交、恢复与展示

涉及 `src/pf/editor.py`、`report.py`、`workflow.py`、`cli.py`、`schemas/apply.py` 和 terminal
apply/minimize/explain 消费者。S2 的公开保护作用域接入实际 workflow，S3 的 report replacement
与 TOML edits 作为同一事务参与者；声明修改与 receipt_created 分别投影到命令结果。

通过 ProjectEditor、ApplyCommandWorkflow、ReportStore 与真实文件验证下列矩阵，不直接调用
Editor 私有恢复函数或手造成功结果替代事务：

- 首次 WRITABLE、首次 NOOP、重复 NOOP、恢复 original 后合法重入；报告和 TOML 的最终 bytes、
  applied_at 及结果字段均符合 D044。已有回执仍重新走所有准入及有限 force 边界。
- 备份、各 participant 发布、写后验证、COMMITTED 前后的故障；记录回滚或只清理的结果。
  第三种 bytes、缺失/不可信材料、越界或替换路径须保留现场，不能覆盖新修改。
- 报告 revision 单独变化，含语义 identity 相同而文件 bytes 不同；并行 writer 不能丢更新。
- apply/minimize 早期恢复；测试让恢复前的文件状态无法通过后续规划，以证明顺序，不能只
  断言 helper 被调用。Check 不进入报告/回执接口；Explain 只显示历史事实。

主要扩展 `tests/test_editor.py`、`test_report_workflows.py`、`test_authorization.py`、
`test_snapshot.py`、`test_cli.py`、`test_terminal.py`、`test_explain_terminal.py`。
S4 的进程内矩阵不关闭 AC12 的崩溃恢复要求；真实证据在 S5 独立取得。

### S5 — 真实进程证据

优先扩展 `tests/test_end_to_end.py` 的连续生命周期；为强杀/竞争场景增加按风险分组的测试。
全部真实产品 CLI 路径标 `process` + `e2e`；只验证底层跨进程协议的测试标 process。

| 场景 | 必须保留的可观察证据 | AC |
| --- | --- | --- |
| 同快照连续两次 check，及再次 smoke | 每次新 prepare 与真实完整 verifier 执行记录；report bytes 不变；不是仅断言退出 0 | AC2、AC5 |
| 报告已 replace、COMMITTED 尚未 durable 时强杀 | 父进程在确定边界外部终止子进程，绕过 finally；真实退出状态、pending/前后文件摘要；独立 read/explain 拒绝消费；新 apply 在 load/authorize 前恢复 | AC12 |
| apply 与 report writer 竞争 | 两个真实进程通过 barrier/事件形成重叠；验证互斥、revision 拒绝或串行结果；进程死后 pending 仍阻止 search/update/merge 覆盖 | AC13 |
| 独立只读报告 | 实际权限使报告及父目录不可写，read/explain 仍成功；不能仅注入 PermissionError 或在可写目录回放 | AC13 |
| 无 Git 生命周期 | 真实 search → apply → 重复 apply → 修改源码/测试 → check；ID/NOOP/报告字节与 verifier 事实闭合 | AC10、AC15、AC16 |
| minimize 遇旧事务 | 真实入口在 search planning/snapshot 前恢复，然后完成新的 search/apply；不消费恢复前 grant | AC15 |

场景可共享 fixture 与连续命令，但不可互相冒充证据。强杀不等同 SIGINT 或进程内异常。
需要网络或额外 Python minor 时显式记录；用固定小项目和受控依赖缩小外部波动。Cell target
语义与实际 Host 资格分开，按测试 owner 的 Host/Cell 展开规则执行。

### S6 — 回归、验收与 owner 吸收

按 D044 §2/§9 的替换表吸收 D001/D002/D004/D006/D008/D012/D014；特别核对 D002 §5 的
Smoke workflow、D001 §7 的验证命令限定、D008 的阶段间中断、D012 的命令分支和 D014 两种编码。
双语 README、CLI help、CONTEXT 术语、生成 schema/examples 及现行引用同步。

完成必需完整车道后，逐项将 §3 的待验改为结论及精确证据；回执/事务轴另核对吸收文本与
S5 强杀事实。仅文档通过、仅命令轴通过或仅进程内矩阵通过均不能关闭 D044。
全部 AC 完成后，在同一完成变更内归档 D044/P048，更新现行/归档索引与入链，再跑 docs。
不回写已有历史归档；C005 的增量 apply/缓存/可回滚历史仍开放。

## 3. 逐项验收矩阵

本表定位证据，不替代 [D044 §8](../designs/D044-pf-check-first-and-apply-receipts.md#8-验收标准)。
当前所有行为 AC 均未验证；实施中填写实际测试 node、命令/日志及结论。

| AC | 切片 / 证据槽 | 直接证据入口与核对重点 | 当前结论 |
| --- | --- | --- | --- |
| AC1 | S1、S6 / E1、E6 | CLI/workflow 当前声明准入、选项与配置；D001/help/双语 README | 待验 |
| AC2 | S1、S5 / E1、E5 | Smoke runtime-only 调用与真实重复 invocation | 待验 |
| AC3 | S1 / E1 | Check 两类 DEGENERATE、active IDs、environment plan 复证 | 待验 |
| AC4 | S1 / E1 | REQUIRED 的 eligible harness、prepare/verifier 顺序和最高环境失败 | 待验 |
| AC5 | S1、S5 / E1、E5 | 连续 check 与源码变化；真实 verifier 记录、报告无读写 | 待验 |
| AC6 | S1 / E1 | DI 边界；全 fixed Search 的完整 highest/static 路径 | 待验 |
| AC7 | S1 / E1 | typed outcome/events/Journal/diagnose、聚合、排队、阶段间中断、空 sidecar | 待验 |
| AC8 | S3 / E3 | strict reader、wire/fixture/生成投影、回执目标验证 | 待验 |
| AC9 | S3、S4 / E3、E4 | 固定摘要向量、同 core/target、不同 evidence、raw revision/grant 绑定 | 待验 |
| AC10 | S4、S5 / E4、E5 | 四类成功/NOOP/重入、时间和 bytes、命令结果与终端 | 待验 |
| AC11 | S3、S4 / E3、E4 | 原授权/force/内容 drift、合法 reentry；Git dirty 不代替内容 identity | 待验 |
| AC12 | S2、S4、S5 / E2、E4、E5 | 故障矩阵 + 外部强杀 + 新进程恢复；不能用进程内异常关闭 | 待验 |
| AC13 | S2、S4、S5 / E2、E4、E5 | guard/pending、竞争、第三种 bytes/路径、真实只读报告 | 待验 |
| AC14 | S3、S4 / E3、E4 | 新 report 清空、原 report 保留、merge/update/Explain 语义 | 待验 |
| AC15 | S4、S5 / E4、E5 | minimize 前置恢复及跨命令完整生命周期 | 待验 |
| AC16 | S2–S5 / E2–E5 | 无 Git、snapshot 排除事务材料、三类 Cell family 的授权/投影 | 待验 |
| AC17 | S6 / E6 | 完整车道、逐项审计、owner/摘要/生成物与归档闭合 | 待验 |

## 4. 验证命令与证据保存

所有 PF/uv 测试按 AGENTS.md 在仓库根目录的沙箱外执行。进程内断言使用公开 seam；同类输入
参数化，真实场景仅覆盖无法由进程内证据证明的风险。具体文件可随切片归属调整，必须回填
实际命令；不以预期命令代替执行记录。

| 范围 | 计划命令 | 执行时机 |
| --- | --- | --- |
| 命令轴聚焦 | `uv run pytest --no-testmon -q tests/test_check.py tests/test_smoke.py tests/test_harness.py tests/test_verification.py tests/test_cli.py tests/test_execution_run.py tests/test_runlog.py tests/test_diagnose.py tests/test_baseline.py` | S1；按实际变更补 search/static 用例 |
| 回执/事务聚焦 | `uv run pytest --no-testmon -q tests/test_authorization.py tests/test_editor.py tests/test_report.py tests/test_report_schema.py tests/test_report_workflows.py tests/test_snapshot.py tests/test_terminal.py tests/test_explain_terminal.py` | S3–S4 |
| 真实流程 | `uv run pytest --no-testmon -q -m e2e tests/test_end_to_end.py` | S5；加入实际新增强杀/竞争测试文件或 nodes |
| 跨进程协议补充 | `uv run python scripts/validate.py process` | S2/S5 需要独立进程车道时；已覆盖的证据按测试 owner 复用 |
| 生成报告投影 | `uv run python scripts/generate_report_schema.py` | S3 模型迁移后；随实现提交生成产物 |
| 日常完整 | `uv run python scripts/validate.py daily` | 切片稳定及 S6；适用时由更宽车道覆盖 |
| PR | `uv run python scripts/validate.py pr` | S6，按 CI Python 3.11/3.12 矩阵 |
| 覆盖率 | `uv run python scripts/validate.py coverage` | S6，canonical Python 3.10 / CI OS 覆盖率并集及门禁 |
| 文档与生成一致性 | `uv run python scripts/validate.py docs` | 本轮文档、最终 owner 吸收和归档后 |

工具协议或版本凭据未变化时，不额外刷新 qualification；完整 coverage 仍按既定车道收集。
验证脚本原始输出与环境/源码状态保存在 `tests/.cache/validation/<run>/`；聚焦命令另存 stdout/
stderr、退出码与源码状态，长期验收证据归入稳定产物位置。每个 evidence 槽回填实际命令、
结果计数、日志/manifest、支持的 AC 和未覆盖范围；不得将环境障碍记为产品 PASS。

## 5. 执行记录

| 日期 / 槽 | 行动与结论 | 命令 / 原始证据 | 尚未完成 |
| --- | --- | --- | --- |
| 2026-09-11 / E0 | 完成最后两项评审修订及三项说明，D044 已接受；起草 P048、同步导航；独立复核确认全部 AC 有证据槽 | `uv run python scripts/validate.py docs` → PASS，2 steps，exit 0；`tests/.cache/validation/docs-uap_9vsi/manifest.json`（本地文档检查，非行为证据） | 未授权生产实现，所有行为 AC 待验 |
| E1 | 未执行 | 待 S1 填写 | AC1–AC7 的进程内证据 |
| E2 | 未执行 | 待 S2 填写 | 协调实现选择与可行性证据 |
| E3 | 未执行 | 待 S3 填写 | wire/identity/授权与生成物证据 |
| E4 | 未执行 | 待 S4 填写 | 事务/重入/展示与故障矩阵 |
| E5 | 未执行 | 待 S5 填写 | 真实重复验证、强杀、竞争及生命周期 |
| E6 | 未执行 | 待 S6 填写 | 完整回归、全部 AC 审计与吸收归档 |

切片结束时更新 §1 状态、§3 AC 结论和本表。当前无已证明的行为偏差，也无已完成的实现切片。
不把本轮 Design/Plan 文档检查当作 AC17 的最终交付证据。

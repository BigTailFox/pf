# P004 — failure 语义与 `diagnose` 实施记录

- **状态：** 已完成
- **开始日期：** 2026-08-20
- **完成日期：** 2026-08-21
- **性质：** 非规范性实施记录
- **设计来源：** [D005](../designs/D005-pf-failure-and-diagnose.md)
- **相关模块约束：** [D002](../designs/D002-pf-implementation.md)
- **搜索契约：** [D003](../designs/D003-pf-search-algorithm.md)
- **展示布局约束：** [D006](../designs/D006-pf-cli-enhancement.md)
- **起始提交：** `73173e7105012b3d631324e319858334d10bb242`

本文记录 D005 的纵向 TDD、review 修正和最终验证证据，不复制现行失败契约。D005 继续唯一拥有 cause、disposition、FailureRecord 和 `diagnose` 语义；本记录只回答如何实施以及实际验证到了什么。

## 1. 范围与边界

本轮实现：

- 直接重塑首发 Schema 1，不保留开发期 status 或旧报告兼容层；
- 建立 Attempt identity、operation cause、failure policy、Rejection/Indeterminate 和 FailureRecord；
- 让 baseline、candidate prepare、static/test probe、candidate discovery 和 scheduler deadline 使用同一失败模型；
- 让 `CoordinateSearch` 只消费 `PASS | REJECTED | INDETERMINATE`，并以 `failure_id` 保存 predecessor；
- 在报告中保留可移植失败事实与稳定交叉引用，并增加 report generation identity；
- 用 `(report_generation_id, failure_id)` 维护项目本地 diagnosis index；
- 增加严格离线的 `pf diagnose [package] [--failure FAILURE_ID]`；
- 让实时 search、`explain` 与 `diagnose` 复用 D005 的用户文案。

本轮不实施 D006 独立拥有的完整 help 分组、通用命令摘要、`minimize` 单摘要和 `explain` 全面信息架构；只采用 D005 输出所依赖的规范 cell 标题和渐进披露顺序。cell 完成行上的 Schema status（如 `STATIC_FAIL`）仍按当时 D006 live 布局保留，Enum 作为用户结论的禁令在 `diagnose` Technical details 和 failure title/impact 中落地。

## 2. 深模块与 seam

| module | interface | 隐藏的规则 |
| --- | --- | --- |
| `FailurePolicy` | `classify(scope, stage, failure/evaluation)` | 证据完整性、baseline/probe 分类矩阵、稳定 `failure_id` |
| `EnvironmentFactory` | `prepare(...) -> PreparedEnvironment | PrepareFailure` | 外部操作前的 Attempt identity、prepare stage 与 Proposal 建立时机 |
| `CoordinateSearch` | `minimize(..., evaluator)` | 只把 Rejection 当作 FAIL，把 Indeterminate 当作终止 |
| `PackageFloorReportV1` / `ReportStore` | 严格 Schema 1 读写、merge/update | FailureRecord 唯一性、scope/observation/boundary 交叉引用、generation identity |
| `RunLogStore` | `associate(...)` / `lookup(...)` | 私有原子 diagnosis index 与安全相对 locator |
| `DiagnoseCommandWorkflow` | `run(request)` | 只读报告、定位 failure、可选本地日志；不具备执行 seam |
| `TerminalPresenter` | failure presentation 与 render methods | title、impact、next step、technical details 和日志链接 |

业务分类不进入 adapter、搜索、workflow 或 Presenter；终端文案不进入公共 Schema。`rejection_is_supported` 作为 REJECTED 不变量属于 Evaluation Schema；`FailurePolicy` 只消费它。

## 3. 纵向 TDD 切片

| 切片 | 可观察行为 | 主要测试 | 状态 |
| --- | --- | --- | --- |
| 001 | Adapter 输出稳定 cause；`FailurePolicy` 只把完整、attempt-scoped、契约内失败分类为 Rejection | `tests/test_failure.py`、adapter tests | 已完成 |
| 002 | `EnvironmentFactory` 在任何外部操作前建立稳定 Attempt；prepare 失败没有 Proposal 且保留请求向量和机械事实 | `tests/test_environment.py`、`tests/test_schemas.py` | 已完成 |
| 003 | highest verifier 返回明确的 pass / baseline rejection / baseline indeterminate；没有 PASS 不进入候选发现 | `tests/test_baseline.py`、`tests/test_search_coordinator.py` | 已完成 |
| 004 | candidate install/build/harness/static/test Rejection 推进边界；Indeterminate 立即停止；不再虚构 `prepare:<status>` Proposal | `tests/test_search.py`、`tests/test_search_coordinator.py` | 已完成 |
| 005 | candidate discovery、scheduler deadline 使用 CellFailureScope；报告严格复证 FailureRecord、`failure_id`、boundary 和 generation | `tests/test_scheduling.py`、`tests/test_report.py`、`tests/test_schemas.py` | 已完成 |
| 006 | search 写报告后原子关联本地日志；`diagnose` 列表/单条均离线、只读且在日志缺失时仍可展示可移植事实 | `tests/test_process.py`、`tests/test_report_workflows.py`、`tests/test_cli.py`、`tests/test_diagnose.py` | 已完成 |
| 007 | 实时 search、`explain` 和 `diagnose` 以自然语言 title/impact/next step 为主，Enum 只出现在 technical details | `tests/test_terminal.py`、`tests/test_diagnose.py` | 已完成 |
| 008 | 开发期旧 Schema 1 被严格拒绝；merge/update/apply 保守复证；wheel 入口与全量门禁通过 | report/end-to-end/full suite | 已完成 |

每个切片执行一个公开行为测试 RED → 最小实现 GREEN → 相关类型检查；不先批量写完全部测试。

## 4. RED / GREEN 记录

001–007 的主体实现留在起始提交之后的工作树中，由 `tests/test_failure.py`、`tests/test_environment.py`、`tests/test_baseline.py`、`tests/test_search.py`、`tests/test_search_coordinator.py`、`tests/test_scheduling.py`、`tests/test_report.py`、`tests/test_diagnose.py` 与 `tests/test_terminal.py` 固定。收尾阶段补了 prepare 各阶段失败仍保留 Attempt、check 的 `lowest-direct` 不建立 Attempt、probe timeout 的 D005 impact、旧 `BASELINE_FAILED` 报告拒绝、以及 e2e `diagnose`。

### 008 与 review 修正

- RED：probe `STATIC_REGRESSION` 在 `ty` 退出 0 时被 `rejection_is_supported` 打成 Indeterminate，违反 D005 §8；Schema 从 `pf.failure` 导入分类函数，形成 `schemas ↔ failure` 环。
- GREEN：把 REJECTED 不变量放到 Evaluation Schema；`STATIC_REGRESSION` 与 `HARNESS_CONFLICT` 一样允许完整的零退出；去掉调用方折叠的 `evidence_complete` 布尔；Attempt Indeterminate 的 impact 改回 D005 §12.3 原文。

## 5. 完成门禁

```text
uv run --no-sync pytest --no-testmon --cov=pf --cov-report=term-missing -q
  -> 478 passed, 90.02% branch coverage
uv run --no-sync ty check src tests
  -> All checks passed
uv build
  -> sdist 与 wheel 构建成功
uv lock --check --no-config --default-index https://pypi.org/simple
  -> lock 检查通过
git diff --check
  -> 通过
installed wheel: pf diagnose --help / pf diagnose / pf diagnose --failure FAILURE_ID
  -> 离线列表与单条诊断；无网络、无环境创建；exit 0
```

以设计提交 `73173e7` 为固定点的 Standards / D005 Spec 双轴 review 发现并以回归测试修正：

- Evaluation Schema 不再依赖 `FailurePolicy`；REJECTED 完整性规则与 `FailureRecord` 同层；
- `classify` 不再接收 `evidence_complete` 标志，完整性只由结构化 process/scope/cause/stage 决定；
- probe `STATIC_REGRESSION` 在完整、可归属、增量非空时即使 `ty` 退出 0 也是 Rejection；
- Attempt Indeterminate 使用 D005 的统一 impact，不再为 baseline 另写一句。

未作为本轮缺陷修复、留给 D006 或后续收口：

- D006 help 分组、`--package` 位置参数表面、通用命令摘要和 `explain` 信息架构；
- live cell 完成行仍带 Schema status（`STATIC_FAIL` / `BASELINE_REJECTION`），与 D006 §9.1 示例一致，但与 D005 §12.1“Enum 不得当标题”存在展示层张力；
- `check` 兼容性失败仍走 Evaluation 而非 `FailureRecord`/`pf diagnose`；
- `FailureDiagnosis` 仍是 workflow dataclass（含本机 `Path`），未提升为公共 Schema；
- probe 实际向量偏离 requested vector 时仍合成 `ProcessResult` 以满足 `ToolFailure.process`。

## 6. 结论

D005 `failure-v1` 已在首发 Schema 1 中落地：Attempt 与 Proposal 分离，probe 安装/构建/harness 拒绝可推进边界，Indeterminate 立即停 cell，`pf diagnose` 严格离线。实现留在起始提交 `73173e7` 之后的工作树，尚未单独提交。未执行的外部平台/网络证据与 P001–P003 相同：非当前 CPython minor、非宿主 target、私有 index 与真实多宿主 merge。展示层的其余信息架构由 D006 继续拥有。

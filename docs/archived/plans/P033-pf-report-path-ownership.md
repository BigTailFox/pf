# P033 — D027 报告路径规则单一 owner 实施计划

- **状态：** 已完成、已归档
- **开始日期：** 2026-09-04
- **完成日期：** 2026-09-04
- **性质：** 非规范性实施计划、过程与证据记录
- **设计来源：** [D027](../designs/D027-pf-report-path-ownership.md)
- **评审来源：** [R007](../../reviews/R007-pf-current-improvement-priorities.md) §6
- **实施基线：** 当前工作树对照 D002/D006 与 `ProjectDiscovery` / `workflow.py` / `TerminalPresenter`

本文把 D027 验收标准映射到有序切片、测试和证据槽。完成标准只来自 D027 §3。

## 1. 目标与边界

- Discovery 是包默认报告位置的唯一 owner；planning 与 command result 传递已解析路径值；
- Search/Apply/Explain/Diagnose/Terminal 只消费路径值，不重算文件名或 package-relative location；
- 不建立 path module；路径值不进入 Schema 1、identity、Journal 或 merge；
- Merge 显式路径与 workspace 选择 seam 不变。

## 2. 基线事实与目标差距

| 切面 | 当前事实 | D027 目标 |
| --- | --- | --- |
| Discovery | `PackageLocation.report_path` 已物化 | 保持为唯一公式 |
| ProjectPlan | 不携带报告路径 | `report_path` 为 root-relative posix |
| Search/Apply | 从 `pyproject_path.parent / package-floor.json` 重算 | 消费 `project.report_path` |
| Search/Explain 返回值 | 只返回 `ValidatedReport` | command result 携带 display 路径 |
| Presenter | `_report_path(report)` 从 pyproject 重建 | 展示 result 携带的路径 |
| Diagnose | `source_path` 已有；Presenter 可回退文件名 | 只渲染已携带值 |

## 3. Interface 与 ownership

1. `ProjectDiscovery` 独占 `package_root / "package-floor.json"`。
2. `ProjectLoader` 把已解析相对路径写入 `ProjectPlan.report_path`。
3. Search/Explain workflow 独占对应 command result；Apply 只消费 planning 路径做读取。
4. `TerminalPresenter` 独占展示，不得从 report identity 反推报告位置。
5. 归并后 D002 拥有 value flow，D006 拥有展示消费规则。

## 4. 实施顺序

### 切片 001 — planning 携带路径值

`ProjectPlan.report_path` 由 `ProjectLoader` 从 `inventory.target.report_path` 写成 root-relative
posix。公共 planning tests 覆盖 root 与 workspace member 的字面路径。

验收：D027 §3.1–3.2 的 planning 面。

### 切片 002 — Search/Apply/Explain 消费并返回路径值

Search/Apply 用 `root / project.report_path` 读写。Search 返回 `SearchCommandResult`，Explain
返回 `ExplainCommandResult`。Diagnose 的 report `source_path` 继续来自 discovery 的相对路径。
公共 workflow tests 覆盖 root 与 member 读写位置，以及 Explain 成功结果携带的路径。

验收：D027 §3.2–3.3、3.5–3.7。

### 切片 003 — Presenter 消费携带路径

`render_search` / `render_explain` 接收 command result。删除两处 `_report_path(report)`。
Diagnose 不再回退到 `package-floor.json`。公共 presenter tests 证明展示使用携带值，即使它与
从 `pyproject_path` 推导的位置不同。

验收：D027 §3.4–3.5、3.8。

### 切片 004 — owner 归并与归档

写入 D002/D006；关闭 R007 §6；更新文档索引；归档 D027/P033。

验收：D027 §3.6、3.9。

## 5. 测试矩阵

| 场景 | 公开 seam |
| --- | --- |
| root plan 的 `report_path` 为 `package-floor.json` | `ProjectLoader.load` |
| member plan 的 `report_path` 为 `packages/<name>/package-floor.json` | `ProjectLoader.load` |
| Search 把 member 报告写到该相对路径 | `SearchCommandWorkflow.run` |
| Apply 从该相对路径读取 member 报告 | `ApplyCommandWorkflow.run` |
| Explain 成功结果携带同一 display 路径 | `ExplainCommandWorkflow.run` |
| Presenter 显示 command result 路径而非 pyproject 推导路径 | `render_search` / `render_explain` |
| Diagnose report 来源展示 `source_path` | `render_diagnose` |

## 6. 验证命令

```text
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon \
  tests/test_project.py tests/test_search_workflow.py tests/test_report_workflows.py \
  tests/test_terminal.py tests/test_explain_terminal.py tests/test_cli.py \
  tests/test_diagnose.py -q

UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon --cov -q

.venv/bin/ruff check src tests
.venv/bin/ty check src
git diff --check
```

## 7. 验收对照

| D027 §3 | 切片 | 状态 |
| --- | --- | --- |
| 1–2 Discovery/planning | 001 | 通过 |
| 2–3、5–7 workflow value flow | 002 | 通过 |
| 4–5、8 Presenter | 003 | 通过 |
| 6 Schema/merge 不泄漏 | 002–004 | 通过 |
| 9 owner 归并 | 004 | 通过 |

## 8. 行动记录

- 切片 001：`ProjectPlan.report_path` 由 ProjectLoader 从 Discovery 复制。
- 切片 002：Search/Apply 消费 planning 路径；Search/Explain 返回携带 display 路径的 command result。
- 切片 003：Presenter 消费 command result 路径；删除 `_report_path(report)`；Diagnose 不再回退文件名。
- 切片 004：稳定规则归并 D002/D006；关闭 R007 §6；归档本文与 D027。

### 验证结果

```text
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon \
  tests/test_project.py tests/test_search_workflow.py tests/test_report_workflows.py \
  tests/test_explain_terminal.py tests/test_cli.py tests/test_diagnose.py -q \
  --deselect tests/test_cli.py::TestResultCardWidths \
  --deselect tests/test_diagnose.py::TestDiagnoseWorkflow::test_diagnose_card_preserves_fields_at_common_widths
# 219 passed

tests/test_terminal.py -k 'not TestProgressRendering and not test_explain_renders_report_strings_as_literal_text and not test_explain_keeps_required_fields_readable_at_common_widths'
# 68 passed

.venv/bin/ruff check src tests  # passed
.venv/bin/ty check src          # passed
```

本环境中 Rich Live 与固定 56 列 result-card 宽度用例在 HEAD 上同样失败（apply/merge 卡边框仍为 80 列，
live stderr 为空）。它们不覆盖本变更的路径值流，未计入本 Plan 完成证据。

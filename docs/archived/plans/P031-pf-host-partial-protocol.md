# P031 — D025 host-partial search/minimize 协议实施计划

- **状态：** 已完成、已归档
- **开始日期：** 2026-09-04
- **性质：** 非规范性实施计划、过程与证据记录
- **设计来源：** [D025](../designs/D025-pf-host-partial-protocol.md)
- **评审来源：** [R006](../../reviews/R006-pf-cli-system-review.md) §3.3、
  [R007](../../reviews/R007-pf-current-improvement-priorities.md) §2
- **实施基线：** `945f01e`（`docs: update R008`）
- **实现提交：** 工作树未提交；本 Plan 与 D025 在同一完成变更中归档

本文把 D025 验收标准映射到有序切片、测试和证据槽。R007 的 CI coverage 门禁不属 D025 产品
契约，作为同一次工程变更的独立切片记录。完成标准只来自 D025 §6 与 CI 门禁定义，不以局部
绿色或单一 Python 版本替代验收。

## 1. 目标与边界

- `TerminalPresenter` 用 `(reasons, cell_results, target_cells)` 判定 host-partial success；
  search 该类退出 `0` 且 stderr warning incomplete final；empty-host、同宿主缺失、本机失败
  与混合结果保持非零；
- `render_minimize` 消费 search report 只为投影 remainder/merge next，不重算 apply authority；
- 不改 report wire、Authorizer、merge、`--force` 或 CLI 装配图；
- Python 3.10 CI job 以完整 `--no-testmon --cov` 执行现行 `fail_under = 90`；3.11/3.12 仍跑
  完整无 coverage 套件。

## 2. 基线事实与目标差距

| 切面 | `945f01e` 当前事实 | 目标 |
| --- | --- | --- |
| search 退出 | 任何非空 incomplete reasons → `2` | §2 host-partial → `0`；其余保持 |
| search final | host-partial 已有 reason-aware 文案，但走退出 `2` | 文案保留；通道 stderr；退出 `0` |
| minimize | `del report`；成功 apply 只渲染 apply 卡 | 从 report 投影 remainder；warning final 含 merge next |
| CI | 三版本均 `pytest --no-testmon`，coverage 不阻断 PR | 3.10 带 `--cov` 门禁；其余 minor 无 coverage 全量 |
| owner | D001 退出 `2` = 无 floor；D006 未定义 host-partial 退出 `0` | 归并后归档 D025/P031 |

## 3. Interface 与 ownership

1. `TerminalPresenter` 拥有 D001 退出映射与 D006 final/card 投影；判定函数留在 terminal 包内，
   不新建 public protocol module。
2. `ApplyAuthorizer` 与 `ApplyCommandResult` 不变；minimize 只把 report 的 host-partial remainder
   附加到已有 apply 展示。
3. `cli.py` 的 minimize 顺序（search → 默认 apply → `render_minimize(report, result)`）不变。
4. CI workflow 拥有门禁形状；阈值仍由 `pyproject.toml` `[tool.coverage.report] fail_under`。

## 4. 实施顺序

### 切片 000 — CI coverage 门禁

Python 3.10 job 执行 `uv run pytest --no-testmon --cov --cov-report=term-missing`；3.11/3.12
执行 `uv run pytest --no-testmon`。不降阈值，不用 testmon 或 focused selection 生成门禁
coverage，不 deselect 网络资格测试。

### 切片 001 — search 退出与 host-partial 判定

用 `TerminalPresenter.render_search` 锁定 D025 §3 各类退出码、通道与 final 语义。实现判定：
reasons 仅为 `MISSING_CELL`、观察结果全为 `CellSuccess`、缺失 Cell 的 `target` 均异于已观察
集合。同宿主缺失不得使用 await-other-hosts / merge next。

### 切片 002 — minimize remainder final

`render_minimize` 保留一张 apply/minimize 卡。host-partial 成功 apply 走 stderr warning
final，包含 remaining other-host 计数与 `pf merge`；完整成功路径不变。source-drift 叠加时
仍是一张卡、一个 final。CLI public test 覆盖 host-partial minimize。

### 切片 003 — owner 归并与归档

把稳定规则写入 D001/D006 与根 README；关闭 R006/R007 对应项；更新文档索引；归档 D025/P031。

## 5. 测试矩阵

| 场景 | 公开 seam |
| --- | --- |
| host-partial search 退出 0、stderr incomplete、含 merge next | `render_search` |
| empty-host 退出 2 | `render_search` |
| 同宿主缺失退出 2、无 merge next | `render_search` |
| 本机 no-floor、混合 MISSING_CELL、baseline、indeterminate、完整成功 | 现有 + 校准后的 `render_search` |
| host-partial minimize 退出 0、warning final、Preserved 非 passed | CLI `minimize` / `render_minimize` |
| 完整 search 后 minimize 仍 stdout 成功 | 现有 CLI minimize |

## 6. 验证命令

```text
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon \
  tests/test_terminal.py tests/test_cli.py -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python 3.10 pytest --no-testmon --cov --cov-report=term-missing -q
uv run ruff check src tests
uv run ty check src
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python 3.11 pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python 3.12 pytest --no-testmon -q
uv build
git diff --check
```

网络资格失败与 coverage/代码失败分开记录。

## 7. 验收对照

| AC | 切片 | 证据 |
| --- | --- | --- |
| D025 §6.1 host-partial search 退出 0 | 001 | focused render_search |
| D025 §6.2 非 host-partial 退出不变 | 001 | 参数化 + 同宿主缺失 |
| D025 §6.3 完整成功不变 | 001 | 现有 complete 用例 |
| D025 §6.4–6.5 minimize | 002 | CLI/presenter |
| D025 §6.6–6.7 文档与归档 | 003 | owner/index/archive |
| CI coverage 门禁 | 000 | `.github/workflows/ci.yml` + 3.10 `--cov` |

## 8. 行动记录

### 切片 000–003

- **结论：** Python 3.10 CI 接入 `--cov` 门禁；host-partial search 退出 `0`；minimize warning final
  含 remainder 与 `pf merge`；D001/D006/README/R006/R007 已归并，D025/P031 归档。
- **证据：**
  - `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_terminal.py tests/test_cli.py -k 'search_reasons or search_missing or search_mixed or search_same_host or search_incomplete or MinimizeCommand'` → `17 passed`
  - `uv run ruff check src tests` → All checks passed
  - `uv run ty check src` → All checks passed
  - `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python 3.10 pytest --no-testmon --cov --cov-report=term-missing -q` → `1499 passed`；total coverage `90.16%`，达到 `fail_under = 90`。本地 33 failed 全部为 TTY 宽断言（`80 <= 56` / live frame），与 P030 记录的同一环境限制，不是本变更回归。
  - `.github/workflows/ci.yml`：3.10 `--cov --cov-report=term-missing`；3.11/3.12 `--no-testmon`

## 9. 完成核对

- [x] D025 §6.1–6.5 由 public presenter/CLI tests 覆盖
- [x] D001/D006/README 归并 host-partial 退出与 minimize remainder
- [x] R006/R007 关闭对应项
- [x] CI coverage 门禁接入 3.10
- [x] 本 Plan 与 D025 在同一完成变更中归档

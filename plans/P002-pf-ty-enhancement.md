# P002 — `ty` 增量静态证据实施记录

- **状态：** 已完成
- **完成日期：** 2026-08-20
- **性质：** 非规范性历史记录
- **设计来源：** [D004](../docs/designs/D004-pf-ty-enhancement.md)
- **现行失败处置：** [D005](../docs/designs/D005-pf-failure-and-diagnose.md)
- **起始提交：** `36bcd2f80626204fbc56204a388d4c5b6f9876ac`

本文只记录 D004 的 TDD 与验证过程。现行诊断 identity 与 multiset 语义由 D004 定义；这些静态事实如何形成 Rejection/Indeterminate 及进入 FailureRecord 由 D005 定义。本计划不保留第二份契约。

## 1. 纵向切片

| 切片 | 可观察行为 | RED / GREEN 证据位置 |
| --- | --- | --- |
| 001 | Adapter-owned GitLab argv 与冲突配置在进程前失败 | `tests/test_ty_adapter.py` |
| 002 | exit 0/1 的完整 JSON 规范化为确定 `TyCheck` | `tests/test_ty_adapter.py` |
| 003 | 截断、非法 JSON、缺字段和工具退出 fail closed | `tests/test_ty_adapter.py` |
| 004 | 静态 baseline capture 与诊断多重集增量 | `tests/test_evaluation.py` |
| 005 | FullEvaluator 在增量静态失败后短路测试 | `tests/test_evaluation.py` |
| 006 | check 分离最高版本 baseline 与 lowest-direct 对象 | `tests/test_check.py` |
| 007 | search 每 cell capture 一次并向全部 probe 注入同一 baseline | `tests/test_search_coordinator.py` |
| 008 | 报告、explain 与策略 identity 保留完整证据链 | `tests/test_schemas.py`、`tests/test_report.py`、`tests/test_terminal.py` |
| 009 | 全量门禁和双轴 review 回归 | 全套 tests、`ty check`、`uv build` |

每个切片先增加一个公开行为测试并确认 RED，再写最小实现至 GREEN。外部进程只在 ProcessRunner seam 使用 fake；诊断比较通过 StaticEvaluator interface 测试。

## 2. Review 后补齐的反例

双轴 review 构造并修复了：

- nested package 的相对诊断路径错误；
- dotted config override 绕过 adapter-owned 选项；
- 实际 `ty` 版本未进入策略 identity；
- baseline 被复用到其他 cell/snapshot/policy；
- report 中 baseline/final/probe 来自不同 Proposal 或分类与证据结构矛盾；
- 旧 fixture 使用伪造 snapshot digest 绕过新的一致性检查。

所有修复都先增加回归测试，再修改实现。最终两轮复核无未解决 finding。

## 3. 交付与验证

相关提交：

```text
e188c8c docs: treat ty static pass as incremental vs the highest-version baseline
36bcd2f docs: make ty a gitlab JSON diagnostic collector
1c9284f feat: compare ty diagnostics against highest baseline
```

完成门禁：

```text
uv run pytest -q --no-testmon --cov=pf --cov-report=term-missing
  -> 315 passed, 90.43% branch coverage
uv run ty check src tests
  -> All checks passed
uv build
  -> wheel and sdist built
```

这些数字是 P002 完成时的历史快照。之后的修复提交 `1ac35e1` 与 `932f136` 继续增加回归覆盖，当前结果应以重新运行门禁为准。

## 4. 环境与工作树说明

- 真实本地 `ty` 输出用于确认 GitLab 路径格式；外部平台仍由 adapter/Schema 测试覆盖。
- Rich 非 TTY 竞态通过只在真实 `isatty()` 流启用 live renderer 修复。
- `jobs` 被证明是调度输入，不进入 Evaluation 策略 identity。
- 用户未跟踪的 `package-floor.json` 在实施中未修改、暂存或提交。

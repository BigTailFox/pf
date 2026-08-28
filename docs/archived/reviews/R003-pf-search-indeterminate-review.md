# R003 — PF pytest collection failure 误分类评审

- **状态：** 已归档（问题已解决）
- **日期：** 2026-08-23
- **修订日期：** 2026-08-25
- **性质：** 非规范性评审；不定义命令、算法、Schema 或 module interface
- **对照：** `main` / `86bcf58`（`fix: make smoke and check self-verifying`）；运行证据来自同一 checkout 的 source snapshot `f4b96d18a0c8504fe36fa58d944c01a7d308cc57a79994e9b5d50c011ec9051d`
- **契约所有者：** [D001](../../designs/D001-pf.md)、[D003](../../designs/D003-pf-search-algorithm.md)、[D005](../../designs/D005-pf-failure-and-diagnose.md)、[D008](../../designs/D008-pf-verification-run.md)、[D011](../designs/D011-pf-runtime-backed-static-search.md)
- **前序评审：** [R002](R002-pf-v1-architecture-review.md)
- **探索证据：** [I001](../investigations/I001-pf-pytest-witness-collection.md)
- **后续整改：** [D013](../../designs/D013-pf-pytest-observer.md)

本文记录 PF 自搜索中一次 pytest collection failure 被错误压缩为 `ProbeIndeterminate` 的问题。本文只评审 test evidence classification；不改变现行搜索模型，也不把 dependency harness 的替代计划搜索纳入本次整改。整改契约与结局分类只以 [D013](../../designs/D013-pf-pytest-observer.md) 为准；本文与 [I001](../investigations/I001-pf-pytest-witness-collection.md) 是参考快照。

## 1. 结论

当前存在一个 P1 证据分类缺口：pytest 已经执行用户配置的 test command，并在 collection 中记录测试模块导入失败，但 `TestAdapter` 只按进程退出码分类。pytest 默认以 exit 2 结束该 session，PF 因而产生 `TOOL_FAILURE / ProbeIndeterminate`，而不是 `TEST_FAILURE / ProbeRejection`。

现行 CoordinateSearch 收到真实 `ProbeIndeterminate` 后终止当前 cell，符合 D001、D003 与 D005 的现行契约。本次证据不能证明 terminate-on-Indeterminate 是搜索缺陷；它证明的是：

```text
candidate test-contract failure
        -> wrong TestOutcome
        -> false ProbeIndeterminate
        -> expected cell termination
```

本次整改应提高 pytest failure evidence 的分类完整性，使可由 pytest 直接、可靠证明的 collection/test failure 进入现有 Rejection 路径。它不应通过放宽 search 对 Unknown 的容忍度来掩盖上游误分类。

## 2. `packaging==19.2` 的 dogfooding evidence

报告 `package-floor.json` 的 `report_generation_id` 为 `2ef24572bddabd960e2266014bbecab2440ac5ed169c678bd687dac9a46c819d`，三个 Python cell 均为 `incomplete`。共同的运行链为：

```text
project/environment resolution  PASS
environment installation        PASS
ty static check                 PASS
pytest collection               FAIL
```

pytest 在三个环境中均进入 test session，观察到 `591 items / 3 errors`，然后以 `Interrupted: 3 errors during collection` 和 exit 2 结束：

| Cell | Failure ID | collection failure |
| --- | --- | --- |
| Python 3.10 | `failure-3aac9dabde1a7099` | `packaging.utils` 没有 PF 使用的 `InvalidSdistFilename` |
| Python 3.11 | `failure-955e70b47f5f18df` | `packaging.utils` 没有 PF 使用的 `InvalidSdistFilename` |
| Python 3.12 | `failure-24e2f7fe600949cb` | `packaging.tags` 导入 Python 3.12 已移除的 `distutils` |

这形成一个真实的 PF dogfooding 反例：resolver success 与 static PASS 都不能证明 dependency candidate 可运行。这里的 static PASS 只表示没有观察到 `ty` 能静态证明的 regression，不表示 candidate 已被证明兼容。`packaging==19.2` 不是当前 project metadata 所声明的可接受最低版本，但它是 floor search 主动探测并由动态验证拒绝的真实 candidate。

因此，本次 candidate 的正确动态 disposition 是：

```text
pytest collection failure
        -> TEST_FAILURE
        -> ProbeRejection
```

traceback 中的具体 symbol/module 仍是有价值的诊断信息，但不应参与 Rejection 授权。

## 3. 根因

现行 `TestAdapter` 对任意 test command 使用相同的 exit-code contract：

```text
exit 0                         -> TestPass
configured failure exit code  -> TestFail
timeout                        -> TIMEOUT / ToolFailure
其他退出                       -> TOOL_FAILURE / ToolFailure
```

默认 `test-failure-exit-codes` 只有 1。pytest exit 2 是 session-level summary，可能来自 ordinary collection interruption，也可能来自用户中断或其他未可靠完成的执行；该整数本身不足以授权 Rejection。

因此，当前 interface 存在两种错误选择：保留默认值会漏掉确定的 collection failure；把 exit 2 整体加入失败码则会把不可靠终态误判为 Rejection。真正缺失的是 pytest adapter 能够消费的、由 pytest 直接产生的稳定 failure evidence。

## 4. 整改方向

PF 应按以下原则分类 pytest 结果：

> **PF 不判断谁应为测试失败负责；PF 判断当前 candidate 是否完成了用户声明的 test contract。**

pytest-specific 事实应封装在现有 `TestOperations.run(...) -> TestOutcome` adapter seam 内。只有 pytest 直接、结构化报告的 collection 或 test failure 才能进入现有 `TestFail -> TEST_FAILURE -> Rejection` 路径；启动失败、timeout、signal、usage/config error、pytest internal error以及缺失、损坏或矛盾的结构化结果保持 `ToolFailure -> Indeterminate`。

整改不得依赖：

- stderr 或 traceback substring；
- traceback frame、module 或 distribution ownership；
- project graph provenance 或 root-cause attribution；
- PF 自行维护的 pytest lifecycle 状态机；
- 把 pytest exit 2 默认映射为 test failure。

具体证据协议、pytest 集成方式和 Schema 影响由 D013 在版本实验后定义，不由本 Review 提前规定。

## 5. 完成标准

- `packaging==19.2` × Python 3.10–3.12 的 ordinary collection failure 均形成直接 `ProbeRejection`；
- assertion、fixture setup/call/teardown 等 pytest 明确报告的 test failure 保持 Rejection；
- pytest 启动、usage/config、internal error、没有直接 failure witness 的外部中断、timeout 与 signal 保持 `ProbeIndeterminate`；
- 缺失、损坏、版本不兼容或相互矛盾的结构化 evidence fail closed；
- 分类不解析 stderr/traceback，也不判断 project、dependency、plugin 或 harness 的责任归属；
- generic command adapter 的用户配置 exit-code contract 保持兼容；
- CoordinateSearch 对真实 `ProbeIndeterminate` 的终止语义不变。

## 6. 本次非目标

### 6.1 Unknown-aware search

本文不改变 PF v1 的单调搜索假设、binary search 或 exact-floor authority。现行规则继续是：

```text
PASS          -> 移动 PASS 边界
REJECTED      -> 移动 rejection 边界
INDETERMINATE -> 终止当前 cell
```

是否允许跳过 point-local Unknown，以及它对单调性、probe complexity、exact floor 和报告语义的影响，是独立的产品设计问题。真实项目数据证明有必要之前，不纳入 R003/D013。

### 6.2 Harness alternative-plan search

D012 在 selected harness plan 无法实例化时寻找其他合法 realization，暂记为未来优化项。当前没有观察到必须穷举或搜索替代 harness plan 才能完成的真实 case，本 Review 不为它建立优先级、契约或完成标准。

### 6.3 `ty` classification

本次不修改 D004/D011 的静态证据语义。static PASS 不是 compatibility proof；运行时验证仍是 floor evidence 的必要组成部分。

## 7. 整改路径

不要把 `test-failure-exit-codes = [1, 2]` 当作修复。exit 2 仍可能是 KeyboardInterrupt 或其他不可靠终态；把 2 加入失败码会扩大误拒绝。

整改是 [D013](../../designs/D013-pf-pytest-observer.md) 的 PF-owned pytest failure-witness plugin：在 direct pytest 与默认 `[1]` 下注入最小 observer，只有 pytest 直接报告的 collection/test failure 才进入现有 Rejection 路径。

## 8. 验证范围

原始评审完成了以下只读核对：

- 读取三个 CellResult、terminal FailureRecord、packaging observations 与 projection evidence；
- 读取对应 Process Log，确认 Python 3.10/3.11 的 `InvalidSdistFilename` failure 与 Python 3.12 的 `distutils` failure；
- 对照 TestAdapter、RuntimeEvaluator、FailurePolicy、CoordinateSearch 与 D001/D003/D005/D008/D011；
- 确认 `UNREPRESENTABLE_PROJECTION` 是 cell 没有可授权 floor 的派生结果，不是第二个独立根因。

本次修订收缩了评审结论与整改范围，并去掉 `test-failure-exit-codes = [1, 2]` 临时绕过，改以 D013 plugin 为整改路径。没有重新运行完整 `pf search`、全量 pytest、ty 或 build。R003 仍是非规范性快照，不把文档修订写成新的行为验证。

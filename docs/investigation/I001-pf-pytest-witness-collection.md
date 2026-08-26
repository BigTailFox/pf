# I001 — PF pytest failure-witness collection 探索

- **状态：** 快照
- **日期：** 2026-08-25
- **性质：** 非规范性探索与实验记录；不定义命令、算法、Schema 或 module interface
- **代码基线：** `9f4e088`
- **评审来源：** [R003](../reviews/R003-pf-search-indeterminate-review.md)
- **后续设计：** [D013](../designs/D013-pf-pytest-failure-evidence.md)
- **权威：** 结局分类、协议与 adapter 契约只以 [D013](../designs/D013-pf-pytest-failure-evidence.md) 为准；本文与 R003 是参考

本文记录 minimal pytest failure-witness plugin 的行为实验。实验回答：PF 能否只依赖 pytest 公开 failure-report hooks，在不改变用户 collection/runtest 行为、不解析 traceback、也不维护 pytest lifecycle 状态机的前提下，为 ordinary collection failure 建立保守的 Rejection evidence。

实验夹具与 plugin 是 throwaway prototype，不纳入仓库；本文保存实验问题、输入矩阵、观察结果、反例与设计结论。实验没有修改 PF production code。

## 1. 原型

plugin 除 pytest 与 Python standard library 外没有其他依赖，只实现两个公开 hook：

```text
failed CollectReport
    -> COLLECTION_FAILED / collect

failed TestReport in setup/call/teardown
    -> TEST_FAILED / matching phase
```

原型不读取 traceback、longrepr、nodeid、path 或测试输出，不判断 module/distribution ownership，不记录 lifecycle state，也不改写 pytest exit status。每个 failure 写一条 canonical JSON line；写入失败被 plugin 吞掉，使未来 adapter 可以因 evidence 缺失 fail closed，而不改变 pytest 自身结果。

调用形状为：

```text
PYTHONPATH=<isolated-plugin-directory> \
pytest -p pf_pytest_failure_witness <user arguments>
```

原型只增加 observer plugin，不添加 `--continue-on-collection-errors`、`--maxfail` 或其他测试策略参数。

## 2. 实验范围

### 2.1 pytest core matrix

CPython 3.10 上验证：

```text
pytest 6.2.5
pytest 7.0.1, 7.4.4
pytest 8.0.2, 8.4.2
pytest 9.0.2, 9.1.1
```

七个版本各执行 18 个场景，共 126 次 core observation。

pytest 6.0.2 不作为有效资格点：其 assertion rewriting 在 CPython 3.10 上先失败，错误为 `TypeError: required field "lineno" missing from alias`。该版本没有进入 witness 行为结论。

### 2.2 当前 PF plugin/Python matrix

当前 PF test ecosystem 使用：

```text
pytest 9.1.1
pytest-cov 7.1.0
pytest-env 1.7.0
pytest-testmon 2.2.0
pytest-benchmark 5.2.3
```

该组合在 CPython 3.10、3.11 与 3.12 上分别验证 pass、assertion、collection import、initial conftest、collection internal error、KeyboardInterrupt 与 early plugin import。

## 3. Core 结果

七个 pytest 版本得到一致结果：

| 场景 | Exit | Witness |
| --- | ---: | --- |
| passing suite | 0 | 无 |
| assertion failure | 1 | `TEST_FAILED/call` |
| fixture setup failure | 1 | `TEST_FAILED/setup` |
| fixture teardown failure | 1 | `TEST_FAILED/teardown` |
| test-module import failure | 2 | `COLLECTION_FAILED/collect` |
| nested collection-time conftest import failure | 2 | `COLLECTION_FAILED/collect` |
| initial conftest import failure | 4 | 无 |
| `pytest_collection_modifyitems` internal error | 3 | 无 |
| test raises `KeyboardInterrupt` | 2 | 无 |
| explicit `-p` plugin import failure | 1 | 无 |
| missing plugin named by `pytest_plugins` | 1 | 无 |
| no tests collected | 5 | 无 |
| invalid CLI option | 4 | 无 |
| passing session rewritten to exit 1 | 1 | 无 |
| failing session rewritten to exit 0 | 0 | `TEST_FAILED/call` |
| reporting hook internal error after failed call | 3 | `TEST_FAILED/call` |
| collection failure with default abort | 2 | `COLLECTION_FAILED/collect` |
| collection failure followed by internal error | 3 | `COLLECTION_FAILED/collect` |

不可写的 evidence destination 没有改变 pytest：collection 仍以 exit 2 结束，且没有 witness。这说明 observer-storage failure 可以降级为 Indeterminate，而不必成为新的 pytest internal error。

## 4. 当前 PF plugin/Python 结果

pytest 9.1.1 与当前四个 PF pytest plugins 在 Python 3.10–3.12 上完全一致：

| 场景 | 3.10 | 3.11 | 3.12 | Witness |
| --- | ---: | ---: | ---: | --- |
| pass | 0 | 0 | 0 | 无 |
| assertion failure | 1 | 1 | 1 | `TEST_FAILED/call` |
| collection import failure | 2 | 2 | 2 | `COLLECTION_FAILED/collect` |
| initial conftest import failure | 4 | 4 | 4 | 无 |
| collection hook internal error | 3 | 3 | 3 | 无 |
| KeyboardInterrupt | 2 | 2 | 2 | 无 |
| explicit plugin import failure | 1 | 1 | 1 | 无 |

该矩阵没有观察到当前 PF plugins 吞掉、伪造或重写 prototype witness 的情况。

## 5. `packaging==19.2` 聚焦复现

聚焦 fixture 在 `packaging==19.2` 下导入 PF 使用的 packaging interface：

- Python 3.10/3.11 导入缺失的 `packaging.utils.InvalidSdistFilename`；
- Python 3.12 导入 `packaging.tags`，触发 packaging 19.2 对已移除 `distutils` 的依赖。

当前 PF pytest plugin 组合得到：

| Python | Exit | Witness |
| --- | ---: | --- |
| 3.10 | 2 | `COLLECTION_FAILED/collect` |
| 3.11 | 2 | `COLLECTION_FAILED/collect` |
| 3.12 | 2 | `COLLECTION_FAILED/collect` |

这不是一次完整 `pf search` 重跑，但机械复现了 R003 所需的关键事实：相同 runtime incompatibility 会被 minimal plugin 观察为 failed `CollectReport`，无需解析具体 import traceback。

## 6. 反例与分类约束

实验否定了“pytest exit 1 可以单独授权 Rejection”：

- early plugin import exception 在所有代表版本中都可能直接产生 exit 1，但没有 test/collection failure witness；
- passing session 可以被第三方 plugin 改写为 exit 1，同样没有 witness；
- failing session 可以被改写为 exit 0，此时 witness 与最终 exit 相互矛盾；
- failed report 后仍可能发生 pytest internal error，形成 exit 3 + earlier witness。

因此，production adapter 必须联合验证 ProcessResult 与 witness。下面的名字只是本次实验的观察用语，不是 Schema 或 `TestOutcome`；规范决策表只在 D013。

```text
exit 0 + no witness
    -> PASS

exit 1 + direct collection/test witness
    -> TEST_REJECTED

exit 2 + direct collection witness
    -> TEST_REJECTED

missing/malformed/unsupported/conflicting witness
pytest internal/usage/bootstrap failure
unwitnessed exit 1/2
    -> TEST_UNAVAILABLE
```

exit 3 即使已有 earlier witness，v1 也应保守保持 unavailable。原型证据表明 classifier 不需要 complete；它只需确保进入 Rejection 的 evidence 可靠。

## 7. 结论

minimal two-hook failure witness 足以覆盖 R003：

```text
packaging==19.2
    -> failed CollectReport
    -> collection witness
    -> existing TestFail / Rejection path
```

相比 invocation normalization，它不要求继续执行已成功收集的 tests；相比 lifecycle observer，它不维护 pytest 阶段状态机。pytest-specific knowledge 可以保留在 `TestAdapter` implementation 内，RuntimeEvaluator、FailurePolicy 与 CoordinateSearch 无需学习 pytest 细节。

原型的 append-only JSON line 只用于探索，不是 production protocol。D013 应使用 run-unique identity、严格版本校验与 atomic per-event files；多 process 的等价重复 event 合法折叠，身份字段不一致则 fail closed。

## 8. 限制

- core pytest 6–9 matrix 运行于 CPython 3.10；跨 Python minor 只验证当前 PF plugin 组合。pytest 无 native 代码，这不构成缩小 v1 资格范围的理由；D013 落地测试须在 3.11/3.12 上复跑各 major 代表版本的核心 witness 场景；
- 没有资格化 pytest-xdist 或任意第三方 plugin ecosystem；
- 没有重跑完整 `pf search`、全量 PF pytest、ty 或 build；
- 本文只记录行为事实与设计输入，不把 D013 草案写成现行 PF 行为。

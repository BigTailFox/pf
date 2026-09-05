# PF pytest observer 与诊断遥测

- **状态：** 现行
- **Observer 协议：** `pf-pytest-observer-v1`
- **最后核对：** 2026-09-05
- **Verifier authority：** [D002](D002-pf-implementation.md)、[D005](D005-pf-failure-and-diagnose.md)
- **进程事实：** [D007](D007-pf-process-output.md)
- **运行时投影：** [D008](D008-pf-verification-run.md)

本文只拥有 direct pytest 的私有 observer、progress 与 detail telemetry。pytest facts 不决定
compatibility disposition；配置 verifier 的唯一 authority 是 D002/D005 定义的 child process
terminal。

## 1. 边界

`ConfiguredVerifier` 可机械识别以下 direct pytest command shape，仅用于注入 UI telemetry：

```text
pytest ...
py.test ...
python -m pytest ...
python3 -m pytest ...
pythonX.Y -m pytest ...
```

wrapper、tox、nox、coverage 或其他 generic command 不注入 observer。这个 selector 不进入
evaluation policy identity，也不改变 D005 定义的 terminal outcome。pytest version、execution
mode、facts、output completeness、progress 和 detail 都不能改变 disposition；terminal 与
failure metadata 冲突只形成 runtime `summary_code`。

## 2. Trusted observer

PF 把 `pf/_pytest_observer.py` 复制到 invocation-local 私有目录，并通过 `-p <module>` 注入。
私有环境变量是：

```text
PF_PYTEST_OBSERVER_DIR
PF_PYTEST_OBSERVER_NONCE
PF_PYTEST_OBSERVER_DETAILS_DIR
PF_PYTEST_OBSERVER_CASES_DIR
PF_PYTEST_OBSERVER_CASES_PROJECTION
PF_PYTEST_PROGRESS_DIR
PF_PYTEST_PROGRESS_NONCE
PF_PYTEST_PRUNE_REQUEST
PF_PYTEST_PRUNE_NONCE
```

嵌套 invocation 必须先从继承环境删除全部私有变量，再应用本次 overlay。Observer 不得修改
test selection、collection continuation、hook outcome、执行顺序或 pytest exit status。
`PF_PYTEST_PRUNE_*` 由 `ConfiguredVerifier` 的 private pruning plugin 消费；observer 只读取
nonce 一致性，不替换 `Config.args`。

Observer 只观察 pytest 公开 hook：

```text
pytest_collectreport(failed)                         -> COLLECTION_FAILED / collect
pytest_runtest_logreport(failed, setup|call|teardown) -> TEST_FAILED / phase
pytest_internalerror                                 -> INTERNAL_ERROR / pytest
```

normal exit 后，`ConfiguredVerifier` 尝试读取可选 finalized summary。缺失、不可读、损坏、
非 canonical、nonce 不符、identity 冲突或资源超限时严格丢弃整份投影，不抛命令级
InfrastructureError、不修改已取得的 terminal，也不重跑未注入的原命令。正常 exit 0/非零仍
分别授权 PASS/REJECTED；timeout、signal、start failure 或 typed terminal unavailable 的
Indeterminate 映射保持。进程启动前 observer/pruning 准备、非法进程终态及必需资源 cleanup
失败仍按 D002 的基础设施失败处理，不静默取消注入。

summary 不可用时 diagnostics 保留 process，pytest version/minor/mode 留空、facts 为空；
这不表示观测到“没有失败”，也不计算 terminal/summary metadata-conflict。独立合法的
progress/detail/cases 继续消费。

## 3. Summary protocol

每个 summary 是 canonical UTF-8 JSON：字段精确、排序、无多余空白、单个末尾换行。协议
identity 是 `pf-pytest-observer-v1`，record 至少包含：

```json
{
  "execution_mode": "serial",
  "facts": [],
  "finalized": true,
  "protocol": "pf-pytest-observer-v1",
  "pytest_version": "9.1.1",
  "python_implementation": "cpython",
  "python_minor": "3.12",
  "run_nonce": "..."
}
```

Reader 一次有界枚举并逐文件有界读取；只接受 regular file、`summary-<32 hex>.json`、当前
nonce、CPython identity、规范 fact 集合以及一致的 runtime identity。多个合法 worker
summary 可以合并，facts 作 set union；未知文件、重复冲突 identity、临时文件或过量文件均
使整份 summary 投影丢弃，不能部分采用坏文件中的 facts。

`execution_mode = serial | xdist | unknown` 只是诊断事实。PF 不维护 pytest 版本/执行模式的
Rejection 白名单。

## 4. Optional progress 与 detail

Progress 使用 invocation-local `pf-pytest-progress-v1` snapshot，表达
`completed/total/unit=tests`。只有 serial、非 collect-only、nodeid 唯一且 nonce 匹配时发布；
monitor 启动、读取、consumer 或 cleanup 失败只能丢弃 progress，不改变 terminal outcome。
最后一个合法 determinate progress 可以由 D006 UI 冻结展示。

Failure detail 使用 `pf-pytest-observer-details-v1`，只保存首个安全 nodeid/phase 与去重后的
失败总数。Reader 有文件数、字节数与显示文本边界；缺失、损坏、写入失败、非法控制字符或
多 artifact 只省略 detail。它通过：

```text
VerifierRun.diagnostics
-> RuntimeEvaluationRun.diagnostics
-> D008 completion projection
-> CellResultDetail(detail_failure_id)
```

detail 不进入 disposition、cause、FailureRecord、Attempt、Proposal、cache、Journal、report、
merge/apply 或 policy identity。

## 4.1 Optional collected / failed projection

Observer 按阶段写入同一私有协议 `pf-pytest-observer-cases-v1` 的不同 projection：

- 原命令阶段：`failed`，仅 setup / call / teardown 失败 nodeid，供 FailedCaseSet additions；
  不序列化完整原命令 collection。
- failed-set 阶段：`collected`，collection 是否完成及最终 `session.items` nodeids，供
  collection 证明；即使同时观察到 failure，也不作为 additions。

serial 与 xdist **controller** 在 `pytest_collection_finish` 之后的 `session.items` 是
`collected` 权威。pytest-xdist 的 controller `pytest_collection` 禁止收集 items，因此
controller 通常没有 collected projection；此时视为无法证明，回退原命令，不得用 worker
投影单独授权 Rejection。worker `collected` 只做防御：内部唯一且 ⊆ requested 即可；含请求外
item 则回退。`failed` 对多个 worker 作 set union 后按 nodeid 排序，不设数量上限。冲突、非法、
资源越界或无法证明本次 invocation 时丢弃整个可选 projection，不得截断。

该 artifact 只供 `ConfiguredVerifier` 决定 failed-set normal terminal 能否采用；不进入
`VerifierRun.authoritative`、Failure authority 或任何 schema。summary 与 cases 资格独立：
只有合法 collected 证明才可采用 failed-set 正常非零 terminal；不能证明时即使 summary 合法
也须回退原命令。证明有效但 summary 不可用时仍可采用非零结果。failed-set exit 0 仍须运行
完整原命令，不定终态仍不回退。failed additions 只来自独立通过资格检查的 failed projection，
不能从 summary 推导。pruning plugin 与 argv overlay 不属于本文。

## 5. 透明性资格与发布资源

`scripts/qualify_pytest_observer.py` 的版本矩阵只证明注入在已观察 case 中保持 pytest exit、
selection、hook outcome、执行顺序与 canonical telemetry。`scripts/qualify_pytest_pruning.py`
覆盖 FailedCaseSet 的 `Config.args` 替换、动态 collection 回退与 xdist `--dist load`：controller 无
collected projection 时必须回退原命令，不得用 worker 投影单独授权 Rejection。资格结果不授予
Rejection authority，也不进入生产 selector 或 identity。

Wheel 与 sdist 必须包含 `pf/_pytest_observer.py` 和 `pf/adapters/pytest_observer.py`。公开行为
测试只穿过 `ConfiguredVerifier.run(VerifierRequest) -> VerifierRun`；terminal classifier、
command-shape selector、注入与 telemetry projection 都是私有实现。private pruning plugin 是
D002 `ConfiguredVerifier` 资源，资格矩阵须覆盖 `Config.args` 替换顺序保证。

## 6. 非目标

- 不解析 traceback、stderr、exception type 或 pytest facts决定 disposition；
- 不根据 observer 推测“未注入时会得到的 exit code”；
- 不重跑 verifier、修复 pytest 配置或维护 test-runner classifier registry；
- 不替代 D004 的 runtime interface witness；`RuntimeWitness` 仍是独立 pre-verifier operation。

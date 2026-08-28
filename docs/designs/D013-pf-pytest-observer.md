# PF pytest observer 与诊断遥测

- **状态：** 现行
- **Observer 协议：** `pf-pytest-observer-v1`
- **最后核对：** 2026-08-28
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
PF_PYTEST_PROGRESS_DIR
PF_PYTEST_PROGRESS_NONCE
```

嵌套 invocation 必须先从继承环境删除全部私有变量，再应用本次 overlay。Observer 不得修改
test selection、collection continuation、hook outcome、执行顺序或 pytest exit status。

Observer 只观察 pytest 公开 hook：

```text
pytest_collectreport(failed)                         -> COLLECTION_FAILED / collect
pytest_runtest_logreport(failed, setup|call|teardown) -> TEST_FAILED / phase
pytest_internalerror                                 -> INTERNAL_ERROR / pytest
```

normal exit 后，`ConfiguredVerifier` 要求恰有合法 finalized summary；缺失、损坏、非规范、
冲突或超限 artifact 是 PF implementation/protocol failure，命令级抛出
`InfrastructureError`，不产生 Evaluation，也不重跑未注入的原命令。timeout、signal、start
failure 或 typed terminal unavailable 已有完整 authority，不要求 final summary。

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
使 mandatory protocol 失败。

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

## 5. 透明性资格与发布资源

`scripts/qualify_pytest_observer.py` 的版本矩阵只证明注入在已观察 case 中保持 pytest exit、
selection、hook outcome、执行顺序与 canonical telemetry。资格结果不授予 Rejection
authority，也不进入生产 selector 或 identity。

Wheel 与 sdist 必须包含 `pf/_pytest_observer.py` 和 `pf/adapters/pytest_observer.py`。公开行为
测试只穿过 `ConfiguredVerifier.run(VerifierRequest) -> VerifierRun`；terminal classifier、
command-shape selector、注入与 telemetry projection 都是私有实现。

## 6. 非目标

- 不解析 traceback、stderr、exception type 或 pytest facts决定 disposition；
- 不根据 observer 推测“未注入时会得到的 exit code”；
- 不重跑 verifier、修复 pytest 配置或维护 test-runner classifier registry；
- 不替代 D004 的 runtime interface witness；`RuntimeWitness` 仍是独立 pre-verifier operation。

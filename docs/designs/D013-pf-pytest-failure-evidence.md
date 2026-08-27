# PF pytest failure evidence

- **状态：** 现行
- **日期：** 2026-08-26
- **适用范围：** direct pytest `test-command` 的动态失败分类
- **探索记录：** [I001](../investigation/I001-pf-pytest-witness-collection.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **CLI 显示：** [D006](D006-pf-cli-enhancement.md)
- **验证运行：** [D008](D008-pf-verification-run.md)
- **实施记录：** [P012](../plans/P012-pf-pytest-failure-evidence.md)

本文是 pytest failure-witness profile 及其临时协议的唯一契约。它把 pytest 公开 report hook 产生的直接失败事实归一为现有 `TestOutcome`，但不把 pytest 细节扩散到 evaluator、search 或报告 Schema。

## 1. Profile selection

`TestAdapter` 私有地选择两个 profile：

```text
configured-exit-code-v1
pytest-failure-witness-v1
```

pytest profile 只在以下条件同时成立时启用：

- `test-failure-exit-codes == [1]`；
- command 是 direct `pytest`、`py.test`、`python -m pytest`、`python3 -m pytest` 或 `pythonX.Y -m pytest` invocation。

可执行文件可以是绝对路径；selector 只看 basename。wrapper、`coverage run`、tox、nox、`env` 前缀和任何自定义 failure-code 集合都使用 generic profile：exit 0 为 Pass，配置列表中的非零码为 Fail，其余为 ToolFailure。

Profile selector 同时决定实际执行路径和 `evaluation_policy_identity`，不得复制 command-shape 判定。

## 2. Embedded plugin

pytest profile 把 PF wheel 中的 standalone plugin 复制到 run-unique 临时目录，通过前置 `PYTHONPATH` 和 `-p <unique-module>` 注入。Plugin 只依赖标准库和目标 pytest，不导入 PF 或 project source，也不修改用户参数、collection continuation、`maxfail` 或执行顺序。

Authoritative hooks 只记录以下 facts：

```text
pytest_collectreport(failed)                         -> COLLECTION_FAILED / collect
pytest_runtest_logreport(failed, setup|call|teardown) -> TEST_FAILED / phase
pytest_internalerror                                 -> INTERNAL_ERROR / pytest
```

`pytest_sessionstart` 记录 `serial | xdist | unknown` execution mode。`pytest_cmdline_main` 的 outer wrapper 在 pytest action 结束后原子提交最终 fact set；提交失败不能改变 pytest 自己的结果。

Facts 不含 traceback、longrepr、path、nodeid、异常正文或输出。PF 不判断失败属于 project、dependency、plugin 还是 harness，也不建立 pytest lifecycle 状态机。

## 3. Failure-witness protocol

协议 identity 为 `pf-pytest-failure-witness-v1`。每个完成 finalization 的 process 写一个 canonical UTF-8 JSON 文件：

```json
{
  "execution_mode": "serial",
  "facts": [{"kind": "COLLECTION_FAILED", "phase": "collect"}],
  "finalized": true,
  "protocol": "pf-pytest-failure-witness-v1",
  "pytest_version": "9.1.1",
  "python_implementation": "cpython",
  "python_minor": "3.12",
  "run_nonce": "<32 lowercase hex>"
}
```

文件以 `sort_keys=True`、compact separators、ASCII escaping 和末尾单个 LF 编码。Facts 去重并按 `(kind, phase)` 排序。合法 fact 仅为：

- `COLLECTION_FAILED / collect`；
- `TEST_FAILED / setup | call | teardown`；
- `INTERNAL_ERROR / pytest`。

Writer 先完整写临时文件，再原子替换为 `summary-<32 lowercase hex>.json`。不同 process 可以并发提交；adapter 对合法 facts 作 set union，但所有 summary 的 protocol、nonce、execution mode、Python identity 和 pytest version 必须一致。

Adapter 最多读取 1024 个 summary，每个不超过 4 KiB，并要求 bounded regular file、精确字段、canonical bytes、当前 nonce 和合法枚举。未知文件、残留临时文件、非法 JSON/UTF-8、超限、重复 fact 或 identity 冲突都会使整个 evidence 无效。

## 4. Qualification 与分类

Negative-evidence authority 只授予：

- CPython 3.10、3.11 或 3.12；
- serial execution；
- 规范 stable pytest release，且 major/minimum 为 6/6.2.5、7/7.0.1、8/8.0.2、9/9.0.2。

Prerelease、postrelease、dev/local build、其他 major/minor、xdist 或 unknown mode 都未获 Rejection authority。资格证据位于 [`tests/pytest_witness_qualification/matrix-manifest.json`](../../tests/pytest_witness_qualification/matrix-manifest.json)；新增 major、扩大 execution mode 或修改协议时必须更新 matrix 和 policy identity。

分类首先要求 process 正常、完整退出；timeout、signal、start error 或不完整 stdout/stderr 一律为 ToolFailure。随后按以下顺序：

1. evidence 为空：只有 exit 0 为 `TestPass`，非零为 `ToolFailure`；
2. 任意 artifact 非法：`ToolFailure`；
3. 存在 `INTERNAL_ERROR`：`ToolFailure`；
4. profile 未资格化：只有 exit 0 且无 failure fact 为 `TestPass`，其余为 `ToolFailure`；
5. 已资格化 serial profile 按表分类。

| Exit | Failure fact | Outcome |
| ---: | --- | --- |
| 0 | 无 | `TestPass` |
| 1 或 2 | 至少一个 collection/test fact | `TestFail` |
| 1 或 2 | 无 | `ToolFailure` |
| 0 | 有 | `ToolFailure` |
| 其他 | 任意 | `ToolFailure` |

因此 ordinary collection failure 的 exit 2 可以成为 `TestFail`；无 witness 的 KeyboardInterrupt 不能。`TestFail` 到 `TEST_FAILURE` disposition 的映射只由 D005 定义。Internal error、事实矛盾和协议故障总是 fail closed。稳定 `summary_code` 可解释具体 ToolFailure，但不改变 disposition。

`TestAdapter.run(...) -> TestPass | TestFail | ToolFailure` 是 pytest-specific 复杂度的边界。RuntimeEvaluator、FailurePolicy 与 CoordinateSearch 只消费这些既有领域结果。

## 5. UI-only telemetry

进度和失败详情使用独立临时目录与协议，不参与 outcome classification、policy identity、Process Log、Journal、cache 或公共报告。每次 direct-pytest invocation 在启动 child 前先删除继承的 pytest 私有 environment 名字，再注入自己的 witness、nonce 与可用 UI 目录；被测套件内的嵌套 pytest 不得继承或覆盖外层 telemetry。Progress 只有在 invocation-local `PF_PYTEST_PROGRESS_NONCE` 存在且等于当前 witness nonce 时才初始化和提交；仅继承目录或来自另一 invocation 的 activation 不能写 snapshot。任何准备、读取、校验、写入或 cleanup 故障都只能省略 UI 信息。

### 5.1 Progress

`pf-pytest-progress-v1` 的 snapshot 为：

```json
{
  "completed": 37,
  "protocol": "pf-pytest-progress-v1",
  "run_nonce": "<32 lowercase hex>",
  "total": 120,
  "unit": "tests"
}
```

只有 serial、非 collect-only 且无 collection failure 的唯一 nodeid 集合产生 determinate progress。Collection 完成提交 `0/total`；每个 nodeid 首次 `pytest_runtest_logfinish` 后递增一次。

Parent 每 50 ms 轮询不超过 1 KiB 的 bounded canonical file。首次合法 snapshot 后 `total` 固定、`completed` 单调且不超过 total。非法、倒退、缺失或 consumer 异常永久关闭本次 telemetry：首个合法值前继续显示 spinner，之后冻结最后值。

### 5.2 Failure detail

`pf-pytest-failure-details-v1` 只在 authoritative classifier 已产生 `TestFail` 后提供：

```json
{
  "first": {"nodeid": "tests/test_cli.py::test_example", "phase": "call"},
  "protocol": "pf-pytest-failure-details-v1",
  "run_nonce": "<32 lowercase hex>",
  "total": 3
}
```

Plugin 按 nodeid 首次出现去重，最多记录 10,000 个失败，每个 nodeid 最长 4,096 字符且不含控制字符。Adapter 最多枚举 1024 个 artifact、每个不超过 8 KiB；只接受唯一一个当前 nonce 的合法详情。详情存入从 model dump 排除的 `PytestFailureDetail`，只供当前 CLI 显示。

## 6. Degradation

| 场景 | 结果 |
| --- | --- |
| plugin resource、临时目录或注入准备失败 | 执行原 command；exit 0 可 Pass，非零为 ToolFailure |
| plugin import/bootstrap 或 summary commit 失败 | 非零且无 finalized summary 为 ToolFailure |
| initial conftest、config 或 usage failure | 无合格 witness，ToolFailure |
| pytest internal error | ToolFailure，不受最终 exit 改写影响 |
| collection、setup、call 或 teardown failure | 合格 exit 1/2 + witness 为 TestFail |
| xdist pass | 完整 exit 0 且无 failure fact 可 Pass |
| xdist 非零 | ToolFailure |

该 classifier 可以漏掉真实 candidate incompatibility；它不能用不完整证据制造 Rejection。pytest process 和 embedded plugin 也不被视为安全工具。

## 7. 持久化边界

Ephemeral witness 只帮助 adapter 产生既有 `TestFail`。Schema 2 仍只保存 terminal Evaluation、`TEST_FAILURE @ test`、完整 `ProcessResult`、Attempt/Proposal 与 resolution identities，以及本地 Process Log association；pytest fact、nodeid、progress 和临时文件均不进入报告。

## 8. 不变量

- pytest profile 的 exit 1 和 2 都必须有 finalized、合法、已资格化的 direct failure witness，才能形成 TestFail。
- Exit 0 与 failure fact 冲突；internal error、协议损坏和不完整 process 均为 ToolFailure。
- User test selection、collection continuation 和 runtest order 保持不变。
- Generic profile 与用户显式 failure-code contract 保持不变。
- xdist/unknown execution mode 不具有 v1 Rejection authority。
- UI telemetry 只能增加当次可读性，不能改变任何 authoritative outcome。

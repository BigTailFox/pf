# PF failure 语义与 diagnose

- **状态：** 现行
- **策略版本：** `failure-runtime-v2`
- **最后核对：** 2026-08-28
- **领域词汇：** [CONTEXT](../../CONTEXT.md)
- **搜索消费：** [D003](D003-pf-search-algorithm.md)
- **Runtime interface witness：** [D004](D004-pf-ty-enhancement.md)
- **进程事实：** [D007](D007-pf-process-output.md)
- **运行角色与读取面：** [D008](D008-pf-verification-run.md)
- **Harness negative evidence：** [D012](D012-pf-harness-relaxation.md)
- **pytest diagnostics：** [D013](D013-pf-pytest-observer.md)

本文是 Attempt、cause、disposition、FailureRecord identity 和 `diagnose` 语义的唯一
所有者。Configured verifier 先形成 terminal disposition；FailurePolicy 记录它，并对其他
operation facts应用本节资格规则。搜索只消费 disposition；Presenter 只组织稳定文案。

## 1. 三层结果模型

```text
完整成功事实                         -> PASS
完整负向事实                         -> REJECTED
没有可信完整终态                     -> INDETERMINATE
```

Cause 回答发生了什么，不等于 disposition，也不声称单个 dependency 是根因。不得解析
stderr substring、traceback、pytest facts 或自然语言来补 classification。

现行 cause 集合：

```text
RESOLUTION_CONFLICT
BUILD_FAILURE
HARNESS_CONFLICT
RUNTIME_INTERFACE_MISSING
VERIFIER_EXITED_NONZERO
SOURCE_FAILURE
ENVIRONMENT_FAILURE
TOOL_FAILURE
TIMEOUT
INTERNAL_INVARIANT
NONDETERMINISTIC
```

`PASS` 不产生 FailureRecord。Static transition 本身不经过 FailurePolicy。

## 2. Attempt 与 scope

Attempt 在 environment resolution 前建立，identity 绑定 source snapshot、完整 Cell、
resolution request、exact managed vector、active declarations、source/evaluation/resolution/
harness policy 以及可用 selected-candidate evidence。Schema 1 只接受 `attempt-v2`。

Failure scope 是判别 union：

```text
AttemptFailureScope(attempt)
CellFailureScope(package, cell, source_snapshot_digest, policy_identity)
```

Candidate discovery 或 scheduling 在 Attempt 建立前失败时使用 Cell scope；它只能是
Indeterminate，不得虚构 Proposal 或 managed vector。

## 3. Rejection 资格

Rejection 只否定完整 Attempt。现行允许：

| Cause | Stage | Authority |
| --- | --- | --- |
| `RESOLUTION_CONFLICT` | `resolve-project` | D012 资格 profile 证明 project request UNSAT |
| `HARNESS_CONFLICT` | `resolve-environment` | D012 资格 profile 证明 final environment request UNSAT |
| `RUNTIME_INTERFACE_MISSING` | `witness` | D004 structured witness 的 `CONFIRMED_MISSING` |
| `VERIFIER_EXITED_NONZERO` | `test` | configured verifier 的 `NormalExit(exit_code != 0)` |

Configured verifier 的 terminal disposition 已由 `ConfiguredVerifier` 机械形成；
FailurePolicy 不再读取 exit code、output completeness、pytest facts 或 cause 重新判断。
normal nonzero 的具体整数、stdout/stderr 是否完整、pytest phase 与 observer metadata 都不
改变 Rejection。

Cell scope、source/build/environment/tool/internal/nondeterministic failure、timeout、signal、
start failure、typed terminal unavailable 或未建模异常始终不足以 Rejection。它们形成
Indeterminate，或在 adapter/schema invariant 破坏时以命令级 `InfrastructureError` 结束。

D004 runtime witness 的资格保持独立：`PRESENT | NOT_APPLICABLE` 继续 verifier；
`CONFIRMED_MISSING` 可形成 Rejection；witness `ToolFailure` 形成 Indeterminate。

## 4. FailureRecord v2

```text
FailureRecord
  failure_id
  scope: AttemptFailureScope | CellFailureScope
  disposition: REJECTED | INDETERMINATE
  cause
  stage
  authority:
    ProcessFailureAuthority(process)
    | ConfiguredVerifierFailureAuthority(terminal)
    | StructuredFailureAuthority(detail)
  project_plan_digest?
  environment_plan_digest?
```

每条记录必须恰有一种 authority：

- uv/ty/resolver/runtime witness 等 operation 可保存 D007 portable `ProcessResult`；typed
  terminal unavailable 转为稳定 structured authority，不伪造 process facts；
- configured verifier 只保存 `VerifierTerminal`，不重复保存完整 `ProcessResult`；
- scheduler/source 等无进程路径保存稳定、脱敏、可移植 `FailureDetail`。

`failure_id` 对完整 v2 preimage 做 canonical hash：

```text
sha256("pf:failure:v2\0" + canonical_identity_json(payload))
-> failure-<16 hex>
```

Preimage 包含 scope、disposition、cause、stage、完整 authority 与已有 plan digests。Verifier
identity 吸收 terminal kind、normal exit code 或 signal；不吸收 duration、output
completeness、stdout/stderr、start-error 正文、pytest facts/detail/progress、summary、run ID 或
日志 locator。相同 Attempt 的 exit 1 与 exit 4 因而是不同 failure ID。

Environment plan digest 存在时 project plan digest 必须存在。`FailureDetail` 必须非空、
有界、脱敏且可移植；不能保存绝对路径、credential、动态异常正文或本地 locator。

## 5. FailurePolicy interface

```text
FailurePolicy.classify(scope, cause, stage, process?, summary_code?, detail?, plan digests?)
    -> FailureRecord

FailurePolicy.record_evaluation(scope, evaluation, ...)
    -> FailureRecord | None
```

Evaluation 映射固定为：

```text
PassEvaluation                    -> None
RuntimeInterfaceMissingEvaluation -> RUNTIME_INTERFACE_MISSING @ witness
VerifierRejectedEvaluation        -> VERIFIER_EXITED_NONZERO @ test
IndeterminateEvaluation           -> evaluation 自带 verifier/process authority
```

Adapter、RuntimeEvaluator、workflow、CoordinateSearch、ReportStore 与 Presenter 不复制
Rejection classifier。Role 不改变分类或 identity。

## 6. Diagnose

报告、latest Journal 和 Diagnosis Index 的读取范围、优先级与去重只由 D008 定义。
`pf diagnose [package] [--failure FAILURE_ID]`：

- 指定 ID 不存在时配置失败；
- 省略 ID 时按 package、Cell、Attempt/vector 与 failure ID 稳定排序；
- 成功展示零条或多条记录都返回 `0`；
- 不做自动归因、重试、环境重建、项目或报告修改。

每条诊断按 `Failure / Outcome → What happened → Impact → Next step → Context →
Technical details → optional log tail` 表达。Cause 的稳定用户语义：

| Cause | What happened | Next step |
| --- | --- | --- |
| `RESOLUTION_CONFLICT` | requirements 明确冲突 | 检查冲突约束后重跑 |
| `BUILD_FAILURE` | 组合未能构建 | 检查 build requirements、Python support 与 artifacts |
| `HARNESS_CONFLICT` | test dependencies 与 project graph 冲突 | 调整 test dependencies |
| `RUNTIME_INTERFACE_MISSING` | witness 确认 runtime interface 缺失 | 检查 module/member 后决定约束 |
| `VERIFIER_EXITED_NONZERO` | 配置 verifier 正常非零退出 | 查看 verifier 诊断与日志 |
| `SOURCE_FAILURE` | source 不可达或不可读 | 检查 URL、network、credentials 与 availability |
| `ENVIRONMENT_FAILURE` | 当前 Python/system 无法执行 | 检查 interpreter、platform 与 system tools |
| `TOOL_FAILURE` | verification tool 未可靠完成 | 查看 technical facts/log 并验证工具 |
| `TIMEOUT` | operation 超时 | 先查日志，仅在预期可完成时调大 timeout |
| `INTERNAL_INVARIANT` | PF 观察到内部不一致 | 保留 failure ID，不信任 Cell result |
| `NONDETERMINISTIC` | 同一 Proposal 得到冲突 authority | 稳定测试或外部输入后全量重跑 |

## 7. 不变量

- Rejection 必须由 FailureRecord portable authority 单独复证；本地 diagnostics/log 不提升 authority。
- 一个 failure ID 不能映射到不同 payload；reader 必须复算 v2 preimage。
- Probe Rejection 不证明单个 version 是根因，也不证明整个区间失败。
- 相同 Proposal 的 exit code、signal 或 terminal kind 漂移是 `NONDETERMINISTIC`，不能因 disposition 相同复用。
- v1 不做 flaky quorum、自动重放、日志上传或根因推断。

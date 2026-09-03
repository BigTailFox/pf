# PF failure 语义与 diagnose

- **状态：** 现行
- **策略版本：** `failure-runtime-v2`
- **最后核对：** 2026-09-03
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
resolution request、exact managed vector、active declarations、唯一 SourcePlan identity、evaluation
policy、`ResolutionContext` digest、original/relaxed harness policy、harness declarations/baseline，以及
exact request 的 selected-candidate evidence。当前唯一布局与摘要均为 `attempt-v1` / `pf:attempt:v1`；
Schema 1 只按该完整 preimage 重建和复算，不接受开发期旧 identity。

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

SEARCH resolution 若managed workspace coordinate仍选择local/editable source，使用
`INTERNAL_INVARIANT @ resolve-project`与structured `managed-source-leakage`；registry
version/source/artifact不闭合，或environment plan未精确保留project selection，使用structured
`managed-source-mismatch`。两者均为Indeterminate，只保存稳定code/message及失败前已取得的plan
digests，不保存locator或动态stderr。

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

报告、latest Journal 和 Diagnosis Index 的读取范围与优先级只由 D008 定义。
`pf diagnose FAILURE_ID [--package PACKAGE]`每次必须指定并只返回一个FailureRecord；短
`<16 hex>`在CLI边界规范化为`failure-<16 hex>`，workflow只接受完整canonical ID。合法但未知
的ID配置失败；不提供批量、空成功、历史遍历、自动归因、重试、环境重建或项目/报告修改。

单条诊断按 `Failure / Outcome → What happened → Impact → Next step → Context →
Technical details → optional log tail` 表达。Failure title与Next step保持本节稳定语义；
Role-aware Impact由D008拥有。Cause 的稳定用户语义：

| Cause | What happened | Next step |
| --- | --- | --- |
| `RESOLUTION_CONFLICT` | `This version combination has conflicting dependency requirements and cannot be installed.` | `Review the conflicting requirements, adjust project constraints if needed, then rerun PF.` |
| `BUILD_FAILURE` | `This version combination could not be built.` | `Inspect the build details and log; check build requirements, Python support, and available artifacts.` |
| `HARNESS_CONFLICT` | `The test dependencies cannot be installed without changing the versions being checked.` | `Adjust the configured test dependencies so they preserve the dependency graph under test.` |
| `RUNTIME_INTERFACE_MISSING` | `A required runtime interface is missing from this version combination.` | `Review the confirmed missing module or member before changing dependency constraints.` |
| `VERIFIER_EXITED_NONZERO` | `The configured verifier rejected this version combination.` | `Review the verifier diagnostics and log before changing code or dependency constraints.` |
| `SOURCE_FAILURE` | `PF could not reach or read a configured package source.` | `Check the index URL, network, credentials, and source availability, then rerun PF.` |
| `ENVIRONMENT_FAILURE` | `The current Python or system environment cannot run this check.` | `Verify the interpreter, platform support, permissions, and required system tools.` |
| `TOOL_FAILURE` | `PF could not complete a verification tool operation reliably.` | `Inspect the technical details and log; verify that the named tool can run in this environment.` |
| `TIMEOUT` | `The operation timed out, so compatibility is unknown.` | `Inspect the log and increase the relevant timeout only if the operation is expected to finish.` |
| `INTERNAL_INVARIANT` | `PF detected an inconsistent verification result.` | `Keep the failure ID and technical details when reporting the problem; do not trust this cell result.` |
| `NONDETERMINISTIC` | `The same version combination produced conflicting results.` | `Stabilize flaky tests or external inputs, then rerun the full search.` |

## 7. 不变量

- Rejection 必须由 FailureRecord portable authority 单独复证；本地 diagnostics/log 不提升 authority。
- 一个 failure ID 不能映射到不同 payload；reader 必须复算 v2 preimage。
- Probe Rejection 不证明单个 version 是根因，也不证明整个区间失败。
- 相同 Proposal 的 exit code、signal 或 terminal kind 漂移是 `NONDETERMINISTIC`，不能因 disposition 相同复用。
- v1 不做 flaky quorum、自动重放、日志上传或根因推断。

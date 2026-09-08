# PF failure 语义与 diagnose

- **状态：** 现行
- **策略版本：** `failure-execution-v4`
- **最后核对：** 2026-09-08
- **领域词汇：** [CONTEXT](../../CONTEXT.md)
- **搜索消费：** [D003](D003-pf-search-algorithm.md)
- **静态事实：** [D004](D004-pf-ty-enhancement.md)
- **进程事实：** [D007](D007-pf-process-output.md)
- **运行角色与读取面：** [D008](D008-pf-verification-run.md)
- **Harness negative evidence：** [D012](D012-pf-harness-relaxation.md)
- **pytest diagnostics：** [D013](D013-pf-pytest-observer.md)

本文是 Attempt、cause、disposition、FailureRecord identity 和 `diagnose` 语义的唯一
所有者。Configured verifier 先形成 terminal disposition；FailurePolicy 记录它，并对其他
operation facts 应用本节共享纯规则。生产投影与离线 reader 复用同一分类和绑定校验；
搜索只消费 disposition，Presenter 只组织稳定文案。

## 1. 三层结果模型

```text
完整成功事实                         -> PASS
完整负向事实：Attempt 不满足配置的验证契约 -> REJECTED
异常终态或直接外因/协议/一致性失败      -> INDETERMINATE
```

Rejection 证明当前 Attempt 不满足 configured validation contract，不声称项目在其他 oracle 下不兼容。
Certified harness UNSAT 保留 HARNESS_CONFLICT cause。有效 resolve/install 请求的未知普通非零
也拒绝该 Attempt，但只证明本次执行契约未通过，不证明 UNSAT、可重复失败或单依赖根因。
后端可能把网络、权限或内部错误包装成普通非零；没有可靠归因时允许误拒绝，因此可能遗漏可行
向量、抬高结果下界或无结果。PF 直接观察的外因、一致性破坏与异常终态仍不能伪装为
verifier/runtime incompatibility。Static ty regression 不新增为直接 Rejection cause。

Cause 回答发生了什么，不等于 disposition，也不声称单个 dependency 是根因。不得解析
stderr substring、traceback、pytest facts 或自然语言来补 classification。

现行 cause 集合：

```text
RESOLUTION_CONFLICT
RESOLUTION_FAILED
INSTALLATION_FAILED
HARNESS_CONFLICT
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
resolution request、exact managed vector、active declarations、唯一 SourcePlan identity、
`ExecutionPolicy.identity`、`ResolutionContext` digest、original/relaxed harness policy、harness
declarations/baseline，以及 exact request 的 selected-candidate evidence。ty args/timeout/tool
version 不进入 Attempt。当前唯一布局与摘要均为 `attempt-v1` / `pf:attempt:v1`；
Schema 1 只按该完整 preimage 重建和复算，不接受开发期旧 identity。
uv version/protocol/profile 准入先于 Attempt；无法建立协议是 ConfigurationError，不补造
Attempt 或 FailureRecord。非法内部对象、未建模 PF 异常沿 InfrastructureError 路径结束。

Failure scope 是判别 union：

```text
AttemptFailureScope(attempt)
CellFailureScope(package, cell, source_snapshot_digest, execution_policy_identity)
```

Candidate discovery 或 scheduling 在 Attempt 建立前失败时使用 Cell scope；它只能是
Indeterminate，不得虚构 Proposal 或 managed vector。

## 3. Rejection 资格

Rejection 只否定完整 Attempt。现行允许：

| Cause | Stage | Authority |
| --- | --- | --- |
| `RESOLUTION_CONFLICT` | `resolve-project` | D012 资格 profile 证明 project request UNSAT |
| `HARNESS_CONFLICT` | `resolve-environment` | D012 资格 profile 证明 final environment request UNSAT |
| `RESOLUTION_FAILED` | `resolve-project` / `resolve-environment` | 有效请求的 NormalExit(nonzero) / Unattributed |
| `INSTALLATION_FAILED` | `install-project` / `install-environment` | 选定 final plan 的 NormalExit(nonzero) / Unattributed |
| `VERIFIER_EXITED_NONZERO` | `test` | configured verifier 的 `NormalExit(exit_code != 0)` |

Configured verifier 的 terminal disposition 已由 `ConfiguredVerifier` 机械形成；
FailurePolicy 不再读取 exit code、output completeness、pytest facts 或 cause 重新判断。
normal nonzero 的具体整数、stdout/stderr 是否完整、pytest phase 与 observer metadata 都不
改变 Rejection。已采用的 failed-set 阶段 terminal 使用同一映射：collection artifact 证明
有效 requested-set collection 后，任意 `NormalExit(exit_code != 0)` 都是 `VerifierRejected`；
timeout、signal、start failure 与 typed terminal unavailable 仍是 Indeterminate，不回退原命令。
不按 pytest 退出码 1/2/3/4/5 建立专用 disposition 分支。

Cell scope、PF 直接观察的 source/environment/artifact/internal 外因、nondeterministic failure、
timeout、signal、start failure、typed terminal unavailable 不形成 Rejection。uv 内部 build
不是独立 PF stage：没有合格归因时使用实际父 resolve/install 操作的兜底，不从日志生成
build Attempt、terminal 或未取得的 plan。辅助 create/inspect 不采用候选拒绝兜底。

静态 prepare/ty/比较失败不形成 Rejection 或 Indeterminate；它们只影响 guidance / Journal
审计。孤立 runtime interface missing 不再生产。

### 3.1 共享执行规则

操作集合封闭，不按名称前缀匹配：R = `resolve-project | resolve-environment`，I =
`install-project | install-environment`，A = `create-environment | inspect-interpreter | inspect`，
Q = `inspect-project-plan | inspect-environment-plan | proposal-vector`，V = `test`。

先采用已直接观察的合法结构化事实；否则提取 terminal（timeout 优先于清理后的 exit/signal）；
NormalExit(0) 仅在全部必要成功产物/一致性检查通过后允许继续；普通非零先用合格归因，再兜底。
失败退出的残留 lock/wheel 不进入成功解析分支；缺失本就不应产生的成功产物不能吞掉非零兜底。

| Stage | Terminal / attribution | Disposition / cause |
| --- | --- | --- |
| R/I/A/V | TimedOut | INDETERMINATE / TIMEOUT |
| R/I/A/V | Signaled / StartFailed / Unavailable | INDETERMINATE / TOOL_FAILURE |
| resolve-project | NormalExit(1) / UvUnsatAttribution | REJECTED / RESOLUTION_CONFLICT |
| resolve-environment | NormalExit(1) / UvUnsatAttribution | REJECTED / HARNESS_CONFLICT |
| R | NormalExit(nonzero) / Unattributed | REJECTED / RESOLUTION_FAILED |
| I | NormalExit(nonzero) / Unattributed | REJECTED / INSTALLATION_FAILED |
| A | NormalExit(nonzero) / Unattributed | INDETERMINATE / TOOL_FAILURE |
| V | NormalExit(nonzero) | REJECTED / VERIFIER_EXITED_NONZERO |

ExecutionTerminal = NormalExit / StartFailed / TimedOut / Signaled / Unavailable；exit_code 为非负
strict integer，signal 为正 strict integer。ExecutionFailure 不接受 NormalExit(0)；UNSAT 只允许
R 的 NormalExit(1)，不能接在 install、辅助或异常 terminal 上。Q 不接受 execution authority。
有效原始观察中同时出现 normal 0 和失败归因，应先形成 evidence-conflict；非法内部凭据对象
不得交给 classifier 猜测修复。planning、ty、Ctrl+C 保持其独立协议。

首轮 attribution 只有 `Unattributed = {kind: "unattributed"}`，以及：

```text
UvUnsatAttribution = {
  kind: "uv-unsat", tool: "uv", tool_version: "0.12.5",
  protocol: "uv-pip-compile-pylock-v1", profile: "uv-diagnostics-0.12.5-v1",
  request_binding: {attempt_id, stage, project_plan_digest, environment_plan_digest},
  facts: {code: "direct-version-contradiction" | "transitive-version-contradiction",
          stdout_complete: true, stderr_complete: true}
}
```

全部字段 required，拒绝额外字段；两个 code 是独立判别变体，completeness 必须是 JSON boolean
true。凭据不携带诊断正文、digest、自由文本 cause 或本机 locator。producer 按 D012 检查固定
版本/profile、完整矛盾形状和既有 source/build/availability 排除条件，并逐字段校验运行期
ResolutionContext 与凭据的 tool version/protocol/profile 相等。输出截断、未知形状、排除形状
均为 Unattributed。可选日志缺失不抹去进程终态；成功路径的必需完整输出仍按 D007/D012。

### 3.2 StructuredOperationFact

每个 fact 只有唯一字面量 `code`，无任意 message/路径/异常字段。全部为 INDETERMINATE，
authority 是 `operation-structured(fact, terminal)`；terminal required-nullable。T? 表示该
操作实际观察的 terminal 或 JSON null，不能借用另一操作的进程。

| Code | Stage | Terminal | Cause |
| --- | --- | --- | --- |
| request-invariant | R/I/A/Q | T? | INTERNAL_INVARIANT |
| evidence-conflict | R/I/A/Q | T? | INTERNAL_INVARIANT |
| source-access-failed | R/I | T? | SOURCE_FAILURE |
| environment-access-failed | R/I/A | T? | ENVIRONMENT_FAILURE |
| artifact-invalid | R/I | T? | SOURCE_FAILURE |
| resolution-output-incomplete | R | NormalExit(0) | TOOL_FAILURE |
| resolution-plan-invalid | R | NormalExit(0) | TOOL_FAILURE |
| artifact-policy-mismatch | R | NormalExit(0) | INTERNAL_INVARIANT |
| managed-source-leakage | resolve-project | NormalExit(0) | INTERNAL_INVARIANT |
| managed-source-mismatch | R | NormalExit(0) | INTERNAL_INVARIANT |
| interpreter-observation-invalid | inspect-interpreter | NormalExit(0) | TOOL_FAILURE |
| interpreter-mismatch | inspect-interpreter | NormalExit(0) | ENVIRONMENT_FAILURE |
| graph-observation-invalid | inspect | NormalExit(0) | TOOL_FAILURE |
| installed-graph-mismatch | inspect-project-plan / inspect-environment-plan | null | INTERNAL_INVARIANT |
| proposal-vector-mismatch | proposal-vector | null | INTERNAL_INVARIANT |

只有实际执行检查才能产生相应 fact。request-invariant 是合法请求与投影/绑定不一致；
evidence-conflict 是直接权威事实互斥。其余依表顺序对应 PF 直接 source I/O、环境权限 I/O、
artifact hash/格式/metadata、必需 output/native plan、artifact policy、managed source closure、
interpreter 与实际安装图/向量检查。native plan 不可读归 resolution-plan-invalid；缺 managed
node 归 installed-graph-mismatch。子进程错误文字不是直接 source/environment/artifact fact。
多个适用事实按表中先后顺序只采用第一项；未建模异常不创造新 code 或 TOOL_FAILURE。

## 4. FailureRecord v3

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
    | ExecutionFailureAuthority(terminal, attribution)
    | StructuredOperationFailureAuthority(fact, terminal)
  project_plan_digest?
  environment_plan_digest?
```

每条记录必须恰有一种 authority：

- ty 及排除的 planning 等操作可保存 D007 portable `ProcessResult`；typed
  terminal unavailable 转为稳定 structured authority，不伪造 process facts；
- configured verifier 只保存 `ExecutionTerminal`，不重复保存完整 `ProcessResult`；
- R/I/A 采用 execution 或 operation-structured，Q 仅采用 operation-structured，scope 必须是
  Attempt；独立 process/structured authority 不得用于这些 stage，test 仅允许 configured-verifier；
- scheduler/source 等无进程路径保存稳定、脱敏、可移植 `FailureDetail`。

SEARCH 的 managed local/editable leakage 与 source/artifact closure 使用 §3.2 对应 fact，
只保存 code、该操作 terminal 和失败前已取得的 plan digests，不保存 message/locator。

没有 active external harness 的 Cell 不执行 environment resolution，不能产生 `HARNESS_CONFLICT @ resolve-environment`。其 project plan 安装失败使用 `install-project`，安装 graph 不符使用 `inspect-project-plan`；有 harness 的分支仍为 `install-environment` / `inspect-environment-plan`。缺失 test tool 按 configured verifier 实际 start/exit terminal 分类，不能反推 dependency conflict；只保存失败前实际取得的 plan digest。

`failure_id` 对完整 v3 preimage 做 canonical hash：

```text
sha256("pf:failure:v3\0" + canonical_identity_json(payload))
-> failure-<16 hex>
```

Preimage 包含 scope、disposition、cause、stage、完整 authority 与已有 plan digests。新 execution、
operation-structured 与 verifier
identity 吸收 terminal kind、normal exit code 或 signal；不吸收 duration、output
completeness、stdout/stderr、start-error 正文、pytest facts/detail/progress、summary、run ID 或
日志 locator。相同 Attempt 的 exit 1 与 exit 4 因而是不同 failure ID。
UNSAT completeness=true 是被采用的准入事实，进入 identity；未采用的日志 completeness 不进入。
原 process authority 的 portable identity 不在本次改写。

Plan digest 只在对应 plan 通过全部检查后提交：resolve-project 无 plan；resolve-environment
仅已有 project；install-project 仅 project；install-environment 两者都有。create/interpreter
均无 plan；inspect/Q 必须有 project，environment 非空当且仅当 active harness 非空。
inspect-project-plan 仅 project-only，inspect-environment-plan 仅 harness 分支。binding 在调用前
由 Factory 创建，四个 required 字段必须逐项等于顶层 Attempt/stage/已取得 plan 的投影；其两个
nullable plan 字段显式保留 null。FailureRecord 顶层 optional plan 空值仍省略。

reader 检查固定凭据、facts、terminal/stage、harness/plan 时序与 binding，并复算保存事实的
cause/disposition、Failure ID、Attempt identity 和 refs。Attempt 的 resolution_context_digest
必须非空但保持 opaque；不保存 context preimage，不从本机 context/日志补证或声称复算原文。
重新哈希不能豁免可观察语义检查。Schema 1 的 required identity.failure_policy 与 generation
规则由 D014 拥有；ExecutionPolicy 吸收固定 execution-outcome-v1、
operation-structured-facts-v1 和上述唯一 attribution profile，不根据运行结果动态变更。
ty 采集只进入 GuidancePolicy / TyObservationPolicy。

Environment plan digest 存在时 project plan digest 必须存在。`FailureDetail` 必须非空、
有界、脱敏且可移植；不能保存绝对路径、credential、动态异常正文或本地 locator。

## 5. FailurePolicy interface

```text
FailurePolicy.classify(scope, cause, stage, process?, summary_code?, detail?, plan digests?)
    -> FailureRecord  # 仅适用独立 process/structured fact family，不能用于 R/I/A/Q 或 test

FailurePolicy.record_prepare(PrepareFailure)
    -> FailureRecord  # 原样采用 OperationFailure；由共享规则派生 cause/disposition

FailurePolicy.record_evaluation(scope, evaluation, ...)
    -> FailureRecord | None
```

Evaluation 映射固定为：

```text
PassEvaluation                    -> None
VerifierRejectedEvaluation        -> VERIFIER_EXITED_NONZERO @ test
IndeterminateEvaluation           -> evaluation 自带 verifier/process authority
```

Adapter、RuntimeEvaluator、workflow、CoordinateSearch、ReportStore 与 Presenter 不复制
Rejection classifier。Role 不改变分类或 identity。

## 6. Diagnose

报告、latest Journal 和 Diagnosis Index 的读取范围与优先级只由 D008 定义。
diagnose 只按 Failure ID 遍历合法 execution/selection 静态关联；未关联静态事实不是独立
selector。命令参数与 Failure ID 准入只见 [D001 §5、§8](D001-pf.md#5-命令)；本节拥有 cause 的稳定用户语义。

单条诊断按 `Failure / Outcome → What happened → Impact → Next step → Context →
Technical details → optional log tail` 表达。Failure title与Next step保持本节稳定语义；
Role-aware Impact由D008拥有。Cause 的稳定用户语义：

| Cause | What happened | Next step |
| --- | --- | --- |
| `RESOLUTION_CONFLICT` | `This version combination has conflicting dependency requirements and cannot be installed.` | `Review the conflicting requirements, adjust project constraints if needed, then rerun PF.` |
| `RESOLUTION_FAILED` | `This resolution attempt did not pass; a dependency conflict has not been proven.` | `Inspect the resolution diagnostics and log before changing dependency constraints.` |
| `INSTALLATION_FAILED` | `The selected plan did not pass this installation attempt.` | `Inspect the installation diagnostics and log for the selected plan.` |
| `HARNESS_CONFLICT` | `The test dependencies cannot be installed without changing the versions being checked.` | `Adjust the configured test dependencies so they preserve the dependency graph under test.` |
| `VERIFIER_EXITED_NONZERO` | `The configured verifier rejected this version combination.` | `Review the verifier diagnostics and log before changing code or dependency constraints.` |
| `SOURCE_FAILURE` | `PF could not reach or read a configured package source.` | `Check the index URL, network, credentials, and source availability, then rerun PF.` |
| `ENVIRONMENT_FAILURE` | `The current Python or system environment cannot run this check.` | `Verify the interpreter, platform support, permissions, and required system tools.` |
| `TOOL_FAILURE` | `PF could not complete a verification tool operation reliably.` | `Inspect the technical details and log; verify that the named tool can run in this environment.` |
| `TIMEOUT` | `The operation timed out, so compatibility is unknown.` | `Inspect the log and increase the relevant timeout only if the operation is expected to finish.` |
| `INTERNAL_INVARIANT` | `PF detected an inconsistent verification result.` | `Keep the failure ID and technical details when reporting the problem; do not trust this cell result.` |
| `NONDETERMINISTIC` | `The same version combination produced conflicting results.` | `Stabilize flaky tests or external inputs, then rerun the full search.` |

## 7. 不变量

- Rejection 必须由 FailureRecord portable authority 单独复证；本地 diagnostics/log 不提升 authority。
- 一个 failure ID 不能映射到不同 payload；reader 必须复算 v3 preimage。
- Probe Rejection 不证明单个 version 是根因，也不证明整个区间失败。
- 重复观察、cache conflict 与 merge 冲突承诺只见 [D003 §3](D003-pf-search-algorithm.md#3-核心不变量)。
- v1 不做 flaky quorum、自动重放、日志上传或根因推断。

Diagnose 从 authority 展示实际 terminal、qualified attribution 或 normal-nonzero fallback；
structured 分支展示 fact.code。后端 build 细节只在日志，不从 cause 猜 UNSAT 或建议自动提高下界。
Report、Journal、Diagnosis Index 使用同一 portable authority，运行期 process sidecar 按 failure ID
关联本机日志，不进入新 authority 的身份。

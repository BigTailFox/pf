# PF failure 语义与 diagnose

- **状态：** 现行
- **策略版本：** `failure-runtime-v1`
- **最后核对：** 2026-08-26
- **领域词汇：** [CONTEXT](../../CONTEXT.md)
- **搜索消费：** [D003](D003-pf-search-algorithm.md)
- **进程事实：** [D007](D007-pf-process-output.md)
- **运行角色与读取面：** [D008](D008-pf-verification-run.md)
- **Harness negative evidence：** [D012](D012-pf-harness-relaxation.md)
- **pytest negative evidence：** [D013](D013-pf-pytest-failure-evidence.md)

本文是 Attempt、cause、disposition、FailureRecord 和 `diagnose` 语义的唯一所有者。Adapter 提供机械 operation facts；FailurePolicy 分类；搜索只消费 disposition；Presenter 只组织本文与 D008 提供的文案事实。

## 1. 三层结果模型

```text
完整成功事实                         -> PASS
process/operation failure facts
  -> Cause                           稳定说明发生了什么
  -> FailurePolicy                   -> REJECTED | INDETERMINATE
```

Cause 不是 disposition，也不声称单个 dependency 是根因。相同 cause 在证据完整性、stage 或 scope 不同时可以得到不同 disposition。不得解析 stderr substring、traceback 或自然语言来补 classification。

现行 cause 集合：

```text
RESOLUTION_CONFLICT
BUILD_FAILURE
HARNESS_CONFLICT
RUNTIME_INTERFACE_MISSING
TEST_FAILURE
SOURCE_FAILURE
ENVIRONMENT_FAILURE
TOOL_FAILURE
TIMEOUT
INTERNAL_INVARIANT
NONDETERMINISTIC
```

`PASS` 不产生 FailureRecord。Static transition 本身不经过 FailurePolicy。

## 2. Attempt 与 scope

Attempt 在 environment resolution 前建立。它的 identity 至少绑定：

- source snapshot digest 与完整 Cell；
- `highest | lowest-direct | exact-vector` request；
- exact request 的 managed vector；
- active declaration IDs 与 source-plan identity；
- evaluation policy、resolution context 与 harness policy；
- relaxed request 的 harness baseline；
- exact request 的 selected-candidate evidence。

Schema 2 持久化只接受 `attempt-v2`；开发期 `attempt-v1` 仍只存在于内部兼容的领域 model，不是可写 wire 变体。

Failure scope 是判别 union：

```text
AttemptFailureScope(attempt)
CellFailureScope(package, cell, source_snapshot_digest, policy_identity)
```

Candidate discovery 或 scheduling 在 Attempt 建立前失败时使用 Cell scope；它只能是 Indeterminate，不得虚构 resolution、managed vector 或 Proposal。

## 3. Rejection 资格

Rejection 是明确负向证据，只否定完整 Attempt。现行 v1 仅允许下表四种组合：

| Cause | Stage | 附加权威 |
| --- | --- | --- |
| `RESOLUTION_CONFLICT` | `resolve-project` | D012 资格 profile 认证的完整 project requirement contradiction |
| `HARNESS_CONFLICT` | `resolve-environment` | D012 资格 profile 认证的完整 final-environment contradiction |
| `RUNTIME_INTERFACE_MISSING` | `witness` | D004 adapter-owned witness 的 `CONFIRMED_MISSING` |
| `TEST_FAILURE` | `test` | D013 direct-pytest witness 或用户显式 generic failure-exit contract |

此外必须同时满足：

- scope 是 `highest | lowest-direct | exact-vector` Attempt；
- 有可信 `ProcessResult`；
- 没有 start error、timeout 或 signal；
- `stdout_complete` 与 `stderr_complete` 都为 true；
- resolution/test 路径具有正常非零 exit；runtime witness 的 confirmed-missing 允许其结构化成功进程；
- cause 与 stage 精确匹配上表。

`lowest-direct` 与 exact Probe 使用同一 Rejection 资格，但 Role 的产品影响不同。Baseline 不降低证据门槛。

以下情况始终不足以 Rejection：

- Cell scope；
- `BUILD_FAILURE`、`SOURCE_FAILURE`、`ENVIRONMENT_FAILURE`、`TOOL_FAILURE`、`TIMEOUT`、`INTERNAL_INVARIANT` 或 `NONDETERMINISTIC`；
- candidate unavailable、index/DNS/auth、artifact、build、installation、graph inspection、parser 或未知 resolver failure；
- start error、timeout、signal、缺失 process facts 或不完整输出；
- static regression、runtime witness `PRESENT | NOT_APPLICABLE | FAILURE`；
- D013 未资格化、缺失或冲突的 pytest witness；
- stderr 文本看似冲突或测试失败。

这些结果形成 Indeterminate。保守漏掉 Rejection 会停止 Cell；错误 Rejection 会移动边界，因此不能放宽。

## 4. Baseline、declaration 与 probe

Request 决定 evidence scope，Role 不改变本章的 Rejection 资格。Verification Run 的编排与 Baseline/Declaration/Probe 影响只由 D008 定义；D003 只消费已经分类的 Probe Rejection 或 Indeterminate。

## 5. FailureRecord

```text
FailureRecord
  failure_id
  scope: AttemptFailureScope | CellFailureScope
  disposition: REJECTED | INDETERMINATE
  cause
  stage
  process?
  summary_code?
  detail?
  project_plan_digest?
  environment_plan_digest?
```

`failure_id` 对上述结构化可移植 facts 做 `pf:failure:v1` canonical hash 并使用 `failure-` 前缀。Environment plan digest 存在时 project plan digest 必须存在；Failure 只能保存失败发生前已经取得的 plan evidence。

`ProcessResult` 只保存 D007 定义的 portable facts，不保存 stdout/stderr、run ID 或 log path。`FailureDetail` 必须非空、有界、脱敏且可移植；不能保存绝对路径、credential、动态异常正文或本地 locator。详细输出只在 Process Log。

Schema 2 只定义一次 FailureRecord；observation、boundary、CellResult 与 diagnosis 都引用 `failure_id`。Wire ownership、ref closure 与 public-locator validation 由 D014 定义。

## 6. FailurePolicy interface

```text
FailurePolicy.classify(
    scope, cause, stage, process,
    summary_code=None,
    detail=None,
    project_plan_digest=None,
    environment_plan_digest=None,
) -> FailureRecord

FailurePolicy.classify_evaluation(scope, evaluation, ...)
    -> FailureRecord | None
```

`classify_evaluation` 的映射固定为：

```text
PassEvaluation                    -> None
RuntimeInterfaceMissingEvaluation -> RUNTIME_INTERFACE_MISSING @ witness
TestFailEvaluation                -> TEST_FAILURE @ test
IndeterminateEvaluation           -> evaluation 自带 cause/stage/process
```

Adapter、RuntimeEvaluator、workflow、CoordinateSearch、ReportStore 与 Presenter 不复制 `rejection_is_supported`。

## 7. Diagnose 语义与排序

报告、latest Journal 和 Diagnosis Index 的读取范围、优先级与去重只由 D008 定义。对解析后的 FailureRecords，`pf diagnose [package] [--failure FAILURE_ID]` 保持以下语义：

- 指定的 failure ID 不存在时配置失败；
- 省略 ID 时按 package、target、Python、extra、resolution/vector 与 failure ID 稳定排序；
- 成功展示零条或多条记录都返回 `0`；
- 不做自动根因归属、重试、环境重建、项目修改或报告修改。

每条诊断按以下层级表达：

```text
Failure / Outcome
What happened
Impact
Next step
Context
Technical details
optional output tail / Process Log link
```

D005 拥有 cause 对应的 title 与 next step，以及 `REJECTED`/`INDETERMINATE` 的通用含义；D008 拥有 Role→impact；D006 只拥有布局与通道。

Cause 的稳定用户语义：

| Cause | What happened | Next step |
| --- | --- | --- |
| `RESOLUTION_CONFLICT` | requirements 明确冲突，组合无法安装 | 检查冲突约束后重跑 |
| `BUILD_FAILURE` | 组合未能构建 | 检查 build requirements、Python support 与 artifacts |
| `HARNESS_CONFLICT` | test dependencies 无法在保持 project graph 时安装 | 调整 test dependencies |
| `RUNTIME_INTERFACE_MISSING` | witness 确认所需 runtime interface 缺失 | 检查 module/member，再决定约束 |
| `TEST_FAILURE` | 完整 test command 失败 | 查看结构化摘要与日志 |
| `SOURCE_FAILURE` | source 不可达或不可读 | 检查 URL、network、credentials 与 availability |
| `ENVIRONMENT_FAILURE` | 当前 Python/system 无法执行 | 检查 interpreter、platform、permissions 与 system tools |
| `TOOL_FAILURE` | verification tool 未可靠完成 | 查看 technical facts/log 并验证工具 |
| `TIMEOUT` | operation 超时 | 先查日志，仅在预期可完成时调大 timeout |
| `INTERNAL_INVARIANT` | PF 观察到内部不一致 | 保留 failure ID 与 technical facts，不信任 Cell result |
| `NONDETERMINISTIC` | 同一组合得到冲突结果 | 稳定测试或外部输入后全量重跑 |

## 8. 不变量与非目标

- Rejection 必须能由 FailureRecord 的可移植 facts 单独复证；本地日志不可提升 authority。
- 一个 FailureRecord 只属于一个 scope；一个 failure ID 不能在不同 payload 下复用。
- Probe Rejection 不证明某个 dependency version 是根因，也不证明整个版本区间失败。
- `diagnose` 不做自动根因分析、环境重建、隐式重放、日志上传或修复建议执行。
- v1 不对多次随机 observation 做 quorum；flaky/nondeterministic 结果 fail closed。

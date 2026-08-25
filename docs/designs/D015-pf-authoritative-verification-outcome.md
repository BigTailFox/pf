# PF 权威验证终态

- **状态：** 草案
- **日期：** 2026-08-25
- **适用范围：** 配置 verifier 的权威终态、Attempt disposition 与诊断 metadata 的分离
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **CLI 交互与展示：** [D006](D006-pf-cli-enhancement.md)
- **进程输出与日志：** [D007](D007-pf-process-output.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)
- **pytest 诊断与进度现状：** [D013](D013-pf-pytest-failure-evidence.md)
- **报告 Schema 2：** [D014](D014-pf-report-schema.md)（已批准，待实现）

本文定义配置 verifier 的 `ProcessResult -> VerifierOutcome` 规则，以及权威终态与 pytest 诊断 metadata 的隔离。D005 继续拥有一般性的 Attempt、Rejection、Indeterminate、FailureRecord 与 prepare 分类；D003 继续只消费 disposition；本文落地前，D013 仍拥有现行 pytest failure-witness classifier。

本文是草案，不描述现行行为。批准并落地前，D001/D002/D005/D007/D008/D013 与当前代码仍是唯一有效契约。落地时项目尚未发布，直接替换现行 classifier、配置和 Schema，不保留双行为、兼容 reader 或旧报告迁移。

## 1. 决策摘要

配置 verifier 的唯一权威契约是：

> 只要配置的验证命令被完整执行并产生明确的失败终态，该 Attempt 就是 Rejected；只有 PF 无法获得可信终态时，结果才是 Indeterminate。

英文规范句为：

> An attempt is rejected when its configured verifier completes with a trustworthy negative result; it is indeterminate only when PF cannot obtain a trustworthy terminal result from the evaluation attempt.

对 verifier process，规则固定为：

```text
exited(0)          -> PASS
exited(nonzero)    -> REJECTED
start failed       -> INDETERMINATE
PF timeout         -> INDETERMINATE
process signaled   -> INDETERMINATE
terminal unknown   -> INDETERMINATE
```

exit code 的具体整数、pytest lifecycle、failure witness、stdout/stderr 文本、异常类型和推测根因都不改变该映射。

## 2. 问题

现行 D013 把“pytest 是否提供已资格化 failure witness”作为负向 observation 的授权条件：

```text
exit 1/2 + qualified failure witness -> TestFail / Rejected
nonzero + no finalized witness       -> ToolFailure / Indeterminate
pytest internal error                -> ToolFailure / Indeterminate
```

这把两个不同问题合并了：

1. 配置 verifier 是否以成功终态结束；
2. PF 是否知道 verifier 为什么失败、失败发生在哪个 pytest phase。

`packaging==20.9` × Python 3.12 是直接反例。该 exact vector 已成功 prepare，pytest process 已启动并完整退出；`packaging` 在 pytest bootstrap 中导入 Python 3.12 已移除的 `distutils`，最终 exit 1。对 PF 的产品问题——“这个 exact vector 能否完成用户配置的验证命令”——答案已经是确定的 No。没有 collection/test witness 只降低解释能力，不使 process 终态消失。

D013 因此把 failure attribution 的置信度错误地变成了负向 observation 是否有效的前提。PF v1 不需要证明 `packaging` 是根因，也不应维护 pytest lifecycle 状态机；它只拒绝当前完整 Attempt/context。

## 3. 目标与非目标

### 3.1 目标

1. 对任意配置 verifier 使用统一的零/非零终态，不识别 pytest、tox、nox、coverage 或 wrapper；
2. 所有正常非零 exit 都形成 verifier Rejection，包括 bootstrap、usage、internal、collection、test 与 no-tests；
3. timeout、signal、start error 和无法可靠观察进程终态保持 Indeterminate；
4. baseline 只提供搜索 PASS anchor，不进入 verifier classifier；
5. pytest witness、progress、phase 和 Process Log 只提供诊断，不改变 outcome；
6. PF 控制的 source/index/network/调度/进程基础设施失败不伪装成 verifier Rejection；
7. `CoordinateSearch` 继续只消费 `PASS | REJECTED | INDETERMINATE`；
8. report、cache、merge 与 apply authority 不再依赖 pytest version/profile/witness qualification；
9. 真实 `packaging==20.9` × Python 3.12 bootstrap exit 1 成为合法 rejection boundary。

### 3.2 非目标

- 不判断 test command 的失败来自 assertion、import、plugin、CLI、网络或外部服务；
- 不解析 stderr/traceback 来决定 disposition；
- 不自动重试 verifier，不引入 quorum、flaky 或统计稳定性策略；
- 不把一次 Rejection 归因到单个 dependency version；
- 不改变 D003 的 probe 顺序、单调假设或 Unknown 终止规则；
- 不在本文扩大 prepare 阶段 build/install/source failure 的 Rejection 资格；
- 不让 pytest diagnostics、test ID、异常正文或本地日志进入 report identity；
- 不保证 PF 能识别 verifier 内部发生的网络、OOM、子进程 signal 或 wrapper 重写；
- 不新增 test-runner classifier registry 或用户可选 pytest profile。

## 4. 领域模型

### 4.1 Attempt 是 disposition 的主语

Rejection/Indeterminate 的主语是 Attempt，不是 dependency version，也不总是 Proposal。

```text
Attempt
  ├── prepare 失败             -> 可能尚无 Proposal
  └── prepare 成功 -> Proposal -> evaluate
```

prepare 前已建立 Attempt；只有解析图与环境 identity 成功后才有 Proposal。因此 certified resolution conflict 可以拒绝 Attempt，却不能虚构 Proposal。本文只重定义成功 prepare 后配置 verifier 的 outcome；D005 继续拥有 prepare cause 矩阵。

### 4.2 权威结果与诊断 metadata

**权威结果**决定 disposition。对配置 verifier，唯一权威结果通道是 PF 观察到的 process terminal facts：

```text
exit_code | signal | start_error
timed_out
```

**诊断 metadata**帮助用户理解结果，但没有 disposition authority：

- pytest collection/test/internal facts；
- pytest phase、execution mode 与 progress；
- stdout/stderr 与 Process Log；
- bounded summary、异常名称或本地日志 locator；
- observer protocol 是否可用。

诊断缺失、损坏或互相矛盾不得把已知 `exited(0)` 改成 Indeterminate，也不得把已知 `exited(nonzero)` 改成 Indeterminate。

### 4.3 Cause 不再决定搜索

对配置 verifier，Cause/reason 只解释已经得到的 outcome：

```text
REJECTED
  reason: verifier-exited-nonzero
  exit_code: 3

INDETERMINATE
  reason: process-signaled
  signal: SIGKILL
```

`TEST_FAILURE`、`TOOL_FAILURE`、`HARNESS_FAILURE` 等名字不得作为 `CoordinateSearch` interface。搜索只接收 disposition；FailureRecord 可以保存稳定 reason、stage 与机械事实。Prepare adapter 仍按 D005 从已资格化的结构化 operation facts 形成 Rejected/Indeterminate；单独一个 cause enum 同样不具 authority。

## 5. 配置 verifier 的 outcome

### 5.1 Interface

`TestAdapter` 对外只暴露一个深 interface：

```text
run(command, cwd, environment, timeout, progress=None)
  -> VerifierPass
   | VerifierRejected
   | VerifierIndeterminate
```

说明性结构：

```text
VerifierPass
  process

VerifierRejected
  process
  reason = verifier-exited-nonzero

VerifierIndeterminate
  process?
  reason = process-start-failed
         | process-timed-out
         | process-signaled
         | terminal-unavailable
         | adapter-internal-error
```

`pytest` 不是第二个 adapter seam；没有 generic/pytest outcome profile selector。pytest-specific logic 只能作为 `TestAdapter` 的可选诊断 implementation，不能改变上述 union。

### 5.2 决策顺序

分类顺序必须固定，避免同时存在 timeout/signal/exit 时各调用方自行选择：

1. adapter 未能构造可信 `ProcessResult`：`VerifierIndeterminate`；
2. `start_error != null`：`VerifierIndeterminate`；
3. `timed_out == true`：`VerifierIndeterminate`；
4. `signal != null`：`VerifierIndeterminate`；
5. `exit_code == 0`：`VerifierPass`；
6. `exit_code != 0`：`VerifierRejected`；
7. 其余非法终态：`VerifierIndeterminate(adapter-internal-error)`。

Schema/runner 必须继续验证合法 terminal shape。timeout 后为清理进程而观察到的 signal/exit 不得覆盖 timeout。

### 5.3 所有非零 exit 等价

以下结果对 disposition 完全等价：

```text
pytest exit 1   assertion / plugin import / rewritten status
pytest exit 2   collection abort / caught KeyboardInterrupt
pytest exit 3   internal error
pytest exit 4   usage/config error
pytest exit 5   no tests collected
pytest exit N   其他正常非零终态
```

它们统一形成 `VerifierRejected`。stage、phase、exit code 和可选摘要可以不同，但不能进入搜索判断。

若 OS signal 被 shell/wrapper 吸收并转换成普通 exit 128+N，PF 只能观察到 nonzero exit，因此结果是 Rejected。用户若希望 signal 保持 Indeterminate，verifier/wrapper 必须保留 signal 语义；PF 不从整数反推隐藏的子进程状态。

### 5.4 exit 0 与矛盾 diagnostics

如果 process `exited(0)`，而 best-effort pytest metadata 声称发生 collection/test failure，权威 outcome 仍是 `VerifierPass`。PF 可以记录 `diagnostic-conflict` warning，但不能让非权威 observer 改写用户 verifier 的成功终态。

同理，nonzero exit 即使没有 witness、witness 非 canonical、pytest runtime 未资格化或 execution mode 是 xdist/unknown，也仍是 `VerifierRejected`。

## 6. 输出、日志与结果完整性

### 6.1 verifier 不解析 stdout/stderr

配置 verifier 的 success contract 只使用 process terminal facts。`stdout_complete` / `stderr_complete` 描述 Process Log 是否保存了完整正文，不是 verifier result channel。

因此，只要 exit code 可信：

```text
exit 0  + incomplete Process Log -> PASS + diagnosis degraded
exit !=0 + incomplete Process Log -> REJECTED + diagnosis degraded
```

这只适用于不解析正文的配置 verifier。uv/ty/structured-output adapter 若必须解析完整正文才能知道 operation outcome，仍按其契约在正文不可用或不可解析时返回 Indeterminate。D007 继续拥有磁盘日志与 Output Cache 的完整性含义。

### 6.2 Process Log 不拥有 disposition

Process Log、Output Cache 和 diagnosis association 丢失不能改变已经形成的 VerifierOutcome。日志写入故障作为独立运行基础设施事实上报；不得回写并篡改 Attempt 的 process outcome。

## 7. prepare 与 verifier 的责任边界

本文不把所有 prepare failure 简化为 exit-code Rejection。EnvironmentFactory、uv 和 source adapter 是 PF 控制的环境准备机制，其 result contract 与用户 verifier 不同。

现行 D005 prepare 分类继续有效：

- qualified resolver 完整证明 project request UNSAT：Attempt Rejected；
- qualified resolver 完整证明 final environment/harness request UNSAT：Attempt Rejected；
- index、DNS、凭据、远端 source、cache、artifact 获取失败：Indeterminate；
- timeout、signal、start error、未知/损坏 resolver output：Indeterminate；
- build、安装、graph inspection 与 PF invariant failure：本设计不扩大其资格，仍为 Indeterminate。

这不是 failure attribution 的例外。resolver 的结构化 UNSAT 是该 operation 自己定义的权威负终态；网络错误只是未能获得 resolver 结论。

## 8. baseline、declaration 与 probe

Verifier classifier 不读取 Attempt role、requested resolution 或 baseline。

```text
TestAdapter
  ProcessResult -> VerifierOutcome

FailurePolicy / run orchestration
  outcome + Attempt scope -> FailureRecord / terminal result

CoordinateSearch
  baseline PASS anchor + probe disposition -> boundary movement
```

相同 VerifierRejected 在不同 role 中的消费方式不同：

| Role | VerifierRejected | VerifierIndeterminate |
| --- | --- | --- |
| Baseline | Baseline Rejection，终止 cell | Baseline Indeterminate，终止 cell |
| Declaration | 声明不满足完整验证契约，终止 check cell | 声明结果未知，终止 check cell |
| Probe | 合法负向 observation，继续 D003 | 立即终止 search cell |

Baseline PASS 只证明该 cell 存在已知 PASS anchor。它不证明后续 failure 是 dependency change 导致，也不进入 verifier policy identity。

## 9. pytest diagnostics 与 progress

### 9.1 降级为非权威 metadata

D013 的 pytest facts 可以继续帮助 `diagnose`/CLI：

- collection、setup、call、teardown、internal phase；
- serial/xdist/unknown execution mode；
- completed/total tests progress；
- bounded failure count 或 summary code。

它们不得：

- 产生或撤销 VerifierRejected；
- 把 exit 0 改成 Indeterminate；
- 进入 `evaluation_policy_identity`、Attempt identity、Proposal identity 或 `failure_id` 的 disposition preimage；
- 决定 cache、merge、report 或 apply authority；
- 要求 pytest/CPython qualification matrix 才允许 nonzero rejection。

### 9.2 Observer 故障

observer 资源、临时目录或注入准备失败时，必须执行未注入的原 verifier。observer protocol、progress 或 cleanup 失败只关闭 diagnostics，不能覆盖已经观察到的 process terminal result。

PF-owned observer 必须吞掉自身 telemetry/serialization failure，不能主动改变 pytest exit。若 observer 已注入且整个 process 最终正常 nonzero exit，v1 不做 root-cause attribution，仍按 VerifierRejected 处理；这是保留进程内 observer 的已知限制。若不能接受该限制，应删除权威 invocation 中的 observer，而不是恢复 witness authority。

### 9.3 持久化与展示

公共 FailureRecord 只保存有界、脱敏、稳定的诊断 facts。异常正文、test ID、nodeid、路径和完整 stdout/stderr 继续只在本地 Process Log；`pf diagnose` 提供 locator，`pf explain` 只聚合稳定 reason/phase/count。

诊断 metadata 缺失时，展示：

```text
What happened: The configured verifier exited with status N.
Impact: This attempt did not satisfy the verification contract.
Diagnose: pf diagnose PACKAGE --failure FAILURE_ID
```

不得显示 “PF could not determine compatibility” 或建议检查 pytest witness，因为 disposition 已是 Rejected。

## 10. 配置与 policy identity

### 10.1 删除 `test-failure-exit-codes`

所有正常非零 exit 都是 negative verifier result，因此 `test-failure-exit-codes` 不再表达可变策略。落地时从以下位置直接删除：

- `[tool.pf]` 配置 Schema、默认值与 merge；
- D001 配置表与 README/help；
- `TestOperations.run` / `TestAdapter.run` 参数；
- CLI/project planning 与测试 fixture；
- test outcome profile selector。

项目尚未发布，不保留 deprecated alias。配置中继续出现该 key 时按严格 unknown key 返回配置错误，提示删除。

### 10.2 固定 outcome policy

`evaluation_policy_identity` 使用固定的 verifier outcome policy identity，例如：

```text
configured-verifier-exit-v1
```

它不包含 pytest version、execution mode、witness protocol、progress protocol 或 diagnostic availability。`test-command`、timeout 与其他真正改变验证契约的配置仍按现行规则进入 policy identity。

落地后旧 policy identity 的报告不能与新结果 merge/cache。search 生成新 report generation；项目未发布，不迁移开发期旧报告。

## 11. Schema 与 module 影响

### 11.1 运行期 outcome

现行：

```text
TestPass | TestFail | ToolFailure
```

目标：

```text
VerifierPass | VerifierRejected | VerifierIndeterminate
```

名称必须表达 disposition 资格，不表达 pytest phase 或工具责任。`RuntimeEvaluator` 可以继续把该 union 包装进 full Evaluation，但不能重新解释 exit code。

### 11.2 FailurePolicy

`FailurePolicy` 继续验证 scope、构造稳定 `failure_id`、生成 FailureRecord，并拥有 prepare cause 的 disposition 矩阵。对于 VerifierOutcome，它不得按 cause、pytest facts 或 exit integer 再分类：

```text
VerifierRejected      -> REJECTED
VerifierIndeterminate -> INDETERMINATE
```

PASS 仍不经过 FailurePolicy。

### 11.3 FailureRecord

Verifier Rejection 的稳定 facts 至少包含：

```text
disposition = REJECTED
stage = test
reason = VERIFIER_EXITED_NONZERO
process.exit_code = N
```

`cause=TEST_FAILURE` 可以在迁移期间作为内部名字，但目标 Schema 应使用不暗示 test phase attribution 的 reason。`TOOL_FAILURE` 不得用于表示“verifier 内部某个工具报错”；Indeterminate 必须使用实际 observation-validity reason。

### 11.4 Schema 2 协调

D014 当前草案固定了 `TEST_FAIL` terminal Evaluation 和 `cause=TEST_FAILURE` wire 字符串。若 D015 获批，D014/P013 在完成前必须同步为新的 verifier outcome/reason；不能先发布 Schema 2 再在同一 `schema_version` 静默改名。

Schema 2 仍保留：

- `PASS | REJECTED | INDETERMINATE` direct observation；
- FailureRecord disposition、stage、reason 与 Portable Process Facts；
- Attempt/Proposal refs、boundary 和 final PASS 闭环。

pytest diagnostic metadata 若进入公共报告，必须是可选非权威字段；其存在与否不参与引用闭环或 authority validator。

## 12. 责任边界与随机性

PF 只对自己能够机械观察的层负责。

- PF 控制的 source/index/resolve/process launch/timeout/signal 可结构化为 Indeterminate；
- verifier 内部访问网络后正常 exit nonzero，PF 记录 Rejected；
- verifier 捕获 signal 后自行 exit nonzero，PF 记录 Rejected；
- verifier/plugin 把 failure 改写为 exit 0，PF 记录 Pass；
- wrapper 隐藏子进程 OOM/Signal 并返回整数，PF 不反推隐藏事实。

用户提供的 verifier 应尽量 deterministic、自包含。未来若需要处理随机 verifier，应单独设计 retry/quorum，例如：

```text
FAIL, FAIL -> REJECTED
FAIL, PASS -> INDETERMINATE / unstable
PASS, PASS -> PASS
```

这属于多次 observation 的可靠性策略，不属于 pytest lifecycle 或 failure attribution，且不在本文 v1 范围。

## 13. 场景矩阵

| 场景 | 权威结果 | 诊断 |
| --- | --- | --- |
| pytest assertion，exit 1 | Rejected | 可选 `phase=call` |
| pytest collection import，exit 2 | Rejected | 可选 `phase=collect` |
| pytest bootstrap/plugin incompatibility，exit 1 | Rejected | Process Log / 可选 phase |
| pytest internal error，exit 3 | Rejected | 可选 `phase=internal` |
| pytest invalid option，exit 4 | Rejected | Process Log |
| pytest no tests，exit 5 | Rejected | 可选 count `0` |
| xdist worker failure，controller 正常 exit nonzero | Rejected | xdist metadata 可选 |
| witness 缺失/损坏，exit nonzero | Rejected | diagnostics unavailable |
| witness 声称 failure，exit 0 | Pass | diagnostic conflict warning |
| PF timeout kill | Indeterminate | timeout facts |
| OS signal | Indeterminate | signal facts |
| process start error | Indeterminate | start error class |
| Process Log 写入不完整但 exit nonzero 已知 | Rejected | diagnosis degraded |
| certified project/harness UNSAT | Rejected | resolver reason |
| PyPI 503 / DNS / credential failure | Indeterminate | source reason |

## 14. 实施顺序

本文获批后再创建实施计划，按垂直 TDD 切片推进：

1. **Public outcome contract**：先以 `TestOperations.run` 的 exit 0/1/2/3/4/5、timeout、signal、start error 表建立 RED；实现统一 VerifierOutcome。
2. **Remove classification authority**：删除 pytest outcome profile selector、qualification authority和 witness-dependent classifier；保留 diagnostics/progress 时证明它们不改变 outcome。
3. **Config/policy**：删除 `test-failure-exit-codes`，固定新 policy identity，覆盖 unknown-key 错误与 generation/cache 隔离。
4. **Evaluation/failure/report**：重命名 runtime Evaluation、reason 与 Schema 2 wire；保持 Attempt/Proposal/failure refs 和 search boundary 不变量。
5. **CLI/diagnose**：让 Rejected nonzero exit 展示明确 reason/exit；diagnostic metadata 缺失只降低详情。
6. **Dogfood regression**：真实运行 PF 自搜索，证明 Python 3.12 `packaging==20.9` 是 Rejection 而不是 cell-terminal Indeterminate，并继续定位更高 PASS。
7. **Quality gates**：全量 pytest、ty、Ruff、build、wheel entry-point smoke、文档链接与 `git diff --check`。

D014/P013 正在修改报告 Schema；实施 D015 前必须明确文件所有权并在同一 Schema 2 变更中协调 wire names，不能让两个 agent 分别修改 `schemas/evaluation.py`、`schemas/report.py` 或 `report.py`。

## 15. 验收不变量

1. 任意正常非零 verifier exit 都产生 Rejected，不依赖 command shape、pytest phase、witness、版本或 execution mode；
2. exit 0 产生 Pass，非权威 diagnostics 不能覆盖；
3. timeout、signal、start error 与未知 terminal 产生 Indeterminate；
4. verifier stdout/stderr 或 witness 不完整只降低诊断，不改变已知 exit outcome；
5. `test-failure-exit-codes` 从配置与 interface 完全删除；
6. pytest diagnostics/progress 不进入 evaluation/report/cache/apply authority identity；
7. baseline 不进入 classifier，只在 D003 授权 probe rejection 成为搜索边界；
8. Rejection 只否定完整 Attempt/context，不声明 dependency root cause；
9. certified resolver UNSAT 仍可在 Proposal 建立前拒绝 Attempt；其他 prepare 分类不因本文扩大；
10. `CoordinateSearch` 不导入或识别 pytest、exit code、reason taxonomy 或 diagnostics；
11. D014 Schema 2 在首次落地前采用最终 outcome/reason 名，不在同一版本内漂移；
12. `packaging==20.9` × Python 3.12 bootstrap nonzero exit 成为 direct Probe Rejection；
13. verifier 内部网络失败若正常 exit nonzero 是 Rejected，PF 不解析文本猜测基础设施原因；
14. retry/quorum 不作为本设计的隐藏补偿机制。

## 16. 被拒绝的方案

- **继续由 pytest witness 授权 Rejection。** 把 failure attribution 置信度误当成 observation validity，并使 bootstrap/plugin/usage failure 假装 unknown。
- **只把 exit 1/2 当 failure。** 复制 pytest lifecycle 语义；其他 verifier 与 wrapper 仍需 classifier registry。
- **保留 `test-failure-exit-codes`。** 与“所有正常非零终态都是 negative verifier result”形成两套权威。
- **从 stderr/traceback 猜 network/tool/project root cause。** 规则无法封闭，也会把用户 verifier 内部故障错误提升为 PF 基础设施事实。
- **让 baseline PASS 自动归因后续 failure。** baseline 只提供搜索锚点，不证明时序因果。
- **把 witness/progress protocol 损坏升级为 Attempt Indeterminate。** 非权威 diagnostics 不得撤销已观察到的 verifier terminal result。
- **让 search 直接消费 exit code。** exit 解释应留在 `TestAdapter` interface 后；CoordinateSearch 只消费三态。

## 17. 对现行契约的取代

本文落地后：

- D001 删除 direct pytest witness outcome 规则与 `test-failure-exit-codes` 配置，只保留 test command、timeout、命令聚合和退出码；
- D002 以统一 VerifierOutcome 取代 generic/pytest outcome profile，pytest logic 仅作私有 diagnostics/progress；
- D005 更新“结果完整”的定义、test stage classification 与 cause/reason 术语，保留 prepare 矩阵和 FailureRecord 所有权；
- D007 明确 verifier 的 stdout/stderr 日志完整性不影响已知 exit outcome，structured-output adapter 规则不变；
- D008 以 VerifierRejected/VerifierIndeterminate 投影统一运行结果，不再引用 D013 witnessed failure；
- D013 删除 §§4–5、§8 negative-evidence authority、§9 outcome 表、§10 policy identity、§12 disposition 降级和相应验收条款；保留或重写 best-effort diagnostics/progress；
- D014 在 Schema 2 首次落地前同步 terminal Evaluation 与 FailureRecord wire names；
- D003、D004、D006、D009–D012 的搜索、静态、展示、架构和 harness 规则不因本文改变，只更新必要交叉引用。

# PF 权威验证终态

- **状态：** 已归档（已归并）
- **日期：** 2026-08-25
- **最后核对：** 2026-08-28
- **适用范围：** 配置 verifier 的权威终态、Attempt disposition、诊断 metadata 与迁移
- **现行分类：** [D005](../../designs/D005-pf-failure-and-diagnose.md)
- **现行报告：** [D014](../../designs/D014-pf-report-schema.md)

本文记录已经完成的迁移决策，不再拥有现行条款。configured verifier interface、failure、
process、runtime diagnostics、pytest observer 与 Schema 1 的唯一现行所有者分别是
D001/D002/D005/D007/D008/D013/D014；下文保留迁移理由与验收证据口径。

## 1. 决策

成功 prepare 后，配置 verifier 的唯一权威输入是 PF 实际观察到的 process terminal
facts：

```text
normal exit 0        -> PASS
normal exit nonzero  -> REJECTED
start failed         -> INDETERMINATE
PF timeout           -> INDETERMINATE
process signaled     -> INDETERMINATE
terminal unavailable -> INDETERMINATE
```

normal exit 精确定义为：`timed_out = false`、`start_error = null`、
`signal = null` 且 `exit_code` 是整数。exit code 的具体整数不改变 disposition；pytest
lifecycle、failure-witness、stdout/stderr、异常类型和推测根因都不参与映射。

所有正常非零 exit 在 **disposition** 上等价，包括 pytest 1–5、其他 verifier exit N，
以及 wrapper 把内部 signal 转换成的 128+N。具体整数仍是 portable terminal fact，进入
VerifierTerminal、Failure identity 与同一 Proposal 的冲突检测。PF 不从整数反推 wrapper
隐藏的 signal。若 PF 直接观察到 `signal != null`，结果仍是 Indeterminate。

Rejection 只否定完整 Attempt/context，不归因到某个 dependency version，也不声称
assertion、import、plugin、CLI、网络或外部服务是根因。Baseline PASS 只提供已知通过
锚点，不证明后续 Rejection 是依赖变化造成的。

该决策解决 D013 的现行边界：成功 prepare 后，verifier 若在 pytest bootstrap、usage、
internal error、collection 或 no-tests 阶段正常非零退出，不再因为缺少已资格化
failure-witness 得到 Indeterminate。

## 2. 领域模型与唯一 authority

Attempt 仍是 disposition 的主语；Proposal 只在 prepare 成功后存在：

```text
Attempt
  ├── prepare failure            -> Proposal 不存在
  └── prepare success -> Proposal -> configured verifier
```

本设计区分三个事实层：

- **Verifier Terminal**：从实际 process run 投影出的结构化终态；是配置 verifier
  outcome 和可移植 identity 的唯一输入。
- **Authoritative Result**：`VerifierPass | VerifierRejected |
  VerifierIndeterminate`；已经决定 disposition。
- **Diagnostic Metadata**：duration、output completeness、pytest phase/progress/detail、
  summary、原始输出和 Process Log association；只解释结果。

authority 只形成一次：生产 `ConfiguredVerifier` module 从 terminal facts 返回
Authoritative Result。RuntimeEvaluator 把 authoritative result 装入 Evaluation，并把
runtime-only diagnostics 放在独立的 `RuntimeEvaluationRun` wrapper；FailurePolicy 对配置
verifier 只机械记录并验证已经形成的 disposition，不得再次读取 exit code、output
completeness、pytest facts、cause 或 Role 重分类。

```text
ConfiguredVerifier
  -> VerifierRun(authoritative, diagnostics)
  -> RuntimeEvaluator / RuntimeEvaluationRun
       ├── evaluation  -> FailurePolicy.record_evaluation
       │                  -> FailureRecord
       │                  -> CoordinateSearch(disposition only)
       └── diagnostics -> D008 completion/event projection
                          -> CellResultDetail(detail_failure_id)
```

`RuntimeEvaluationRun` 不是 Evaluation cache 或 report 的一部分。Evaluation cache 只接收
`run.evaluation`；D008 在 FailurePolicy 已形成 Failure ID 后，才可把 final diagnostics
投影为 `CellResultDetail` 并绑定 `detail_failure_id`。动态 progress 继续直接发布
`CellStageEvent`，不进入 Evaluation、FailureRecord 或 wrapper identity。

```text
RuntimeEvaluator.evaluate(...) -> RuntimeEvaluationRun

RuntimeEvaluationRun
  evaluation: Evaluation
  diagnostics: VerifierDiagnostics | None  # invocation-local
```

`CoordinateSearch` 只消费 `PASS | REJECTED | INDETERMINATE`，不得导入 pytest、
exit code、reason taxonomy、ProcessResult 或 diagnostics。除
`ConfiguredVerifier` implementation 外，任何调用方复制 terminal classifier 都是契约
违规。

## 3. ConfiguredVerifier module 与 interface

consumer 定义最小 `VerifierOperations` interface；生产 adapter 与测试 fake 都通过同一
seam：

```text
VerifierOperations.run(request, progress=None) -> VerifierRun

VerifierRequest
  command
  cwd
  environment
  timeout

VerifierRun
  authoritative: VerifierOutcome
  diagnostics: VerifierDiagnostics | None  # runtime-only
```

`VerifierRun` 是运行期结果，不是 report wire type。`VerifierRequest` 不再包含
`failure_exit_codes`、pytest profile 或 qualification selector。

ConfiguredVerifier 使用 D007 拥有的既有 ProcessRunner seam，但 D015 落地时把其返回值
收敛为一个可判别 union：

```text
ProcessRunner.run(ProcessSpec) -> ProcessObservation

ProcessObservation
  ProcessResult
  ProcessTerminalUnavailable
```

`ProcessTerminalUnavailable` 不是 `None`、任意异常或非法 `ProcessResult` 的兜底。生产
SubprocessRunner 只有在 child 已进入受管 process 生命周期、完成有界 cleanup，但受支持的
平台 process primitive 明确无法提供 exit/signal/start-error 终态时才能返回它；动态错误
正文只进入 runtime diagnostics。没有这类 typed fact 时，不得制造 `Unavailable`。
未建模的 adapter 异常、schema validation error、programmer error 和 observer protocol
invariant violation 必须上抛为命令级 `InfrastructureError`，由 D001 返回 PF command exit
`4`；它们不产生 VerifierOutcome、FailureRecord 或 candidate observation。

这是共享 ProcessRunner seam 的穷尽变更，不是 verifier-only duck type。uv、ty、resolver、
runtime witness 等所有现有 consumer 都必须显式处理 `ProcessTerminalUnavailable`，并按各自
现行 operation 契约映射为 ToolFailure/Indeterminate；它不能为这些 operation 新增
Rejection authority。D007 的 Process Log/cache association 同样必须接受完整
ProcessObservation，使 unavailable 的本地诊断不依赖伪造 ProcessResult。

权威类型只保存规范 terminal projection：

```text
VerifierTerminal
  NormalExit(exit_code)
  StartFailed
  TimedOut
  Signaled(signal)
  Unavailable

VerifierPass
  terminal = NormalExit(0)

VerifierRejected
  terminal = NormalExit(nonzero)
  reason = verifier-exited-nonzero

VerifierIndeterminate
  terminal = StartFailed | TimedOut | Signaled | Unavailable
  reason = process-start-failed
         | process-timed-out
         | process-signaled
         | terminal-unavailable
```

完整 `ProcessResult` 可以作为 `VerifierDiagnostics` 的运行期输入，但不得成为
Authoritative Result 字段。Terminal identity 只包含 terminal kind 与适用的 exit
code/signal；duration、stdout/stderr completeness、start-error 正文和清理后观察到的
次级 exit/signal 不进入。

ConfiguredVerifier 必须先验证 ProcessObservation，再按下表投影；不得用分支优先级掩盖
非法组合：

| observation | 必须满足 | VerifierTerminal |
| --- | --- | --- |
| `ProcessTerminalUnavailable` | typed variant，无伪造 process facts | `Unavailable` |
| `ProcessResult(start_error)` | `timed_out=false`，无 exit/signal | `StartFailed` |
| `ProcessResult(timed_out=true)` | 无 start error；cleanup 后恰有 exit 或 signal | `TimedOut` |
| `ProcessResult(signal)` | `timed_out=false`，无 exit/start error | `Signaled(signal)` |
| `ProcessResult(exit_code)` | `timed_out=false`，无 signal/start error | `NormalExit(exit_code)` |

timeout 优先于 cleanup 后观察到的 signal/exit，因此 `TimedOut` 不吸收次级 terminal fact。
D007 的 ProcessResult invariant 必须同步禁止 `start_error + timed_out` 等组合。任何不符合
上表的输入都走命令级 `InfrastructureError`，不得缓存成 compatibility unknown。

这个 module 的 interface 是 verifier 行为测试的唯一测试面。terminal classifier、pytest
observer 注入和 telemetry projection 都是 private implementation；不得为单一生产实现再
公开一个 classifier interface。

## 4. Diagnostics 与 pytest observer

配置 verifier 不解析 stdout/stderr 来决定 outcome：

```text
exit 0  + stdout/stderr incomplete -> PASS，诊断降级
exit N  + stdout/stderr incomplete -> REJECTED，诊断降级
```

这不适用于必须解析结构化输出才能知道 operation outcome 的 uv/ty/resolver adapter；
它们继续按各自现行契约分类。

Final diagnostics 的唯一数据路径是：

```text
VerifierRun.diagnostics
  -> RuntimeEvaluationRun.diagnostics
  -> D008 completion projector
  -> CellResultDetail(detail_failure_id)
```

`VerifierDiagnostics` 可以包含完整运行期 ProcessObservation、pytest phase/failure count 与
有界 detail。它不得嵌入 Evaluation，也不得由 Evaluation cache 保存或返回；completion
projector 只能在对应 FailureRecord 已形成后保留 detail。Pass 的 diagnostics 可以用于当前
live/final UI，命令结束后丢弃。Process Log association 仍由 D007/D008 的本地 diagnosis
seam 管理，不进入这个 wrapper。

D013 的 pytest failure-witness 在落地后改为 **pytest observer**。它可以继续提供 phase、
execution mode、progress、failure count 和有界 detail，但不得进入：

- disposition 或 Failure cause；
- verifier/evaluation/failure/policy identity；
- Attempt、Proposal、cache、Journal 或 report authority；
- merge、apply 或 CoordinateSearch 决策。

PF 注入的 pytest observer/plugin 是简单、可信的 implementation 基座。它必须保持
transparent：不得重写 pytest exit status、test selection、collection continuation、hook
outcome 或执行顺序。注入准备、plugin import、强制 protocol artifact 或 serialization
invariant 违反该假设时属于 PF implementation bug，走命令级 `InfrastructureError`；PF
不得把它分类为 verifier Rejection/Indeterminate，也不得再执行一次未经注入的原命令。

child 启动后，实际 child terminal facts 始终是 verifier authority。已取得合法 observer
facts 后，可选 progress monitor、detail 读取或 UI projection 失败只能省略相应
diagnostics，不能覆盖 terminal outcome。PF 不推断“若没有 observer 注入，child 会得到
什么 exit code”的反事实。

observer 的安全/透明性 qualification 可以保留，但只证明 telemetry implementation，
不授予 Rejection authority，也不进入 evaluation policy identity。exit 0 与 pytest
failure metadata 冲突时仍为 Pass；可以显示 diagnostic conflict，但不能改写结果。

## 5. 两种 witness 的边界

D015 只取消 **D013 pytest failure-witness** 的 negative-evidence authority，不取消
D004/D011 runtime interface witness：

| 机制 | Role | authority |
| --- | --- | --- |
| pytest observer | 配置 verifier 的 UI diagnostics | 无 |
| runtime interface witness | static regression 后的独立 pre-verifier operation | 保持现行 |

runtime interface witness 仍可在已资格化、完整 `CONFIRMED_MISSING` 事实下产生
`RUNTIME_INTERFACE_MISSING` Rejection，并在配置 verifier 前结束该 Proposal。它只否定
完整 Attempt/context；cause 用于解释，不把 dependency version 声明为根因。

为避免实现和文档混淆，D013 落地时应把 `failure-witness` authority 术语改为
`pytest observer` / `pytest diagnostic telemetry`；`RuntimeWitness` 名称只留给 D004
runtime interface probe。

## 6. Prepare 范围不变

D015 只改变成功 prepare 后的配置 verifier，不扩大 D005/D012 的 prepare Rejection：

- qualification profile 完整证明 project request UNSAT：Rejected；
- qualification profile 完整证明 final environment/harness request UNSAT：Rejected；
- index、DNS、凭据、source、cache、artifact、build、installation、graph inspection、
  timeout、signal、损坏或未知 resolver output：按现行规则保持 Indeterminate。

Resolver 的结构化 UNSAT 是该 operation 自己的权威负终态，不从 stderr 或裸 nonzero
exit 推断。D015 不改变 ty/static transition、runtime interface witness 或
EnvironmentFactory 的 authority。共享 ProcessRunner 返回
`ProcessTerminalUnavailable` 时，这些 consumer 只走其现有 ToolFailure/Indeterminate
路径，不改变 cause/disposition 资格。

## 7. Role 消费

ConfiguredVerifier 不读取 baseline/declaration/probe Role。相同 outcome 由 D008 运行
编排消费：

| Role | VerifierRejected | VerifierIndeterminate |
| --- | --- | --- |
| Baseline（smoke/search） | Baseline Rejection，终止 Cell | Baseline Indeterminate，终止 Cell |
| Declaration（check） | 声明不满足完整验证契约 | 声明结果未知 |
| Probe（search） | 合法负向 observation，继续 D003 | 立即终止 Cell |

`declaration-capture` 只捕获 static baseline，不运行配置 verifier，因此不消费本表。
Role 不进入 terminal classifier、Attempt ID、Proposal ID 或 verifier outcome identity。

## 8. FailureRecord、identity 与 cache

配置 verifier 的 Failure 映射固定为：

```text
VerifierPass
  -> 无 FailureRecord

VerifierRejected
  -> disposition = REJECTED
  -> cause = VERIFIER_EXITED_NONZERO
  -> stage = test

VerifierIndeterminate(TimedOut)
  -> disposition = INDETERMINATE
  -> cause = TIMEOUT
  -> stage = test

VerifierIndeterminate(StartFailed | Signaled | Unavailable)
  -> disposition = INDETERMINATE
  -> cause = TOOL_FAILURE
  -> stage = test
```

`FailurePolicy.record_evaluation` 只执行上述机械映射和结构校验；不得调用现行
`rejection_is_supported` 重新判断 verifier disposition。其他 prepare/resolver/witness
operation 继续使用 D005 的现行 classification。

Schema 1 的全部 FailureRecord 在本次迁移中统一使用 `pf:failure:v2`。FailureRecord 用一个
可判别 authority union 代替顶层 `process? + detail?` 的重叠 optional fields：

```text
FailureAuthority
  ProcessFailureAuthority
    kind = process
    process: ProcessResult
    summary_code?
    detail?

  ConfiguredVerifierFailureAuthority
    kind = configured-verifier
    terminal: VerifierTerminal

  StructuredFailureAuthority
    kind = structured
    detail: FailureDetail
    summary_code?
```

`ProcessFailureAuthority` 保留非 verifier operation 现行 portable process/detail facts；
`StructuredFailureAuthority` 表达没有 process 的结构化失败。Configured verifier 必须使用
`ConfiguredVerifierFailureAuthority`，且不得携带 summary、detail 或完整 ProcessResult。
cross-validator 固定为：

- `VERIFIER_EXITED_NONZERO @ test` 只能是 `REJECTED + NormalExit(nonzero)`；
- `TIMEOUT @ test` 只能是 `INDETERMINATE + TimedOut`；
- `TOOL_FAILURE @ test` 只能是 `INDETERMINATE + StartFailed | Signaled |
  Unavailable`；
- 其他 cause/stage 不得使用 configured-verifier authority，继续按 D005/D007/D012
  使用 process 或 structured authority。

`pf:failure:v2` canonical preimage 对全部 FailureRecord 只包含：

- Failure scope（`AttemptFailureScope | CellFailureScope`）；
- disposition、cause、stage；
- 完整 `FailureAuthority` canonical payload；
- 已取得的 project/environment plan digests。

对 configured-verifier authority，payload 只有 VerifierTerminal kind 与适用的 exit
code/signal；duration、stdout/stderr completeness、start-error 正文、pytest facts/detail、
summary、Process Log locator 和 cleanup facts 不进入 Failure ID。同一 Attempt 的相同
terminal facts 必须在 diagnostics 降级或展示细节变化后得到相同 Failure ID。其他
authority 的 process/detail identity 规则保持其现行语义，但改用 v2 prefix 与判别形状。

D008 Verification Journal 与 D014 report 必须保存同一个 `FailureAuthority` 领域形状与
同一个 `pf:failure:v2` ID；Journal 不保留顶层 process/detail 布局。

Evaluation cache 对配置 verifier 比较 canonical Authoritative Result，不得只比较
Evaluation status。相同 Proposal/terminal、不同 diagnostics 复用同一结果；相同 Proposal
得到不同 terminal kind、不同 exit code 或不同 signal 时形成 `CacheConflict /
NONDETERMINISTIC`。因此 exit 1 与 exit 4 虽同为 Rejected，仍是冲突的 authoritative facts。

`evaluation_policy_identity` 增加固定
`configured-verifier-terminal-v1` policy，删除 test failure code、command-shape profile、
pytest version、execution mode、failure-witness、progress 和 observer qualification。test
command、cwd、timeout 等真实 verifier 配置仍按 D002/D008 进入现行 config/Attempt facts。

落地时直接删除现有开发期 evaluation cache、report、Journal 与 diagnosis association，
再按新 identity 重新生成；不读取、转换、合并或复用旧开发产物。

## 9. Schema 1 一次性修订

项目尚未上线，Schema 1 也不是公开兼容接口。因此 D015 直接原地修订 Schema 1，不提升
`schema_version`，不提供 dual-read、dual-write、legacy reader 或 migrator。现有开发期
`package-floor.json`、evaluation cache、Journal 和 diagnosis index 可以直接删除并重生。

Schema 1 同一变更中必须：

1. 在 report identity 增加 required literal
   `verifier_outcome_policy = "configured-verifier-terminal-v1"`，并让它进入
   evaluation/report generation identity；
2. terminal Evaluation union 从
   `PASS | TEST_FAIL | RUNTIME_INTERFACE_MISSING | INDETERMINATE` 改为
   `PASS | VERIFIER_REJECTED | RUNTIME_INTERFACE_MISSING | INDETERMINATE`；
3. 删除 `TEST_FAILURE` cause，增加 `VERIFIER_EXITED_NONZERO`；
4. 用 `FailureAuthority` 判别 union 替换 FailureRecord 的 `process? + detail?`，全部
   FailureRecord 使用 `pf:failure:v2`，禁止 authority both/neither；
5. PASS Evaluation 唯一保存 `NormalExit(0)`；VERIFIER_REJECTED 和 verifier
   INDETERMINATE Evaluation 只引用拥有 VerifierTerminal 的 FailureRecord，不重复保存
   terminal；其他 INDETERMINATE 通过 referenced FailureAuthority 区分 process/structured
   evidence；
6. verifier wire 不保存完整 `ProcessResult`、observer diagnostics 或
   `RuntimeEvaluationRun`；
7. 更新 identity validator、typed refs、reachability 与 merge/update；
8. 重新生成 JSON Schema 和 complete/incomplete 示例；
9. 删除现有开发期 report/cache/Journal/index，并由新实现重生，不实现或测试兼容读取。

Schema 1 的其他 operation 仍可按 D005/D007/D012/D014 保存其现行 portable process
facts；本节不全局重写 uv/ty/resolver 的结构化协议。

## 10. 配置、文档与最终所有者

同一落地变更必须删除：

- `[tool.pf].test-failure-exit-codes`；
- 配置默认值、schema 字段、adapter 参数和 CLI/help 文案；
- `configured-exit-code-v1` / `pytest-failure-witness-v1` outcome profile selector；
- D013 pytest version/execution-mode Rejection qualification 与 authority tests。

未知旧配置 key 必须直接配置失败。UI telemetry 的私有 command-shape selector 可以保留，
但它只选择 observer implementation，不进入 authority 或 identity。

D015 是一次迁移决策，不成为新的永久 owner。落地后把规则归并到以下现行文档，再把
D015 标为“已归并”：

| 规则 | 最终 owner |
| --- | --- |
| ConfiguredVerifier module/interface 与依赖方向 | D002 |
| verifier disposition、cause、FailureRecord/identity | D005 |
| Role 消费、RuntimeEvaluationRun diagnostics、Journal、completion | D008 |
| pytest observer 与 UI telemetry | D013 |
| Schema 1 wire、reader、merge/update | D014 |
| 用户配置、命令结果与退出码 | D001 |
| ProcessObservation、进程执行与 Process Log | D007 |

D001、D002、D005、D007、D008、D013、D014、CONTEXT、JSON Schema、示例和生成器必须
一次同步，不允许中间状态同时保留两套 authority。

## 11. 验收条件

### 11.1 ConfiguredVerifier interface

通过 `VerifierOperations.run` 的公开 interface，以 fake ProcessRunner 覆盖：

| Process facts | diagnostics | outcome |
| --- | --- | --- |
| exit 0 | 完整或截断 | PASS |
| exit 1/2/3/4/5/137 | generic 无 observer 或合法 observer facts；完整或截断输出 | REJECTED |
| timeout，cleanup 后另有 exit/signal | 任意 | INDETERMINATE |
| 原生 signal | 任意 | INDETERMINATE |
| start error | 任意 | INDETERMINATE |
| typed terminal unavailable | 任意 | INDETERMINATE |
| `start_error + timed_out` 等非法组合 | 任意 | InfrastructureError，不产生 outcome |
| 损坏/缺失强制 observer artifact 或 unexpected adapter exception | 任意 | InfrastructureError，不产生 outcome |

generic command、direct pytest、`python -m pytest`、wrapper、pytest 6–9、xdist 和 unknown
execution mode 对相同 terminal facts 必须产生相同 outcome。旧的 shallow
profile/classifier 测试由 interface 行为测试替换，不在删除 authority 后继续保留第二套
测试面。

### 11.2 Diagnostics 与 identity

必须证明：

1. trusted observer qualification 证明注入不改变 exit status、selection、hook outcome 或
   执行顺序；plugin/protocol invariant 失败走命令级 InfrastructureError，且不重跑原命令；
2. 合法 observer facts 之后的可选 monitor/detail/UI projection 失败不改变 child terminal
   outcome；
3. `RuntimeEvaluationRun.diagnostics` 能投影到绑定对应 Failure ID 的
   `CellResultDetail`，但 Evaluation cache/Journal/report 中不存在该 wrapper；
4. exit 0 与 failure metadata 冲突仍为 Pass；
5. 相同 Attempt/terminal、不同 duration、output completeness、detail、summary 或日志得到
   相同 disposition、Failure ID、policy/cache identity 和 report authority；
6. 相同 Proposal 的 exit 1→4、signal 9→15 或 terminal kind 变化形成
   `NONDETERMINISTIC`，不能因 disposition/status 相同而复用；
7. pytest diagnostics 不出现在 Evaluation cache、Journal、report、merge/apply identity；
8. runtime interface witness 现行 Rejection 路径保持通过。

### 11.3 Schema、配置与 fail-closed

必须证明：

1. `test-failure-exit-codes` 与 authority profile 从 config/schema/help/source 完全删除，
   配置模型只接受新字段集合；
2. reader 只接受 required `verifier_outcome_policy`、新 Evaluation union 与新 cause 集合；
3. reader 拒绝 FailureAuthority both/neither、cause/stage/authority 不匹配，并复算全部
   `pf:failure:v2` preimage；
4. JSON Schema、两个示例和 Pydantic wire model 无漂移；
5. 实施不包含 legacy reader、alias、migrator 或兼容测试；开发期
   report/cache/Journal/index 由新实现重生；
6. 所有共享 ProcessRunner consumer 穷尽处理 `ProcessTerminalUnavailable`，且
   prepare、resolver、ty/static、runtime witness 与 D003 的 disposition-only interface
   保持不变；
7. Verification Journal 与 report 对同一 Failure ID 展开为相同 FailureAuthority。

### 11.4 产品路径与 dogfood

至少从原始 CLI 路径证明：

1. `packaging==20.9` × Python 3.12 的正常 pytest bootstrap nonzero 是 direct Probe
   Rejection，search 继续寻找更高 PASS；
2. `pydantic==1.7.4` 的 conftest/import **child verifier exit 4** 是 direct Probe
   Rejection，search 继续到 Pydantic 2.x，而不是以 ToolFailure 停止 Cell；
3. smoke/search baseline normal nonzero 分别形成 Baseline Rejection；
4. check declaration normal nonzero 形成声明 Rejection；
5. timeout/signal/start error 仍产生 **PF command exit 4 / compatibility unknown**，并立即
   停止 Probe Cell；
6. `pf explain`、`pf diagnose`、search terminal card 的 Failure ID、title、disposition 与
   terminal attempt 一致，不回退到历史 probe diagnostics。

最终门禁包括聚焦 interface/schema tests、Python 3.10–3.12 全量测试、Ruff、ty、build、
安装后 CLI smoke、JSON Schema/examples generator、文档链接、`git diff --check`，以及用
新 policy 重新生成的 PF self-search 报告。

## 12. 非目标

- 不判断 verifier 非零退出由 project、dependency、plugin、harness、网络或外部服务
  中哪一方造成；
- 不解析 traceback、stderr 或 pytest facts 决定 disposition；
- 不自动重试或引入 quorum/flaky 策略；
- 不扩大 prepare、resolver、ty 或 runtime witness 的负向证据资格；
- 不把诊断正文、test ID、本地日志或 duration 放入公共 authority identity；
- 不新增 test-runner classifier registry 或 pytest authority profile；
- 不保留、读取或转换任何旧开发期配置、cache、report、Journal 或 diagnosis index。

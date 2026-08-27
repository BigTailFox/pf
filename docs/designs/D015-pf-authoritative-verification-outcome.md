# PF 权威验证终态

- **状态：** 草案
- **日期：** 2026-08-25
- **最后核对：** 2026-08-27
- **适用范围：** 配置 verifier 的权威终态、Attempt disposition、诊断 metadata 与迁移
- **现行分类：** [D013](D013-pf-pytest-failure-evidence.md)
- **现行报告：** [D014](D014-pf-report-schema.md)

本文不描述当前行为。当前代码仍使用 D013 的
`TestPass | TestFail | ToolFailure`、pytest failure-witness authority 和
`test-failure-exit-codes`；当前 Schema 2 仍使用
`PASS | TEST_FAIL | RUNTIME_INTERFACE_MISSING | INDETERMINATE`。只有另行批准并
完整落地 D015 后，下面的规则才生效。

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

所有正常非零 exit 等价，包括 pytest 1–5、其他 verifier exit N，以及 wrapper 把内部
signal 转换成的 128+N。PF 不从整数反推 wrapper 隐藏的 signal。若 PF 直接观察到
`signal != null`，结果仍是 Indeterminate。

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
Authoritative Result。RuntimeEvaluator 只把它装入 Evaluation；FailurePolicy 对配置
verifier 只机械记录并验证已经形成的 disposition，不得再次读取 exit code、output
completeness、pytest facts、cause 或 Role 重分类。

```text
ConfiguredVerifier
  -> VerifierRun.authoritative
  -> RuntimeEvaluator / Evaluation
  -> FailurePolicy.record_evaluation
  -> FailureRecord
  -> CoordinateSearch(disposition only)
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

分类顺序固定为：

1. 没有预期的 typed process result：`Unavailable`；
2. `start_error != null`：`StartFailed`；
3. `timed_out == true`：`TimedOut`；
4. `signal != null`：`Signaled(signal)`；
5. `exit_code == 0`：`NormalExit(0)`；
6. `exit_code != 0`：`NormalExit(nonzero)`；
7. 违反 process terminal invariant：抛出命令级 `INTERNAL_INVARIANT`，不得制造
   candidate observation。

timeout 优先于 cleanup 后观察到的 signal/exit。预期的 process start failure 或 runner
明确返回 terminal unavailable 可形成 VerifierIndeterminate；任意未建模的 adapter
异常、schema invariant 或 programmer error 必须上抛到命令级 internal-failure 路径，
不得缓存成兼容性未知。

这个 module 的 interface 是 verifier 行为测试的唯一测试面。terminal classifier、pytest
observer 准备和 fallback 都是 private implementation；不得为单一生产实现再公开一个
classifier interface。

## 4. Diagnostics 与 pytest observer

配置 verifier 不解析 stdout/stderr 来决定 outcome：

```text
exit 0  + stdout/stderr incomplete -> PASS，诊断降级
exit N  + stdout/stderr incomplete -> REJECTED，诊断降级
```

这不适用于必须解析结构化输出才能知道 operation outcome 的 uv/ty/resolver adapter；
它们继续按各自现行契约分类。

D013 的 pytest failure-witness 在落地后改为 **pytest observer**。它可以继续提供 phase、
execution mode、progress、failure count 和有界 detail，但不得进入：

- disposition 或 Failure cause；
- verifier/evaluation/failure/policy identity；
- Attempt、Proposal、cache、Journal 或 report authority；
- merge、apply 或 CoordinateSearch 决策。

Observer 的运行语义固定为：

1. child 启动前 observer 准备或注入失败：清理全部 PF 私有环境，执行一次未经注入的
   原始命令；
2. child 已启动后，实际 child terminal facts 是 authority；
3. observer artifact 缺失/损坏、读取、校验、serialization、monitor 或 cleanup 失败
   只能省略 diagnostics，不能覆盖已经取得的 terminal outcome；
4. PF 不推断“若没有 observer 注入，child 会得到什么 exit code”的反事实；
5. observer implementation 必须 fail-open，不得主动重写 pytest exit status、test
   selection、collection continuation 或执行顺序。

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
EnvironmentFactory 的 authority。

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

配置 verifier Failure identity 使用 `pf:failure:v2`，其 preimage 只包含：

- Attempt scope；
- disposition、cause、stage；
- VerifierTerminal kind 与适用的 exit code/signal；
- 已取得的 project/environment plan digests。

duration、stdout/stderr completeness、start-error 正文、pytest facts/detail、summary、
Process Log locator 和 cleanup facts 不进入 Failure ID。同一 Attempt 的相同 terminal facts
必须在 diagnostics 降级或展示细节变化后得到相同 Failure ID。

`evaluation_policy_identity` 增加固定
`configured-verifier-terminal-v1` policy，删除 test failure code、command-shape profile、
pytest version、execution mode、failure-witness、progress 和 observer qualification。test
command、cwd、timeout 等真实 verifier 配置仍按 D002/D008 进入现行 config/Attempt facts。

旧 evaluation policy、Attempt/Proposal/Failure identity 和 cache entry 不得与新结果合并
或复用。项目尚未发布，不提供开发期 cache/report 迁移器。

## 9. Schema 2 一次性修订

项目尚未发布，因此 D015 落地时直接修订未发布的 Schema 2，不提升
`schema_version`，也不提供 dual-read、dual-write 或 migrator。该选择只适用于首次
公开发布前；若 Schema 2 已对外发布，必须另行设计 Schema 3，不能继续按本节实施。

Schema 2 同一变更中必须：

1. 在 report identity 增加 required literal
   `verifier_outcome_policy = "configured-verifier-terminal-v1"`，并让它进入
   evaluation/report generation identity；旧报告因缺失该字段 fail closed；
2. terminal Evaluation union 从
   `PASS | TEST_FAIL | RUNTIME_INTERFACE_MISSING | INDETERMINATE` 改为
   `PASS | VERIFIER_REJECTED | RUNTIME_INTERFACE_MISSING | INDETERMINATE`；
3. 删除 `TEST_FAILURE` cause，增加 `VERIFIER_EXITED_NONZERO`；
4. verifier Evaluation/Failure 只保存 `VerifierTerminal`，不保存完整
   `ProcessResult` 或 observer diagnostics；
5. 更新 `pf:failure:v2` identity validator、typed refs、reachability 与 merge/update；
6. 重新生成 JSON Schema 和 complete/incomplete 示例；
7. 证明旧 report/cache 不能 read、merge、reuse 或 apply；当前开发报告由新的
   `pf search` 重生。

Schema 2 的其他 operation 仍可按 D005/D007/D012/D014 保存其现行 portable process
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
| Role 消费、Journal、completion | D008 |
| pytest observer 与 UI telemetry | D013 |
| Schema 2 wire、reader、merge/update | D014 |
| 用户配置、命令结果与退出码 | D001 |
| 进程执行与 Process Log | D007 |

D001、D002、D005、D007、D008、D013、D014、CONTEXT、JSON Schema、示例和生成器必须
一次同步，不允许中间状态同时保留两套 authority。

## 11. 验收条件

### 11.1 ConfiguredVerifier interface

通过 `VerifierOperations.run` 的公开 interface，以 fake ProcessRunner 覆盖：

| Process facts | diagnostics | outcome |
| --- | --- | --- |
| exit 0 | 完整或截断 | PASS |
| exit 1/2/3/4/5/137 | 无/合法/损坏 observer facts，完整或截断输出 | REJECTED |
| timeout，cleanup 后另有 exit/signal | 任意 | INDETERMINATE |
| 原生 signal | 任意 | INDETERMINATE |
| start error | 任意 | INDETERMINATE |
| typed terminal unavailable | 任意 | INDETERMINATE |
| 非法 terminal facts / unexpected adapter exception | 任意 | INTERNAL_INVARIANT，不产生 outcome |

generic command、direct pytest、`python -m pytest`、wrapper、pytest 6–9、xdist 和 unknown
execution mode 对相同 terminal facts 必须产生相同 outcome。旧的 shallow
profile/classifier 测试由 interface 行为测试替换，不在删除 authority 后继续保留第二套
测试面。

### 11.2 Diagnostics 与 identity

必须证明：

1. observer setup 失败只执行一次未经注入的原命令；
2. observer artifact/monitor/read/cleanup 失败不改变 child terminal outcome；
3. exit 0 与 failure metadata 冲突仍为 Pass；
4. 相同 Attempt/terminal、不同 duration、output completeness、detail、summary 或日志得到
   相同 disposition、Failure ID、policy/cache identity 和 report authority；
5. pytest diagnostics 不出现在 Evaluation cache、Journal、report、merge/apply identity；
6. runtime interface witness 现行 Rejection 路径保持通过。

### 11.3 Schema、配置与 fail-closed

必须证明：

1. `test-failure-exit-codes` 与 authority profile 从 config/schema/help/source 完全删除，旧
   key 配置失败；
2. 新 reader 拒绝缺少/未知 `verifier_outcome_policy`、旧 `TEST_FAIL` 和
   `TEST_FAILURE`；
3. JSON Schema、两个示例和 Pydantic wire model 无漂移；
4. 旧 report/cache 不能 merge、reuse 或 apply；
5. prepare、resolver、ty/static、runtime witness 与 D003 的 disposition-only interface
   保持不变。

### 11.4 产品路径与 dogfood

至少从原始 CLI 路径证明：

1. `packaging==20.9` × Python 3.12 的正常 pytest bootstrap nonzero 是 direct Probe
   Rejection，search 继续寻找更高 PASS；
2. `pydantic==1.7.4` 的 conftest/import exit 4 是 direct Probe Rejection，search 继续到
   Pydantic 2.x，而不是以 ToolFailure 停止 Cell；
3. smoke/search baseline normal nonzero 分别形成 Baseline Rejection；
4. check declaration normal nonzero 形成声明 Rejection；
5. timeout/signal/start error 仍产生 exit 4/compatibility unknown，并立即停止 Probe Cell；
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
- 不为未发布的旧配置、cache 或 report 提供兼容迁移。

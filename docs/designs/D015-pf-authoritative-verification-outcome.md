# PF 权威验证终态

- **状态：** 草案
- **日期：** 2026-08-25
- **最后核对：** 2026-08-26
- **适用范围：** 配置 verifier 的拟议终态、Attempt disposition 与诊断 metadata 分离
- **现行分类：** [D013](D013-pf-pytest-failure-evidence.md)
- **现行报告：** [D014](D014-pf-report-schema.md)

本文不描述当前行为。当前代码仍使用 D013 的 `TestPass | TestFail | ToolFailure` 与 pytest failure-witness authority；当前 Schema 2 仍使用 `PASS | TEST_FAIL | RUNTIME_INTERFACE_MISSING | INDETERMINATE`。只有另行批准并完整落地 D015 后，下面的拟议规则才生效。

## 1. 拟议决策

配置 verifier 的唯一权威通道改为 PF 观察到的 process terminal facts：

```text
exited(0)          -> PASS
exited(nonzero)    -> REJECTED
start failed       -> INDETERMINATE
PF timeout         -> INDETERMINATE
process signaled   -> INDETERMINATE
terminal unknown   -> INDETERMINATE
```

exit code 的具体整数、pytest lifecycle、failure witness、stdout/stderr、异常类型和推测根因都不改变该映射。Rejection 只否定完整 Attempt/context，不归因到单个 dependency version。

该方案解决 D013 的一个边界：已成功 prepare 的 verifier 若在 pytest bootstrap、usage、internal error、collection 或 no-tests 阶段正常 nonzero exit，当前实现可能因缺少已资格化 witness 得到 Indeterminate；本草案拟把它视为明确的 verifier negative result。

## 2. 拟议领域模型

Attempt 仍是 disposition 的主语；Proposal 只在 prepare 成功后存在：

```text
Attempt
  ├── prepare failure            -> Proposal 不存在
  └── prepare success -> Proposal -> configured verifier
```

本草案引入两个仅在落地后成立的区分：

- **Authoritative Result**：足以决定 disposition 的结构化终态；配置 verifier 只使用 process terminal facts。
- **Diagnostic Metadata**：pytest phase/progress、summary、Process Log 等解释信息；它不能产生、撤销或改变 disposition。

Cause/reason 只解释已经形成的 outcome。`CoordinateSearch` 继续只消费 `PASS | REJECTED | INDETERMINATE`，不得导入 pytest、exit code、reason taxonomy 或 diagnostics。

## 3. 拟议 interface

```text
TestAdapter.run(command, cwd, environment, timeout, progress=None)
  -> VerifierPass
   | VerifierRejected
   | VerifierIndeterminate
```

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

分类顺序固定为：

1. 无可信 `ProcessResult`；
2. `start_error != null`；
3. `timed_out == true`；
4. `signal != null`；
5. `exit_code == 0`；
6. `exit_code != 0`；
7. 其他非法终态。

前四项和第七项是 Indeterminate，第五项是 Pass，第六项是 Rejected。timeout 后清理进程产生的 signal/exit 不能覆盖 timeout。

所有正常非零 exit 等价，包括 pytest 1–5、其他 verifier exit N，以及 wrapper 把内部 signal 转成的 128+N。PF 不从整数反推被 wrapper 隐藏的进程事实。exit 0 与 pytest failure metadata 冲突时仍为 Pass；可以记录 diagnostic conflict，但不能改写 outcome。

## 4. 日志与 diagnostics

配置 verifier 不解析 stdout/stderr 来决定 outcome：

```text
exit 0  + incomplete Process Log -> PASS，诊断降级
exit N  + incomplete Process Log -> REJECTED，诊断降级
```

这不适用于需要解析结构化输出才能知道 operation outcome 的 uv/ty 等 adapter；它们继续由各自现行契约分类。

pytest observer 可以继续提供 phase、execution mode、progress、failure count 或有界摘要，但不得进入 disposition、policy identity、Attempt/Proposal/failure identity、cache、merge 或 apply authority。Observer 的准备、协议、cleanup 或 serialization 失败只能关闭 diagnostics；若 observer 注入失败，应执行未注入的原命令。

## 5. Prepare 边界不变

本草案只改变成功 prepare 后的配置 verifier，不扩大 D005/D012 的 prepare Rejection：

- qualification profile 完整证明 project request UNSAT：Rejected；
- qualification profile 完整证明 final environment/harness request UNSAT：Rejected；
- index、DNS、凭据、source、cache、artifact、build、installation、graph inspection、timeout、signal、损坏或未知 resolver output：按现行规则保持 Indeterminate。

Resolver 的结构化 UNSAT 是该 operation 自己的权威负终态，不是从 stderr 或裸 nonzero exit 推断。

## 6. Role 消费

Classifier 不读取 baseline/declaration/probe role。相同 outcome 由运行编排按角色消费：

| Role | VerifierRejected | VerifierIndeterminate |
| --- | --- | --- |
| Baseline | Baseline Rejection，终止 Cell | Baseline Indeterminate，终止 Cell |
| Declaration | 声明不满足完整验证契约 | 声明结果未知 |
| Probe | 合法负向 observation，继续 D003 | 立即终止 Cell |

Baseline PASS 只提供已知通过锚点，不证明后续 failure 的 dependency 根因，也不进入 classifier identity。

## 7. 配置与 identity 迁移

若落地：

- 删除 `[tool.pf].test-failure-exit-codes`、配置默认值、adapter 参数和 outcome profile selector；未知旧 key 直接配置失败；
- `evaluation_policy_identity` 采用新的固定 verifier outcome policy identity；pytest version、execution mode、witness 和 progress 不进入；
- 不兼容的旧 policy/report/cache 结果不得与新结果合并或复用；项目尚未发布，不提供开发期迁移器。

## 8. Schema 与文档协调

D014 Schema 2 已经落地；采用本草案将改变 terminal Evaluation 与 Failure reason 的含义和 wire 值。若批准，必须在同一实现变更中：

1. 明确选择修订未发布的 Schema 2 还是提升 `schema_version`，不能静默改变现有 wire；
2. 同步 D001、D002、D005、D007、D008、D013、D014、JSON Schema、示例和生成器；
3. 更新 policy identity，并证明旧报告/cache fail closed；
4. 以 Plan 记录迁移、测试和 dogfood 证据。

D013 的 progress/failure detail 可以保留为 best-effort UI telemetry；其 negative-evidence authority、pytest qualification selector 和 witness-dependent outcome table 必须删除或重写。

## 9. 验收条件

若本草案获批，落地至少必须证明：

1. 任意正常非零 verifier exit 都是 Rejected，不依赖 command shape、pytest、witness、版本或 execution mode；
2. exit 0 是 Pass，诊断冲突不能覆盖；
3. timeout、signal、start error 与未知 terminal 是 Indeterminate；
4. output/witness 不完整只降低诊断；
5. `test-failure-exit-codes` 与 profile authority 完全删除；
6. pytest diagnostics 不进入任何 authority identity；
7. prepare 分类与 D003 搜索接口保持不变；
8. Schema/文档/policy 一次同步，不产生双行为；
9. `packaging==20.9` × Python 3.12 的正常 bootstrap nonzero exit 成为 direct Probe Rejection。

## 10. 非目标

- 不判断 assertion、import、plugin、CLI、网络或外部服务谁是根因；
- 不解析 traceback/stderr 决定 disposition；
- 不自动重试或引入 quorum/flaky 策略；
- 不扩大 prepare 阶段的负向证据资格；
- 不让诊断正文、test ID 或本地日志进入公共 identity；
- 不新增 test-runner classifier registry 或 pytest profile 配置。

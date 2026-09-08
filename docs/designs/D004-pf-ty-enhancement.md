# PF `ty` 静态事实与比较

- **状态：** 现行
- **策略版本：** `static-guidance-v1`
- **最后核对：** 2026-09-08
- **产品结果：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **已归并决策：** [D011](../archived/designs/D011-pf-runtime-backed-static-search.md)、
  [D038](../archived/designs/D038-pf-static-guidance-authority.md)

本文是 PF 中 `ty` 运行、诊断身份、规范静态投影、原始 TyCheck/Unavailable、Run 内缓存、
`S_hi` / `S_slice` 比较准入和 GuidancePolicy 的唯一契约。静态事实不决定 compatibility
disposition；边界由 D003/D005 的动态证据决定。生产路径不再包含 static witness、AST
classifier 或 isolated runtime-interface rejection。

D005 的 resolve/install 正常非零拒绝兜底不适用于本文件协议：ty 非零仍须解码其合法诊断，
工具失败只形成 `TyCheckUnavailable`，不终止 verifier。prepare rejection 没有 Proposal，
不产生本文件静态观察。完整 PASS 仍须成功准备并运行完整 verifier。

## 1. 目标

PF 识别依赖环境变化引入的静态状态变化，而不要求项目 type-clean，也不把 `ty` 的模型结论
直接等同于 runtime incompatibility。静态证据可以指导探测，但不能排除候选。

## 2. `S_hi` 与 `S_slice`

每个 Cell/run 有一个最高版本的显式 `StaticBaselineState`：

- `AVAILABLE`：真实最高版本 Proposal、完整 TyCheck、诊断多重集与 digest。
- `UNAVAILABLE`：真实 capture Proposal 与明确不可用原因；没有 diagnostic baseline digest，
  不以空诊断代替失败。

highest prepare 失败没有 Proposal，继续按现行 preparation contract 处理。可用时捕获 TyCheck
同时作为最高版本 Proposal 的空增量事实，不重复运行 ty。捕获失败不通过换一个候选重建全局
baseline；全局比较保持不可用，局部比较独立判定。

每个坐标需要静态 guidance 时，从直接 PASS 上端 `U` 冻结 `S_slice`。通常 `U=current`；若
predecessor 的真实 PASS 已提供更低上端，则用该上端的精确 Proposal。`S_slice` 保存该
Proposal 的完整 ty diagnostics，而不是相对 `S_hi` 的增量。没有合格完整观察时返回
NO_HINT(`anchor-unavailable`)。

```text
global_delta(P) = diagnostics(P) ⊖ diagnostics(S_hi)
local_delta(P, S_slice) = diagnostics(P) ⊖ diagnostics(S_slice)
```

两种比较都使用多重集 subtraction。COMPARED 必须通过显式 GLOBAL/SLICE 准入。合法
original→relaxed 与同一 baseline 的两个 relaxed 可以比较；非法 harness、不同 Python/surface/
冻结坐标或 context 返回 UNCOMPARED(`context-mismatch`)。

## 3. 原始 TyCheck 与 StaticComparison

原始 `TyCheck` 保存 immutable diagnostics、规范 diagnostic identities、实际 ty process
outcome、安全 process/log refs 与静态请求 scope。它不保存 baseline、delta、
STATIC_UNCHANGED/REGRESSION、StaticHint 或 compatibility disposition。失败由独立的
`TyCheckUnavailable` 保存。

`StaticComparison` 是对已有原始事实的解释，绑定比较 context 与 GuidancePolicy。更换
anchor 或 policy 不复用比较。同一 TyCheck 可对 GLOBAL 与 SLICE 得到不同合法 delta。

## 4. TyAdapter

```text
check(
    interpreter,
    package,
    python_minor,
    target,
    args,
    timeout_seconds,
    snapshot_root,
) -> TyCheck | ToolFailure
```

Adapter 固定拥有：

```text
ty check
--output-format gitlab
--python <interpreter>
--python-version <minor>
--python-platform <linux|darwin|win32|all>
--no-progress
--color never
```

用户 `ty-args`、显式 config override 不得改变 owned options；`--config-file` 仍禁止，冲突在
进程启动前失败。项目合法 `[tool.ty.terminal]` 展示默认配置允许存在，由 PF 固定 argv 覆盖。
解释器、minor、platform scope 继续由 PF 固定指定。非法 owned ty options 不因 guidance 降级而合法。

### 4.1 工具完成

```text
exit 0/1 + 完整合法 GitLab JSON -> TyCheck
timeout                         -> TIMEOUT / TyCheckUnavailable
其他退出、signal、启动失败、
截断或非法输出                  -> TOOL_FAILURE / TyCheckUnavailable
```

Exit code 只说明 TyCheck 是否可收集，不证明 diagnostic 数量或 compatibility。

### 4.2 GitLab JSON

stdout 必须是 JSON array。每条记录必须提供非空 `check_name`、`description`、`severity`、路径和正整数起始行；column 若存在也必须为正整数。任一记录残缺使整次检查失败，Adapter 不丢弃坏记录或从人类文本猜字段。

## 5. DiagnosticIdentity

`TyDiagnostic` 保存 identity、origin、规范 path、line/column、code、severity 和 message；后两项只用于报告。

Snapshot 内：

```text
identity = snapshot | posix-path | line | column? | code
```

External path 先 resolve，再依次规范到 `site-packages/`、`typeshed/` 或 `interpreter/` namespace：

```text
identity = external | normalized-path | code
```

无法得到稳定 external namespace 时整次检查为 TOOL_FAILURE。External identity 不保留 line/column。

`TyCheck.diagnostics` 按 identity，并以 severity/message 稳定打破相同 identity 的排序，保留重复项。

## 6. 静态请求与六组输入

`StaticRequestFactory` 从已复证的 ExecutionSubject 形成规范 `StaticSubject`。缓存 key 使用实际
静态对象的规范投影，不依赖完整动态 Proposal identity。六组输入与采集子身份变化必须改变投影
或拒绝未闭合外部输入；相同 sdist 的不同安装内容相互隔离；合法路径重定位与不同 Proposal 的
同投影可以命中同一原始事实。

## 7. StaticEvaluator 与 Run cache

`StaticEvaluator` 拥有 ty 收集、diagnostic identity、多重集 subtraction 和 fingerprint。
`RuntimeEvaluator` 独占 verifier 调用及动态结果组装。两者通过显式静态结果相连，静态结果
不再复用兼容性的 `IndeterminateEvaluation`。

原始 `TyCheck` / `TyCheckUnavailable` 由 Run 内独立 `TyCheckCache` 共享。缓存静态事实，
不缓存某次 guidance 解释。lookup 只读；collect 才原子加入或启动，只有 owner 占 ty permit。
等待者不占 permit。cached unavailable 不是 CacheMiss，也不产生兼容性 disposition。
prepare 失败、缺 baseline/anchor 与 context-mismatch 不进入原始 negative cache。

物化环境释放不清除原始事实。关闭环境后仍可 lookup/compare；重建并复证同投影时不重跑 ty。
不同 Proposal 不能共享动态 authority。capture 前 cache 已存在；跨 Run/Cell 与已关闭 refs
即使 key/payload 相同也被拒绝。

同 key 并发请求只启动一个 ty 操作并共享终态。owner 环境保留至进程收拢；取消/异常不泄漏
资源或等待者。同 ty key 不授权提前释放不同 Proposal 的环境。

## 8. RuntimeEvaluator 路由

```text
collect static facts as needed
  └── ty unavailable -> 继续 verifier；比较记 UNAVAILABLE / NO_HINT
        ↓
run configured verifier
  -> D005 terminal disposition
  -> PassEvaluation | VerifierRejectedEvaluation | IndeterminateEvaluation
```

ty regression、optional/fallback 路径和孤立接口缺失都不能提前拒绝。完整 PASS 只来自配置的
verifier 原命令阶段 `NormalExit(0)`。

## 9. check、smoke 与 search

命令如何组合 capture/full evaluation 只见 [D008 §3](D008-pf-verification-run.md#3-命令序列)；
搜索的静态/oracle 调度只见 D003。完整 PASS 的资格由 D005 拥有。
模块依赖与 public-seam 测试边界只见 [D002 §7、§11](D002-pf-implementation.md#7-verification-modules)。

check 保留真实 HarnessBaseline；capture ty 失败仍验证 lowest-direct。smoke 复用 capture 的
TyCheck（若有），只运行一次完整 test-command。

## 10. Schema、cache 与报告

公共证据至少保留原始 TyCheck/Unavailable、GLOBAL/SLICE 比较、producer/consumer 关联、
anchor PASS ref 与 GuidancePolicy。这些事实 intern 到文档级 table，scope 只保留 membership。
静态 refs 无兼容性权限。Failure 的 execution/selection 辅助关联不进入 Failure ID。

概念 cache key：

```text
TyCheckKey        = (StaticSubjectIdentity, TyObservationPolicy)
StaticCompareKey  = (subject fact, reference fact, context, GuidancePolicy)
TestEvaluationKey = (proposal_id, execution policy identity)
```

没有跨运行 Evaluation cache。`StaticEvaluator` 只消费 `EffectiveConfig.ty.args/timeout_seconds`，
并只在真正调用 `TyOperations.check` 时取得 invocation-wide ty permit。`RuntimeEvaluator` 消费
`test.command/cwd/timeout_seconds`，只有真正调用 configured verifier 时取得 test permit。
cache hit / negative hit 不生成新 ty stage 或复制耗时。

## 11. 策略 identity

本文件独占 GuidancePolicy / TyObservation 子身份；ExecutionPolicy 与 SearchDerivationPolicy 分开。
报告 identity 的三类字段、generation 与 apply 比较由 [D014 §1.1](D014-pf-report-schema.md#11-identity) 拥有。
ty args/timeout/tool version/内容 identity 只进入本节采集子对象，不进入 ExecutionPolicy 或 Attempt identity。

```text
policy             = static-guidance-v1
output_format      = gitlab
comparison         = multiset-subtraction
identity_rule      = snapshot-path-line-column-code+external-namespace-path-code
boundary_rule      = runtime-evidence-only
final_verification = direct-test-command-pass
```

`final_verification` 表示原命令阶段 `NormalExit(0)`。failed-set `NormalExit(0)` 不授权 PASS。
仅改变 guidance/ty heuristic 不改变同一执行对象已有动态证据的 authority identity。

## 12. 不变量与非目标

1. 同一 GLOBAL 比较必须引用同 scope 的 `S_hi` 状态，包括 UNAVAILABLE。
2. `V_hi` capture 是空增量 unchanged，不重跑 ty。
3. Regression 当且仅当 multiset increment 非空；它没有 disposition。
4. 静态失败最多退回无提示的 oracle 搜索。
5. 完整 PASS 必须由本 Proposal 的 PassEvaluation 证明。
6. 截断、坏 JSON 或未闭合输入不能形成 compatibility boundary。

非目标包括要求仓库 type-clean、解析 message、静态 floor、跨运行 baseline/evaluation cache、
以及用 CFG/reachability 分析恢复孤立接口缺失的拒绝权限。

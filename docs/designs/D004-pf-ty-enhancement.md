# PF `ty` 静态事实与比较

- **状态：** 现行
- **策略版本：** `static-guidance-v1`
- **最后核对：** 2026-09-11
- **产品结果：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **已归并决策：** [D011](../archived/designs/D011-pf-runtime-backed-static-search.md)、
  [D038](../archived/designs/D038-pf-static-guidance-authority.md)、
  [D039](../archived/designs/D039-pf-static-evaluation-module.md)、
  [D043](../archived/designs/D043-pf-static-subject-v2.md)

本文是 PF 中 `ty` 运行、诊断身份、规范静态投影、原始 TyCheck/Unavailable、Run 内缓存、
`S_hi` / `S_slice` 比较准入和 GuidancePolicy 的唯一契约。静态事实不决定 compatibility
disposition；边界由 D003/D005 的动态证据决定。未建模的静态异常同样不是命令失败：它们转为
typed unavailable / NO_HINT，最多发出 `RuntimeWarning`，不得让 smoke / check / search /
minimize 的 Cell 操作以未捕获异常中断。`OperationCancelled` 与 D008 的 Journal / ty-cache
持久化失败除外。生产路径不再包含 static witness、AST
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
Proposal 的完整 ty diagnostics，而不是相对 `S_hi` 的增量。上端环境已 `tested` 并 close
后，未命中 cache 则经 Run-owned `reprepare` 重建再 collect；失败返回
NO_HINT(`anchor-unavailable`)，不改写 `S_hi`。

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

## 6. 静态请求与 `static-subject-v2`

规范投影与采集请求是静态 module 的 implementation：`StaticEvaluator` 在 `collect_prepared` /
`capture_highest` 内装配 `StaticTyRequest`。`StaticRequestFactory` 不是 composition 或产品测试
入口。缓存 key 使用实际静态对象的规范投影，不依赖完整动态 Proposal identity。合法路径重定位
与不同 Proposal 的同投影可以命中同一原始事实。

`projection = static-subject-v2`。Identity 域 `pf:static-subject:v2`。`StaticSubject.identity`
是下列完整预像的 digest，不能再取子集：

```text
{
  "projection": "static-subject-v2",
  "source_snapshot_digest": "<64 hex>",
  "cell": { "package", "python_minor", "target", "extra_surface" },
  "interpreter": { "implementation", "abi" },
  "resolution_projection": [ ResolutionBinding, ... ]
}
```

`resolution_projection` 按 canonical name 排序。`interpreter.python_minor` 必须等于
`cell.python_minor`，不重复写入。不进入预像：补丁号、venv 路径、process environment、图
edges、request/plan/graph digest、attempt/proposal id、安装后 RECORD / 文件树。

`ResolutionBinding` 取自该 Proposal 实际使用的 plan（有 harness 则为 environment，否则
project）中的 `ResolutionPackage`：

```text
{
  "name": "<canonical>",
  "version": "<normalized> | null",
  "source": SourceIdentity,
  "artifact":
      { "kind": "selected", "content_hash": "sha256:<64 hex>" }
    | { "kind": "available-set", "digest": "<64 hex>" }
    | { "kind": "source-tree" }
}
```

普通 registry 包的 `selected_artifact` 为 `None`，只填 `available_artifacts`。`available-set`
digest 为 `sha256("pf:resolution-artifacts:v1\0" + canonical_identity_json(sorted unique
[(kind, filename, content_hash), ...]))`，triple 编成 JSON 数组。只哈希 `kind` /
`filename` / `content_hash`，不含 locator。同一 artifact 输入集合视为可互换。

| 情况 | `artifact` | 不能形成时 |
| --- | --- | --- |
| 已有 `selected_artifact.content_hash` | `selected` | hash 非法 |
| registry / url 且 `selected_artifact is None`，`available_artifacts` 非空且每项有 hash | `available-set` | 任一项缺 hash → `resolution-artifact-unbound` |
| path / workspace / 项目自身 | `source-tree`；隔离靠 snapshot digest + `source.locator` | locator 缺失或越出快照 |
| git | `source-tree`；`source.commit` 必填 | 无 commit → `resolution-artifact-unbound` |
| registry/url 且 available 与 selected 皆空 | — | `resolution-artifact-unbound` |

构造 subject 不再走文件树采集器。inspect 只核 interpreter identity 与诊断前缀；name/version
复用 [D012](D012-pf-harness-relaxation.md)。`revalidate` 只查 lease / `tested` / 前缀 / 根存在，
不整树再散列。内存 subject 与观测策略不携带 content manifest，不读 RECORD 编身份。

只承认快照内 ty 配置。宿主用户配置存在、`TY_CONFIG_FILE` 指向快照外、向父目录走查离开
snapshot 副本、或 argv / 快照内配置引用未冻结的快照外根 → `snapshot_ty_config.kind=unavailable`
（`undeclared-analysis-root`），不启动 ty。快照内 `TY_CONFIG_FILE` 合法，且为物化最高优先级。

## 7. StaticEvaluator 与 Run cache

`StaticEvaluator` 拥有 ty 收集、diagnostic identity、多重集 subtraction、fingerprint、
Preparation registry 与 Direct-PASS ledger。采集与比较的公开方法是 `collect_prepared`、
`capture_highest`、`compare_global`、`record_runtime` 与 `open_slice`。Search 另经
`record_phase_skip` / `record_oracle_selection` 写入 Run 内 search/skip/selection 账本；
该账本不是 Journal、report 或 diagnose 事实。`compare_global` 不向调用方索取
`GuidancePolicy`。比较准入、减法与 `locate_static_hint` 的唯一实现在静态 module；
`compare_global`、`StaticSlice` 与 `_admit_saved_static_audit` 委托同一内部 derive/hint。
`RuntimeEvaluator` 独占 verifier 调用及动态结果组装，不读 static consumer、不写 Run cache。
只有 Search 的 Highest / probe 路径在每次 `evaluate` 之后、prepared close 之前调用
`record_runtime`；Smoke/Check 不取得 TyCheck，也不登记 runtime PASS。

原始 `TyCheck` / `TyCheckUnavailable` 由 Run 内独立 `TyCheckCache` 共享。缓存静态事实，
不缓存某次 guidance 解释。lookup 只读；collect 才原子加入或启动，只有 owner 占 ty permit。
等待者不占 permit。cached unavailable 不是 CacheMiss，也不产生兼容性 disposition。
prepare 失败、缺 baseline/anchor 与 context-mismatch 不进入原始 negative cache。
`TyCheckKey = (StaticSubject.identity, TyObservationPolicy.cache_identity)`。
`TyCheckFact.observation_policy_identity` 绑定完整 generation identity。搜索主路径只用内存
cache，不从 sidecar 恢复 oracle。lookup 只读由连续 `collect_prepared` 的第二次结果证明。

Preparation registry 按 exact prepared 对象身份登记，允许同一 Proposal 多个 registered
prepared。Direct-PASS ledger 每 Proposal 一个 runtime owner；后续 collect 只绑定 consumer。
`TyCheckCache` 生产只由 `VerificationRunner` 构造。产品调用方只转交 cache。

物化环境释放不清除原始事实。关闭环境后仍可经已登记 handle 比较；重建并复证同投影时不重跑 ty。
不同 Proposal 不能共享动态 authority。capture 前 cache 已存在；跨 Run/Cell 与已关闭 refs
即使 key/payload 相同也被拒绝。

同 key 并发请求只启动一个 ty 操作并共享终态。owner 环境保留至进程收拢；取消不泄漏
资源或等待者。collect 的未建模 `Exception` 转为 typed `StaticContentUnavailable`
（`detail=invalid-layout`），等待者共享该结果，不停止 Run、不 cancel 其他 key。该次结果
不写入 completed cache，后续同 key 可再 collect。`StaticEvaluator` 的 `capture_highest` /
`compare_global` / `record_runtime` / `open_slice` / `record_phase_skip` /
`record_oracle_selection` 与 `locate_static_hint` 对未建模 `Exception` 同样转为 typed
unavailable、`None` 或 NO_HINT，并允许 `RuntimeWarning`；不得让命令以未捕获异常中断。
身份或账本冲突不再写入 Direct-PASS 或比较结果。`OperationCancelled` 仍停止本 Run 的收集。
同 ty key 不授权提前释放不同 Proposal 的环境。

## 8. RuntimeEvaluator 路由

```text
collect static facts as needed
  └── ty unavailable / 未建模静态异常 -> 继续 verifier；比较记 UNAVAILABLE / NO_HINT
        ↓
run configured verifier
  -> D005 terminal disposition
  -> PassEvaluation | VerifierRejectedEvaluation | IndeterminateEvaluation
```

ty regression、optional/fallback 路径和孤立接口缺失都不能提前拒绝。完整 PASS 只来自配置的
verifier 原命令阶段 `NormalExit(0)`。

## 9. check、smoke 与 search

命令如何组合 prepare/full evaluation 只见 [D008 §3](D008-pf-verification-run.md#3-命令序列)；
搜索的静态/oracle 调度只见 D003。完整 PASS 的资格由 D005 拥有。
模块依赖与 public-seam 测试边界只见 [D002 §7、§11](D002-pf-implementation.md#7-verification-modules)。

static capture、compare 与 Direct-PASS ledger 只服务 Search。Smoke/Check 不运行 ty、不
capture/compare `S_hi`、不登记 runtime PASS。Search 即使全部 harness 固定，仍先取得完整
highest satisfaction evidence 并执行 `S_hi` 与 full baseline verifier。

## 10. Schema、cache 与报告

公开报告不保存 TyCheck、比较或静态 intern 表。Run 内原始静态事实落在
`.pf/logs/<run-id>/ty-cache.json`（外层 `pf-ty-cache-v1`），由 `RunLogStore` 编解码；完整
`TyFactDocument` 含 §6 subject 与 §11 完整观测预像。静态 refs 无兼容性权限。Failure 的
execution/selection 辅助关联不进入 Failure ID。比较准入：两端 `source_snapshot_digest`、
`snapshot_ty_config`（含 unavailable union）、`host_config` 与 `cache_identity` 相等；不同
`TyCheckKey` 不得 COMPARED。GLOBAL / SLICE 仍看 `S_hi` 状态、anchor PASS、窗口与固定坐标。
`admit_harness_relation` 保留。

概念 cache key：

```text
TyCheckKey        = (StaticSubject.identity, TyObservationPolicy.cache_identity)
StaticCompareKey  = (subject fact, reference fact, context, GuidancePolicy)
TestEvaluationKey = (proposal_id, execution policy identity)
```

没有跨运行 Evaluation cache。`StaticEvaluator` 只消费 `EffectiveConfig.ty.args/timeout_seconds`，
并只在真正调用 `TyOperations.observe` 时取得 invocation-wide ty permit。`RuntimeEvaluator` 消费
`test.command/cwd/timeout_seconds`，只有真正调用 configured verifier 时取得 test permit。
cache hit / negative hit 不生成新 ty stage 或复制耗时。保存审计 admission 只接受完整
`StaticAuditDocument`，不能只凭 `TyFactDocument` 重放比较或 hint。

## 11. 策略 identity

本文件独占 GuidancePolicy / TyObservation 子身份；ExecutionPolicy 与 SearchDerivationPolicy 分开。
报告 identity 的三类字段、generation 与 apply 比较由 [D014 §1.1](D014-pf-report-schema.md#11-identity) 拥有。
ty args/timeout/`tool_version` 只进入本节采集子对象，不进入 ExecutionPolicy 或 Attempt identity。
D014 `guidance_policy_identity` 等于 `GuidancePolicy.identity`，禁止写成观测 digest。

观测策略升版：`rules = ty-observation-v2`，`analysis_scope = explicit-static-subject-v2`。
`args` 与 `timeout_seconds` 在观测策略顶层（后者 required-nullable）。删除嵌套 `TyConfig`、
`tool_content` 与 `executable`。`host_config = reject-undeclared-v1`。

```text
tool_version =
    { "kind": "distribution", "name": "ty", "version": "<PEP 440>" }
  | { "kind": "unavailable" }

snapshot_ty_config =
    { "kind": "materialized", "digest": "<64 hex>" }
  | { "kind": "unavailable", "reason": "configuration-..." | "undeclared-analysis-root" }
```

`tool_version.version` 来自宿主 `importlib.metadata.version("ty")`，不把 `ty --version` 原文
写入 digest。元数据 available 时规范解析 `ty --version`，必须相等，否则该次 collect
unavailable，不启动 ty。元数据 unavailable 时不 collect，报告身份用 `kind=unavailable`。
一次 Run 至多读一次元数据、至多一次 `--version`。

`materialized` 才允许启动 ty。`digest = sha256("pf:ty-config:v2\0" + effective_config_bytes)`，
只哈希实际 `--config-file` 字节。effective TOML 只含快照内合并结果，不含 PF-owned CLI
overrides。`unavailable` 仍进入完整观测预像。

三层 digest 不得合并。完整观测预像含 `timeout_seconds` 与 `snapshot_ty_config` union；cache
子集去掉 `timeout_seconds`：

```text
TyObservationPolicy.identity
  = sha256("pf:ty-observation-policy:v2\0" + complete observation preimage)
TyObservationPolicy.cache_identity
  = sha256("pf:ty-observation-cache:v2\0" + cache subset)
GuidancePolicy.identity
  = sha256("pf:guidance-policy:v1\0" + complete GuidancePolicy preimage)
```

`GuidancePolicy` 预像含完整观测预像、`observation_identity`、`comparison =
multiset-subtraction`、`fingerprint`、`anchors`、`bisection`、`authority = advisory-v1`、
`unavailable = no-hint-fallback-v1`。改变 subtraction / anchor / hint / fallback 必须改变
`guidance_policy_identity`。

命令开始时固定一份完整 `TyObservationPolicy` 与 `GuidancePolicy`（两处 union 均可为
unavailable）。其后任何静态失败不得让「无法构造 `guidance_policy_identity`」。

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
4. 静态失败（含未建模异常）最多退回无提示的 oracle 搜索；不得中断 smoke / check / search /
   minimize。允许 `RuntimeWarning`，不改变 disposition 或退出码。
5. 完整 PASS 必须由本 Proposal 的 PassEvaluation 证明。
6. 截断、坏 JSON 或未闭合输入不能形成 compatibility boundary。

非目标包括要求仓库 type-clean、解析 message、静态 floor、跨运行 baseline/evaluation cache、
用安装后文件树隔离同一 artifact 集合、把宿主 ty 配置读进 digest、以及用 CFG/reachability
分析恢复孤立接口缺失的拒绝权限。

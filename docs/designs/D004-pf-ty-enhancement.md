# PF `ty` static transition 与 runtime witness

- **状态：** 现行
- **策略版本：** `static-transition-v1`
- **最后核对：** 2026-08-23
- **产品结果：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **决策来源：** [D011](D011-pf-runtime-backed-static-search.md)

本文是 PF 中 `ty` 运行、诊断身份、最高版本静态基线、增量 transition、diagnostic 分类和 runtime witness 的唯一契约。静态事实不决定 compatibility disposition；边界由 D003/D005 的 runtime evidence 决定。

## 1. 目标

PF 识别依赖环境变化引入的静态状态变化，而不要求项目 type-clean，也不把 `ty` 的模型结论直接等同于 runtime incompatibility。

项目在最高版本环境已有的 diagnostic 被 `S_hi` 抵消。候选的新增 diagnostic 完整保存、分类并生成 fingerprint；它可以触发 witness 或 `test-command`，但自身不是 Rejection。

## 2. `V_hi`、`S_hi` 与 scope

每个 cell 在当前源码快照和声明的 highest Proposal `V_hi` 上运行一次 `ty`：

```text
S_hi : multiset[DiagnosticIdentity]
```

`S_hi` 只在相同 cell、source snapshot 和 Evaluation policy 内有效，不跨运行或 cell 复用。其 digest 是 Static/Test Evaluation key 的独立 context。

捕获 `S_hi` 的同一次 TyCheck 同时构成 `V_hi` 的空增量 `StaticUnchangedEvaluation`；不得重跑 `ty`。无法获得完整 TyCheck 时不能构造 baseline，结果为 Indeterminate。

## 3. Increment 与 static fingerprint

对同 scope Proposal `P`：

```text
increment(P) = diagnostics(P) ⊖ S_hi

increment(P) = ∅  -> STATIC_UNCHANGED
increment(P) != ∅ -> STATIC_REGRESSION
```

`⊖` 是 multiset subtraction；相同 identity 按重数逐一抵消。Baseline diagnostic 消失或减少不进入 increment。Message、severity、GitLab fingerprint 与 `ty` exit code 不参与比较。

两种状态都是 transition evidence，不是 `PASS` / `REJECTED`。每个状态都保存非空 fingerprint：

```text
sha256(
  "pf:ty-static-state:static-transition-v1\0"
  + canonical ordered incremental identity list
)
```

重复 identity 按实际重数进入列表。空增量有固定 digest，不能用空值替代。Schema 反算 fingerprint，并要求 regression 的 incremental 使用规范顺序且是 TyCheck diagnostics 的子多重集。

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

用户 `ty-args`、config override 和 `[tool.ty.terminal]` 不得改变 owned options。冲突在进程启动前失败。

### 4.1 工具完成

```text
exit 0/1 + 完整合法 GitLab JSON -> TyCheck
timeout                         -> TIMEOUT
其他退出、signal、启动失败、
截断或非法输出                  -> TOOL_FAILURE
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

无法得到稳定 external namespace 时整次检查为 TOOL_FAILURE。External identity 不保留 line/column，避免依赖内部行号漂移形成项目 regression。

`TyCheck.diagnostics` 按 identity，并以 severity/message 稳定打破相同 identity 的排序，保留重复项。Baseline digest 为：

```text
sha256(
  "pf:ty-diagnostic-baseline:static-transition-v1\0"
  + canonical identity list
)
```

## 6. Diagnostic 分类与 witness plan

每个 regression occurrence 必须有一条一一对应的 `DiagnosticClassification`。分类只使用 structured code、规范路径、源码 AST、Proposal vector/graph 与 active managed declaration，不使用 message、severity 或模糊字符串。

Strong eligibility 同时要求：

1. code 命中版本化 allowlist；
2. AST 唯一恢复 module/symbol/member；
3. import root 唯一映射到当前 active managed dependency；
4. 能生成无歧义 RuntimeWitnessPlan。

`strong-classifier-v1` allowlist 为 `unresolved-import` 与 `unresolved-attribute`。第一版 planner 支持：

- `import module` -> `import-module`；
- `from module import symbol` -> `import-symbol`；
- 直接 imported module attribute -> `has-member`。

相对导入、star import、复合/动态 owner、多义位置、非受管归因以及 allowlist 外 code 都降级为 general，并保存稳定 reason code。

`RuntimeWitnessPlan` 保存 covered diagnostic identities、managed dependency、operation、module、owner/member 和 planner version。它属于当前 Proposal，不跨 Proposal 复用。重复 diagnostic occurrence 仍分别分类并进入 fingerprint；执行列表只对完全相同的 plan 保序去重。

## 7. RuntimeWitnessAdapter

Adapter 在当前 prepared environment 中执行：

```text
<interpreter> -I -c <adapter-owned-harness> <canonical-plan-json>
```

不使用 shell，也没有用户 witness command。Harness 只输出一行 canonical JSON result；adapter 要求 stdout 精确等于该行加换行且 stderr 为空：

- `PRESENT`：目标 runtime 名称存在；
- `CONFIRMED_MISSING`：精确目标 module/symbol/member 缺失；
- `NOT_APPLICABLE`：执行完成但不能无歧义回答；
- `ToolFailure`：timeout、signal、启动失败、非零退出、截断或非法输出。

ModuleNotFoundError 必须指向目标 module 或其前缀；AttributeError 必须携带目标 owner 对象和 member name。`import-symbol` 使用 Python `fromlist` 导入语义后再核对属性，不能把可导入的 package submodule 误判为缺失。Import side-effect exception、任意 traceback 或无关缺失不能解释为 confirmed missing。

Witness result 必须完整正常 exit 0，并保留 plan 与 ProcessResult。Schema 要求 witness attempts 按本 Proposal 保序去重后的 classification plans 形成前缀；PASS/TestFail 不得保留 confirmed missing 或 tool failure，RuntimeInterfaceMissing 必须在首个 confirmed missing 停止，witness Indeterminate 必须在对应 ToolFailure 停止。

## 8. RuntimeEvaluator 路由

```text
run static transition
  ├── Ty failure -> Indeterminate
  ├── eligible strong plans
  │     ├── CONFIRMED_MISSING -> RuntimeInterfaceMissingEvaluation
  │     ├── PRESENT / NOT_APPLICABLE -> continue
  │     └── ToolFailure -> Indeterminate
  └── unchanged / general / no selected witness -> continue
        ↓
run configured test-command
  ├── pass -> PassEvaluation
  ├── configured failure exit -> TestFailEvaluation
  └── incomplete/tool result -> IndeterminateEvaluation
```

Witness 是内部负向优化，不产生正向 compatibility。未选择 witness 时直接运行 test-command。Pass/TestFail/RuntimeInterfaceMissing/Indeterminate 都保留本 Proposal 的 static evidence；运行过的 witness attempts 同样保留。

## 9. check、smoke 与 search

- `check`：highest 只 capture `S_hi`；lowest-direct 使用同一 baseline 运行 RuntimeEvaluator。Static regression 不短路。
- `smoke`：HighestVersionVerifier 复用 capture TyCheck 后只运行一次 test-command；不运行 witness、不发现候选。
- `search`：baseline 必须直接完整 PASS；每个 candidate 先建立自己的 transition，随后按 D003 的 region/runtime 路由。

完整 PASS 当且仅当当前 Proposal 自身 test-command pass。Static unchanged、witness PRESENT 和 region representative pass 都不能授权另一个 Proposal。

## 10. Schema、cache 与报告

公共证据至少保留 baseline Proposal/TyCheck/digest、每个 candidate 的 TyCheck/increment/fingerprint/classification、witness plan/result、test result，以及 Proposal/cell/snapshot/policy 一致性。

概念 cache key：

```text
TyCheckKey        = proposal_id
StaticStateKey    = (proposal_id, S_hi digest, static policy identity)
WitnessKey        = (proposal_id, witness plan identity)
TestEvaluationKey = (proposal_id, S_hi digest, full policy identity)
```

Proposal identity 已吸收 static/full policy；EvaluationCache 仍显式接收 baseline digest，并把 static 与 full evidence 分仓。Region 调度 cache 由 D003 拥有，不构造 Proposal-level Evaluation。没有跨运行 Evaluation cache。

旧 `STATIC_PASS` / `STATIC_FAIL`、`StaticPassEvaluation` / `StaticFailEvaluation` 和 `increment-v2` 证据不兼容，不能 merge/apply。

## 11. 策略 identity

Evaluation policy 包含实际 `ty` distribution、有效 ty/test 配置及：

```text
static_policy      = static-transition-v1
output_format      = gitlab
comparison         = multiset-subtraction
fingerprint        = ordered-incremental-identity-multiset
identity_rule      = snapshot-path-line-column-code+external-namespace-path-code
region_scope       = fixed-slice-contiguous
strong_classifier  = strong-classifier-v1
witness_planner    = witness-planner-v1
witness_harness    = witness-harness-v1
boundary_rule      = runtime-evidence-only
final_verification = direct-test-command-pass
```

改变 identity、multiset、allowlist、AST attribution、witness protocol、region scope 或 final rule 必须提升相应版本。

## 12. 不变量与非目标

1. 同一 Evaluation 必须引用同 scope frozen `S_hi`。
2. `V_hi` capture 是空增量 unchanged，不重跑 ty。
3. Regression 当且仅当 multiset increment 非空；它没有 disposition。
4. 每个 increment occurrence 有同序 classification 和显式 fingerprint。
5. 只有 confirmed-missing witness 或 test failure 可从 static 路径形成 runtime negative evidence。
6. 完整 PASS 必须含本 Proposal 的 TestPass。
7. 截断、坏 JSON、side-effect exception 或归因歧义不能形成 compatibility boundary。

非目标包括要求仓库 type-clean、为所有 unresolved 自动建 witness、解析 message、static-only floor、region runtime 等价证明和跨运行 baseline/evaluation cache。

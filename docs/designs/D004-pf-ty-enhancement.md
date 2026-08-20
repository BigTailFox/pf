# PF `ty` 增量静态证据

- **状态：** 实施中
- **策略版本：** `increment-v2`
- **最后核对：** 2026-08-20
- **产品结果：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)

本文是 PF 中 `ty` 运行、诊断身份、最高版本静态基线和增量比较的唯一契约。D001 只使用本文产生的静态结果；D002 只定义模块位置；D003 只把分类结果当作搜索证据。

## 1. 目标

PF 定位依赖降级相对当前最高允许版本引入的静态回归，而不是替项目执行“必须 type-clean”的 CI。

项目源码在最高版本环境中已有的诊断被接受为参考状态。候选只要没有增加诊断身份或重数，就满足静态兼容性；完整兼容仍必须通过 D001 的完整测试。

## 2. `V_hi` 与 `S_hi`

对每个 cell，PF 在当前源码快照和当前声明上按最高允许版本解析出精确 Proposal `V_hi`，运行一次 `ty`，并把规范化诊断多重集冻结为 `S_hi`。

```text
S_hi : multiset[DiagnosticIdentity]
```

`S_hi` 是该 cell、源码快照和 Evaluation 策略内的运行时证据，不是项目配置，也不跨运行复用。不同 cell 不共享基线。

`S_hi` 的 digest 是 D002 定义的 static/full Evaluation identity 的独立 context 部分；Proposal identity 本身不吸收 baseline。

捕获 `S_hi` 的同一次 `TyCheck` 同时构成 `V_hi` 的自比较静态通过证据；不得再运行一次 `ty` “确认” baseline。

无法获得完整合法 `TyCheck` 时，不能构造 `S_hi`，该 cell 返回对应非证据状态。

## 3. 增量语义

对任意同 scope Proposal `P`：

```text
increment(P) = diagnostics(P) ⊖ S_hi

increment(P) = ∅  -> STATIC_PASS
increment(P) != ∅ -> STATIC_FAIL
```

`⊖` 是 multiset subtraction。相同 identity 在 candidate 中出现 `n` 次，只能由 `S_hi` 中同 identity 的 `n` 次逐一抵消；多出的重数进入 increment。

以下情况通过：

- candidate 与 baseline 的 identity 重数相同；
- baseline 诊断在 candidate 中消失或重数减少；
- 两边均为空。

`S_hi` 为空时，candidate 的任意诊断都是增量。

消息文本、severity、GitLab fingerprint 与 `ty` 退出码不参与 multiset 比较。`STATIC_FAIL` 当且仅当结构化增量非空。

## 4. TyAdapter

外部 interface：

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

`TyAdapter` 固定并拥有：

```text
ty check
--output-format gitlab
--python <interpreter>
--python-version <minor>
--python-platform <linux|darwin|win32|all>
--no-progress
--color never
```

用户 `ty-args`、`--config-file`、`-c` / `--config` override，以及从包目录到 snapshot root 的 `[tool.ty.terminal]` 都不得改变 adapter-owned 选项或别名。冲突在启动进程前作为配置错误失败；不依赖“最后一个参数获胜”。

### 4.1 工具完成与退出码

```text
exit 0 或 1 + 未截断、合法 GitLab JSON -> TyCheck
timeout                              -> TIMEOUT
其他退出、启动失败、截断或非法输出   -> TOOL_ERROR
```

退出 `0` 的 JSON 可以含诊断，退出 `1` 的 JSON 也可以为空。退出码只说明本次工具执行是否可收集，不证明诊断数量或静态兼容性。

### 4.2 GitLab JSON

stdout 必须是 JSON array；每条记录必须是对象并提供：

- 非空 `check_name`；
- 非空 `description`；
- 非空 `severity`；
- `location.path`；
- `location.positions.begin.line`，或 GitLab 的 `location.lines.begin`；
- 若提供 column，则必须是正整数。

任一记录残缺会使整次检查成为 `TOOL_ERROR`。Adapter 不静默丢弃坏记录，也不从人类输出猜测字段。`fingerprint` 被忽略。

## 5. 诊断身份

规范记录：

```text
TyDiagnostic
  identity
  origin      snapshot | external
  path
  line
  column
  code        check_name
  severity    仅报告
  message     仅报告
```

相对路径先以 `ty` 的 package cwd 解析，再判断是否位于 snapshot root。

### 5.1 Snapshot 诊断

快照内路径转为相对 snapshot root 的 POSIX 路径：

```text
identity = snapshot | path | line | column? | code
```

line 必填；GitLab 没有 column 时不虚构空 column。

### 5.2 External 诊断

路径先以 package cwd 补全相对路径，再用 `Path.resolve(strict=False)` 消解绝对形式和可解析的 symlink，最后按以下优先级分类：

1. 位于 snapshot root：按 §5.1 处理；
2. 路径中含 `site-packages` 或 `dist-packages`：统一为 `site-packages/<relative-path>`；
3. 其余路径中含 `typeshed`：统一为 `typeshed/<relative-path>`；
4. 位于所选解释器环境 root 的其他文件：统一为 `interpreter/<environment-relative-path>`；
5. 其余快照外路径无法获得稳定 namespace，整次 `TyCheck` 为 `TOOL_ERROR`。

`site-packages` 的判定先于 `typeshed`，因此 `site-packages/typeshed/foo.pyi` 与 `typeshed/foo.pyi` 不会 collision。运行时虚拟环境绝对前缀和 `dist-packages` / `site-packages` 差异不进入 identity。

规范路径进入：

```text
identity = external | path | code
```

External identity 不保留 line/column。这是有意的保守策略：依赖内部文件位置漂移不会自动成为项目静态回归。

### 5.3 规范顺序与 digest

`TyCheck.diagnostics` 按 identity（并以 severity/message 稳定打破相同 identity 的排序）保存，保留重复项。

基线 digest 对按顺序保存的 identity 列表计算：

```text
sha256("pf:ty-diagnostic-baseline:increment-v2\0" + canonical identity list)
```

消息和 severity 不进入 digest。

## 6. StaticEvaluator

接口由 D002 定义。职责分为：

### 6.1 capture

1. 调用 `TyAdapter.check(V_hi)`；
2. 构造 `StaticBaseline(proposal, ty, digest)`；
3. 使用完全相同的 Proposal、TyCheck 和 digest 构造空 increment 的 `StaticPassEvaluation`；
4. 返回 `StaticBaselineCapture`。

### 6.2 evaluate

运行 `ty` 前先验证 baseline 与 Proposal 的 cell、snapshot digest 和 policy identity 相同。不同 scope 直接违反调用契约，不能产生证据。

成功 `TyCheck` 通过 multiset subtraction 产生：

- `StaticPassEvaluation`：空 `incremental`；
- `StaticFailEvaluation`：非空 `incremental`。

工具失败产生 `IndeterminateEvaluation`，保留 `ToolFailure` 和 Proposal。

`FullEvaluator` 只接受 static pass 进入完整测试。`CoordinateSearch` 看见分类与 Proposal identity，不读取诊断内容。

## 7. check、smoke 与 search 的基线生命周期

### 7.1 check

`CompatibilityChecker`：

```text
prepare highest V_hi
  -> StaticEvaluator.capture
  -> close highest environment
prepare lowest-direct V_check
  -> FullEvaluator(V_check, same S_hi)
```

最高版本环境不运行测试。`check` 的兼容性对象是 `V_check`；`V_hi` 只建立参考静态状态。

### 7.2 search

`HighestVersionVerifier` 在 baseline 环境 capture 一次，复用捕获所得 static pass 运行 baseline 完整测试；`SearchCoordinator` 消费该结果，并把同一 `StaticBaseline` 注入 D003 的所有 static/full probe。

项目既有诊断本身不会使 baseline static fail；baseline 是否继续、两阶段 probe 顺序和终态由 D001/D003 定义。

### 7.3 smoke

`smoke` 直接消费 `HighestVersionVerifier` 的完整结果，不发现候选。capture 返回的同一个 `TyCheck` 同时作为最高版本 Proposal 的 static pass，随后进入完整测试；不得为了展示 warning 再运行 `ty`。

合法 `TyCheck` 的诊断数量可以为任意非负整数。每条诊断由 Presenter 按 D001 的短格式显示为 warning；这些展示规则不进入 `S_hi`、digest 或 Evaluation 分类。测试通过即为 smoke pass，测试正常失败即为 smoke compatibility failure；不能产生合法 `TyCheck` 或完整测试结果时保持非证据状态。

## 8. Schema 与报告

公共证据保留：

- `V_hi` Proposal；
- 完整 `TyCheck` 机械结果和规范诊断；
- `S_hi` digest；
- 每个 static evaluation 的 baseline digest 与 increment；
- static/full probe 与 cell baseline/final 之间的 Proposal、cell、snapshot、policy 一致性。

Schema validator 双向检查分类和结构：static pass 必须空 increment，static fail 必须非空且是当前 TyCheck 的子多重集，baseline capture 必须复用同一个 TyCheck。缺失或跨 scope 的证据不能读成有效 Schema 1 报告。

`check`、`smoke`、`search` 和 `explain` 复用同一个 `TyDiagnostic` 短摘要格式。它们分别展示与命令有关的 baseline/current 诊断或 candidate 新增诊断，不能把 baseline 既有错误描述成本次不兼容原因。

完整 `TyCheck` 继续保留 severity 和 message，因此 identity 命中但 message 改变仍可在报告中离线分析；这不改变 `STATIC_PASS` / `STATIC_FAIL`。

## 9. 策略 identity

Evaluation 策略 identity 包含实际 `ty` distribution 版本、影响 Evaluation 的有效配置，以及：

```text
policy        = increment-v2
output_format = gitlab
comparison    = multiset-subtraction
identity_rule = snapshot-path-line-column-code+external-namespace-path-code
```

`jobs` 不进入 identity。改变诊断 identity、输出格式或比较代数时必须提升策略版本，使新旧报告不能 merge/apply。

## 10. 所有权

| 规则 | 唯一所有者 |
| --- | --- |
| ty argv、配置冲突、GitLab JSON、路径与 identity 规范化 | `TyAdapter` |
| `S_hi`、scope 校验、digest 与 multiset subtraction | `StaticEvaluator` + Evaluation Schema |
| check 的 highest/lowest-direct 生命周期 | `CompatibilityChecker` |
| search 中一次 capture 和 baseline 注入 | `SearchCoordinator` |
| probe 顺序与边界 | D003 / `CoordinateSearch` |
| 报告证据链一致性 | Report Schema |

## 11. 不变量

1. 每个 static/full Evaluation 先拥有同 scope 的冻结 `S_hi`，否则为非证据或调用错误。
2. 成功 capture 后，`V_hi` 自比较必为 `STATIC_PASS` 且不重跑 `ty`。
3. `STATIC_FAIL` 当且仅当 multiset increment 非空。
4. baseline 诊断消失或减少不推进失败边界。
5. check 与 search 共享同一 StaticEvaluator 语义。
6. 完整 `PASS` 仍要求完整测试。
7. 截断、坏 JSON、缺字段或 owned-option 冲突不能生成兼容性边界。
8. `CoordinateSearch` 不解释诊断或 `ty` 退出码。
9. 一个 cell 的 baseline 不能复用于另一个 cell、snapshot 或 policy。

## 12. 非目标

- 要求仓库本身 type-clean；
- 规则白名单或消息文本匹配；
- 把增量诊断归因到某个依赖；
- static-only floor；
- 跨运行 `S_hi` cache；
- 把 severity、message 或 fingerprint 纳入 identity；
- 把 external line/column、message 或 severity 纳入 identity；
- 在 `V_hi` 上运行测试来代替 `V_check` 测试。

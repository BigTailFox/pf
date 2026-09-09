# R011 — PF 现行架构评审

- **状态：** 开放
- **日期：** 2026-09-08
- **性质：** 非规范性架构评审；不定义命令、算法、Schema 或 module interface，不授权实施
- **对照：** 当前 HEAD；模块 owner 为 [D002](../designs/D002-pf-implementation.md)
- **目标 Design：** [D039](../archived/designs/D039-pf-static-evaluation-module.md)（已吸收进 D002/D004/D008，D003 §5 已改写；Plan [P046](../archived/plans/P046-pf-static-evaluation-module.md) 已完成）
- **前序：** [R005](../archived/reviews/R005-pf-module-depth-review.md) 已完成 SourcePlan、WorkspaceInventory、Verification Run request 与评价 Protocol 合并并归档；CLI 剩余项由 [R006](R006-pf-cli-system-review.md) 拥有；搜索性能由 [R008](R008-pf-search-performance-review.md) 2026-09-08 重评拥有；实现偏移由 [R010](R010-pf-engineering-document-audit.md) 拥有
- **契约所有者：** [D001](../designs/D001-pf.md)–[D008](../designs/D008-pf-verification-run.md)、
  [D012](../designs/D012-pf-harness-relaxation.md)–[D014](../designs/D014-pf-report-schema.md)、
  [D037](../designs/D037-pf-candidate-search-policy.md)

本文回答：R005 轨完成且 D038 删除 witness/region 之后，现行 PF 还有哪些 **module / interface / seam** 值得加深。
它不重复 R006 的 CLI 展示、R008 的墙钟候选、R010 的契约偏移，也不把建议写成现行规范。

评审沿用 `module`、`interface`、`implementation`、`seam`、`adapter`、`depth`、`leverage` 与删除测试。
Depth 是调用方通过较小 interface 获得多少行为，不是文件行数。只有规则所有权交叉、interface
泄漏内部知识、假想 seam 或测试必须穿透公开表面时，才构成架构候选。

## 1. 结论

没有新的 P0 正确性、安全或证据授权缺口。R005 加深的 SourcePlan、WorkspaceInventory、command-discriminated
Verification Run 与「三个产品编排器共用真实 Environment/Static/Runtime，不复制假想 Protocol」仍然成立。
叶子层已经深：`EnvironmentFactory.prepare`、`CoordinateSearch.minimize`、`ConfiguredVerifier.run`、
`SnapshotBuilder`、`ProjectLoader`、`ValidatedReport` / `ReportStore`。`cli.py` 仍是唯一生产
composition root，并已按命令装配 capability graph。

剩余杠杆集中在 **D038 之后膨胀的静态评价簇**：公开 seam 写成
`StaticEvaluator.lookup/collect/compare`，实现却是十余个平行文件加 schema 层向上执行 domain 算法。
这降低 locality，并把测试面压到内部文件。D002 §3 的包布局仍写着不存在的 `static.py`，会把文件清单
误读成所有权边界。

| 优先级 | 事项 | 结论 |
| --- | --- | --- |
| P1 | 静态评价收成一个深模块 | 2026-09-10 已吸收进 D002/D004；公开表面为五方法 |
| P1 | Schema 记录与准入/比较/hint 算法分家 | 2026-09-10 已吸收；schemas 不再重放 compare/hint/harness |
| P2 | 修订 D002 模块地图 | 2026-09-10 已吸收进 D002 §3 |
| P3 | 收回 `FailurePolicy` 可选注入 | 2026-09-10 已吸收进 D002 §7 |
| 已跟踪 | result-card、非 TTY 活动 | [R006](R006-pf-cli-system-review.md) |
| 已跟踪 | hints、single-flight、materialize、xdist、当前 HEAD 基线 | [R008](R008-pf-search-performance-review.md) |
| 已跟踪 | NO_PASS 文案、Journal Role、资格矩阵 | [R010](R010-pf-engineering-document-audit.md) |
| 构想 | 树搜索、registry 分析 CLI、成功解析日志门槛、乐观单调搜索 | C001–C004；不因本评审进入 Design |

不要按行数拆 `report.py` / `schemas/evaluation.py`，不要把 Check/Smoke/Search 收成评价 facade，
不要为性能恢复 region 或 witness。

## 2. 仍然成立的分层

D002 的依赖方向仍然是正确目标：

```text
cli / workflow / terminal
             │
             v
project / snapshot / candidates / environment / evaluation
baseline / failure / search / verification / report / editor
             │
             v
          adapters + schemas
```

真实 seam 仍只有外部进程、uv/ty/test、Evaluator、cell task 与 activity consumer。
`CheckCellOperations` / `SmokeCellOperations` / `CellSearchOperations` 有生产编排器与测试 adapter，
是实际 seam。CLI 七个 workflow Protocol 同样有生产 context 与 `NeverCalledWorkflow` 等测试 adapter，
不因评价层已删除假想 Protocol 而删除。`UvOperations` 仍是 `EnvironmentFactory` 内部的真实 seam。

R005 已否定、本评审继续否定的方向：把三个产品编排器合成一个 Cell 评价 module；用 facade 或
service locator 隐藏 D002 写明的 Environment/Static/Runtime 依赖图。

## 3. P1：静态评价是一圈浅文件

### 3.1 问题

D002 §3 把静态写成 `static.py / static_cache.py`、`static_request.py`、`static_guidance.py`，
公开 interface 为 `StaticEvaluator.lookup/collect/compare`（D002 §7）。`static.py` 不存在。
包根另有 `static_admission.py`、`static_configuration.py`、
`static_paths.py`、`static_subject.py`，以及 `ty_fact.py`、`ty_options.py`、
`adapters/static_inputs.py`。v1 文件树叶子（`static_external` / `static_ignores` /
`static_process` / `static_relocation`）已随 D043 切齐删除。`schemas/` 下另有 `static.py`、`static_baseline.py`、
`static_comparison.py`、`static_consumer.py`、`static_preparation.py`、`static_scope.py`、
`static_search.py`、`ty_fact.py`。

`StaticEvaluator.lookup` / `compare` 把参数转给 `TyCheckCache`；`collect` 才有 permit、
`static_use` 与 revalidate。`SearchCoordinator`、`VerificationRunner`、`CompatibilityChecker`、
`HighestVersionVerifier`、`cli.py`、`runlog.py` 与大量 tests 直接进口 cache ref、
`StaticRequestFactory`、`StaticPoint` 以及路径/配置物化。

删除测试：删掉 `StaticEvaluator` 后复杂度不会回到少数调用方，它已经散在 cache、request 装配和
schema 比较函数里。因此当前 Evaluator 是浅 facade，不是深模块。D002 §11 要求静态事实从
Evaluator outcome 观察；`tests/test_static_paths.py`、
`test_static_subject.py`、
`test_static_configuration.py` 等越过该表面。内部 seam 可以存在，
但不应继续规定产品形状。

`TyCheckCache` 由 Verification Run 拥有、经 `search(..., run_cache=)` 传入是有意的 Run 作用域，
不要收成 service locator。问题是 cache 类型、request factory 与叶子文件同时成为调用方必须学习的
interface。

### 3.2 方向

把静态采集、Run cache、准入、比较与纯 guidance 收成 **一个** 深模块。对外大约是：

```text
StaticEvaluator.lookup / collect / compare
StaticGuidanceEvaluator.open_static_slice   # CoordinateSearch 已依赖的纯 hint seam
TyCheckCache                                # Run 拥有，不从 cli/check 直接装配内部类型
```

路径解析、ignore、relocation、configuration materialize 留在 implementation。产品代码与新测试走
同一公开表面。`SearchCoordinator` 用模块提供的 `StaticSlice` adapter，不再进口 cache ref 与
hint 算法类型去手写 slice。

停止条件：新 interface 与现有 Evaluator + Cache + RequestFactory 等宽；或只把文件挪进子目录而不
减少调用方知识。文件行数不是启动理由。

归属 D002（模块边界）与 D004（静态事实/比较）。须先接受 [D039](../archived/designs/D039-pf-static-evaluation-module.md)。D038 已归档，不把「静态无
compatibility disposition」改回去。

## 4. P1：Schema 层向上执行 domain 算法

### 4.1 问题

D002 §2 把 adapters 与 schemas 放在底层；§4 规定 Schema validator 只验证纯结构/identity，
I/O 与多记录事务由 owner module 验证。现状相反：

| Schema 文件 | 向上依赖 | 行为 |
| --- | --- | --- |
| `schemas/static_comparison.py` | `pf.static_admission.admit_static_consumer_context` | 比较准入 |
| `schemas/static_search.py` | `pf.static_guidance.locate_static_hint` | validator 重放静态二分 |
| `schemas/static_preparation.py` | `pf.harness`、`pf.resolution` 的 digest/graph | validator 重算 harness 与 identity |
| `schemas/static_scope.py`、`static_baseline.py` | `pf.resolution` | 记录依赖 resolution evidence 类型与 digest |
| `schemas/policy.py` | `pf.ty_options.validate_ty_args` | policy 记录调用 argv 校验 |

这是跨层循环，即使用延迟导入能跑。`schemas.static_search` → `static_guidance` →
`schemas.static_comparison` 已形成 schema ↔ domain 往返。比较减法与 hint 定位是 D004 的算法，
不应住在 FrozenSchema 模块里。

`StaticPreparationEvidence` 在 validator 中调用 `original_harness` / `relax_harness` 属于 D012
的变换，不是「纯结构不变量」。identity digest 函数若必须给 records 复算，应收到 schemas 可依赖的
纯函数层，而不是让 schema 进口 `EnvironmentFactory` 的兄弟 module。

### 4.2 方向

FrozenSchema 只保存不可变记录与结构/identity 闭合。`admit_*`、diagnostic subtraction、
`locate_static_hint` 回到 D004 owner（可与 §3 同一静态模块的内部文件）。harness 重算留在
prepare/D012。`ty_options.validate_ty_args` 由 ConfigLoader / 静态 request 装配调用，policy
record 只保存已资格化的 args 投影。

Schema 可以引用其他 schema 与 `canonical_identity_json`。不能执行搜索、准入或 harness 变换。

与 §3 同一 Design 交付：先分家再收模块，避免 schemas 仍调用「换了路径的同一函数」。目标见
[D039](../archived/designs/D039-pf-static-evaluation-module.md)。

## 5. P2：D002 包布局不是现行 module 地图

D002 §3 仍列出 `static.py`，未列出 `cancellation.py`、`ty_fact.py`、`ty_options.py`、
静态簇其余文件，也未列出 `schemas.policy` / `schemas.journal` / `schemas.static_*`。
§4 的 schema 表只有 config/project/evaluation/report/apply。

风险不是「文件多」，而是阅读 owner 的人会把每个 `.py` 当成独立 module。D002 §1 写明：文件大小
不决定拆分；只有独立规则所有权或真实 adapter 分化才形成新 module。

若只更正不存在的文件名、补上 `cancellation.py`，属于 D002 owner 文档修正，不必另立产品 Design。
若把静态簇写成「一个 module 的内部文件」，必须与 §3 的 interface 设计一起接受，不能只改目录。
两项均已写入 [D039](../archived/designs/D039-pf-static-evaluation-module.md)，与加深同一吸收。

## 6. P3：`FailurePolicy` 仍是单实现假想 seam

`CompatibilityChecker`、`HighestVersionVerifier`、`SearchCoordinator` 与 `_ProposalRunner`
接受 `failures: FailurePolicy | None = None`，缺省再 `FailurePolicy()`。
`VerificationRunner` 已改为内部构造。生产只有一个实现。R005 已将其标为次要 interface 税，
评价 Protocol 合并后仍未收回。

方向：编排器内部构造；测试从公开 outcome 观察分类。不能仅因「一个生产实现」删除
`ConfiguredVerifier`、`UvOperations` 或 workflow Protocol 这类双 adapter 真实 seam。

不单独开 Design。已合入 [D039](../archived/designs/D039-pf-static-evaluation-module.md)。

## 7. 本评审不重复跟踪的开放项

| 事项 | 唯一跟踪者 |
| --- | --- |
| apply/no-floor/配置错误的 result-card；非 TTY 搜索活动 | [R006 §5.1–5.2](R006-pf-cli-system-review.md) |
| 跨运行 hints、per-key 锁、copytree、xdist failed-set、当前 HEAD 分阶段基线 | [R008](R008-pf-search-performance-review.md) 2026-09-08 重评 |
| `NO_PASS`「完整评估」文案；Journal Role 错配；ty×Python / 多宿主资格 | [R010](R010-pf-engineering-document-audit.md) §2、§4 |
| 树搜索默认化 | [C001](../concepts/C001-pf-multi-resolution-coordinate-search.md)；E005 未证明 |
| 独立 registry 分析 CLI | [C002](../concepts/C002-pf-registry-analysis-cli.md) |
| 成功解析是否仍要求日志完整性 | [C003](../concepts/C003-pf-resolution-output-completeness.md) |
| 乐观单调与反例 refinement | [C004](../concepts/C004-pf-evidence-respecting-optimistic-monotone-search.md) |

PEP 508 规范化在 `report.py` 与 `authorization.py` 各有一份：R010 已标为低优先级，且授权必须独立
重求值。本评审不把它升级为合并两个 owner 的理由。

## 8. 明确不要做

- 仅因 `report.py`、`schemas/evaluation.py`、`adapters/uv.py` 或 `TerminalPresenter` 行数多而拆文件。
  ReportStore / ValidatedReport / PackageReportBuilder 已是深模块；没有新的规则所有权就拆，只增加
  import 税。
- 把 Check / Smoke / Search 收成一个评价 facade。共享的是 environment/static/runtime，不是命令
  outcome。
- 删除 CLI 七个 workflow Protocol，或新增 public `terminal.render(union)`。
- 为性能恢复 region、witness，或跨运行 Evaluation cache、共享已跑 verifier 的 venv。
- 建立 DI 框架、event bus、通用 `utils.py` 或 hint/cache manager。
- 另开一份跨 Review 总优先级清单。本页只拥有 §3–6；其余继续留在 R006/R008/R010 与 Concept。

## 9. 建议顺序与治理

1. [D039](../archived/designs/D039-pf-static-evaluation-module.md) / [P046](../archived/plans/P046-pf-static-evaluation-module.md) 已于 2026-09-10 完成吸收；现行行为以 D002/D003 §5/D004/D008 为准。
2. R006/R008/R010 的开放项不因本评审启动。R008 须先有当前 HEAD 分阶段基线再决定 hints。

任何实质 module/schema 边界变更都必须先接受 Design，再写覆盖每条验收的 Plan。本文不授权实施。

## 10. 核对范围

静态核对了：D002 布局与 §1–4、§7、§11；`src/pf/` 现行文件与 `schemas/` import；
`evaluation.py` 的 Evaluator 转调；`search.py` / `check.py` / `baseline.py` / `cli.py` 对
cache 与 request 的进口；`schemas/static_comparison.py`、`static_search.py`、
`static_preparation.py`、`policy.py` 的向上依赖；R005/R006/R008/R010 与 C001–C004 的开放边界。

未跑全量 pytest、dogfood 或性能基线。分层结论来自公开 interface 与 import 图，不是墙钟。
D038 之后的静态正确性以 D003/D004 为准；本评审不评估 hint 是否减少 verifier 次数。

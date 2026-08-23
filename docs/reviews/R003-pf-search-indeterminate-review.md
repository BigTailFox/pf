# R003 — PF search Indeterminate 提前终止评审

- **状态：** 快照
- **日期：** 2026-08-23
- **性质：** 非规范性评审；不定义命令、算法、Schema 或 module interface
- **对照：** 当前 `main` / `86bcf58`（`fix: make smoke and check self-verifying`）；运行证据来自同一 checkout 的 source snapshot `f4b96d18a0c8504fe36fa58d944c01a7d308cc57a79994e9b5d50c011ec9051d`
- **契约所有者：** [D003](../designs/D003-pf-search-algorithm.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、[D011](../designs/D011-pf-runtime-backed-static-search.md)、[D012](../designs/D012-pf-harness-relaxation.md)
- **前序评审：** [R002](R002-pf-v1-architecture-review.md)
- **整改：** 尚未建立后续 Design / Plan；本文建议不得直接视为现行行为

本文记录一次由 PF 自搜索触发的证据分类与搜索终止评审。评审回答三个问题：为什么 `packaging==19.2` 使三个 cell 返回 `incomplete`，该点是否应允许搜索继续，以及现行实现中还有哪些 harness 或验证故障会形成 `Indeterminate` 并提前停止搜索。

本文区分 dependency harness 与 test command。D012 中的 dependency harness 是为执行验证而安装的测试依赖及其 environment resolution；本次 `packaging==19.2` 已经成功形成并安装 environment plan，实际失败发生在 `stage=test` 的 pytest collection。它不是 `HARNESS_CONFLICT`，但暴露了相邻的验证 harness 分类缺口。

评审使用 `module`、`interface`、`implementation`、`seam` 和 `adapter` 描述结构。建议以现有 `TestOperations.run(...) -> TestOutcome` 和 `EnvironmentFactory.prepare(...)` interface 为外部 seam，优先加深其 implementation，不把 pytest、uv 或 traceback 细节扩散到 CoordinateSearch。

## 1. 结论

当前行为存在一个 P1 搜索完整性缺口：PF 能在人工可验证的日志中看出 `packaging==19.2` 无法满足当前源码使用的运行时接口，但 `TestAdapter` 只按进程退出码分类。pytest collection 返回 2 后，该候选被压缩成 `TOOL_FAILURE / ProbeIndeterminate`；CoordinateSearch 又把任意 `ProbeIndeterminate` 无条件升级为 cell 终止。因此，一个可归因到当前 project graph 的候选失败使三个 cell 都在已知 PASS 与已知 Rejection 之间提前停止。

这个结果由两个独立问题共同造成：

1. **证据分类过浅：** 通用 exit-code adapter 无法区分 project-attributable collection failure 与 pytest 配置、plugin、internal 或 harness failure；
2. **终止范围过宽：** CoordinateSearch 没有 point-local Unknown 与 cell-global failure 的区别，任何 Indeterminate 都立即停止整个 cell。

真正的 dependency harness 还有一个 P2 韧性限制：D012 v1 只安装一个 final environment plan；若其中 harness-only 节点发生 build/source failure，不会在冻结 resolver universe 中尝试其他合法 harness configuration。该失败保持 Indeterminate 是正确的，但立即放弃所有替代配置会降低可完成率。

不应以 `test-failure-exit-codes = [1, 2]` 作为默认整改。它能让本次 pytest exit 2 变成 `TEST_FAILURE / REJECTED`，但也会把 pytest usage/config/plugin/internal error 一并误判为 project incompatibility。

## 2. 复现与运行证据

报告 `package-floor.json` 的 `report_generation_id` 为 `2ef24572bddabd960e2266014bbecab2440ac5ed169c678bd687dac9a46c819d`，结果为：

```text
status:  incomplete
reasons: INDETERMINATE, UNREPRESENTABLE_PROJECTION
cells:   0/3 covered
```

三个 cell 使用同一个受管向量：

```text
cyclopts==4.10.2
packaging==19.2
pydantic==2.13.4
rich==15.0.0
tomli==2.4.1
tomlkit==0.15.1
ty==0.0.74
```

终止事实如下：

| Cell | Failure ID | 进程事实 | collection failure |
| --- | --- | --- | --- |
| Python 3.10 | `failure-3aac9dabde1a7099` | `TOOL_FAILURE @ test`, exit 2 | `packaging.utils` 没有 `InvalidSdistFilename` |
| Python 3.11 | `failure-955e70b47f5f18df` | `TOOL_FAILURE @ test`, exit 2 | `packaging.utils` 没有 `InvalidSdistFilename` |
| Python 3.12 | `failure-24e2f7fe600949cb` | `TOOL_FAILURE @ test`, exit 2 | `packaging.tags` 导入已从 Python 3.12 移除的 `distutils` |

三个 cell 的 packaging slice 都已有相同的直接观察：

| 版本 | 观察 | Cause |
| --- | --- | --- |
| `14.0` | `REJECTED` | `RUNTIME_INTERFACE_MISSING` |
| `26.3` | `PASS` | — |
| `19.2` | `INDETERMINATE` | `TOOL_FAILURE` |

pytest 在三个环境中都只收集到 `591 items / 3 errors`，随后以 `Interrupted: 3 errors during collection` 结束。失败发生在测试执行前，但 traceback 已分别指向当前 PF 源码导入的受管 project dependency `packaging`。从人工诊断看，这是 candidate-attributable project failure；从现行 PF 机械事实看，它仍只是一个未配置为 test failure 的退出码 2。

`UNREPRESENTABLE_PROJECTION` 不是第二个独立根因。三个 cell 都因 Indeterminate 没有产生可授权的 floor，七个声明的 projection evidence 因而都是 `floors=[]`、`representable=false`。

## 3. P1：test-command classification 丢失候选归因

### 3.1 现状

`src/pf/adapters/test_command.py:16-47` 明确不解释 test output：

```text
exit 0                         -> TestPass
configured failure exit code  -> TestFail
timeout                        -> TIMEOUT / ToolFailure
其他退出                       -> TOOL_FAILURE / ToolFailure
```

当前默认 `test-failure-exit-codes` 只有 1。RuntimeEvaluator 把最后一类投影成 `IndeterminateEvaluation`；FailurePolicy 只允许 `TEST_FAILURE @ test` 形成 test Rejection。该 interface 对任意命令保持通用，但它隐藏的行为很少：调用方仍必须用一组裸退出码表达某个测试工具的全部终态语义。

pytest 的 exit 2 至少混合以下不同事实：

- project 或其 dependency 在 collection/import 时失败；
- 测试配置或命令用法错误；
- pytest plugin 或 dependency harness 无法加载；
- pytest internal error 或外部中断。

单一退出码无法安全决定这些情况是 Rejection 还是 Indeterminate。

### 3.2 影响

- 受管 project dependency 的确定接口缺失无法形成 `TEST_FAILURE`，CoordinateSearch 失去继续定界所需的 Rejection；
- 用户只能在“错误提前终止”和“把所有 exit 2 错误拒绝”之间选择；
- stderr 包含人工可读证据，但公共 FailureRecord 不保留输出正文，且 D005 不允许用不稳定字符串直接授权 Rejection；
- 若在 workflow 或 CoordinateSearch 中解析 pytest 文案，工具知识会越过 adapter seam 并破坏 locality。

### 3.3 建议

在现有 `TestOperations.run(...) -> TestOutcome` seam 后增加结构化 test outcome implementation。通用 exit-code adapter 继续服务任意命令；pytest 使用显式选择的 pytest adapter，由 PF 注入不依赖被测 package 的轻量 plugin，并写出版本化、规范化、可校验的结果。

建议的机械分类至少区分：

```text
PASS
ASSERTION_FAILURE
PROJECT_COLLECTION_FAILURE
HARNESS_COLLECTION_FAILURE
TOOL_FAILURE
```

`PROJECT_COLLECTION_FAILURE` 只有在结构化 traceback/module evidence 能把失败节点映射到当前 snapshot 或 project resolution graph `G(P)` 时才形成 `TEST_FAILURE / REJECTED`。pytest config、plugin、internal、timeout、signal、启动失败和协议损坏保持 Indeterminate。原始 traceback 继续只进入 Process Log；FailureRecord 只保存稳定、脱敏的 kind、owner distribution/module 和协议 identity。

pytest 是 true external dependency，通用 command 与 pytest structured command 是两个真实 adapter；建立这个 seam 有实际变化来源。不要建立只有一个 implementation 的通用 classifier registry，也不要让调用方学习 pytest hook 或 JSON 文件细节。

### 3.4 完成标准

- 本次三个 `packaging==19.2` collection failure 都形成直接 `ProbeRejection`，CoordinateSearch 继续探测；
- pytest 配置错误、plugin import error、internal error 和人工中断仍形成 `ProbeIndeterminate`；
- 分类不依赖 stderr substring、Rich 文案或 Process Log 可用性；
- structured outcome 的 malformed、truncated、version mismatch 和多结果输入 fail closed；
- generic command adapter 的现行 exit-code 行为保持兼容；
- 测试通过 `TestOperations` interface 断言结果，不直接锁定 plugin 内部函数。

## 4. P1：CoordinateSearch 把点级 Unknown 当成 cell 级终止

### 4.1 现状

`src/pf/coordinate_search.py:412-423` 的 `_check_terminal` 对任意 `ProbeIndeterminate` 立即调用 `_stop("INDETERMINATE")`。算法不记录 Unknown point，也不区分 failure 只影响一个 Proposal、一个 Slice，还是整个 cell 的执行基础设施。

这条规则保护了证据边界：Indeterminate 不会被误当作 PASS 或 Rejection。但“不能用于边界”并不推出“不能继续探测其他点”。

### 4.2 影响

- 一个 candidate-local collection、build、witness 或 tool failure 会丢弃该 cell 其余仍可执行的 probe；
- Unknown 以下可能已经有直接 Rejection，Unknown 以上仍可能形成新的直接 Rejection/PASS 邻接边界，但算法不会寻找；
- 多坐标搜索中，一个 dependency 的局部故障阻止其他 dependency 收集证据，降低诊断与后续重试价值；
- `ProbeIndeterminate` 同时承担点状态与 cell 终止信号，interface 暴露的语义过宽。

### 4.3 建议

为 Indeterminate 增加显式影响范围，而不是从 cause 或 stderr 推断：

```text
ATTEMPT_LOCAL  -> 记录 UNKNOWN，继续当前 Slice 的可用候选和其他坐标
CELL_GLOBAL    -> 立即停止 cell
```

Unknown 永远不能充当 PASS、Rejection、predecessor 或静态 region representative。只有直接运行证据建立的 Rejection/PASS 邻接才能关闭 floor 边界。

例如：

```text
19.2 UNKNOWN, 20.0 REJECTED, 21.0 PASS
```

可以形成 `20.0 -> 21.0` 的直接边界；19.2 位于已拒绝点以下，不再阻塞 floor。反之：

```text
19.2 UNKNOWN, 20.0 PASS
```

仍不能宣称 20.0 是精确 floor；若没有其他直接 predecessor 证据，最终结果必须保持 incomplete。

影响范围应由产生 failure 的 module 给出稳定结构化事实，CoordinateSearch 只消费，不导入 pytest、uv、source 或 process 细节。Candidate discovery 等 Attempt 建立前的 cell-scoped failure 继续在 SearchCoordinator 层终止，不进入纯向量算法。

### 4.4 完成标准

- 表驱动测试覆盖 `UNKNOWN -> REJECTED -> PASS`、`UNKNOWN -> PASS`、多个 Unknown、多个坐标和 Unknown 位于已关闭边界外的情况；
- ATTEMPT_LOCAL Unknown 不立即停止，CELL_GLOBAL failure 保持立即停止；
- Unknown 不能进入 floor、boundary、static region 或 complete report authority；
- 若 Unknown 最终仍切断必要 predecessor/PASS 边界，cell 返回 Indeterminate 并引用阻塞该边界的 failure；
- 同一 Proposal 的冲突结果仍按 nondeterministic/invariant 规则保守终止；
- D003、D005、D008 与 D011 的现行“Indeterminate 立即终止”必须先由后续 Design 明确取代，不能只改 implementation。

## 5. P2：dependency harness 只尝试一个 environment plan

### 5.1 现状

D012 已实现 project resolution `G(P)` 与 relaxed harness environment resolution `E(P)` 的两阶段计划，并保证 `G(P) ⊆exact E(P)`。认证过的完整 requirement contradiction 形成 `HARNESS_CONFLICT / REJECTED`，CoordinateSearch 可以继续。

但 D012 §15 明确不在 selected harness build failure 后枚举其他 configuration。当前 `EnvironmentFactory.prepare(...)` 只取得一个 final environment plan 并安装一次。若 harness-only artifact、source 或 build 失败，PF 不能证明所有合法 environment plan 都失败，因此正确地返回 Indeterminate；同时也不会尝试可能成功的替代 harness selection。

### 5.2 建议

若产品接受扩大 D012 范围，在 `EnvironmentFactory.prepare(...)` interface 内建立私有 Harness Feasibility module，隐藏冻结 universe 中的替代配置枚举、受控重试和 exhaustiveness bookkeeping：

```text
prepare(P)
  -> PreparedEnvironment
  -> CertifiedHarnessConflict
  -> Indeterminate
```

- 任一合法 environment plan 成功安装并通过 graph verification：继续验证 P；
- 完整 relaxed requirement universe 被认证为逻辑无解：`HARNESS_CONFLICT / REJECTED`；
- source、artifact、build 或 tool failure 未能穷尽合法配置：保持 Indeterminate；
- 网络、凭据、metadata 和可变 source 故障不得因重试耗尽而升级为 Rejection。

该能力应保留在 EnvironmentFactory 的深 implementation 中。CoordinateSearch 只观察最终 `ProbePass | ProbeRejection | ProbeIndeterminate`，不把 harness version 或 artifact 变成 project floor 坐标。

### 5.3 完成标准

- selected harness-only build failure 后可以尝试同一冻结 universe 中的另一个合法 plan；
- 找到可实例化 plan 时，同一 project vector 继续 static/witness/test；
- 只有完整 certified incompatibility 形成 `HARNESS_CONFLICT`；
- source/build/tool failure 无论重试次数都不能伪装成逻辑 UNSAT；
- project graph、baseline ceiling、release cutoff、source policy 和 artifact/hash evidence 在所有替代计划中保持冻结；
- 外部 `EnvironmentFactory.prepare(...)` 与 CoordinateSearch interface 不因枚举细节变宽。

## 6. 当前会形成 Indeterminate 的出口

现行 `FailurePolicy` 只有以下四个 cause/stage 组合可以在进程事实完整时形成 Rejection：

```text
RESOLUTION_CONFLICT      @ resolve-project
HARNESS_CONFLICT         @ resolve-environment
RUNTIME_INTERFACE_MISSING @ witness
TEST_FAILURE             @ test
```

其余 cause/stage，或上述组合缺少完整进程事实，都会形成 Indeterminate。对 search 的实际出口如下。

### 6.1 dependency harness / environment preparation

| 阶段 | 现行 Indeterminate 场景 | 当前搜索含义 |
| --- | --- | --- |
| `resolve-environment` | index/DNS/auth/transport/metadata/hash source failure | 未取得 harness satisfiability 事实，停止 cell |
| `resolve-environment` | unsupported uv profile、timeout、signal/start error、输出截断、未知 diagnostic | resolver 结论不可靠，停止 cell |
| `resolve-environment` | package/version/wheel/Requires-Python unavailable 或 cache/offline miss | 候选可用性与逻辑 UNSAT 有歧义，停止 cell |
| `resolve-environment` | sdist/build backend failure | 未证明其他 environment plan 不可行，停止 cell |
| resolution plan | pylock 缺失、非法、无法解析，或缺少 harness selection | `resolution-plan-invalid`，停止 cell |
| `create-environment` | uv/Python 创建失败或 timeout | 环境基础设施不可用，停止 cell |
| `inspect-interpreter` | 解释器启动/JSON 失败，或实现/minor 与 cell 不符 | `TOOL_FAILURE` / `ENVIRONMENT_FAILURE`，停止 cell |
| `install-environment` | artifact 下载、hash、格式、metadata、build 或 uv failure | plan 未可靠实例化，停止 cell |
| graph verification | 安装图与 environment plan 不同、`G(P)` 漂移、受管 vector 漂移 | `INTERNAL_INVARIANT`，停止 cell |

认证过的完整 direct/transitive version contradiction 是本表的反例：它产生 `HARNESS_CONFLICT / REJECTED`，搜索继续。

### 6.2 不属于 dependency harness、但同样会停止 search

| 阶段 | 现行 Indeterminate 场景 | 当前搜索含义 |
| --- | --- | --- |
| candidate discovery | registry/source/PEP Simple 查询失败 | Attempt 尚未建立，cell-scoped `SOURCE_FAILURE` |
| `resolve-project` | source/build/candidate ambiguity、未知或不完整 resolver outcome | project satisfiability 未知 |
| `ty` | timeout、异常退出、输出截断或非法 JSON | static evaluation 未完成 |
| `witness` | timeout、非零/异常退出、stderr 非空、非法或非规范 JSON | runtime negative evidence 未确认 |
| `test` | timeout、signal/start error、未配置退出码；完整性不足还会阻止 Rejection | 动态契约未可靠完成；本次 packaging 属于这里 |
| evaluation cache | 同一 Proposal 出现冲突的 static/full 结果 | nondeterministic/invariant failure |
| proposal identity | prepare 后实际 vector 与请求不同 | `INTERNAL_INVARIANT` |

`resolution_run_context` 无法建立受支持的 uv protocol 时，当前 EnvironmentFactory 抛出 `ConfigurationError`，不是 package-floor 中的 ProbeIndeterminate；它属于运行级配置失败，不应与本表的 candidate-local Unknown 混合。

## 7. 建议顺序

| 优先级 | 项 | 原因 | 完成证据 |
| --- | --- | --- | --- |
| P1 | pytest structured outcome | 直接修复当前 candidate-attributable collection failure 的错误分类 | packaging 19.2 × Python 3.10–3.12 成为 ProbeRejection；pytest harness 反例保持 Indeterminate |
| P1 | Unknown-aware CoordinateSearch | 点级未知不再无条件终止整个 cell，同时不放宽 floor authority | Slice/多坐标 unknown barrier 表驱动测试与报告 Schema 反例 |
| P2 | Harness Feasibility | selected harness plan 失败后仍可尝试合法替代配置 | 冻结 universe 中 alternate plan success / exhaustive UNSAT / unresolved failures 三分测试 |
| P2 | 受控重试 | 降低瞬时 source/timeout 故障造成的无效 incomplete | 同一 resolution context/cutoff 下有界重试；失败不升级为 Rejection |

前两项改变 D003/D005/D008/D011 的现行分类和终止契约，应先建立一个规范性 Design，再建立有序 Plan。Harness Feasibility 扩大 D012 §15 的非目标，也必须由 Design 明确新的 universe、穷尽性和证据所有权。

## 8. 临时绕过与非建议方案

项目可以显式设置：

```toml
[tool.pf]
test-failure-exit-codes = [1, 2]
```

这会让当前 pytest collection exit 2 进入 `TEST_FAILURE`，但也会把 usage/config/plugin/internal error 当成 project Rejection。它只适合用户明确接受该测试命令退出码契约时作为项目级临时策略，不应成为 PF 默认值，也不构成本 Review 的整改完成。

同样不建议：

- 把任意 Indeterminate 当作 Rejection 或 PASS；
- 在 CoordinateSearch 中解析 pytest/uv stderr；
- 用重试次数耗尽代替 resolver universe 的逻辑穷尽；
- 把 harness version 加入 project floor vector；
- 因单个 unknown 存在就输出可 apply 的“保守 floor”，除非后续 Design 明确定义不同于当前精确 floor 的新产品结果。

## 9. 验证范围

本次完成：

- 读取当前 `package-floor.json` 的三个 CellResult、terminal FailureRecord、packaging observations 与 projection evidence；
- 对三个 terminal failure 运行 `pf diagnose pf --failure <id>`，均定位到 run `20260823T072959.345687Z-1307776-67f2851a`；
- 读取三个 Process Log，确认 Python 3.10/3.11 的 `InvalidSdistFilename` import failure 与 Python 3.12 的 `distutils` import failure；
- 对照 TestAdapter、RuntimeEvaluator、FailurePolicy、CoordinateSearch、EnvironmentFactory、uv diagnostic classifier 与 D003/D005/D008/D011/D012；
- `git diff --check`：通过。

本次没有重新运行完整 `pf search`、全量 pytest、ty 或 build。R003 是对已生成报告与现行实现的评审快照，不把文档检查或历史测试结果写成新的行为验证。

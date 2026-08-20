# PF 实现结构

- **状态：** 实施中
- **最后核对：** 2026-08-20
- **产品与命令：** [D001](D001-pf.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **静态证据：** [D004](D004-pf-ty-enhancement.md)

本文是 PF v1 模块接口、依赖方向、Schema 所有权、adapter 与持久化结构的唯一所有者。用户可见值与退出码不在这里重复定义；坐标 probe 规则由 D003 定义；`ty` 诊断比较由 D004 定义。

## 1. 设计原则

PF 按深模块组织：小接口隐藏完整行为，调用方和测试都通过同一 interface 使用模块。

- 每条业务规则只有一个所有者。候选过滤、静态增量、坐标搜索、报告授权和编辑授权不能散落在调用方。
- 跨模块领域数据使用冻结、严格的 Pydantic Schema；运行时资源句柄可以使用内部 Python 对象。
- 只为真实变化建立 seam。现行 seam 是外部进程、uv/ty/test 操作、Evaluator、cell 调度任务和进度消费。
- 文件系统通过 `pathlib` 与真实临时目录测试，不建立通用 filesystem/repository 层。
- 只出现一次的转换留在所属方法；只有多个真实调用方或独立不变量才提取。
- 不创建 `utils.py`、`helpers.py`、`common.py`、manager 或通用 repository 层。
- 类用于状态、资源生命周期或不变量；一次性纯转换不包装成单方法类。
- composition root 显式装配依赖，不引入依赖注入框架。

## 2. 技术选择

| 领域 | 实现 | 所有权约束 |
| --- | --- | --- |
| CLI | Cyclopts 4.x | `cli.py` 注册命令；说明文本来自 command docstring |
| 终端 | Rich | 只有 `terminal.py` 创建业务 Rich renderable |
| Schema | Pydantic 2.x | `schemas/` 保存跨模块不可变记录与 discriminator union |
| 需求与版本 | `packaging` | PEP 440/508 和名称规范化的唯一库实现 |
| TOML | `tomli` + `tomlkit` | `tomli` 读取；`tomlkit` 保留 apply 格式与注释 |
| 外部工具 | `uv`、`ty` 可执行文件 | 只经 adapter 调用，不依赖私有 Python 接口 |

这些都是 `pyproject.toml` 的直接依赖。项目没有第二套 msgspec Schema 或 JSON 路径。

## 3. 包布局

现行布局及职责：

```text
src/pf/
├── __init__.py          # 版本；无 CLI 副作用
├── __main__.py          # python -m pf -> cli.main
├── cli.py               # Cyclopts、CliContext、唯一 composition root
├── terminal.py          # TerminalPresenter 与 ActivityEvent 消费
├── errors.py            # PfError 分类及 D001 退出码映射
├── config.py            # [tool.pf] 层合并与 CLI duration/jobs 解析
├── policy.py            # Evaluation 策略 identity
├── project.py           # ProjectLoader、声明/cell/source/test group 规划
├── snapshot.py          # SnapshotBuilder 与 SourceSnapshot 生命周期
├── runlog.py            # RunLogStore 与脱敏的有界进程详细日志
├── candidates.py        # CandidateBuilder
├── environment.py       # EnvironmentFactory 与 PreparedEnvironment
├── evaluation.py        # StaticEvaluator、FullEvaluator、EvaluationCache
├── baseline.py          # HighestVersionVerifier 的最高版本完整验证生命周期
├── search.py            # CoordinateSearch 与 SearchCoordinator
├── scheduling.py        # Scheduler、deadline 与规范结果顺序
├── report.py            # PackageReportBuilder 与 ReportStore
├── editor.py            # ProjectEditor 与恢复日志
├── workflow.py          # 七个命令的应用工作流
├── schemas/
│   ├── base.py
│   ├── config.py
│   ├── project.py
│   ├── evaluation.py
│   └── report.py
└── adapters/
    ├── process.py
    ├── uv.py
    ├── ty.py
    └── test_command.py
```

文件名表示规则所有权，不要求“一类一文件”。拆分条件是出现两个可以独立描述、测试和演进的深模块，而不是文件变长。

## 4. 依赖方向

```text
cli.py / workflow.py / terminal.py
              │
              v
 project / snapshot / candidates / environment / evaluation
 search / scheduling / report / editor / policy
              │
              v
       adapters + schemas
```

约束：

- `cli.py` 是唯一生产 composition root；命令 handler 不读取 TOML、不拼外部 argv、不执行搜索规则。
- `workflow.py` 编排一个用户命令的完整用例；深模块自身仍强制安全不变量。
- `search.py` 不导入 Cyclopts、Rich、`subprocess` 或 TOML 库。
- 业务模块不得导入 Rich；adapter 不得导入 CLI/terminal，也不直接打印。
- `schemas` 只依赖标准库、Pydantic 和其他 Schema；不读取文件或启动进程。
- 跨层循环依赖是所有权错误，不能用延迟导入掩盖。

## 5. Schema

### 5.1 基类与边界

所有公共记录继承 `FrozenSchema`：

```python
ConfigDict(
    extra="forbid",
    frozen=True,
    validate_default=True,
    allow_inf_nan=False,
)
```

Schema validator 只验证本记录的结构和纯不变量。需要 I/O 或多个记录共同参与的规则由所属模块验证。公共 JSON 不保存 `Path`、`Version`、异常、PID、临时目录或资源句柄；路径是相对源码 root 的 POSIX 字符串，版本是规范化字符串。

状态差异使用 discriminator union，不使用相互独立的布尔值或一组隐含状态的可空字段。内部代码用类型收窄保留状态证据，不把结果折叠成 `bool`。

### 5.2 Schema 分组

`schemas/config.py`：

- `EffectiveConfig`：合并并规范化后的完整包配置；
- `CheckRequest`、`SmokeRequest`、`SearchRequest`、`ReportRequest`、`ApplyRequest`、`MergeRequest`：CLI 与工作流之间的严格请求。

原始 TOML patch 只在 `ConfigLoader` 内部使用普通 mapping，不跨模块传播。默认值、层级权限、互斥条件和字段规范化均由 `ConfigLoader` 一次完成。

`schemas/project.py`：

- `RequirementDeclaration`、`Cell`、`SourceIdentity` / `SourcePlan`；
- `SourceSnapshotIdentity` / `SnapshotEntry`；
- `ResolvedNode`、`VersionPin`、`InterpreterIdentity`、`Proposal`；
- `AvailableCandidate` / `AvailableArtifact`、`Candidate` / `CandidateSnapshot`；
- `PackagePlan`、`ProjectPlan`。

稳定 declaration ID 覆盖 `pyproject.toml` 路径、base/optional 位置、extra、规范化名称、requested extras、marker 与来源 identity，不包含数组下标、行号或会被 apply 改写的 specifier。

Proposal ID 覆盖源码快照、cell、受管向量、固定声明、实际解析图、解释器和 Evaluation 策略 identity。策略 identity 已包含影响 `ty` 执行、诊断判定和完整测试的有效配置，因此 cache key 不再重复拼接这些 policy 字段。

`schemas/evaluation.py`：

- `ProcessSpec` / `ProcessResult` 与外部工具结果；
- D004 定义的 `TyDiagnostic`、`TyCheck`、`StaticBaseline` 与 static/full Evaluation union；
- `CheckResult`、`SmokeResult`、`CacheConflict`；
- `ProgressEvent`、`StatusEvent`、`CellMatrixEvent`、`ProcessEvent`。

详细日志引用不进入 `ProcessResult` 或其他公共 Schema。`RunLogStore` 只在当前进程内按 `ProcessResult` 对象 identity 维护引用；报告序列化/重新读取后没有本地引用。日志丢失不能改变已经记录的 Evaluation 证据。

`schemas/report.py`：

- probe、坐标边界与 `CoordinateOutcome`；
- `CellSuccess` / `CellFailure`；
- 投影证据与 complete/incomplete report result；
- `PackageFloorReportV1`、`ProjectEditResult`。

`PackageFloorReportV1` validator 复证目标 cell 精确覆盖、成功证据、投影授权以及 D004 的静态基线一致性。公共 JSON 即使被手工编辑也不能绕过这些不变量。

## 6. CLI 与应用工作流

### 6.1 入口

`cli.py` 提供：

```text
create_app(context: CliContext) -> cyclopts.App
build_context() -> CliContext
main() -> None
```

`pf` 入口为 `pf.cli:main`；`pf/__main__.py` 调用同一个 `main()`。`pf/__init__.py` 不导入 CLI。

每个 command docstring 使用 Cyclopts 可解析的 NumPy-style `Parameters` 章节。typing annotation 只表达类型；文案不放在 `Annotated[..., Parameter(help=...)]` 或重复常量中。

handler 只构造 request、调用一个 workflow、交给 `TerminalPresenter` 渲染并返回 D001 退出码。领域异常继承 `PfError`，只在 `main()` 最外层映射；内部模块不调用 `sys.exit()`。

### 6.2 工作流所有权

| 工作流 | 编排职责 | 写入 |
| --- | --- | --- |
| `CheckCommandWorkflow` | load → snapshot → 选择宿主 cell → Scheduler → CompatibilityChecker | `.pf/logs`、临时环境 |
| `SmokeCommandWorkflow` | load → snapshot → 选择宿主 cell → Scheduler → HighestVersionVerifier | `.pf/logs`、临时环境 |
| `SearchCommandWorkflow` | load → snapshot → 宿主 cell 搜索 → report build/update/write | `.pf/logs`、`package-floor.json` |
| `ExplainCommandWorkflow` | load → 定位并读取报告 | 无 |
| `MergeCommandWorkflow` | read → merge → write | 显式 output |
| `ApplyCommandWorkflow` | load → 读取并核对报告 → `ProjectEditor.apply_many` | `pyproject.toml`、`.pf` 日志 |

`minimize` 只在 handler 中顺序复用 search 和 apply workflow；安全规则仍由报告 Schema、ReportStore 和 ProjectEditor 强制。

## 7. 项目规划与快照

### 7.1 ProjectLoader

外部 interface：

```text
load(root: Path, package_selection: str | None) -> ProjectPlan
```

它隐藏并唯一拥有：

- workspace 与可安装包发现、root packages/exclude 选择；
- 三层配置加载；
- PEP 508 声明解析、固定/可搜索/受管分类；
- marker 投影、extra surface 与有序 cell；
- uv index/source 解释和测试 dependency group 展开。

调用方只消费 `ProjectPlan`，不得再次读取 `[tool.pf]` / `[tool.uv.sources]` 或重新解释 marker。

### 7.2 SnapshotBuilder

外部 interface：

```text
build(root: Path) -> SourceSnapshot
```

Git 项目通过唯一 `ProcessRunner` 执行 `git ls-files --cached --others --exclude-standard`。非 Git 项目按 `.gitignore` 与硬排除遍历。模块负责路径安全、模式/符号链接、内容摘要、不可变 staging、独立 materialize 和清理。

源快照范围由 D001 定义；`EnvironmentFactory` 不复制遍历或摘要规则。

## 8. 候选、环境与 Evaluation

### 8.1 CandidateBuilder

```text
build(package, cell, baseline) -> tuple[CandidateSnapshot, ...]
```

候选策略值由 D001 定义。`CandidateBuilder` 是 source、artifact、prerelease/yanked、specifier、search-space、granularity、baseline cap、排序、series representative 与 digest 的唯一实现所有者。它不决定 probe 顺序，也不把来源失败解释为版本失败。

### 8.2 EnvironmentFactory

```text
prepare(package, cell, snapshot, resolution, managed_vector=None)
  -> PreparedEnvironment | ToolFailure
```

它创建独立源码副本和虚拟环境，物化受管向量，安装 editable 包，记录实际解释器与解析图，合并测试支撑依赖并复查目标图。`PreparedEnvironment` 是带 `close()` 生命周期的内部资源对象，不进入公共报告。

不同 Proposal 不通过原地升级/降级依赖复用。运行完整测试后环境标记为已测试并视为可能污染；只复用下载/build cache 和同一 Proposal、同一 Evaluation context 已有的静态证据。

### 8.3 Evaluator

`StaticEvaluator` 的 interfaces：

```text
capture(prepared, package) -> StaticBaselineCapture | IndeterminateEvaluation
evaluate(prepared, package, baseline) -> StaticEvaluation
```

`FullEvaluator`：

```text
evaluate(prepared, package, baseline, static_result=None) -> Evaluation
```

静态比较的完整规则由 D004 定义。`FullEvaluator` 只在 `StaticPassEvaluation` 后运行完整 `TestAdapter`，并保留原始 static/test 机械证据。

### 8.4 最高版本完整验证

`HighestVersionVerifier` interface：

```text
verify(package, cell, snapshot)
  -> HighestVersionVerification | ToolFailure | IndeterminateEvaluation
```

它唯一拥有 `prepare(highest) -> static capture -> full evaluate with captured static -> close` 生命周期，返回冻结的 `StaticBaseline` 与完整 Evaluation。`smoke` 和 `SearchCoordinator` 是两个真实调用方；两者不得复制最高版本验证序列或再次运行 `ty`。`CompatibilityChecker` 有意只 capture 最高版本静态基线、再测试 `lowest-direct`，因此不调用该 interface。

本设计把单 cell 当前使用的 Evaluation context 定义为：

```text
EvaluationContext = (cell, frozen StaticBaseline S_hi, effective policies)
```

它是 `SearchCoordinator` / `_ProposalRunner` 绑定 evaluator 的内部概念，不增加公共 Pydantic Schema 或透传类。Evaluator 仍通过现有 `baseline` 参数接收 context 中的冻结静态基线；static、fast path 和 dynamic 必须共享同一个 context。

`TyCheck` 是 Proposal 的原始工具事实；`StaticEvaluation` 是 `TyCheck` 相对 `S_hi` 得到的兼容性证据。现行代码不单独缓存 `TyCheck`。若把三层身份写成概念 key：

```text
TyCheckKey          = proposal_id
StaticEvaluationKey = (proposal_id, s_hi_digest)
FullEvaluationKey   = (proposal_id, s_hi_digest)
```

Full 所需 test policy 和 static 所需 diagnostic policy 已在 `proposal_id` 的策略 identity 中。`EvaluationCache` 的 get/record interface 因此显式接收 `baseline_digest`，在一次 search 内按二元 key 分离 static/full 结果、复用相同证据并检测同 context 冲突。公共报告不是 cache。

## 9. 搜索与调度

`CoordinateSearch` interface：

```text
minimize(start, candidates, evaluator, hints=()) -> CoordinateOutcome
```

它只读取冻结版本坐标和分类后的 `ProbeEvidence`；具体算法由 D003 唯一定义。`small_threshold` 的现行默认值是 `8`。

`SearchCoordinator` interface：

```text
search(package, cell, snapshot) -> CellResult
```

它拥有单 cell 搜索状态机：消费 `HighestVersionVerifier` 已建立的 baseline 与 D004 静态基线、冻结候选、调用 static/dynamic CoordinateSearch、组装强类型 CellResult。它不复制最高版本验证，不负责跨 cell 并发或总时限。

`Scheduler.run(tasks, jobs, max_duration_seconds, events)` 只调度独立 cell callable，限制并发、停止启动超过 deadline 的任务、为未启动 cell 产生 `TIMEOUT`、消费进度事件并按 package/target/python/extra 规范排序。单 cell 内 probe 始终串行。

## 10. 外部 adapter

### 10.1 ProcessRunner

```text
run(ProcessSpec) -> ProcessResult
```

生产 adapter 是 `SubprocessRunner`。它唯一拥有 `shell=False` argv、cwd、最小环境增量、独立进程组、输出上限、timeout 终止、signal/启动错误机械记录和通用 secret/URL 脱敏。它不知道 uv、ty 或测试退出码语义。

`RunLogStore` 是 `SubprocessRunner` 的可选记录 seam。生产 composition root 为每次 CLI 运行注入一个 store；runner 在进程完成并完成脱敏后调用：

```text
record(process_id, redacted_spec, redacted_result) -> absolute_log_path
reference_for(process_result) -> absolute_log_path | None
```

store 在 `.pf/logs/<UTC-run-id>/` 写一个 run manifest 和每进程一个 UTF-8 `.log` 文件，使用项目相对展示路径、随机化 run-id、私有目录/文件权限和原子 replace。环境只记录变量名。`reference_for` 使用当前进程内对象 identity，不修改 Schema，也不尝试用可能碰撞的内容 hash 关联日志。写日志失败是基础设施错误，不能静默继续产生一个违反 CLI 可诊断性契约的 Evaluation。

### 10.2 UvAdapter、TyAdapter、TestAdapter

- `UvAdapter` 唯一构造解释器发现、venv、editable install、harness install、graph inspection 和候选查询的 uv argv，并把机械结果分类为强类型工具结果。
- `TyAdapter` 的输出、诊断规范化和参数所有权由 D004 定义。
- `TestAdapter` 只执行已决定的完整 argv、cwd、环境、timeout 与失败退出码策略。

所有 adapter 在返回前完成脱敏。Presenter 与 ReportStore 不负责补救原始 secret。

## 11. 报告与编辑

### 11.1 PackageReportBuilder 与 ReportStore

`PackageReportBuilder.build(package, source_snapshot, cell_results)` 从 cell 证据生成投影与 complete/incomplete 授权。`project(declaration, target_cells, active_cells, floors)` 在此唯一实现 D001 的 cell-set 等价校验；投影的产品正确性契约不在本文复制。

`ReportStore` 唯一拥有：

- Schema 1 读取与 64 MiB 上限；
- canonical JSON 与原子写；
- 同一 generation 的 search update；
- 严格 merge 及合并后投影重算。

未知 Schema、不匹配 generation、重复 cell 冲突或结构验证失败都保守失败。

### 11.2 ProjectEditor

```text
apply(report, root) -> ProjectEditResult
apply_many(reports, root) -> tuple[ProjectEditResult, ...]
```

它只消费 complete 报告，重新计算每条投影并验证源码快照。`ApplyCommandWorkflow` 在进入编辑器前还要求报告策略 identity 等于当前有效策略，使诊断或测试策略升级后的旧报告不能 apply。workspace 批量 apply 先统一验证快照，再逐包编辑。

写入用 `tomlkit` 保留格式，之后重新解析项目。恢复日志状态为：

```text
PREPARED -> PROJECT_REPLACED -> REPORT_CONFIRMED -> COMMITTED
```

每次 apply 先检查未完成日志；identity 不匹配时停止，不覆盖未知用户修改。TOML 与日志使用临时文件、`fsync` 和原子 replace。

## 12. TerminalPresenter

`terminal.py` 是业务 Rich 的唯一使用点：

- stdout：最终成功摘要、explain、merge/apply 结果；
- stderr：错误、警告、进度和外部工具诊断摘要；
- TTY：主线程消费 ActivityEvent 并动态刷新；
- 非 TTY：相同事件变为稳定文本行。

Presenter 还唯一拥有 D001 的人类摘要规则：

- 从强类型 `TyDiagnostic` 生成稳定单行摘要；
- 把 adapter stage 映射为 install/harness/static/dynamic 用户阶段；
- 从 `ProcessResult` 选择一行原因并折叠空白，不转储多行工具输出；
- 用运行时日志引用生成项目相对文本和可选 OSC 8 本地文件链接。

adapter、Evaluator、workflow 和 report 不拼终端文案。日志文件保存机械详情，Presenter 不重新读取日志来决定状态。

生产代码不固定 Console/Table/Progress 的 width、height 或列尺寸，让 Rich 适配终端。worker 和 adapter 不直接打印。

## 13. 验证边界

测试以模块 interface 为表面：

- Schema：严格/冻结、union、证据链 validator、JSON round-trip；
- CLI：两个入口、七命令 help、参数默认值、stdout/stderr 和退出码；
- 核心：真实临时项目/快照、fake adapter、D003 focused algorithm、D004 增量证据；
- adapter：recording ProcessRunner、argv、状态、timeout、truncation 与脱敏；
- 持久化：canonical JSON、merge/update、投影、恢复日志、幂等 apply；
- 端到端：安装 wheel 后执行真实 `smoke -> check -> search -> explain -> apply`。

需要网络、其他 CPython minor 或非宿主平台的验证必须单独标注，不能由 fake 或契约测试冒充。历史验证证据见 [P001](../../plans/P001-pf-v1.md) 与 [P002](../../plans/P002-pf-ty-enhancement.md)。

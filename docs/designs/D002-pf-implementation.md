# PF 实现结构

- **状态：** 现行
- **最后核对：** 2026-08-21
- **产品与命令：** [D001](D001-pf.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **静态证据：** [D004](D004-pf-ty-enhancement.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **CLI 交互与展示：** [D006](D006-pf-cli-enhancement.md)
- **进程输出与日志：** [D007](D007-pf-process-output.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)

本文是 PF v1 模块接口、依赖方向、Schema 所有权、adapter 与持久化结构的唯一所有者。用户可见值与退出码不在这里重复定义；坐标 probe 规则由 D003 定义；`ty` 诊断比较由 D004 定义；failure cause、disposition 与 `diagnose` 文案由 D005 定义；CLI 信息层级、调用错误和终端布局由 D006 定义；进程输出、磁盘日志与内存投影由 D007 定义；Attempt 序列、Cell Completion 和 Verification Journal 由 D008 定义。

## 1. 设计原则

PF 按深模块组织：小接口隐藏完整行为，调用方和测试都通过同一 interface 使用模块。

- 每条业务规则只有一个所有者。候选过滤、静态增量、失败分类、坐标搜索、报告授权和编辑授权不能散落在调用方。
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
├── runlog.py            # RunLogStore 与脱敏的进程详细日志
├── windows_runlog.py    # Windows reparse-safe directory handle 与私有 DACL
├── candidates.py        # CandidateBuilder
├── environment.py       # EnvironmentFactory 与 PreparedEnvironment
├── evaluation.py        # StaticEvaluator、FullEvaluator、EvaluationCache
├── baseline.py          # HighestVersionVerifier 的最高版本完整验证生命周期
├── failure.py           # FailurePolicy：结构化 scope + cause -> disposition
├── search.py            # CoordinateSearch 与 SearchCoordinator
├── scheduling.py        # Scheduler、deadline 与规范结果顺序
├── report.py            # PackageReportBuilder 与 ReportStore
├── editor.py            # ProjectEditor 与恢复日志
├── workflow.py          # 七个命令工作流；minimize 复用 search/apply
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
 failure / search / scheduling / report / editor / policy
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
- `CheckRequest`、`SmokeRequest`、`SearchRequest`、`ReportRequest`、`DiagnoseRequest`、`ApplyRequest`、`MergeRequest`：CLI 与工作流之间的严格请求。

原始 TOML patch 只在 `ConfigLoader` 内部使用普通 mapping，不跨模块传播。默认值、层级权限、互斥条件和字段规范化均由 `ConfigLoader` 一次完成。

`schemas/project.py`：

- `RequirementDeclaration`、`Cell`、`SourceIdentity` / `SourcePlan`；
- `SourceSnapshotIdentity` / `SnapshotEntry`；
- `ResolvedNode`、`VersionPin`、`InterpreterIdentity`、`Proposal`；
- `AvailableCandidate` / `AvailableArtifact`、`Candidate` / `CandidateSnapshot`；
- `PackagePlan`、`ProjectPlan`。

稳定 declaration ID 覆盖 `pyproject.toml` 路径、base/optional 位置、extra、规范化名称、requested extras、marker 与来源 identity，不包含数组下标、行号或会被 apply 改写的 specifier。

Proposal 只在解析图完成复查后建立，并同时保存 `attempt_id`；prepare 失败不得虚构 Proposal。`AttemptIdentity` / `Attempt` 与 Failure 模型同属 Evaluation Schema，见下节。

Proposal ID 覆盖源码快照、cell、受管向量、固定声明、实际解析图、解释器和 Evaluation 策略 identity。策略 identity 已包含影响 `ty` 执行、诊断判定、failure policy 和完整测试的有效配置，因此 cache key 不再重复拼接这些 policy 字段。

`schemas/evaluation.py`：

- `ProcessSpec` / `ProcessResult` 与外部工具结果；
- D005 定义的 `AttemptIdentity` / `Attempt`、operation cause、prepare failure、`AttemptFailureScope | CellFailureScope`、`FailureRecord` 与 baseline outcome union；
- D004 定义的 `TyDiagnostic`、`TyCheck`、`StaticBaseline` 与 static/full Evaluation union；
- `CheckResult`、`HighestVersionPass`、`BaselineRejection`、`BaselineIndeterminate`、`SmokeResult`、`CacheConflict`；
- `ProgressEvent`、`StatusEvent`、`CellMatrixEvent`、`ProcessEvent`、`SearchFailureEvent`。

`ProcessResult` 的可移植部分由 D007 定义为 Portable Process Facts：脱敏终态和 `stdout_complete` / `stderr_complete`，不含 stdout/stderr 文本，也不含详细日志引用。运行期 Output Cache 不得进入 `package-floor.json`。`FailureRecord` 可以保存该 `ProcessResult`；公共报告不得保存 run ID、绝对路径或其他本机 locator。`RunLogStore` 另行维护 D005 定义的项目本地 diagnosis index；索引或日志丢失不能改变已经记录的 disposition。

`schemas/report.py`：

- `ProbePass` / `ProbeRejection` / `ProbeIndeterminate`、坐标边界与 `CoordinateOutcome`；
- cell result discriminator union（包括 Attempt 前的 `CellIndeterminate`）、baseline outcome 与 `FailureRecord` 引用；
- 投影证据与 complete/incomplete report result；
- `PackageFloorReportV1`、`ProjectEditResult`。

`PackageFloorReportV1` validator 复证目标 cell 精确覆盖、成功证据、FailureRecord 交叉引用、投影授权，以及 D004 的静态基线和 D005 的 disposition 一致性。公共 JSON 即使被手工编辑也不能绕过这些不变量。

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

除 `minimize` 外，handler 只构造 request、调用一个 workflow、交给 `TerminalPresenter` 渲染并返回 D001 退出码。领域异常继承 `PfError`，只在 `main()` 最外层映射；内部模块不调用 `sys.exit()`。

### 6.2 工作流所有权

| 工作流 | 编排职责 | 写入 |
| --- | --- | --- |
| `CheckCommandWorkflow` | load → snapshot → 选择宿主 cell → Scheduler → CompatibilityChecker | `.pf/logs`、临时环境 |
| `SmokeCommandWorkflow` | load → snapshot → 选择宿主 cell → Scheduler → HighestVersionVerifier | `.pf/logs`、临时环境 |
| `SearchCommandWorkflow` | load → snapshot → 宿主 cell 搜索 → report build/update/write | `.pf/logs`、`package-floor.json` |
| `ExplainCommandWorkflow` | load → 定位并读取报告 | 无 |
| `DiagnoseCommandWorkflow` | load → 定位并读取报告 → 解析 FailureRecord → 可选读取本地 locator/log | 无 |
| `MergeCommandWorkflow` | read → merge → write | 显式 output |
| `ApplyCommandWorkflow` | load → 读取并核对报告 → `ProjectEditor.apply_many` | `pyproject.toml`、`.pf` 日志 |

`minimize` 只在 handler 中顺序复用 search 和 apply workflow；安全规则仍由报告 Schema、ReportStore 和 ProjectEditor 强制。最终展示必须走 `TerminalPresenter.render_minimize(reports, edits)`：`edits is None` 表示 apply 未运行。handler 不得连续调用 `render_search` 和 `render_apply`。

## 7. 项目规划与快照

### 7.1 ProjectLoader

外部 interface：

```text
load(root: Path, package_selection: str | None) -> ProjectPlan
```

它隐藏并唯一拥有：

- workspace 与可安装包发现、root packages/exclude 选择；未知 package 时把已发现名称放入 `ConfigurationError.candidates`；
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
  -> PreparedEnvironment | PrepareFailure
```

`resolution=highest`、`lowest-direct` 或带 `managed_vector` 的精确向量会在任何外部操作前构造 Attempt。prepare 失败返回 `PrepareFailure`，不得为 `lowest-direct` 省略 Attempt 或把 `PrepareFailure` unwrap 成裸 `ToolFailure`。随后创建独立源码副本和虚拟环境，物化受管向量，安装 editable 包，记录实际解释器与解析图，合并测试支撑依赖并复查目标图。`PrepareFailure` 保留 Attempt、stage、adapter cause 和机械事实；`PreparedEnvironment` 是带 `close()` 生命周期的内部资源对象，不进入公共报告。各命令的 Attempt 序列与完成投影由 D008 定义。

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
  -> HighestVersionPass | BaselineRejection | BaselineIndeterminate
```

它唯一拥有 `prepare(highest) -> static capture -> full evaluate with captured static -> classify -> close` 生命周期。成功结果返回冻结的 `StaticBaseline` 与完整 Evaluation；任何非 PASS 结果经 `FailurePolicy` 分类并携带 `FailureRecord`。`smoke` 和 `SearchCoordinator` 是两个真实调用方；两者不得复制最高版本验证序列或再次运行 `ty`。`CompatibilityChecker` 有意只 capture 最高版本静态基线、再测试 `lowest-direct`；两轮都必须保留 Attempt 并分类，见 D008。

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

### 8.5 FailurePolicy

```text
classify(scope, cause, stage, process, summary_code=None, detail=None)
  -> FailureRecord
```

该深模块唯一实现 D005 的 REJECTED / INDETERMINATE 分类：它验证 `AttemptFailureScope | CellFailureScope` 和证据完整性，再由 scope、stage、cause 与机械事实构造 `FailureRecord`。Baseline、Declaration 与 probe 的区分来自 Attempt 的 `requested_resolution`（`highest` / `lowest-direct` / `exact-vector`），没有单独的 role 参数。Verification Role 由 D008 拥有，不进入本模块。Cell scope 只允许 Indeterminate。PASS 不经过本模块。Adapter 只提供稳定 cause，搜索只消费 disposition；二者都不得按 stderr substring 或裸退出码复制分类规则。`failure-v1` 是 Evaluation policy identity 的组成部分。

## 9. 搜索与调度

`CoordinateSearch` interface：

```text
minimize(start, candidates, evaluator, hints=(), start_is_known_pass=False)
  -> CoordinateOutcome
```

它只读取冻结版本坐标和分类后的 `ProbePass | ProbeRejection | ProbeIndeterminate`；`start_is_known_pass` 为真时把 start 当作已有 PASS，不重新评估。具体算法由 D003 唯一定义。`small_threshold` 的现行默认值是 `8`。

`SearchCoordinator` interface：

```text
search(package, cell, snapshot) -> CellResult
```

它拥有单 cell 搜索状态机：消费 `HighestVersionVerifier` 返回的 baseline outcome；只有 `HighestVersionPass` 才冻结候选并调用 static/dynamic CoordinateSearch，其他 outcome 直接成为 cell 终态。它组装强类型 CellResult 与 FailureRecord 引用，不复制最高版本验证，不负责跨 cell 并发或总时限。

`Scheduler.run(tasks, jobs, max_duration_seconds, events)` 只调度独立 cell callable，限制并发、停止启动超过 deadline 的任务、为未启动 cell 产生带 `CellFailureScope` 的 `TIMEOUT / INDETERMINATE` cell 结果、消费进度事件并按 package/target/python/extra 规范排序。它不得虚构 Attempt。单 cell 内 probe 始终串行。

## 10. 外部 adapter

### 10.1 ProcessRunner

```text
run(ProcessSpec) -> ProcessResult
```

生产 adapter 是 `SubprocessRunner`。它唯一拥有 `shell=False` argv、cwd、最小环境增量、独立进程组、timeout 终止、signal/启动错误机械记录和通用 secret/URL 脱敏。输出如何写入 Process Log、如何进入每进程 16 MiB Output Cache，以及 `stdout_complete` / `stderr_complete` 的含义由 D007 规定。它不知道 uv、ty 或测试退出码语义。

`RunLogStore` 是 `SubprocessRunner` 的可选记录 seam。生产 composition root 为每次 CLI 运行注入一个 store；runner 在进程完成并完成脱敏后调用：

```text
record(process_id, redacted_spec, redacted_result) -> internal_log_path
reference_for(process_result) -> internal_log_path | None
associate(report_generation_id, failure_id, process_result) -> None
lookup(report_generation_id, failure_id) -> project_relative_log_path | None
```

store 在 `.pf/logs/<UTC-run-id>/` 写一个 run manifest 和每进程一个 UTF-8 `.log` 文件，并在 `.pf/logs/diagnosis-index.json` 维护 `(report_generation_id, failure_id) -> 相对日志路径`。两类文件都使用随机化 run-id、私有目录/文件权限和原子 replace。环境只记录变量名；`.pf` 或 `.pf/logs` 是 symlink 时 fail closed，不把日志写到项目 root 之外。`reference_for` 只用于当前运行中按 `ProcessResult` 对象 identity 找到刚写入的日志；报告写入完成后，workflow 通过 `associate` 原子更新 locator index。不得用内容 hash、目录扫描或模糊输出匹配恢复关联。写日志或更新应有 locator 失败是基础设施错误，不能静默继续产生违反 CLI 可诊断性契约的报告。

日志正文、每进程 16 MiB Output Cache 和 `stdout_complete` / `stderr_complete` 由 D007 规定。元数据字段（argv、cwd、环境变量名）在 store 边界仍可施加硬上限。在支持 `dir_fd` 的平台，目录创建和替换通过逐级 directory fd、禁止跟随 symlink 的打开方式以及 run directory inode identity 完成；初始化后的目录被替换也必须 fail closed。Windows 先用 `GetVolumePathNameW` 解析项目实际承载卷（包括嵌套挂载点），并要求该卷声明 `FILE_PERSISTENT_ACLS`，再使用原生 directory handle 打开每一级目录：拒绝 reparse point、只共享 read 而拒绝后续 write/delete handle，并保持 guard 到运行结束；run directory 在 `CreateDirectoryW` 时通过 `SECURITY_ATTRIBUTES` 原子安装仅 owner 与 SYSTEM 可访问且对子文件继承的 protected DACL。其他无法提供等价安全原语的平台以已分类基础设施错误停止，不能退回有 TOCTOU 窗口的路径检查。

candidate probe 的每个 Rejection/Indeterminate 都把可移植 `FailureRecord` 持久化进报告；prepare failure 保留 Attempt 与原始脱敏机械事实，即使没有 Proposal。Attempt 前的 candidate discovery/scheduling Indeterminate 使用 Cell scope。边界和 cell 终态通过 `failure_id` 引用该记录。详细日志路径和进程输出文本不进入报告 Schema：`SearchCommandWorkflow` 在成功写入报告后才以最终 `report_generation_id` 更新本地 diagnosis index。其他宿主 merge 进来的 FailureRecord 可以没有本地 locator，`diagnose` 此时仍展示报告内的 Portable Process Facts，完整输出按 D007 只存在于本机 Process Log。

### 10.2 UvAdapter、TyAdapter、TestAdapter

- `UvAdapter` 唯一构造解释器发现、venv、editable install、harness install、graph inspection 和候选查询的 uv argv，并把机械结果分类为 D005 的稳定 operation cause，例如 `RESOLUTION_CONFLICT`、`BUILD_FAILURE`、`HARNESS_CONFLICT`、`SOURCE_FAILURE` 或 `TOOL_FAILURE`。
- `TyAdapter` 的输出、诊断规范化和参数所有权由 D004 定义。
- `TestAdapter` 只执行已决定的完整 argv、cwd、环境、timeout 与失败退出码策略。

所有 adapter 在返回前完成脱敏。Adapter 不决定 disposition；Presenter 与 ReportStore 也不负责补救原始 secret。

## 11. 报告与编辑

### 11.1 PackageReportBuilder 与 ReportStore

`PackageReportBuilder.build(package, source_snapshot, cell_results)` 从 cell 证据生成投影与 complete/incomplete 授权。`project(declaration, target_cells, active_cells, floors)` 在此唯一实现 D001 的 cell-set 等价校验；投影的产品正确性契约不在本文复制。

`ReportStore` 唯一拥有：

- 首发 Schema 1 严格读取与 64 MiB 上限；
- canonical JSON 与原子写；
- 同一 generation 的 search update；
- 严格 merge 及合并后投影重算。

未知 Schema、缺少 D005 Attempt/FailureScope/FailureRecord union 的开发期旧结构、不匹配 generation、重复 cell 冲突或结构验证失败都保守失败。项目未发布，不提供 Schema 2、dual reader、迁移器或旧字段兼容分支。

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

`terminal.py` 是业务 Rich 的唯一使用点。`TerminalPresenter` 在主线程消费 `ActivityEvent` 和强类型命令结果：stdout/stderr、TTY live display、非 TTY 稳定行、最终摘要、artifact 布局和诊断短格式由 D006 定义；诊断身份与多重集事实由 D004 定义；FailureRecord 的 title、impact、next step 和次级技术信息由 D005 定义；本地日志链接由 `RunLogStore` 提供。D006 尚未完全落地，现行终端布局以实现为准，但不能在 D002 另写一套展示规则。

adapter、Evaluator、workflow 和 report 不拼终端文案。日志文件保存机械详情；Presenter 可以为 `diagnose` 展示已定位的脱敏日志内容，但不得重新分类或用日志改变报告中的 disposition。

生产布局和输出通道的精确契约由 D006 定义。worker 和 adapter 不直接打印。

## 13. 验证边界

测试以模块 interface 为表面：

- Schema：严格/冻结、union、证据链 validator、JSON round-trip；
- CLI：两个入口、八命令、参数默认值和 D001 退出码；D006 落地后按 D006 验证 help 分组、调用错误和结果摘要；
- 核心：真实临时项目/快照、fake adapter、D003 focused algorithm、D004 增量证据、D005 failure 分类；
- adapter：recording ProcessRunner、argv、状态、timeout、日志完整性与脱敏；
- 持久化：canonical JSON、merge/update、投影、恢复日志、幂等 apply；
- 端到端：安装 wheel 后执行真实 `smoke -> check -> search -> explain -> diagnose -> apply`；另测 `diagnose` 不启动进程、不访问网络、不修改项目。

需要网络、其他 CPython minor 或非宿主平台的验证必须单独标注，不能由 fake 或契约测试冒充。历史验证证据见 [P001](../plans/P001-pf-v1.md)、[P002](../plans/P002-pf-ty-enhancement.md)、[P003](../plans/P003-pf-smoke-observability.md) 与 [P004](../plans/P004-pf-failure-and-diagnose.md)。

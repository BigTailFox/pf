# PF 实现结构

- **状态：** 现行
- **最后核对：** 2026-09-04
- **产品契约：** [D001](D001-pf.md)
- **算法与证据：** [D003](D003-pf-search-algorithm.md)–[D005](D005-pf-failure-and-diagnose.md)
- **展示与运行：** [D006](D006-pf-cli-enhancement.md)–[D008](D008-pf-verification-run.md)
- **Harness：** [D012](D012-pf-harness-relaxation.md)
- **pytest observer：** [D013](D013-pf-pytest-observer.md)
- **报告 wire：** [D014](D014-pf-report-schema.md)

本文是当前模块、interface、依赖方向、composition 与持久化边界的唯一所有者。它只说明“规则位于哪里、模块如何连接”，不复制产品、算法、failure、展示或 wire 规则。D009–D011 已归档，不再覆盖本文。

## 1. 结构原则

- 深模块以小 interface 隐藏完整行为；调用方和测试都走同一表面。
- 每条规则只有一个 owner；workflow 不重写 candidate、static、failure、search、report 或 apply 规则。
- 跨模块领域记录使用 strict/frozen Pydantic Schema；运行时资源句柄使用内部 Python 对象。
- 只为真实变化建立 seam：外部进程、uv/ty/test、Evaluator、cell task 与 activity consumer。
- 类用于状态、生命周期或不变量；一次性转换留在 owner 中。
- 不建立 `utils.py`、通用 filesystem/repository、DI framework、event bus 或 daemon。
- 文件大小不决定拆分；只有独立规则所有权或真实 adapter 分化才形成新 module。

## 2. 技术与依赖方向

| 领域 | 实现 | 约束 |
| --- | --- | --- |
| CLI | Cyclopts | 只有 `cli.py` 注册命令 |
| Terminal | Rich | 只有 `pf.terminal` 创建业务 renderable |
| Schema | Pydantic 2 | `schemas/` 保存 strict/frozen records 与 discriminated unions |
| Python packaging | `packaging` | 名称、PEP 440/508 的唯一库实现 |
| TOML | `tomli` + `tomlkit` | 前者读取，后者保留 apply 格式 |
| 外部工具 | uv、ty executables | 只经 adapter/ProcessRunner 调用 |

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

`cli.py` 是唯一生产 composition root。业务模块不得导入 Rich；adapter 不得导入 CLI/terminal 或打印；`schemas` 不做 I/O；`coordinate_search.py` 与 `search.py` 不依赖 Cyclopts、Rich、subprocess 或 TOML。跨层循环是所有权错误，不能用延迟导入掩盖。

## 3. 包布局与 owner

```text
src/pf/
├── __init__.py / __main__.py    version 与统一 CLI entry point
├── cli.py                       Cyclopts、CliContext、composition root
├── errors.py                    PfError 与 D001 exit-code mapping
├── config.py / policy.py        observation 上的配置合并、CLI parser、evaluation identity
├── project_discovery.py         离线 package catalog、在线 immutable workspace inventory
├── project.py                   inventory planning、declarations、Cells、test groups
├── snapshot.py                  immutable SourceSnapshot lifecycle
├── candidates.py                frozen CandidateSnapshots
├── harness.py                   original/relaxed direct harness 纯变换
├── resolution.py                resolution protocol、plans、outcomes、identity
├── environment.py               prepare 与 PreparedEnvironment lifecycle
├── static_transition.py         fingerprint、classifier、witness planning
├── evaluation.py                Static/Runtime Evaluator 与 run-local cache
├── baseline.py                  highest full-verification lifecycle
├── check.py                     declaration two-phase CompatibilityChecker
├── failure.py                   FailurePolicy
├── coordinate_search.py         pure vector search
├── search.py                    single-Cell SearchCoordinator
├── scheduling.py                generic Scheduler 与 schedule order
├── verification.py              command requests、VerificationRunner、Run lifecycle 与 Journal timing
├── report.py                    builder、resolved facade、store transaction
├── authorization.py             report/current plan/snapshot → frozen apply grant
├── editor.py                    authorized TOML transaction/recovery
├── workflow.py                  seven command use cases
├── runlog.py                    Process Logs、Journal、Diagnosis Index
├── _secure_runlog.py            private secure-directory protocol/adapters
├── windows_runlog.py            Windows native handle/DACL implementation
├── _pytest_observer.py          wheel-packaged standalone pytest observer plugin
├── _pytest_pruning.py           wheel-packaged standalone pytest pruning plugin
├── terminal/                    presenter 与 private live/explain/diagnose views
├── schemas/                     base/config/project/evaluation/report/apply records
└── adapters/                    process、uv/uv-lock、ty、verifier/pytest 与 runtime-witness seams
```

## 4. Schema boundary

所有跨模块 records 继承 `FrozenSchema`：

```python
ConfigDict(
    extra="forbid",
    frozen=True,
    validate_default=True,
    allow_inf_nan=False,
)
```

状态差异用 discriminator union 表达，不用互相独立的 bool 或不受约束的 optional 组合。Schema validator 只验证纯结构/identity 不变量；I/O 和多记录事务由 owner module 验证。公共 JSON 不保存 `Path`、`Version`、异常、PID、临时目录或资源句柄。

| Schema module | 记录范围 | 行为所有者 |
| --- | --- | --- |
| `schemas.config` | effective config、CLI/workflow requests | D001 / `ConfigLoader` |
| `schemas.project` | declarations、Cells、SourcePlan、candidates、Proposal、project plan | `ProjectLoader`、SourcePlan、Candidate/Environment owner |
| `schemas.evaluation` | process、Attempt、Failure、static/runtime outcome、Journal、activity events | D004、D005、D008、D013 |
| `schemas.report` | search evidence、CellResult、projection、private Schema 1 wire | D003、D014 |
| `schemas.apply` | workspace/package/group授权、presentation facts与command result | `ApplyAuthorizer`、`ProjectEditor` |

Proposal 只在 prepare 成功并复证 graph 后建立，保存 Attempt ID、两个 semantic plan digest、managed vector、fixed declarations、graph、interpreter 与 policy identity。Prepare failure 只能保存已取得的事实，不能虚构 Proposal。

`ValidatedReport` 是report module暴露给workflow/authorizer/explain/diagnose的immutable resolved facade。Wire records、typed indexes、refs和join规则都是私有implementation；editor不读取report。完整wire契约见D014。

## 5. Application boundary

```text
create_app(context: CliContext) -> cyclopts.App
build_context() -> CliContext
main() -> None
```

`pf` 与 `python -m pf` 进入同一个 `main()`。除 `minimize` 外，handler 只构造 request、调用一个 workflow、让 `TerminalPresenter` 渲染。`minimize` 顺序复用 search/apply workflow。Expected failures 继承 `PfError` 并只在最外层映射退出码；内部 module 不调用 `sys.exit()`。

`CliContext` 保存七个 workflow、presenter 与 RunLogStore；幂等 `close()` 先关闭 presenter，再关闭 logs。`build_context()` 装配失败时关闭已创建资源。

| Workflow | Owner boundary |
| --- | --- |
| `CheckCommandWorkflow` | planning → snapshot → VerificationRunner → CompatibilityChecker |
| `SmokeCommandWorkflow` | planning → snapshot → VerificationRunner → HighestVersionVerifier |
| `SearchCommandWorkflow` | planning → snapshot → VerificationRunner → report update → diagnosis associations |
| `ExplainCommandWorkflow` | offline discovery → report read |
| `DiagnoseCommandWorkflow` | offline discovery → selected report then latest Journal → one `FailureDiagnosis` |
| `MergeCommandWorkflow` | ordered report read → merge → write → `MergeCommandResult` |
| `ApplyCommandWorkflow` | planning → current owned snapshot → reports → ApplyAuthorizer → ProjectEditor transaction |

## 6. Planning 与 source snapshot

```text
ProjectDiscovery.select(root=..., selector=RootPackage | WorkspacePackage)
    -> PackageLocation

ProjectDiscovery.inventory(root=..., selector=RootPackage | WorkspacePackage)
    -> WorkspaceInventory

WorkspaceInventory
    -> target + root/target observations + owned paths + member point query

ConfigLoader.load(root_observation=..., target_observation=...)
    -> EffectiveConfig

ProjectLoader.load(root=..., selector=...)
    -> ProjectPlan

SnapshotBuilder.build(root, owned_pyproject_paths=...) -> SourceSnapshot
```

`ProjectDiscovery.select` 是 explain/diagnose 的轻量离线入口：它只完成 root/workspace package catalog 与
selector，省略 selector 时只返回可安装 root，`WorkspacePackage` 只按 canonical distribution name 唯一
匹配并返回一个 location。`ProjectDiscovery.inventory` 是 `ProjectLoader` 的唯一在线 workspace observation
入口。两者复用同一 private catalog，因此 root resolve、workspace glob/exclude、installable name、canonical
uniqueness、selector 与候选列表只有一个实现；selector 失败时不读取或校验 member version、recursive path、
PF config、declaration、Cell 或 harness facts。

一次成功 `inventory` 对每个纳入的 `pyproject.toml` 只保留一份 canonical-path + recursively immutable
TOML observation；root target 复用同一 observation。`WorkspaceInventory` 只暴露 selected location、
root/target observations、排序唯一的 owned paths 和 canonical-name member point query；它不暴露任意
document/members collection、raw bytes、digest、wire、cache 或 cleanup lifecycle，构造后不访问 filesystem。
`ConfigLoader` 只在 root/target observations 上独占 root default → member local 两层 PF config merge/validation，不读取 filesystem；root target 只消费 root observation 一次，`tool.pf.package` 没有内部入口。
`ProjectLoader.load(root, selector)` 每次只构造一个 inventory，并继续独占 PEP 508 declaration、marker/extra
Cell、逐 dependency source route、完整 `NamedSearchPolicy` binding、member-version attachment 与 recursive test-group planning；
`ProjectPlan.target` 仍是唯一执行 target，且 `ProjectPlan` 不保存 inventory 或 TOML。

`ProjectPlan.owned_pyproject_paths` 包含 root；全部 installable、未排除 workspace packages（包括未选中的
member）；以及从这些 metadata 的 `tool.uv.sources.*.path` 递归可达、存在且不越过 root 的 metadata。
Excluded member 不因 workspace glob 进入，只有由合法 in-tree path source 可达时才以 path metadata 进入；
path-only 或 non-installable metadata 不成为 selector/member fact，也不做 workspace version validation。
closure 排序、唯一、cycle-safe；合法 in-tree path 缺少 `pyproject.toml` 时跳过，越界在 existence 前失败。

`SnapshotBuilder` 在 planning 后独立重读 filesystem 是有意的执行 evidence observation；它继续负责
Git/non-Git discovery、路径与 symlink 安全、普通 blob、owned `PyprojectIdentity` 的 type-tagged canonical
TOML 编码、完整摘要、immutable staging、独立 materialize 和 cleanup。所有在线 workflow、authorizer 与
editor 复用同一 builder 和 owned paths；inventory 不替代 SourceSnapshot、drift check、authorization 或
raw CAS。Git 模式使用注入的 ProcessRunner；`without_processes()` 只允许 non-Git traversal。Snapshot
产品范围由 D001、wire 编码由 D014 定义。

## 7. Verification modules

核心 interface：

```text
CandidateBuilder.build(package, cell, baseline, source_plan)
    -> tuple[CandidateSnapshot, ...]

EnvironmentFactory.prepare(package, cell, snapshot, resolution, source_plan)
    -> PreparedEnvironment | PrepareFailure

StaticEvaluator.capture/evaluate(...)
RuntimeEvaluator.evaluate(...)
HighestVersionVerifier.verify(...) -> HighestVersionOutcome
CompatibilityChecker.check(...) -> CheckCellOutcome
ConfiguredVerifier.run(VerifierRequest) -> VerifierRun

CoordinateSearch.minimize(...) -> CoordinateOutcome
SearchCoordinator.search(...) -> CellResult
VerificationRunner.run(CheckVerificationRun) -> tuple[CheckCellOutcome, ...]
VerificationRunner.run(SmokeVerificationRun) -> tuple[HighestVersionOutcome, ...]
VerificationRunner.run(SearchVerificationRun) -> tuple[CellResult, ...]
```

上述 Candidate/Environment/Highest/Check/Search interface 的最后一个参数均为同一 `source_plan`，不是裸 `source_mode`。`SourcePlan.for_package(package, mode)` 是在线 workflow 与 apply 的领域构造入口；其 `source_for`、`registry_routed_workspace_dependencies`、`workspace_member_version_for` 与派生 `identity` 独占 effective source、dual-route/member facts 和 source identity。只有 `source_mode + routes` 进入 wire；查询不保存实例缓存。ProjectLoader 仍独占 route 分类，UvAdapter 独占 argv，ApplyAuthorizer 独占授权，ReportStore 独占 codec/cross-ref。

`CompatibilityChecker`、`HighestVersionVerifier` 与 `SearchCoordinator` 分别拥有 declaration two-phase、
highest full verify 和单 Cell search；三者的构造器直接依赖 composition root 共享的同一
`EnvironmentFactory`、`StaticEvaluator`、`RuntimeEvaluator` 实例，不为 caller 复制 env/static/full
Protocol。Search 还直接依赖 `CandidateBuilder`、共享的 `HighestVersionVerifier` 与 `CoordinateSearch`。
这些 in-process module 不是 adapter seam；真实替换点只保留 uv、candidate provider、ty、configured
verifier、runtime witness/process 及 activity/diagnostic consumer。不得用 evaluator facade、parameter
bundle、factory、locator 或 service registry隐藏该依赖图。

`EffectiveConfig` 是按消费者分组的 frozen interface：`target`、`search`、`resolution`、`ty`、`test`、`scheduling`。ConfigLoader 独占 raw key/default/merge/canonicalization；ProjectLoader 独占 dependency selection 与 `DependencySearchPolicy` 到 managed searchable direct dependency 的资格绑定，并在 `PackagePlan.dependency_search_policies` 中提供排序唯一的完整 named policy。CandidateBuilder 和其他消费者不得重新读取 raw TOML 或实现平行默认逻辑。

`ResolutionRequest` 是 `HighestResolution | LowestDirectResolution | ExactSelection`。跨 Cell request 是
`CheckVerificationRun | SmokeVerificationRun | SearchVerificationRun` 的 closed union；三个 frozen variant
以不进入构造器的 `ClassVar` 固定 command，分别携带现有 `CheckCellOperations`、`SmokeCellOperations`、
`CellSearchOperations`。共同字段只含 package、完整 `source_plan`、borrowed `SourceSnapshot`、operation 与
一次解析后的 `RunLimits(max_cells, ty_jobs, test_jobs, max_duration_seconds)`；request 不进入 Schema、report、Journal、identity 或 cache。

Workflow 在 project load 后、snapshot build 前验证 full evaluation contract并从 persistent scheduling 与显式 CLI override 只解析一次 RunLimits。Runner 构造时固定 composition root 对 `pf.project.host_target()` 的单次探测结果；它验证 command/mode 与 package/routes，使用 `limits.max_cells` 调度 Cell，并在开始任务前把 `limits.ty_jobs/test_jobs` 配置给 composition root 共享的 `StagePermitPools`。

Runner 从 `package.cells` 选择唯一完整 host Cell 集，并把同一 package、plan 与
snapshot对象直接传给每个 operation；workflow不再选择Cell、建立per-Cell closure或保存host target。
candidate、harness、两次resolution、Attempt与search report共同消费该plan。Workflow仍在`finally`独占
snapshot close，Search仍在Run后消费snapshot identity做drift/report工作。structured harness、
two-resolution plan、environment identity和install边界由D012定义。

`PreparedEnvironment` 显式拥有 source copy、venv、interpreter、Attempt/Proposal、两个 validated ResolutionPlan 与 close 生命周期；成功值只由 `EnvironmentFactory.prepare(...)` 构造，产品代码与测试都从该 seam取得并显式关闭。不同 Proposal 不通过原地 upgrade/downgrade 复用环境；同一 Proposal 的 static-only probe 晋升到 full evaluation 时复用尚未关闭的 prepared lifecycle。

Evaluator 的 static transition/witness 由 D004 定义；本章只拥有 `ConfiguredVerifier` interface，
terminal disposition 由 D005 定义；D013 只拥有 pytest diagnostics。Adapter 只返回自己的
稳定 operation facts，不能决定搜索 Role。

`CoordinateSearch` 只拥有 invocation-local vector state；其算法由 D003 定义。`SearchCoordinator` 只拥有
一个Cell的baseline→candidates→coordinate-search状态机。`VerificationRunner`拥有Run admission、host
Cell/matrix、private task/deadline assembly、每个已启动Cell的initial baseline context、跨Cell
scheduling、typed live completion、Journal timing与journal-side association；它不拥有三个单Cell算法、
snapshot lifecycle、命令聚合、report或terminal。generic `Scheduler`只保证started callback在operation
前完成并处理worker/deadline/规范排序，不导入领域结果。

## 8. Adapter 与 process boundary

```text
ProcessRunner.run(ProcessSpec) -> ProcessObservation
```

生产 `SubprocessRunner` 唯一执行 `shell=False` argv、cwd/env、进程组、timeout、output capture
与通用 redaction，并可把完整 Process Log 交给 RunLogStore。
`ProcessSpec.environment_removals` 表达从继承 environment 删除的名字；runner 必须先删除、
再应用 `environment` overlay，使 adapter 可以隔离私有 invocation 状态而不修改进程级
`os.environ`。`ProcessObservation`、Process Log 与 Output Cache 的唯一契约是 D007。

- `UvAdapter` 拥有 uv argv、resolver protocol、candidate query、pylock parsing、venv、install 与 graph inspection；D012 拥有语义和资格边界。
- `TyAdapter` 拥有 ty argv/JSON normalization；D004 拥有诊断语义。
- `RuntimeWitnessAdapter` 只执行 D004 的 structured harness。
- `ConfiguredVerifier.run(VerifierRequest) -> VerifierRun` 是配置 verifier 的唯一 public
  module interface；D005 独占 terminal disposition，`VerifierDiagnostics` 只在运行期存在。
- `VerifierRequest.failed_case_nodeids` 与 `VerifierRun.failed_case_additions` /
  `RuntimeEvaluationRun.failed_case_additions` 是 runtime-only；additions 排除出 dump。
  空 input 只跑原命令阶段。generic command 收到非空 nodeids 是调用方 invariant failure。
- `_ProposalRunner` 唯一拥有 FailedCaseSet；`RuntimeEvaluator` 只把不可变 nodeid tuple 传给
  verifier，不解释其语义。`CoordinateSearch` 只消费 Probe evidence。
- direct pytest 原样保留用户 argv，并注入 PF-owned `--maxfail=1`、invocation-local
  `cache_dir`、observer 与仅 failed-set 使用的 private pruning plugin。pruning plugin 在
  `pytest_cmdline_main`（`hookwrapper=True, trylast=True`）pre-yield 替换已解析的
  `Config.args`。D013 只拥有 observer 透明性、诊断协议与分阶段 collected/failed artifact。

所有 adapter 在返回前脱敏；Presenter、ReportStore 与 workflow 不补救 raw secret，也不解析 stderr 重新分类。

## 9. Persistence boundary

| Module | 唯一负责 | 不负责 |
| --- | --- | --- |
| `RunLogStore` | secure Process Logs、Verification Journal、Diagnosis Index 与 associations | disposition、报告 authority |
| `ReportStore` | Schema 1 codec/validation、merge/update、canonical/atomic write；reader 从 wire SourcePlan 查询 identity/effective source | 搜索、source classification 或 apply authority |
| `PackageReportBuilder` | CellResult roots → interned report/result；dependency group Cell→PEP 508 projection与重求值 | wire I/O、TOML I/O、apply授权 |
| `ApplyAuthorizer` | report/current plan/snapshot的前置条件、platform scope、dependency state、source waiver与frozen authorized edits | TOML I/O、终端措辞、wire join |
| `ProjectEditor` | expected snapshot/pyproject复核、authorized group replacement、raw CAS、写后验证、recovery/rollback | report internals、scope/projection/waiver推导 |

`ApplyAuthorizer.authorize(report, project, current_snapshot, force) -> AuthorizedWorkspaceApply`只产生单数`package_apply`，但grant仍绑定全部owned pyproject identities以保护未选中member。`ProjectEditor.apply(authorization, root)`只执行冻结的target edits；prepare记录原始bytes digest，事务前匹配expected snapshot，每次replace前CAS，并在异常时all-or-nothing rollback。`ApplyCommandResult`携带必填package、edit结果和结构化presentation facts；`MergeCommandResult`携带validated report、有序input paths与output path。Presenter不得从artifact反推这些命令事实。

ReportStore的interface与交易语义只见D014；Process Log只见D007，Journal/Index只见D008；apply产品授权只见D001，展示只见D006。

## 10. Terminal boundary

`pf.terminal` 是业务 Rich 的唯一使用点。`TerminalPresenter.consume(ActivityEvent)` 是 thread-safe consumer；private `LiveVerificationView` 与 `CellPresentation` 管理 live/final view。Run live只消费Runner发布的`CellCompletedEvent`；Run final从Check/Smoke typed outcomes或Search `CellResult`经command-closed private projector形成；Explain与剩余Search failure从Evaluation/Failure facts经另一private projector形成。Terminal不导入Runner private projector，也不存在shared public `object` projector。共享result-card primitive只消费结构化facts，并为explain/apply/minimize/diagnose/merge和typed command errors统一marker、gutter、路径与final样式。Worker、adapter、workflow 和 report module 不打印、不拼文案。Help、通道、cell detail、summary、explain 与 diagnose 布局只见 D006。

Expected command failures使用typed `PfError`：explain report read/validation、diagnose not-found和merge input/compatibility/output分别携带Presenter所需的稳定facts；workflow不构造card文本或Usage。

## 11. 验证边界

测试优先覆盖 public module behavior：strict Schema/identity、真实临时项目与文件系统、recording adapter argv/outcome、CoordinateSearch/Runner、report/store/editor transaction、CLI 与 wheel entry point。评价与产品 tests 通过 lower uv/candidate/ty/verifier/witness adapters装配真实 Environment/Static/Runtime、Highest、Check 与 Search graph；不直接构造 PreparedEnvironment，不替换 concrete prepare/capture/evaluate/verify/minimize，也不读取 evaluator/search private state。需要网络、其他 CPython minor 或非宿主平台的验证必须明确标注，不能由 fake、collection 或窄测试冒充。

历史设计与证据分别保留在 [D009](../archived/designs/D009-pf-v1-refactor.md)–[D011](../archived/designs/D011-pf-runtime-backed-static-search.md) 及[归档计划](../archived/plans/)；它们不覆盖本页当前结构。

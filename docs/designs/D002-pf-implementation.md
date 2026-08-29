# PF 实现结构

- **状态：** 现行
- **最后核对：** 2026-08-29
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
├── config.py / policy.py        配置合并、CLI parser、evaluation identity
├── project_discovery.py         离线 package identity/path discovery
├── project.py                   planning、declarations、Cells、test groups
├── snapshot.py                  immutable SourceSnapshot lifecycle
├── candidates.py                frozen CandidateSnapshots
├── harness.py                   original/relaxed direct harness 纯变换
├── resolution.py                resolution protocol、plans、outcomes、identity
├── environment.py               prepare 与 PreparedEnvironment lifecycle
├── static_transition.py         fingerprint、classifier、witness planning
├── evaluation.py                Static/Runtime Evaluator 与 run-local cache
├── baseline.py                  highest full-verification lifecycle
├── failure.py                   FailurePolicy
├── coordinate_search.py         pure vector search
├── search.py                    single-Cell SearchCoordinator
├── scheduling.py                generic Scheduler 与 schedule order
├── verification.py              VerificationRunner、completion、Journal timing
├── report.py                    builder、resolved facade、store transaction
├── authorization.py             report/current plan/snapshot → frozen apply grant
├── editor.py                    authorized TOML transaction/recovery
├── workflow.py                  seven command use cases
├── runlog.py                    Process Logs、Journal、Diagnosis Index
├── _secure_runlog.py            private secure-directory protocol/adapters
├── windows_runlog.py            Windows native handle/DACL implementation
├── _pytest_observer.py          wheel-packaged standalone pytest plugin
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
| `schemas.project` | declarations、Cells、source、candidates、Proposal、project plan | `ProjectLoader`、Candidate/Environment owner |
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
| `DiagnoseCommandWorkflow` | offline discovery → report/Journal → optional local log |
| `MergeCommandWorkflow` | report read → merge → write |
| `ApplyCommandWorkflow` | planning → current owned snapshot → reports → ApplyAuthorizer → ProjectEditor transaction |

## 6. Planning 与 source snapshot

```text
ProjectDiscovery.select(root=..., selector=RootPackage | WorkspacePackage)
    -> PackageLocation

ProjectLoader.load(root=..., selector=...)
    -> ProjectPlan

SnapshotBuilder.build(root, owned_pyproject_paths=...) -> SourceSnapshot
```

`ProjectDiscovery` 只读取 identity/path 与 workspace/package selection，供在线与离线命令复用；省略 selector 时只返回可安装 root，`WorkspacePackage` 只按 canonical distribution name 唯一匹配并返回一个 location。`ProjectLoader` 在其上唯一完成三层配置、PEP 508 declaration、marker/extra Cell、逐 dependency source route、workspace member version 与 recursive test-group planning；`ProjectPlan.target` 是唯一执行 target。

`ProjectPlan.owned_pyproject_paths`由discovery收集root、全部workspace candidates（包括未选中/排除member）和递归in-tree path packages。`SnapshotBuilder`负责Git/non-Git discovery、路径与symlink安全、普通blob、owned `PyprojectIdentity`的type-tagged canonical TOML编码、完整摘要、immutable staging、独立materialize和cleanup；所有在线workflow、authorizer与editor复用同一builder和owned paths。Git模式使用注入的ProcessRunner；`without_processes()`只允许non-Git traversal。Snapshot产品范围由D001、wire编码由D014定义。

## 7. Verification modules

核心 interface：

```text
CandidateBuilder.build(package, cell, baseline, source_mode)
    -> tuple[CandidateSnapshot, ...]

EnvironmentFactory.prepare(package, cell, snapshot, resolution, source_mode)
    -> PreparedEnvironment | PrepareFailure

StaticEvaluator.capture/evaluate(...)
RuntimeEvaluator.evaluate(...)
HighestVersionVerifier.verify(...) -> HighestVersionOutcome
ConfiguredVerifier.run(VerifierRequest) -> VerifierRun

CoordinateSearch.minimize(...) -> CoordinateOutcome
SearchCoordinator.search(...) -> CellResult
VerificationRunner.run(VerificationRun) -> ordered outcomes
```

`ResolutionRequest` 是 `HighestResolution | LowestDirectResolution | ExactSelection`。`VerificationRun.package` 与 `source_mode` 在跨 Cell 调度前固定；`smoke=DEVELOPMENT`，`check/search=SEARCH`。`PackagePlan.source_routes + source_mode` 规范化为同一 `SourcePlan`，由 candidate、harness、两次 resolution 与 Attempt/report identity 共同消费。Request、structured harness、two-resolution plan、environment identity 和 install 边界由 D012 定义。

`PreparedEnvironment` 显式拥有 source copy、venv、interpreter、Attempt/Proposal、两个 validated ResolutionPlan 与 close 生命周期；测试后标记为可能污染。不同 Proposal 不通过原地 upgrade/downgrade 复用环境。

Evaluator 的 static transition/witness 由 D004 定义；本章只拥有 `ConfiguredVerifier` interface，
terminal disposition 由 D005 定义；D013 只拥有 pytest diagnostics。Adapter 只返回自己的
稳定 operation facts，不能决定搜索 Role。

`CoordinateSearch` 只拥有 invocation-local vector state；其算法由 D003 定义。`SearchCoordinator` 只拥有一个 Cell 的 baseline→candidates→coordinate-search 状态机。`VerificationRunner` 拥有跨 Cell scheduling、deadline outcome、completion projection 与 Journal timing；generic `Scheduler` 不导入领域结果。

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
- direct pytest selector、observer 注入与 telemetry projection 都是 `ConfiguredVerifier`
  私有实现；D013 只拥有其透明性和诊断协议。

所有 adapter 在返回前脱敏；Presenter、ReportStore 与 workflow 不补救 raw secret，也不解析 stderr 重新分类。

## 9. Persistence boundary

| Module | 唯一负责 | 不负责 |
| --- | --- | --- |
| `RunLogStore` | secure Process Logs、Verification Journal、Diagnosis Index 与 associations | disposition、报告 authority |
| `ReportStore` | Schema 1 codec/validation、merge/update、canonical/atomic write | 搜索或领域 identity 算法 |
| `PackageReportBuilder` | CellResult roots → interned report/result；dependency group Cell→PEP 508 projection与重求值 | wire I/O、TOML I/O、apply授权 |
| `ApplyAuthorizer` | report/current plan/snapshot的前置条件、platform scope、dependency state、source waiver与frozen authorized edits | TOML I/O、终端措辞、wire join |
| `ProjectEditor` | expected snapshot/pyproject复核、authorized group replacement、raw CAS、写后验证、recovery/rollback | report internals、scope/projection/waiver推导 |

`ApplyAuthorizer.authorize(report, project, current_snapshot, force) -> AuthorizedWorkspaceApply`只产生单数`package_apply`，但grant仍绑定全部owned pyproject identities以保护未选中member。`ProjectEditor.apply(authorization, root)`只执行冻结的target edits；prepare记录原始bytes digest，事务前匹配expected snapshot，每次replace前CAS，并在异常时all-or-nothing rollback。`ApplyCommandResult`只把edit结果和结构化presentation facts交给TerminalPresenter。

ReportStore的interface与交易语义只见D014；Process Log只见D007，Journal/Index只见D008；apply产品授权只见D001，展示只见D006。

## 10. Terminal boundary

`pf.terminal` 是业务 Rich 的唯一使用点。`TerminalPresenter.consume(ActivityEvent)` 是 thread-safe consumer；private `LiveVerificationView` 与 `CellPresentation` 管理 live/final view。Worker、adapter、workflow 和 report module 不打印、不拼文案。Help、通道、cell detail、summary、explain 与 diagnose 布局只见 D006。

## 11. 验证边界

测试优先覆盖 public module behavior：strict Schema/identity、真实临时项目与文件系统、recording adapter argv/outcome、CoordinateSearch/Runner、report/store/editor transaction、CLI 与 wheel entry point。需要网络、其他 CPython minor 或非宿主平台的验证必须明确标注，不能由 fake、collection 或窄测试冒充。

历史设计与证据分别保留在 [D009](../archived/designs/D009-pf-v1-refactor.md)–[D011](../archived/designs/D011-pf-runtime-backed-static-search.md) 及[归档计划](../archived/plans/)；它们不覆盖本页当前结构。

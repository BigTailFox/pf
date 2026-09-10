# PF 实现结构

- **状态：** 现行
- **最后核对：** 2026-09-10
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
- 持久化领域记录使用 strict/frozen Pydantic Schema，纯表达式 value 使用 frozen dataclass；运行时资源句柄使用内部 Python 对象。
- 只为真实变化建立 seam：外部进程、uv/ty/test、Evaluator、cell task 与 activity consumer。
- 类用于状态、生命周期或不变量；一次性转换留在 owner 中。
- 不建立 `utils.py`、通用 filesystem/repository、DI framework、event bus 或 daemon。
- 受支持 Host 上的 OS 差异留在已经拥有该能力的 module 内部；禁止通用平台独立层、`pf.platform` 与跨能力 HostFacts。内部 seam 的必要条件是同一能力已有两个真实 adapter；Darwin 与 Linux 同属 POSIX，不因「三端」单独成层。
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

## 3. 模块地图

本节画 **module**，不以每个内部 `.py` 冒充独立 owner。物理文件名由实现决定。

```text
cli.py                       Cyclopts、CliContext、composition root
errors.py                    PfError 与 D001 exit-code mapping
config.py / policy.py        observation 上的配置合并、CLI parser、evaluation identity
project_discovery.py         离线 package catalog、在线 immutable workspace inventory
project.py                   inventory planning、declarations、Cells、test groups
snapshot.py                  immutable SourceSnapshot lifecycle
candidates.py                frozen CandidateSnapshots
search_space.py              纯 DSL、默认绑定、系列切片与 search anchor 准入
markers.py                   portable/contextual marker 资格、target facts 与求值
harness.py                   original/relaxed direct harness 纯变换
resolution.py                resolution protocol、plans、outcomes；identity 纯函数见 schemas
environment.py               prepare 与 PreparedEnvironment lifecycle
cancellation.py              Run 取消
static module (pf.static)    原始 TyCheck、Run cache、准入/比较、纯 guidance、request 装配
evaluation.py                RuntimeEvaluator、动态 cache、stage permits
baseline.py                  highest full-verification lifecycle
check.py                     declaration two-phase CompatibilityChecker
failure.py                   FailurePolicy 分类实现；构造由本节 composition 拥有
coordinate_search.py         pure vector search
search.py                    single-Cell SearchCoordinator
scheduling.py                generic Scheduler 与 schedule order
verification.py              command requests、VerificationRunner、Run lifecycle 与 Journal timing
report.py                    builder、resolved facade、store transaction
authorization.py             report/current plan/snapshot → frozen apply grant
editor.py                    authorized TOML transaction/recovery
workflow.py                  seven command use cases
runlog.py                    Process Logs、Journal、Diagnosis Index
_secure_runlog.py            private secure-directory protocol/adapters
windows_runlog.py            Windows native handle/DACL implementation
_pytest_observer.py          wheel-packaged standalone pytest observer plugin
_pytest_pruning.py           wheel-packaged standalone pytest pruning plugin
terminal/                    presenter 与 private live/explain/diagnose views
schemas/                     记录与 identity；`schemas/static.py` 是记录模块
adapters/                    process、uv/uv-lock、ty 与 verifier/pytest seams
```

不存在独立的 `src/pf/static.py` module。`static_request.py`、`static_guidance.py`、其余
`static_*.py`、`ty_fact.py`、`ty_options.py` 不是独立 owner，属于静态 module 的
implementation。`evaluation.py` 不 re-export `StaticEvaluator`。

## 4. Schema boundary

持久化与验证 records 继承 `FrozenSchema`；纯 `search_space` value 使用 frozen dataclass，不承载 I/O：

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
| `schemas.evaluation` | process、Attempt、Failure、runtime outcome、activity events | D005、D008、D013 |
| `schemas.policy` | 已资格化的 observation / guidance / execution identity | D004；validator 不执行 `validate_ty_args` |
| `schemas.journal` | Verification Journal 与 `static_membership` | D008 |
| `schemas.static_*` / `schemas.ty_fact` | 静态 records、TyFactDocument、scope/audit 结构 | D004；validator 不重放 compare / hint / harness |
| `schemas.report` | search evidence、CellResult、projection、private Schema 1 wire | D003、D014 |
| `schemas.apply` | workspace/package/group授权、presentation facts与command result | `ApplyAuthorizer`、`ProjectEditor` |

Schema validator 只做结构与 identity 闭合。D004 的准入、减法、`locate_static_hint`、harness 变换与 ty-args 资格化不在 FrozenSchema 执行。`environment_identity_digest` / `resolution_graph_id` / `resolution_request_digest` 与被静态 records 引用的 `ResolutionPlanEvidence` 定义在 schemas 可依赖的纯层。

Proposal 只在 prepare 成功并复证 graph 后建立，保存 Attempt ID、project semantic digest 与 nullable environment digest、managed vector、fixed declarations、graph、interpreter 与 policy identity。Prepare failure 只能保存已取得的事实，不能虚构 Proposal。

`ValidatedReport` 是report module暴露给workflow/authorizer/explain/diagnose的immutable resolved facade。Wire records、typed indexes、refs和join规则都是私有implementation；editor不读取report。完整wire契约见D014。

## 5. Application boundary

### 5.1 Host OS 读取

命中指 `os.name`、`sys.platform`，以及把 `platform.machine()` / `libc_ver` 读成产品身份。`import os`、`os.environ`、`os.fstat` 不是命中。本节是 Host OS 身份读取的完整 owner 白名单；未列入的产品编排只接收 target 或能力结果，不读取 Host 身份。`configure_utf8_stdio()` 不读 `os.name` / `sys.platform`，不进入本节。禁止 `src/pf/platform/`、`src/pf/host/` 包及同名 module；不禁止 stdlib `platform` 仅由 `host_target()` 使用。

`host_target()` 是唯一允许把 `sys.platform` / `platform.machine()` / libc 读成 Cell 身份的函数，只许返回下列 exact triple。先读并校验 `sys.platform`；未知 OS 立即以 `ConfigurationError` 失败，不再读取 machine/libc。已知 OS 再把 machine 转为小写并规范化别名（`amd64`→`x86_64`，`arm64`→`aarch64`），校验结果只属于 `x86_64` / `aarch64`；只有 Linux 随后读取 libc：

| OS | machine | libc | triple |
| --- | --- | --- | --- |
| `sys.platform.startswith("linux")` | `x86_64` / `aarch64` | 只在 Linux 上读 `platform.libc_ver()[0]`（大小写不敏感）：已识别的 `gnu` / `glibc` → `gnu`；已识别的 `musl` → `musl` | `{machine}-unknown-linux-{gnu\|musl}` |
| `darwin` | `x86_64` / `aarch64` | 不读、不校验 | `{machine}-apple-darwin` |
| `win32` | `x86_64` / `aarch64` | 不读、不校验 | `{machine}-pc-windows-msvc` |

规范化后不在表内的 machine、Linux 上无法识别为 `musl` 或 `gnu`/`glibc` 的 libc（含空串），一律 `ConfigurationError`。返回值不得把原 machine/libc 字符串写进 triple，也不得把「非 musl」默认为 `gnu`。`darwin` / `win32` 不读取 libc，不能因 `libc_ver` 为空或不可调用而失败。CLI composition 可调用并向 `VerificationRunner` 注入字符串；`ProjectLoader` 在应用 D001 默认 `platforms` 时可调用。其它编排 module 只接收 target，不探测 Host，也不对调用次数建立产品不变量。

能力 owner 需要「posix 或 windows」这类单一事实时，在该 owner 的实现路径内读 `os.name`，或接收该能力专用参数。ty 配置根的探测在 `TyConfigurationResolver`：产品装配省略 `platform=`，由 Resolver 读 `os.name`；参数仍可显式传入。`static_request.py` 不读 `os.name`。不把这些事实收成跨模块 HostFacts / Platform 服务。

| 能力 | Owner | 实现路径 | 调用方学习的表面 |
| --- | --- | --- | --- |
| Host→exact target | `host_target()` | `project.py`（`host_target`） | `str` exact triple。Runner 由 composition 注入；Loader 在省略 `platforms` 时可调用 |
| 进程组、超时/中断停止、Windows 子进程 PATH | `SubprocessRunner` | `adapters/process.py` | `ProcessRunner.run(ProcessSpec) → ProcessObservation` |
| 安全日志目录 | 私有 `SecureLogDirectory` | `_secure_runlog.py`、`windows_runlog.py` | `RunLogStore` |
| 临时目录关闭 | `cleanup_temporary_directory` | `snapshot.py` | `SnapshotBuilder` / `EnvironmentFactory` 的 `close()` 调用该函数；消费方不是第二 owner |
| venv 解释器路径 | `EnvironmentFactory` | `environment.py`（`_interpreter`） | `PreparedEnvironment.interpreter` |
| ty 用户配置根 | `TyConfigurationResolver` | `static_configuration.py` | 产品装配省略 `platform`；`static_request.py` 不读 `os.name`。owner 测试可显式传 `platform=` |

### 5.2 Cell target 投影

只读注入或规划得到的 target。未列入 §5.1 的产品编排不得新增宿主身份分支。

| 能力 | Owner | 实现路径 | 调用方学习的表面 |
| --- | --- | --- | --- |
| PEP 508 四平台字段 | `pf.markers` | `markers.py` | `platform_marker_facts(target)`，只读 target |
| wheel / uv platform tag | `UvAdapter` | `adapters/uv.py` | candidate / install 按 Cell target 匹配 |
| apply selector 展示 | `pf.terminal` | `terminal/` | D006 标签；`win32`/`darwin` 是 Cell 别名 |

不列入 §5.1：Cell 解释器 ABI（`UvAdapter` / `static_inputs` 发给 prepared venv 的 probe 脚本读 `SOABI` / `cache_tag`）是目标侧观察；D001 preserved / external harness 的 contextual marker 由 Cell 覆盖五字段、其余沿用 `packaging` 的 context/Host default，不生成 host target。

### 5.3 Composition 与 invocation lifecycle

```text
create_app(context: CliContext) -> cyclopts.App
CliContext.compose(presenter, run_logs, *, root=..., <workflow>=...) -> CliContext
build_context() -> CliContext
main() -> None
```

`pf` 与 `python -m pf` 进入同一个 `main()`。`build_context()` 只建立 `RunLogStore` 与
`TerminalPresenter`，并经 `CliContext.compose` 返回；省略 workflow 参数时仍懒装配。
`compose` 的可选 workflow 是生产 composition 入口，供同一公开表面注入已装配实现；不是测试专用 API。
命令 handler 在同一 `cli.py` root 内按命令装配 capability graph：
help/version 只使用 parser/presenter；explain/diagnose/merge 不构造 UvAdapter、host target、
评价图或 SearchCoordinator；apply 不构造 static/runtime evaluator、SearchCoordinator 或
host target；check/smoke/search/minimize 装配各自验证图。`host_target()` 的两个合法调用位置是
CLI composition（向 `VerificationRunner` 注入）与 `ProjectLoader`（省略 `platforms` 时）；
Runner 只接收注入的字符串，自己不探测。minimize 共享已缓存子图。生产 `CliContext` 构造不要求七个 workflow 同时存在。

除 `minimize` 外，handler 只构造 request、调用一个 workflow、让 `TerminalPresenter` 渲染。
`minimize` 顺序复用 search/apply workflow。Expected failures 继承 `PfError` 并只在最外层映射
退出码，composition-time 预期失败与运行期失败走同一 `render_error()`；`KeyboardInterrupt`
由 `main()` 映射为退出 130。内部 module 不调用 `sys.exit()`。

`CliContext` 保存 presenter、RunLogStore 与 invocation-local 子图缓存；幂等 `close()` 先关闭
presenter，再关闭 logs，并在嵌套中断下仍完成。`build_context()` 装配失败时关闭已创建资源。
`interrupt_processes()` 在 runner 尚未装配时为空操作。

| Workflow | Owner boundary |
| --- | --- |
| `CheckCommandWorkflow` | planning → snapshot → VerificationRunner → CompatibilityChecker |
| `SmokeCommandWorkflow` | planning → snapshot → VerificationRunner → HighestVersionVerifier |
| `SearchCommandWorkflow` | planning → search anchor admission → snapshot → VerificationRunner → report update → diagnosis associations → `SearchCommandResult` |
| `ExplainCommandWorkflow` | offline discovery → report read → `ExplainCommandResult` |
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
`ProjectLoader.load(root, selector)` 每次只构造一个 inventory，并继续独占 PEP 508 declaration 用途/admission、extra
Cell、逐 dependency source route、完整 `NamedSearchPolicy` binding、member-version attachment 与 recursive test-group planning；
`ProjectPlan.target` 仍是唯一执行 target，且 `ProjectPlan` 不保存 inventory 或 TOML。
ConfigLoader 以 `TestConfig.group: str | None` 保存选择意图，不读取 group inventory。
ProjectLoader 以 `PackagePlan.selected_test_group` 暴露选择结果；展开的每条 requirement 只解析一次，
独占 self-reference/external harness 分离、provenance 与最终 Cell 构造；只暴露最终 cells、external requirements 和 source routes。
选择、准入与 surface 行为只见 D001 §2–3、§7。
省略 `pythons` 时允许通过现行 Python discovery 运行 `uv python list`，再完成 marker/version 资格；
资格失败必须早于 snapshot、Attempt 和 resolution/install/verifier。Discovery 自身失败保留基础设施
错误，不触发 target build-metadata probe 或提前创建环境。

`pf.markers` 独占 portable 表达式资格、target aliases 与求值，公开 interface 为：

```python
platform_marker_facts(target) -> PlatformMarkerFacts
PortableMarker.parse(raw) -> PortableMarker
marker.evaluate(cell) -> bool
evaluate_contextual_marker(raw, cell) -> bool
```

facts 与已资格化 marker 不可变；无 marker 恒真。parse 检查完整表达式、不需要 Cell；evaluate 为
portable 输入只消费 Cell minor/exact target。底层 packaging AST、有界 cache 与默认环境行为封装在
module 内，不增加 adapter/Protocol 或 wire AST。`MarkerError.reason` 区分 syntax、unsupported-variable、
target、comparison、missing-fact，unsupported 错误给出首个排序变量；不泄漏依赖异常文本或 host 值。

Loader 仅按 managed/self-reference/preserved 用途选入口，补充相对文件、group/item/name provenance，
映射 ConfigurationError；report/authorization/harness 各自补充语境和现行错误分类。report reader 可重建
portable marker，不能重新分类 ownership。contextual 仅服务 preserved 与 external harness 的既有
准入，不是 portable fallback；五字段与 portable 等价，剩余 context 行为按 D001/D012 限定。

report/authorization/terminal 消费具名 `sys_platform` / `platform_machine` 组成 canonical selector，
不依赖 facts iteration order；uv_lock 复用四个 target facts，但独占 actual profile 资格、实际 patch
与 native active graph，不能把 actual facts 填回 Cell。五字段产品映射与 activation 由 D001 拥有。

`ProjectPlan.report_path` 是所选 package 报告文件的 root-relative posix，由 `ProjectLoader` 从
`inventory.target.report_path` 复制；该绝对路径只由 `ProjectDiscovery` 按
`package_root / "package-floor.json"` 物化。Search/Apply 只用 `request.root / project.report_path`
读写；Explain/Diagnose 消费 `PackageLocation.report_path`。不得在 workflow 或 Presenter 中从
`pyproject_path` 重建文件名或 package-relative location。

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

候选准入、DSL 与采样行为由 [D037](D037-pf-candidate-search-policy.md) 定义。
`search_space` 独占 parse/canonicalize、`DefaultSpace` / `AllSpace` / `SpecifierSpace` / `SeriesSpace`、
anchor、默认分支绑定与系列位置求值。它不依赖 CandidateBuilder、report、配置 loader 或 I/O；ConfigLoader
验证 syntax，ProjectLoader 绑定完整 named requested policy（省略保留 None），Search workflow 在 snapshot 前
对全部 declared Cells 调用 `admit`。CandidateBuilder 和 report reader 复用同一 bind/evaluate 规则。

`CandidateProvider.query(dependency, source, cell) -> RegistryCandidates` 一次成功查询同时返回
过滤前 `release_versions` 和带 artifact facts 的 candidates。UvAdapter 只冻结成功解析的响应；失败不缓存。
CandidateBuilder 从同份观测选择 scope、过滤再采样，并冻结 highest baseline 的精确 artifact；
`CandidateSnapshot` 持有 typed `SpaceSelection`、可选 `SeriesInventory`、唯一搜索序列 `candidates` 与
required `baseline_selection`。其 `select(version)` 是在线 runner 与离线 reader 从
`C[d] ∪ {B[d]}` 取得精确 artifact 的唯一深 interface；调用方不得维护平行 version-to-artifact map。
Wire 是否存储由 D014 独占：派生 selection 不直接序列化，观测 intern 后引用。
`ValidatedReport.search_policy` 和 `search_spaces()` 暴露已验证请求与派生事实，Explain 不重新解析或 join。

核心 interface：

```text
CandidateBuilder.build(package, cell, baseline, source_plan)
    -> tuple[CandidateSnapshot, ...]

EnvironmentFactory.prepare(package, cell, snapshot, resolution, source_plan)
    -> PreparedEnvironment | PrepareFailure
PreparedEnvironment.relocate_to(request) -> PreparedEnvironment

StaticEvaluator.collect_prepared / capture_highest / compare_global
StaticEvaluator.record_runtime / open_slice
StaticEvaluator.record_phase_skip / record_oracle_selection
RuntimeEvaluator.evaluate(prepared, *, package, failed_case_nodeids=())
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
`cli.py` 不构造 `StaticRequestFactory`；生产 `TyAdapter` 与静态 request/inspect 装配绑定同一个
`ProcessRunner`。`RuntimeEvaluator` 不接收 `run_cache`，不读 static consumer。Check / Highest /
Search / `_ProposalRunner` 内部构造 `FailurePolicy()`，不接受 `failures=`。
这些 in-process module 不是 adapter seam；真实替换点只保留 uv、candidate provider、ty、configured
verifier、process 及 activity/diagnostic consumer。不得用 evaluator facade、parameter
bundle、factory、locator 或 service registry隐藏该依赖图。

`EffectiveConfig` 是按消费者分组的 frozen interface：`target`、`search`、`resolution`、`ty`、`test`、`scheduling`。ConfigLoader 独占 raw key/default/merge/canonicalization；ProjectLoader 独占 dependency selection 与 `DependencySearchPolicy` 到 managed searchable direct dependency 的资格绑定，并在 `PackagePlan.dependency_search_policies` 中提供排序唯一的完整 named policy。CandidateBuilder 和其他消费者不得重新读取 raw TOML 或实现平行默认逻辑。

`ResolutionRequest` 是 `HighestResolution | LowestDirectResolution | ExactSelection`。
跨 Cell request 是 `CheckVerificationRun | SmokeVerificationRun | SearchVerificationRun`；
其字段、Role、RunLimits、host Cell admission、activity、scheduling 与 Journal 生命周期只见
[D008](D008-pf-verification-run.md)。Workflow 拥有 project load、一次 RunLimits 解析、snapshot
build/finally-close 和 SourcePlan 构造；Runner 拥有任务装配，Search workflow 在 Run 后继续拥有
source drift/report/association。接口间不通过 per-Cell workflow closure 传递隐含上下文。

`EnvironmentFactory` 独占 prepare 生命周期与 request/outcome envelope 校验；完整阶段、
解释器观察前后的 Attempt、plan digest 提交、harness 分支与清理规则只见
[D012 §4、§6](D012-pf-harness-relaxation.md#4-resolve-project-optionally-augment-install-once)。
Adapter 返回中性 OperationFailure，Factory 绑定 Attempt 和已取得 plan，FailurePolicy 分类；
分类、authority 与 sidecar 约束只见 D005，Run/展示关联只见 D008。

`PreparedEnvironment` 显式拥有 source copy、venv、interpreter、Attempt/Proposal、validated project plan、optional environment plan、EnvironmentIdentity 与 close 生命周期；成功值由 `EnvironmentFactory.prepare(...)` 构造，已成功的环境可通过公开 `relocate_to` 在新根重写 RECORD。产品代码与测试都从这些 seam 取得并显式关闭。不同 Proposal 不通过原地 upgrade/downgrade 复用环境；坐标内合法静态物化环境可供 oracle 复用，同 ty key 不授权提前释放。

`_ProposalRunner` 是一次 Cell search 的唯一执行 cache/lifecycle owner，持有 baseline seed、
完整向量的 prepare/full 结果、保留环境与 FailedCaseSet。原始 TyCheck 由 Run-owned
`TyCheckCache` 共享。`CoordinateSearch` 只保存算法 observation、Slice 状态、boundary history 与当前向量。
两者的 seed/reuse/cleanup 行为只见 D003，不建立第二份执行 cache。

Evaluator 的静态事实与比较由 D004 定义；本章只拥有 `ConfiguredVerifier` interface，
terminal disposition 由 D005 定义；D013 只拥有 pytest diagnostics。Adapter 只返回自己的
稳定 operation facts，不能决定搜索 Role。

`SearchCoordinator` 拥有一个 Cell 的 baseline→candidates→coordinate-search 编排；
`VerificationRunner` 拥有跨 Cell Run，generic `Scheduler` 不导入领域结果。完整时序由 D008 定义。

## 8. Adapter 与 process boundary

```text
ProcessRunner.run(ProcessSpec) -> ProcessObservation
```

生产 `SubprocessRunner` 唯一执行 `shell=False` argv、cwd/env、进程组、timeout、output capture
与通用 redaction，并可把完整 Process Log 交给 RunLogStore。进程组停止与 Windows 子进程 PATH
解析是 `SubprocessRunner` 内部差异，不升为 public ProcessPlatform。
`ProcessSpec.environment_removals` 表达从继承 environment 删除的名字；runner 必须先删除、
再应用 `environment` overlay，使 adapter 可以隔离私有 invocation 状态而不修改进程级
`os.environ`。`ProcessObservation`、Process Log 与 Output Cache 的唯一契约是 D007。

- `UvAdapter` 拥有 uv argv、resolver protocol、candidate query、pylock parsing、venv、install 与 graph inspection；D012 拥有语义和资格边界。
- `TyAdapter` 拥有 ty argv/JSON normalization；D004 拥有诊断语义。
- `ConfiguredVerifier.run(VerifierRequest) -> VerifierRun` 是配置 verifier 的唯一 public
  module interface；D005 独占 terminal disposition，`VerifierDiagnostics` 只在运行期存在。
- `VerifierRequest.failed_case_nodeids` 与 `VerifierRun.failed_case_additions` /
  `RuntimeEvaluationRun.failed_case_additions` 是 runtime-only；additions 排除出 dump。
  空 input 只跑原命令阶段。generic command 收到非空 nodeids 是调用方 invariant failure。
- `_ProposalRunner` 唯一拥有 FailedCaseSet；`RuntimeEvaluator` 只把不可变 nodeid tuple 传给
  verifier，不解释其语义。`CoordinateSearch` 只消费 Probe evidence。
- direct pytest 的 argv overlay 只见 D001 §4。ConfiguredVerifier 管理 observer 与仅 failed-set
  使用的 private pruning plugin；pruning plugin 在
  `pytest_cmdline_main`（`hookwrapper=True, trylast=True`）pre-yield 替换已解析的
  `Config.args`。D013 只拥有 observer 透明性、诊断协议与分阶段 collected/failed artifact。

所有 adapter 在返回前脱敏；Presenter、ReportStore 与 workflow 不补救 raw secret，也不解析 stderr 重新分类。

## 9. Persistence boundary

| Module | 唯一负责 | 不负责 |
| --- | --- | --- |
| `RunLogStore` | secure Process Logs、Verification Journal、`pf-ty-cache-v1` sidecar、Diagnosis Index 与 Failure associations；拥有 ty-cache 编解码、canonical snapshot 与 cache→Journal→latest 原子写入 | disposition、报告 authority、文件树采集器 |
| `ReportStore` | Schema 1 codec/validation、merge/update、canonical/atomic write；reader 从 wire SourcePlan 查询 identity/effective source | 搜索、source classification 或 apply authority |
| `PackageReportBuilder` | CellResult roots → interned report/result；dependency group Cell→PEP 508 projection与重求值 | wire I/O、TOML I/O、apply授权 |
| `ApplyAuthorizer` | report/current plan/snapshot的前置条件、platform scope、dependency state、source waiver与frozen authorized edits | TOML I/O、终端措辞、wire join |
| `ProjectEditor` | expected snapshot/pyproject复核、authorized group replacement、raw CAS、写后验证、recovery/rollback | report internals、scope/projection/waiver推导 |

`ApplyAuthorizer.authorize(report, project, current_snapshot, force) -> AuthorizedWorkspaceApply`只产生单数`package_apply`，但grant仍绑定全部owned pyproject identities以保护未选中member。`ProjectEditor.apply(authorization, root)`只执行冻结的target edits；prepare记录原始bytes digest，事务前匹配expected snapshot，每次replace前CAS，并在异常时all-or-nothing rollback。`ApplyCommandResult`携带必填package、edit结果和结构化presentation facts；`SearchCommandResult` 与
`ExplainCommandResult` 携带 validated report 与已解析的 root-relative `report_path`；
`MergeCommandResult`携带validated report、有序input paths与output path。Presenter不得从artifact或
`pyproject_path` 反推这些命令事实。绝对 filesystem path、checkout root 与 display path 不进入
Schema 1、report identity、Journal 或 merge。Merge 显式 request/result 路径不被 package 默认路径覆盖。

ReportStore的interface与交易语义只见D014；Process Log只见D007，Journal/Index/ty-cache只见D008；编排器不读文件树采集器。apply产品授权只见D001，展示只见D006。

## 10. Terminal boundary

`pf.terminal` 是业务 Rich 的唯一使用点。`TerminalPresenter.consume(ActivityEvent)` 是 thread-safe consumer；private `LiveVerificationView` 与 `CellPresentation` 管理 live/final view。Run live只消费Runner发布的`CellCompletedEvent`；Run final从Check/Smoke typed outcomes或Search `CellResult`经command-closed private projector形成；Explain与剩余Search failure从Evaluation/Failure facts经另一private projector形成。Terminal不导入Runner private projector，也不存在shared public `object` projector。共享result-card primitive只消费结构化facts，并为explain/apply/minimize/diagnose/merge和typed command errors统一marker、gutter、路径与final样式。Worker、adapter、workflow 和 report module 不打印、不拼文案。Help、通道、cell detail、summary、explain 与 diagnose 布局只见 D006。

Expected command failures使用typed `PfError`：explain report read/validation、diagnose not-found和merge input/compatibility/output分别携带Presenter所需的稳定facts；workflow不构造card文本或Usage。

## 11. 验证边界

测试覆盖 public module behavior：strict Schema/identity、临时项目与文件系统、adapter argv/outcome、CoordinateSearch/Runner、report/store/editor transaction、CLI 与 wheel entry point。调用方和测试走同一公开表面。不直接构造 `PreparedEnvironment` 成功值；relocation 经 `EnvironmentFactory.prepare` 或公开 `PreparedEnvironment.relocate_to`。不替换 concrete prepare/collect_prepared/capture_highest/compare_global/record_runtime/open_slice/evaluate/verify/minimize，不读取 evaluator/search private state。产品测试不调用 `TyCheckCache` 领域方法；Runner 可见方法是构造、`admitted_membership`、`documents`、`stop`、`close`。分类从公开 Check/Highest/Search outcome 观察，不注入假 `FailurePolicy`。不写入 `CliContext._check_workflow` 一类私有字段；进程内 CLI 经 `create_app` 与公开 property，替身 workflow 经 `CliContext.compose` 的可选参数注入。产品测试不进口 `pf._secure_runlog`；安全目录行为经 `RunLogStore` 与 `pf.windows_runlog`，必须直接驱动 POSIX/Windows adapter 协议的用例标 `infra`。产品测试不 patch `pf` 包内私有函数。真实 ty 进程经 `TyAdapter.observe`（及其公开装配），不手写 `ty check` argv；`decode_process` 的纯解码矩阵用 recording 的 `ProcessResult`。包装真实 runner 的 recording 不按 argv 识别 `ty check`。

车道只调度真实性，不另开测试专用产品 API。未授权车道不得进入生产 `SubprocessRunner.run`。种类、覆盖率并集与 Host/Cell 展开只见 [tests/README.md](../../tests/README.md)。

静态事实从 `StaticEvaluator.collect_prepared` / `capture_highest` / `compare_global` /
`record_runtime` / `open_slice` 与真实 Check/Highest/Search 的公开 outcome 观察。
`record_phase_skip` / `record_oracle_selection` 只供 Search 写入 Run 内 search/skip/selection
账本；该账本不是 Journal、report 或 diagnose 事实，产品测试不读取 `TyCheckCache.snapshot`
上的 searches/skips/selections。账本闭合由内部测试经 snapshot / `_admit` 证明。
SearchCoordinator tests 使用真实 CoordinateSearch，覆盖
baseline/candidate 终止、direct/static/oracle 顺序、prepare/full reuse、公开 evidence、diagnostics/events 与 cleanup。

历史设计与证据分别保留在 [D009](../archived/designs/D009-pf-v1-refactor.md)–[D011](../archived/designs/D011-pf-runtime-backed-static-search.md)、
[D038](../archived/designs/D038-pf-static-guidance-authority.md)、[D039](../archived/designs/D039-pf-static-evaluation-module.md)、[D040](../archived/designs/D040-pf-test-lanes.md)、[D041](../archived/designs/D041-pf-repository-test-conformance.md)、
[D043](../archived/designs/D043-pf-static-subject-v2.md) 及[归档计划](../archived/plans/)；它们不覆盖本页当前结构。

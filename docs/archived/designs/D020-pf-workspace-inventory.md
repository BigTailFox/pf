# D020 — PF WorkspaceInventory 模块深化

- **状态：** 已完成并归档
- **日期：** 2026-09-03
- **完成日期：** 2026-09-03
- **性质：** 临时性架构优化设计；完成后归并到现行 owner 并与实施 Plan 一同归档
- **设计核对基线：** `c1aff33`（`chore: hidden some cache directories`）
- **评审来源：** [R005](../../reviews/R005-pf-module-depth-review.md) §4、§11.2
- **现行产品契约：** [D001](../../designs/D001-pf.md)
- **现行实现结构：** [D002](../../designs/D002-pf-implementation.md)
- **现行运行语义：** [D008](../../designs/D008-pf-verification-run.md)
- **现行报告 wire：** [D014](../../designs/D014-pf-report-schema.md)
- **历史来源：** [D016](D016-pf-apply-authorization.md)、[D017](D017-pf-single-target-workspace-dependencies.md)
- **实施计划：** [P026](../plans/P026-pf-workspace-inventory.md)

> **临时性声明：** 本文只拥有 `WorkspaceInventory` interface 迁移；它定义已接受的唯一迁移目标，
> 但在实施、验收和 owner 文档归并完成前不冒充现行行为。完成后必须将本文与对应 Plan
> 在同一完成变更中归档。

[R005](../../reviews/R005-pf-module-depth-review.md) 的 Verification Run、评价 seam、terminal-private result card 与 `SearchCoordinator` 测试表面
属于其它独立轨道，不进入本设计。`SourcePlan` 已由 D019/P025 完成；本设计只收敛其上游的 workspace
filesystem observation，不重新设计 source facts。

## 1. 问题与目标

当前一次 `ProjectLoader.load(...)` 会让三个 module 分别观察相同项目元数据：

- `ProjectDiscovery.select` 读取 root、展开 workspace glob，再读取候选 package identity；
- `ProjectLoader._load_package` 重读 target 与 root，`_workspace_members` 再展开 glob、读取全部 member
  identity/version；
- `ConfigLoader.load` 再读 root 与 target；
- `ProjectDiscovery.owned_pyproject_paths` 又从 root 开始，重读 workspace candidates，并递归读取
  in-tree path package 的 `tool.uv.sources`。

同一路径在一次 `ProjectLoader.load(...)` 中可以被多次读取和解析。Selection、workspace member version、
source classification 与 owned paths 因而不是同一份 observation 的投影；
`package identity changed during project loading` 只能在 selection 与 target planning 之间补一条局部防线。

重复 I/O 不是主要目标。核心问题是调用方必须知道 `select -> load target -> discover members -> collect
owned paths` 的观察顺序，而且 workspace 拓扑知识同时存在于 discovery 与 loader，降低 locality。

本设计目标是：

1. 一次 `ProjectLoader.load(...)` 只形成一个不可变 `WorkspaceInventory`；
2. target selection、workspace member facts 与 owned paths 从同一组文件 byte observations 投影；
3. `ConfigLoader` 继续独占三层 PF config merge；`ProjectLoader` 继续独占
   declaration/Cell/source/harness planning；
4. `explain` / `diagnose` 继续使用轻量 discovery，不扩大为 full planning；
5. `SourceSnapshot`、apply authorization 与 editor CAS 继续独占执行/持久证据，不被 inventory 替代；
6. 直接删除旧重读路径，不增加 compatibility layer、inventory digest 或通用 filesystem abstraction。

删除深化后的 `WorkspaceInventory` 时，workspace glob、package identity、member version、path traversal
和观察一致性会重新散回 `ProjectDiscovery`、`ProjectLoader` 与 `ConfigLoader`；这就是本次 depth 的删除
测试。

## 2. 决策摘要

1. `WorkspaceInventory` 是单次 `ProjectLoader.load` 内存活、不可变、非 Pydantic 的 planning value；
   它不进入领域 Schema、report、Journal、identity、cache key 或终端展示。
2. `ProjectDiscovery` 保留两个用途明确的入口：`select(...)` 只供离线 location/report lookup；
   `inventory(...)` 只供在线 `ProjectLoader` 构造完整 planning inventory。不得用 mode flag 合并两者。
3. `ProjectLoader.load(root, selector) -> ProjectPlan` 的 caller-facing interface 不变；内部只调用一次
   `ProjectDiscovery.inventory`，不再组合 `select + owned_pyproject_paths + loader rereads`。
4. `select` 与 `inventory` 必须复用同一个私有 package catalog implementation，并以现行 `select`
   的 installable/name/duplicate/selector 语义为准；inventory 不复制第三套 glob/name 规则。
   Inventory 先完成 catalog/selector 判定，再对合法 target 建立完整 member/owned-path facts；未知
   selector 不因 planning-only metadata 抢先失败。
5. 每个纳入 inventory 的 `pyproject.toml` 在该 inventory 中只形成一份 byte observation 和一份 TOML
   parse。ProjectLoader、ConfigLoader 与 recursive path traversal 复用其不可变 parsed document；读取
   bytes 是 inventory 构造时的实现细节，不进入 planning interface。
6. Inventory 的 planning-internal interface 暴露 selected location、root/target observations、owned
   paths 与按 canonical name 查询的 member facts。它不提供任意 `document(path)`、documents collection、
   raw bytes 或 members collection，也不暴露给 workflow。
7. `ConfigLoader` 继续独占三层 PF config 解析、校验与合并，但改为消费 inventory 中的 root/target
   observation；它不再自行读取 filesystem。ConfigLoader 的 merge 行为仍直接通过该 observation seam
   测试，不强制穿过完整 ProjectLoader planning。
8. `ProjectLoader` 继续独占 requirement、Cell、source route、member-version attachment 与 harness
   planning；`WorkspaceInventory` 只返回事实，不判断 managed/searchable 或构造 `SourcePlan`。
9. `ProjectPlan` 形状保持 `target + owned_pyproject_paths`；不保存 inventory、raw bytes、TOML tree 或
   新 identity。
10. `SnapshotBuilder` 在 planning 后独立重读 filesystem 并产生权威 `SourceSnapshot` 是有意的第二次
    观察，不计入 inventory 内部重读，也不由本设计缓存或短路。
11. Filesystem 是 local-substitutable dependency；discovery/inventory 使用真实临时目录测试，ConfigLoader
    作为 in-process module 直接使用 observations 测试。不增加 filesystem Protocol、repository、adapter、
    watcher、daemon 或跨 invocation cache。
12. PF 尚未发布，接受后原地替换旧内部 interface；`owned_pyproject_paths()`、loader/config 的重复
    `_read` 路径和 identity-drift 补丁测试在同一迁移中删除，不保留 alias。

## 3. 目标 interface

### 3.1 Discovery 与 inventory

```text
# offline location/report lookup；现有 caller-facing 语义不变
ProjectDiscovery.select(
    root: Path,
    selector: RootPackage | WorkspacePackage,
) -> PackageLocation

# online planning 的唯一 workspace observation 入口
ProjectDiscovery.inventory(
    root: Path,
    selector: RootPackage | WorkspacePackage,
) -> WorkspaceInventory

WorkspaceInventory.target -> PackageLocation
WorkspaceInventory.root_observation -> PyprojectObservation
WorkspaceInventory.target_observation -> PyprojectObservation
WorkspaceInventory.owned_pyproject_paths -> tuple[str, ...]
WorkspaceInventory.workspace_member_for(canonical_name)
    -> WorkspaceMemberFact | None

PyprojectObservation
    path: canonical absolute Path
    document: recursively immutable TOML mapping

WorkspaceMemberFact
    name: canonical distribution name
    locator: canonical root-relative POSIX package path
    version: StaticWorkspaceMemberVersion | DynamicWorkspaceMemberVersion
```

`WorkspaceInventory.target` 已经是 selector 的唯一结果，`ProjectLoader` 不再重新比较 target name。
`root_observation.path` 始终是 root `pyproject.toml`；`target_observation.path` 必须等于
`target.pyproject_path`。target 是 root 时，两项必须引用同一个 `PyprojectObservation` 实例；消费方仍按
canonical path 相等判断 root-target 语义，不把 object identity 当作配置规则。

`PyprojectObservation` 是 `ProjectDiscovery`、`ProjectLoader` 与 `ConfigLoader` 之间固定的
planning-internal seam，不进入 workflow、Schema 或 report。`document` 必须递归不可变；只冻结顶层 mapping
而保留嵌套可变 `dict/list` 不合格。具体冻结容器是 implementation detail。读取 bytes 可以在 parse 后
丢弃；observation 不携带 raw bytes、digest 或 snapshot identity。

`workspace_member_for` 要求 canonical name；未知 name 返回 `None`。Facts 与 owned paths 使用稳定排序，
不增加 members list/dict，避免 caller 建立第二份 name/path map。Inventory 也不提供任意 path 的 document
查询；root/target 之外的 parsed observations 只由 inventory implementation 用于 member/owned-path 投影。

### 3.2 ProjectLoader 与 ConfigLoader

```text
# caller-facing interface 保持不变
ProjectLoader.load(root, selector) -> ProjectPlan

# implementation flow
inventory = discovery.inventory(root, selector)
root_observation = inventory.root_observation
target_observation = inventory.target_observation
config = config_loader.load(
    root_observation=root_observation,
    target_observation=target_observation,
)
package = plan_target(
    location=inventory.target,
    root_document=root_observation.document,
    target_document=target_observation.document,
    workspace_member_for=inventory.workspace_member_for,
    config=config,
)
return ProjectPlan(
    target=package,
    owned_pyproject_paths=inventory.owned_pyproject_paths,
)
```

`ConfigLoader.load` 的旧 `(root: Path, package: Path)` filesystem interface 被上述 observation interface
直接替换，不保留第二个 path-based 入口。ConfigLoader tests 直接构造不可变 observations，集中覆盖三层
merge、校验与默认值；`ProjectLoader.load` 的集成测试另行证明 root/target observations 来自同一
inventory，且 ConfigLoader 与 ProjectLoader 均不重读 filesystem。

target 是 root 时，ConfigLoader 不执行 package-only `[tool.pf]` 校验，并继续按 root config → matching
root package override → 同一 root package config 的次序合并；最后一层仍可覆盖 matching override。
这保留现行 root-target 行为，不因 observation interface 改成两个逻辑独立的配置层。

`ProjectLoader` 的 target planning 继续读取 observation 中完整 `[project]`、`dependency-groups`、
`tool.pf`、`tool.uv.sources` 与 index facts。Inventory 不预解析 Requirement、marker、config 或 harness；
否则只是把 ProjectLoader implementation 搬进一个更宽 record。

## 4. Observation 构造

`ProjectDiscovery.select` 与 `ProjectDiscovery.inventory` 必须调用同一个私有 catalog implementation；
不得分别复制 workspace glob、exclude、installable name、canonical uniqueness 或 selector 规则。Catalog
以现行 `select` 行为为规范：缺少 `[project]` 或 `project.name` 的 candidate 不可安装并跳过；已经声明
但类型错误或为空的 `project.name` 是 `ConfigurationError`；不同路径的 canonical name 重复无条件失败，
不得吸收旧 `_workspace_members` 的弱检查。

共享 catalog 按以下固定阶段构造：

1. `root.resolve()` 一次，要求 root `pyproject.toml` 可读并形成唯一 root observation；
2. 从该 observation 校验 selection-owned legacy fields，读取 `tool.uv.workspace.members/exclude`；
3. 展开 glob，对每个结果先 `resolve()` 并验证仍在 root 内；exclude 只抑制 member glob inclusion，
   即使 pattern 匹配 root，也不得移除 root observation；
4. workspace glob 匹配但缺少 `pyproject.toml` 的目录不成为 candidate；root 与每个非排除 candidate 的
   现有 metadata 各读取、解析一次，并从同一 document 投影 installable package identity；
5. 校验 canonical name 唯一，按 D001/D017 选择 root 或显式 member；选择失败立即返回现行
   `ConfigurationError` 与候选列表，不继续 member version、path closure 或 target planning validation。

`ProjectDiscovery.inventory(root, selector)` 在共享 catalog 成功选择 target 后继续：

6. 对全部 installable、非排除 workspace package（包括未选中的 member）从同一 observations 投影
   static/dynamic version facts；virtual root 与其它 non-installable metadata 不做 member version 校验；
7. 以 root pyproject 和全部 installable、非排除 workspace package pyprojects 为 owned-path 起点；root
   无论是否 installable、是否被 exclude pattern 匹配，都始终进入 owned paths；
8. 从已观察 document 的 `tool.uv.sources.*.path` 递归发现 metadata：先规范化 path 并验证仍在 root
   内，再定位 `pyproject.toml`；合法 in-tree path 缺少该文件时跳过，越界 path 即使文件不存在也失败；
9. 已存在的 path pyproject 只观察一次并进入 owned paths，再从同一 document 继续 closure；重复/cycle
   去重。同一路径若已经是 root/workspace member，复用原 observation；
10. 仅由 path source 到达且不属于非排除 workspace package 的 metadata 无论是否 installable 都进入
    owned paths，但不成为 selector candidate，不产生 member version，也不进入 `workspace_member_for`；
11. 对 owned path 形成规范 root-relative POSIX locator，返回排序、唯一的
    `owned_pyproject_paths` 与冻结 inventory；此后所有 query 只读已有 observation，不访问 filesystem。

因此，未选中但未 exclude 的 installable workspace member 仍进入 owned paths；excluded member 不因
workspace glob 进入，只有随后从某个 owned document 的合法 in-tree path source 可达时才以 path package
身份进入。这一规则在实施归并时取代 D002 中“包括未选中/排除 member”的含混表述，不改变 D017 的
selector、snapshot 或未选中 dependency-array identity 语义。

## 5. Offline discovery 保持轻量

`ProjectDiscovery.select` 与 `inventory` 必须共享 §4 的 package catalog implementation；一次 offline
invocation 只执行 catalog 阶段：

- 读取 root 和 workspace candidate identity；
- 校验 workspace selection metadata、installable name、canonical name uniqueness 与 selector；
- 返回一个 `PackageLocation`。

它不读取/校验 member version、target dependency、PF evaluation config、recursive path source、Cell、
harness 或 owned paths。`ExplainCommandWorkflow` 与 `DiagnoseCommandWorkflow` interface 和调用顺序不变，
仍不创建 `ProjectLoader`、`SnapshotBuilder`、uv、environment 或 process 能力。

不在 `WorkspaceInventory` 上增加 lazy loading：lazy query 会让 query 顺序改变 I/O、错误时机与对象
语义，也无法提供一份完成时冻结的 observation。

## 6. Planning、snapshot 与 drift

Inventory 只保证 **一次 `ProjectLoader.load` 内部** 的 observation 一致性：target identity、member
version、source classification input 与 owned path set 来自相同 bytes。它不声称 filesystem 在
planning 后不变。

在线错误时序按新 interface 明确定义为：共享 catalog 与 selector → 全量 member-version/owned-path
inventory validation → ConfigLoader merge 与 target planning。现行实现是 target planning 完成后才计算
owned paths；本设计接受这项时序变化，不保证多个同时存在的 planning error 仍按旧先后出现。稳定保证是
typed `ConfigurationError`、unknown/root selector 的候选列表与“selector 先于 planning-only validation”；
离线 `select` 的失败面不变。

`SnapshotBuilder.build(root, owned_pyproject_paths=...)` 继续独占：

- Git/non-Git source discovery 与路径/symlink安全；
- ordinary blob、`PyprojectIdentity` 与 type-tagged canonical TOML encoding；
- `SourceSnapshot.identity`、immutable staging、materialize 与 cleanup。

SnapshotBuilder 对 owned pyprojects 的再次读取是执行 evidence observation，必须保留。Search 结束 drift
check、ApplyAuthorizer 的 report/current snapshot 比较与 ProjectEditor raw CAS 均不改变。Inventory 不把
bytes 交给 SnapshotBuilder、不计算或缓存 `PyprojectIdentity`，也不增加 `inventory_id` 与 snapshot
identity 竞争。

本设计不建立 planning 与 snapshot 之间的 filesystem transaction。若后续发现这段时间窗造成真实
正确性问题，必须另立 Design；不能借 WorkspaceInventory 暗中扩大 SourceSnapshot authority。

## 7. Error 与生命周期

- root/member/path containment、malformed workspace metadata、invalid/duplicate name、invalid member
  version 与 invalid path-source metadata 继续形成 typed `ConfigurationError`；合法 in-tree source path
  缺少 `pyproject.toml` 继续跳过，越界 path 继续先于文件存在性检查失败；
- CLI value-shape error 与 project selection/configuration error 的 D001 分层不变；
- unknown selector、root non-package 与 duplicate canonical name 在 member version、path closure、config
  与 target planning 前决定，保持稳定候选列表与恢复提示；selector 成功后 inventory validation 可以
  先于 target dependency/config/Cell/harness error 失败；
- `package identity changed during project loading` 路径删除：target planning 使用 inventory 已选中的同一
  observation，该不一致不再可表示；不保留枚举旧竞态的 negative test；
- Inventory 构造失败不返回 partial object；成功对象不持有 file descriptor、临时目录或 cleanup
  lifecycle；
- Inventory 只在一个 `ProjectLoader.load` 调用内存活，不进入 `CliContext`，不在多个 command 或
  repeated load 之间复用。

本设计不要求保留所有旧 error 文本的逐字内容。测试断言 typed error、稳定语义片段、候选与安全结果，
不快照 volatile parser/OS 文案。

## 8. Ownership

| Owner | 本设计后的唯一职责 | 明确不吸收 |
| --- | --- | --- |
| `ProjectDiscovery.select` | offline package catalog、selector 与 `PackageLocation` | version、owned paths、planning、snapshot |
| `ProjectDiscovery.inventory` | 共享 catalog 后的一次 filesystem observation、member facts、recursive owned paths | config merge、Requirement/Cell/source classification |
| `WorkspaceInventory` | selected location、root/target observations、member lookup 与 owned paths | I/O after construction、任意 document/members collection、identity、wire、evaluation |
| `ConfigLoader` | 在 observation seam 上合并并校验三层 PF config | filesystem I/O、selection、workspace traversal |
| `ProjectLoader` | target config/declaration/Cell/source route/harness planning 与 `ProjectPlan` | 重读 filesystem、snapshot identity、evaluation |
| `SnapshotBuilder` | source membership、blob/PyprojectIdentity、immutable source evidence | workspace selection、member/source classification |
| `ApplyAuthorizer` / `ProjectEditor` | current snapshot authorization；raw CAS、transaction 与 rollback | inventory 构造或 planning observation authority |
| `ReportStore` / D014 | SourceSnapshot wire/identity、report validation/merge/write | inventory serialization 或 current project discovery |

`WorkspaceInventory` 是 local-substitutable filesystem module，不定义 port。`ProjectDiscovery` 是唯一
filesystem read owner 仅限 **planning observation**；SnapshotBuilder 和 ProjectEditor 的证据/CAS 读取是
不同 ownership，不构成重复 planning owner。

## 9. Interface 原地替换

| 当前形状 | 目标形状 |
| --- | --- |
| online/offline 都调用 `ProjectDiscovery.select` | offline 保留 `select`；online 只调用 `inventory` |
| `ProjectLoader.load` 调用 `select` 后再调用 `owned_pyproject_paths` | `load` 只消费一个 inventory |
| `ProjectDiscovery.owned_pyproject_paths(root)` public method | 删除；使用 `inventory.owned_pyproject_paths` |
| `ProjectLoader._read(target/root)` | 删除；消费 inventory 的 target/root observations |
| `ProjectLoader._workspace_members(root, root_document)` | 删除；消费 `inventory.workspace_member_for` |
| `ConfigLoader.load(root: Path, package: Path)` 并自行读取 | `load(root_observation, target_observation)`；直接测试该 seam |
| target name 二次读取后比较 identity | target 与 planning 来自同一 observation；比较与错误删除 |
| discovery/loader/config 各自 TOML helper | planning filesystem read/parse 只留在 discovery inventory implementation |
| discovery 与 loader 各自解释 workspace catalog | `select` / `inventory` 强制复用同一私有 catalog implementation |

Workflow、`ProjectPlan`、`PackagePlan`、`SourcePlan`、`SnapshotBuilder`、authorizer、editor 与 report 的
caller-facing interface 不因本设计增加字段。实施期间若临时需要桥接，只能存在于未提交切片；交付时
不得保留旧方法、alias、fallback 或双读测试。

## 10. 测试策略

Filesystem 有成熟的 `tmp_path` stand-in，本设计不增加 adapter。测试通过各 owner 的实际 interface：
discovery/loader/workflow 使用 caller-facing seam，ConfigLoader 使用 planning-internal observation seam：

1. `ProjectDiscovery.select` 覆盖 root/member selection、virtual root、include/exclude、unknown/duplicate
   canonical name、invalid-present name 与 legacy selection config；
2. `ProjectDiscovery.inventory` 覆盖 target、member facts、owned path closure、path cycle/duplicate、
   root escape、missing in-tree path metadata、root/exclude 交互、path-only invalid version 与
   static/dynamic workspace version；inventory 返回后修改磁盘，原对象保持旧 facts/document，下一次
   `inventory(...)` 才观察新内容；
3. `ConfigLoader.load(root_observation, target_observation)` 直接覆盖三层 merge、默认值、校验、递归不可变
   document 与 root-target 同 path 语义；这些测试不构造 Cell、source 或 harness；
4. `ProjectLoader.load` 覆盖 config/declaration/Cell/source/harness outcome。一个 discovery witness 在完成
   inventory 后、将其返回给 loader 前修改磁盘；最终 `ProjectPlan` 必须完整使用旧 root/target identity、
   declaration 与 member version，证明 ConfigLoader/ProjectLoader 没有重新 open path；
5. `ExplainCommandWorkflow` / `DiagnoseCommandWorkflow` 证明无 planning-only metadata validation、无
   SnapshotBuilder/uv/process 调用；
6. `SnapshotBuilder` / authorization tests 继续证明 owned paths、PyprojectIdentity、source drift、
   dependency-array drift 与 raw CAS，不把 inventory 当作 evidence。

“每个 metadata 最多一份 byte observation 与 TOML parse”通过上述冻结/mutation witness、唯一 catalog/read
ownership 静态扫描和旧 read 路径删除共同验收；不 patch `Path.open`、不按 `glob/is_file/resolve/stat`
syscall 次数计数，也不直接断言私有 `_read` helper。

旧 `DriftingDiscovery.select` identity mismatch test 删除；它验证的是被目标 interface 消除的中间状态。
其它现行安全/错误行为测试保留或迁移，不新增测试来枚举已删除 method 或旧调用顺序。

Focused suites 至少覆盖：

```text
tests/test_project.py
tests/test_config.py
tests/test_snapshot.py
tests/test_report_workflows.py
tests/test_diagnose.py
tests/test_authorization.py
tests/test_editor.py
tests/test_cli.py
```

## 11. 非目标

- 不改变 `--package` grammar、root/member selector、单 target 或 report location；
- 不把 excluded/non-installable/path-only package 变成 selector candidate；
- 不改变 config precedence、dependency/source classification、workspace member apply 规则或 Cell；
- 不把 `ProjectDiscovery` 与 `ProjectLoader` 合并成一个拥有 evaluation 的 module；
- 不让 offline command 构造 full inventory 或 `ProjectPlan`；
- 不让 inventory 替代 `SourceSnapshot`、PyprojectIdentity、search drift check、authorization 或 CAS；
- 不给 inventory 增加任意 document query、documents collection 或 workspace members list/dict；
- 不缓存跨 invocation project state，不引入 watcher、repository、filesystem port、DI framework 或 daemon；
- 不顺带实施 R005 的 Verification Run、评价 seam、result-card 或 SearchCoordinator 改进；
- 不因内部重构改变 Schema 1、报告 generation、SourcePlan/Attempt/Failure identity 或生成物。

## 12. 验收标准

1. `ProjectDiscovery.inventory(root, selector) -> WorkspaceInventory` 是在线 planning 的唯一 workspace
   observation 入口；一次 `ProjectLoader.load` 只构造一个 inventory，且 `ProjectLoader.load` 的
   caller-facing interface 保持不变。
2. `select` 与 `inventory` 强制复用同一私有 catalog implementation，并以现行 discovery 的
   installable/name/duplicate/selector 语义为准；RootPackage/WorkspacePackage、virtual root、
   include/exclude、unknown/duplicate canonical name 的 typed error 与候选列表语义不变。
3. 一次成功 inventory 中，每个 root/workspace/path `pyproject.toml` 最多形成一份 byte observation 与
   TOML parse；其 planning interface 仅增加 canonical-path + recursively immutable-document 的
   root/target observations，并保留 target、owned paths 与 member point query；无 raw bytes、任意
   document/members collection、digest、wire、cache 或 cleanup lifecycle。
4. Root 始终进入 owned paths；未选中但未 exclude 的 installable workspace member 仍进入；excluded
   member 不因 glob 进入，只有经合法 in-tree path source 可达时才以 path package 身份进入。
5. Static/dynamic workspace member version 从 inventory 唯一产生；ProjectLoader 不重读 member TOML。
   仅由 path source 到达的非 workspace metadata 不做 version validation，也不成为 selector/member fact；
   SourcePlan route metadata、workspace lookup failure 与 apply 语义不变。
6. Recursive in-tree path closure 排序、唯一、cycle-safe 且不可越过 root；合法 in-tree source path 缺少
   `pyproject.toml` 时跳过，越界 path 即使 metadata 不存在也形成 typed `ConfigurationError`。
7. `ConfigLoader.load(root_observation, target_observation)` 不读取 filesystem，其三层 merge/validation
   直接在 observation seam 上测试；root target 复用同一 observation 并保留现行合并语义。
   `ProjectLoader.load` 集成测试证明 ConfigLoader/ProjectLoader 消费 inventory observations；ProjectLoader
   继续唯一拥有 declaration、Cell、source route 与 harness planning。
8. `ProjectDiscovery.owned_pyproject_paths`、ProjectLoader/ConfigLoader 重复 read、`_workspace_members` 与
   package identity 二次比较全部删除；无 alias、compatibility adapter 或双读路径。
9. Offline explain/diagnose 仍只使用轻量 `select`，不因未选中 package 的 version/dependency/config/path
   planning 问题扩大失败面，也不创建 snapshot、uv、environment 或 process 能力。Online 在 selector
   成功后按 inventory validation → config/target planning 排序；不保证与旧 load 相同的错误先后。
10. `ProjectPlan`、PackagePlan、SourcePlan、Schema 1 与 report JSON 不增加 inventory/raw TOML/identity；
    SnapshotBuilder 仍独占 PyprojectIdentity 与 SourceSnapshot evidence。
11. 新测试通过 discovery/inventory、ConfigLoader observation、ProjectLoader 与 workflow seams 覆盖
    selection、安全、冻结 mutation、root-target config、online error order 与 offline 行为；精确读取
    ownership 由 mutation witness、静态扫描和旧路径删除证明，不 patch syscall/private helper。旧
    shallow/obsolete tests 删除，不断言 volatile 完整错误文本。
12. 对应 Plan 记录 focused tests、Ruff、ty、Python 3.10 coverage/full suite、顺序 Python 3.11/3.12 full
    suites、build、生成物 no-drift、links、diff 与静态旧路径扫描的精确结果；实现完成后将新 interface
    与 ownership 归并到 D002，并把 owned-path 表述改为 §4 的 root/unselected/excluded/path-only 规则；
    核对 D001/D008/D014 并只在契约确实受影响时修订，更新 R005/索引，再将 D020 与 Plan 同变更归档。

## 13. 实施工作流

本设计的接受语义是：以本文 §2–§12 作为唯一迁移目标，由 [P026](../plans/P026-pf-workspace-inventory.md)
把 12 条验收标准逐项映射到有序切片、owner migration、测试与证据槽。实施发现需要改变 CLI、Schema、
snapshot authority 或 offline failure surface 时，先修订并重新接受 Design，不在 Plan 或代码中暗改范围。

# PF 单 target CLI 与 workspace 直接依赖

- **状态：** 已归档（迁移完成，现行规则已归并）
- **日期：** 2026-08-29
- **完成日期：** 2026-08-29
- **现行产品契约：** [D001](../../designs/D001-pf.md)
- **现行实现结构：** [D002](../../designs/D002-pf-implementation.md)
- **搜索算法：** [D003](../../designs/D003-pf-search-algorithm.md)
- **失败语义：** [D005](../../designs/D005-pf-failure-and-diagnose.md)
- **终端展示：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **运行语义：** [D008](../../designs/D008-pf-verification-run.md)
- **解析边界：** [D012](../../designs/D012-pf-harness-relaxation.md)
- **报告 wire：** [D014](../../designs/D014-pf-report-schema.md)

本文保存“单 target CLI”与“workspace-backed 直接依赖搜索”的迁移理由和验收历史，不再拥有
现行规则。稳定规则已分别吸收到上述现行 Design；实施过程和最终证据见
[P023](../plans/P023-pf-single-target-workspace-dependencies.md)。

PF 尚未上线，本次迁移只保留一个最新契约：实现、领域 interface、CLI、report wire、文档与
测试都直接替换现行形状，不提供旧行为兼容、deprecated alias、migrator、dual-read 或
dual-write。

## 1. 问题

现行 CLI 把可选位置参数 `package` 同时用作 package 名、目录、`pyproject.toml` 路径和批量
选择条件；省略时还可能展开多个 package。由此产生的多 package 调度、多报告写入和命令级
聚合，不符合 PF 的实际搜索与报告 authority：搜索算法在一个 package 的直接依赖向量内运行，
一份 `package-floor.json` 也只属于一个 package。

现行 source 分类还把全部 workspace source 视为 fixed。它适合验证当前 monorepo checkout，
却无法回答以下合法发布关系的最低版本问题：

```toml
[project]
name = "carda"
dependencies = [
  "carda-core>=1.0",
  "carda-runtime>=2.0",
]

[tool.uv.sources]
carda-core = { workspace = true }
carda-runtime = { workspace = true }
```

这里 `carda-core` 与 `carda-runtime` 是 `carda` 的直接发布依赖，也是各自独立版本、独立发布的
distribution。PF 应能搜索它们的 registry releases，但不能因此递归搜索成员自身声明的依赖。

## 2. 决策摘要

1. 每次 package-scoped CLI invocation 恰好选择一个 target package。
2. `smoke | check | search | explain | apply | minimize | diagnose` 删除 package 位置参数。
3. 省略 selector 时选择 workspace root 的 `[project]`；`--package PACKAGE` 按规范化
   distribution name 选择一个可安装 workspace package。
4. 不提供隐式 all-packages、`--workspace`、`-ws`、package path 或 `pyproject.toml` selector。
5. `merge REPORT...` 继续接收报告位置参数；它不是 package selection interface。
6. PF 只从 target 的 `project.dependencies` 与 `project.optional-dependencies` 建立直接依赖
   declarations；workspace membership 本身不产生依赖或搜索坐标。workspace-backed declaration
   与普通 registry declaration 使用同一套 `managed-deps` / `unmanaged-deps` 选择规则。
7. workspace-backed managed coordinate 的 development source 是本地 workspace member，search
   source 是忽略该名称的 workspace override 之后、现行 index policy 唯一允许的 registry route。
   workspace checkout 的版本和源码都不是候选或 floor evidence。
8. PF 只搜索 target 自己的受管直接依赖；成员的直接或传递依赖只由 uv 解析，不成为 PF
   coordinate、CandidateSnapshot 或 floor。
9. `PackagePlan` 保存逐 dependency 的 `DependencySourceRoute`；Verification Run 携带
   `ResolutionSourceMode`。candidates、harness 与 prepare 只消费该 mode 下的有效 source；
   `UvAdapter` 在 SEARCH mode 使用逐 package `--no-sources-package`。
10. Apply 仍是一条 workspace 级冻结授权：只编辑 target 的 dependency arrays，owned
    pyproject 与 snapshot CAS 保持 workspace 范围。
11. 动态版本 workspace member 可以参与验证与搜索，但 PF v1 的离线 apply 不授权其 registry
    floor，并给出可操作的 CLI 原因。

## 3. CLI interface

目标命令表面为：

```text
pf smoke   [--package PACKAGE] [--jobs auto|N]
pf check   [--package PACKAGE] [--jobs auto|N]
pf search  [--package PACKAGE] [--jobs auto|N] [--max-duration DURATION]
pf explain [--package PACKAGE]
pf apply   [--package PACKAGE] [--force]
pf minimize [--package PACKAGE] [--jobs auto|N] [--max-duration DURATION]
pf diagnose [--package PACKAGE] [--failure FAILURE_ID]
pf merge REPORT [REPORT ...] --output PATH
```

v1 只提供长选项 `--package`，不增加短别名。`--workspace` 在 uv 语境中通常表达整个 workspace，
与本设计的单 target 含义冲突，因此不复用该名称。

### 3.1 Target selection

```text
TargetSelector = RootPackage | WorkspacePackage(canonical_name)
```

- 省略 `--package` 时，workspace root 必须含可安装的 `[project]`，该 root package 是 target。
- `--package` 只接受规范化后唯一匹配的 distribution name；可以显式选择可安装 root，也可以
  选择一个可安装 member。
- selector 的候选集合来自既有项目根下的 uv workspace discovery；in-tree path dependency 若
  不是 workspace package，不能通过 `--package` 变成 target。
- 本设计不新增从任意子目录向上查找 workspace root 的语义；项目根仍由现行 root discovery
  决定。
- root 没有 `[project]` 且省略 selector 时是配置错误：列出可选 package，并提示使用
  `--package PACKAGE`。不得回退为“处理所有成员”。
- `--package` 的值若不是 distribution name 形状，是 CLI 调用错误。形状合法但没有匹配 package
  时是 project selection 配置错误，展示稳定排序的候选；规范化名称重复也是配置错误。

选择完成后，workflow 不再接收 package collection。一个顶层命令只有一个 target、一个命令级
outcome 和一条 final summary；`search` 至多更新一份报告，`explain` 至多读取一份报告。

调用与项目错误保持分层：

| 类别 | 示例 | 退出码 / 展示 |
| --- | --- | --- |
| CLI grammar / value shape | 未知 command/option、缺少 option value、额外 positional、非 distribution-name 的 `--package` 值 | `1`；stderr；带 Usage |
| project selection / configuration | 未知 canonical package、重复 canonical name、root 无可安装 `[project]`、遗留 selection 字段、不合格 source route | `3`；stderr；不带 Usage |

测试只证明最新 CLI grammar、合法 selector 和上述现行错误分类。迁移时可以临时验证旧实现入口已
消失，但交付测试不枚举旧 CLI 形状或为其建立兼容性契约。

### 3.2 配置选择迁移

- 删除 root-only `[tool.pf].packages` 与 `exclude-packages`；配置不再隐式选择 target 集合。
- 保留 `[tool.pf.package.<canonical-name>]` 作为 workspace root 集中维护成员配置的 patch，
  但它只在 CLI 已选择 target 后参与合并。
- 删除 package patch 的 `path` selection 作用；成员 identity/path 只来自 workspace discovery。
- 配置中出现 `packages`、`exclude-packages` 或 `[tool.pf.package.*].path` 是配置错误：指出
  字段并提示改用 `--package`。不得忽略，不得当作 selection 过滤器。
- root patch、选中 package patch 与选中成员 `[tool.pf]` 的优先级和列表替换规则保持不变。

归并进 D001 时，workspace-backed declaration 与普通 registry declaration 共享 managed 选择：

```text
searchable = target 的 project.dependencies / project.optional-dependencies
             中满足现行 specifier/marker 规则，且具有唯一 search source 的 declarations

managed-deps 存在     managed = searchable ∩ managed-deps
unmanaged-deps 存在   managed = searchable - unmanaged-deps
均不存在              managed = searchable

不在 target dependency declarations 中的 workspace member 永不进入 searchable。
仍为 fixed 的依赖列入 managed-deps 是配置错误：
== / === / ~= / URL / Git / path，以及不合格 workspace source。
```

## 4. 单 target planning 与 ownership

Planning interface 收敛为：

```text
ProjectDiscovery.select(root, selector) -> PackageLocation
ProjectLoader.load(root, selector) -> ProjectPlan

ProjectPlan
  target: PackagePlan
  owned_pyproject_paths

PackagePlan
  ...现行 identity / config / declarations / cells / harness...
  source_routes: tuple[DependencySourceRoute, ...]

DependencySourceRoute
  dependency
  development_source: SourceIdentity
  search_source: SourceIdentity
  workspace_member_version:
      StaticWorkspaceMemberVersion(value) | DynamicWorkspaceMemberVersion | None

ResolutionSourceMode = DEVELOPMENT | SEARCH

VerificationRun
  package
  source_mode: ResolutionSourceMode

ApplyAuthorizer.authorize(report, project, current_snapshot, force)
    -> AuthorizedWorkspaceApply
         package_apply: AuthorizedPackageApply
ProjectEditor.apply(authorization, root)
```

`ProjectPlan` 不再保存 `packages: tuple[...]`。`owned_pyproject_paths` 仍覆盖 root、workspace members
与递归 in-tree path packages，因为 snapshot、source identity 和 apply drift 检查仍需观察这些
文件；被观察不等于成为 target 或搜索坐标。

`ProjectLoader` 是 source-route 规则的唯一 owner：它按 §5 分类每条 declaration，写出
`source_routes`，并导出 `kind` / `managed`。`RequirementDeclaration` 不再用单一
`source.kind` 表示「能否搜索」。candidates、harness 与 `EnvironmentFactory.prepare` 只消费
`(source_routes, source_mode)` 投影出的有效 `SourceIdentity`；它们不得读取原始
`tool.uv.sources` 或按 `source.kind != registry` 自行判断 workspace-backed managed
coordinate。

对 workspace source，`ProjectLoader` 还把对应 member 的 PEP 621 version metadata 规范化为
`StaticWorkspaceMemberVersion(value)` 或 `DynamicWorkspaceMemberVersion`；非 workspace route 为
`None`。该字段只服务于 §8 的离线 apply 安全检查，不成为 candidate、floor 或 compatibility
evidence。下游不得再次读取 member `pyproject.toml` 推导版本类型。

非双 source 的 declaration 两条 route 相同（普通 registry 两边都是 registry；fixed/unmanaged
workspace 两边都是 workspace）。Run 的 mode 选择有效侧。一次 Run 的 SourcePlan identity 是
`(source_routes, source_mode)` 的规范摘要，不是去重后的 `SourceIdentity` 集合。

Apply 只可编辑 target package 的 dependency arrays。授权对象仍是一条 workspace grant：
它直接保存单数 `package_apply`；`owned_pyproject_paths`、未选中 member 的 dependency-array
identity 与 raw CAS 保持现行 workspace 范围。未选中 workspace/path package 不会进入第二份
package 授权或第二份 dependency-array 写入。

## 5. Workspace-backed 受管直接依赖

一个 declaration 只有同时满足下列条件，才是 searchable workspace-backed direct dependency：

1. 它来自 target 的 `project.dependencies` 或 `project.optional-dependencies`，即相对于 target
   是 direct dependency；
2. `tool.uv.sources` 把同名 distribution 指向唯一 workspace member；
3. requirement 仍符合现行 searchable specifier/marker 规则；`==`、`===`、`~=`、direct URL、
   Git 与 path 继续 fixed；
4. 忽略该名称的 workspace development override 之后，现行 SourcePlan index policy 恰好留下
   一条唯一、安全的 registry route。该 route 通常即项目默认 index；不得另猜私有 index 或
   第二来源。剩余 route 若歧义、含凭据、非 HTTPS，或不被现行 registry policy 接受，则是
   配置错误，不启动 Verification Run；
5. `tool.uv.sources` 对该 distribution 只有 PF v1 支持的单一、无 marker source。uv 支持按
   marker 配置多个 source，但 PF v1 主动拒绝 list/多 source，不为各 Cell 建立 source 分支。

searchable workspace-backed dependency 按 §3.2 的统一规则决定 managed：省略两个 selector 时
默认 managed；`managed-deps` / `unmanaged-deps` 只做同一 searchable 集合上的选择。被选择为
managed 但 search route 不合格时是配置错误，不能静默退回本地 source；fixed/unmanaged
workspace dependency 的两条有效 route 都保持 development source。

### 5.1 双 source role

```text
DEVELOPMENT  验证当前 checkout：有效 source = development_source
SEARCH       验证受管直接依赖：有效 source = search_source
```

- `DEVELOPMENT` 保留 `tool.uv.sources` 的 workspace override。
- `SEARCH` 只对受管 workspace-backed direct dependencies 使用 registry search route；fixed/
  unmanaged source route 原样保留。切换一个 direct coordinate 不递归创建新的
  PF coordinates。
- 相同名称和版本在 workspace 与 registry 中不是同一 selection；registry graph 必须绑定实际
  选择的 artifact locator/hash。

Source mode、逐 dependency route 和有效 registry policy 都进入 Attempt、resolution、
CandidateSnapshot、Evaluation 与 report generation identity。

`SourceSnapshot` 仍从项目根复制完整源码树；每个 Attempt 再把它物化成独立、可写的 Proposal
root。target 是 workspace member 时，root `pyproject.toml` 与 snapshot 纳入的其它 members 仍在
Proposal 中，只有 uv 的 package working directory 指向所选 member。SEARCH 只改变解析该
Proposal 时的 `UvAdapter` argv，不修改 Proposal source tables、操作者 checkout 或不可变 staged
snapshot。

SEARCH mode 的 uv source projection 由 D012 的 `UvAdapter` 独占。它从规范化
`(source_routes, source_mode)` 推导按 canonical dependency name 排序、去重的 suppression
集合：只包含 development source 为 workspace、search source 为 registry 的受管直接依赖。
`DEVELOPMENT` 或空集合不增加参数；`SEARCH` 对 project 与 environment 两次 `uv pip compile`
传入相同的重复参数：

```text
--no-sources-package <dependency-1>
--no-sources-package <dependency-2>
```

suppression 集合是 route/mode 的 adapter 投影，不是第二份配置、report authority 或可由调用者
覆盖的 interface。PF 必须从子进程环境移除 `UV_NO_SOURCES`、`UV_NO_SOURCES_PACKAGE` 与其它
外部 source-selection 注入，只允许自身构造的 argv 决定逐 package suppression。

Candidate query 直接消费每条 route 的 registry `search_source`，不依赖该 uv 参数。两次
resolution 必须使用相同 suppression 集合；installation 只消费已经校验的最终 native plan，
不得重新开放 source selection；graph inspection 复证受管坐标没有 workspace/editable leakage。
PF 不得为了切换 source 而编辑 Proposal 中 root 或 member 的 `[tool.uv.sources]`，也不得把 source
declaration provenance 暴露到上层 interface。Proposal 对 dependency vector 的现行物化不受此
限制。

全局 `--no-sources`、`UV_NO_SOURCES` 以及会禁用未受管 path/git/workspace route 的整表
suppression 禁止使用。实现前必须在固定 uv 版本上资格化 root source、member source、两者同名
source precedence、一个/多个受管 suppression、未受管 source 保留、candidate query、两阶段
resolution、native plan、installation 和 graph inspection；未资格化 fail closed，该类
coordinate 不可用。

### 5.2 候选与解析

- Search highest baseline 是 requirement 允许的最高合格 registry release，不是 workspace
  member 当前版本。
- CandidateSnapshot 只包含 registry search source 的精确 releases 与 artifact evidence；本地
  member 的版本、tag、commit 或构建产物不插入候选序列，也不充当 baseline sentinel。
- candidate discovery 与 exact probe 必须消费同一 registry search route。registry 不可访问、
  package/version/artifact 不可用或 source identity 不明确时为 Indeterminate；不得改用
  workspace source继续搜索。
- `ResolveProject(P)` 与最终 environment graph 必须证明每个 workspace-backed managed
  coordinate 选择了请求的 registry version/source/artifact。这些 coordinate 上的
  editable/path/workspace leakage 使该 Attempt 无法建立 Proposal。未受管 workspace/path
  source 仍应以其 development identity 出现在 graph 中。
- 成员 artifact metadata 引入的 dependencies，以及任何其它 transitive distributions，只由
  uv 正常求解。它们可以出现在 resolution graph，但不建立 PF catalog、coordinate、
  CandidateSnapshot、boundary 或 floor。

PF coordinate 只来自 target 的 project/optional dependency declaration，HarnessRequirement
永不成为搜索坐标。同名 distribution 若同时出现在 test group，它仍是 D012 独立拥有的 harness
declaration；其 fixed/relaxable/ceiling 分类必须使用本次 Run 的有效 source，与 uv 实际解析
选择一致，但不会产生第二个 coordinate 或 floor。D003 Slice 绑定 source snapshot、source mode
与 route；同一版本的 workspace HEAD 与 registry artifact 不是同一证据。这只扩展“哪些 target
direct declarations 可以成为 D003 坐标”，不改变坐标下降、runtime promotion、非单调检测或
终止算法，也不改变 D012 的 two resolutions / one install。

## 6. Command source semantics

| 命令 | Managed workspace dependency source | 含义 |
| --- | --- | --- |
| `smoke` | development/workspace | 验证当前 monorepo checkout 的最新可解析开发环境 |
| `check` | search/registry | 验证受管直接依赖的 highest capture 与 declared lower bounds |
| `search` | search/registry | 建立 registry candidate floor evidence |
| `minimize` | search 使用 registry route；apply 保留 development override | 搜索后写回 target requirement |
| `explain` / `apply` / `diagnose` | 不解析环境 | 离线消费所选 target 的 report/current identity |

被选择的 target 自身始终是待验证源码，不因 `--package` 变成 dependency candidate。例如选择
`carda-core` 时，PF 搜索的是 `carda-core` 自己的受管直接依赖；选择 `carda` 时，受管的
`carda-core` 才是 `carda` 的一个 coordinate。受管 workspace-backed coordinate 没有可用
registry release 时，`check`/`search` 为 Indeterminate，不能改用 checkout 继续。fixed/unmanaged
workspace dependency 仍是报告上下文中的本地固定 source，因此 SEARCH mode 不承诺验证整个
项目脱离全部 development sources 后可发布。

## 7. Report、explain 与 merge

- 报告路径继续位于 target package 现行位置；一次 `search` 只有一个 `ValidatedReport` update。
- `explain --package X` 和 `diagnose --package X` 只定位 X 的 report/Journal，不扫描或汇总其它
  members。
- `merge` 继续接收多个 report 文件，但 D014 的 package identity 兼容规则不变；它不能把
  不同 package 合成一份报告。
- D014 在实施时原地替换 Schema 1 的唯一 wire 形状。generation 预映像必须包含
  `ResolutionSourceMode` 与逐 dependency `DependencySourceRoute`；Reader 复证 registry artifact
  evidence 没有 workspace leakage。
- 现行实现的 Schema 1 `SourcePlan.identities` 去重集合不能表达 mode 与逐 dependency route，
  因而不再是可读布局。Reader/Writer 只实现修改后的最新 Schema 1；现有报告全部失效，用户
  重新运行 `pf search`。不保留旧 reader、migrator、alias、dual-read 或 dual-write，JSON Schema
  与 examples 由同一 wire model 直接替换。

终端展示保持 D006 的一个顶层命令一条 final summary。删除命令级多 target 汇总：多份报告
路径、按 package 计数的聚合和多 package artifact 列表。D006 的
`selected N cells, P active packages (F pinned)` 是所选 Cell 内 direct dependency 计数，
不是 workspace target 集合，予以保留。`explain` 仍只陈述 report intrinsic evidence，不把
development smoke 或当前 checkout 状态描述成 apply-time authorization。

## 8. Apply projection

Workspace-backed managed dependency 的 floor 仍写回 target 的标准 PEP 508 requirement：

- 只替换现行授权范围内的 lower bound；保留上界、排除项、extras、marker、location 与其它
  declaration semantics；
- 保留 target 的 `[tool.uv.sources]` workspace override；它是 development source，不是要被
  floor 替换的发布 metadata；
- 不编辑 dependency member 的版本或 `pyproject.toml`，也不根据 member 的内部 dependencies
  生成约束；
- member 的静态 PEP 621 `[project].version` 若存在，必须是可解析的 PEP 440 并满足 intended
  requirement，否则 apply 阻止。该检查只保护写回后的 development resolution，不提供候选或
  floor evidence；
- workspace-backed managed dependency 的 member 若使用动态版本，apply 按 §8.1 阻止；
- source route、target declaration 或任一 owned member identity 漂移继续按 D001 的结构化
  identity 规则阻止，不能由 `--force` waiver。

### 8.1 动态 member version

静态版本直接写在 member 的 `[project].version`。动态版本则省略该字段并在
`[project].dynamic` 中声明 `"version"`，最终值由 build backend 从源码、Git tag 或其它
backend-specific 配置生成。只读 TOML 无法取得后者的可信值。

PF v1 保持 apply 完全离线且 fail closed：动态版本 member 可以参与 `smoke`、`check` 与
`search`，但 `ApplyAuthorizer` 不得授权其 registry floor，也不得运行 build backend、metadata
hook 或 Git version provider 猜测当前 member version。该限制只针对被写入 floor 的
workspace-backed dependency member；target package 自身使用动态版本不因此阻止它更新其它
dependency requirements。

CLI 将该结果作为 apply authorization/configuration error 输出到 stderr，退出 `3` 且不展示
Usage。错误必须给出 dependency/member 名称、intended requirement，以及以下原因和恢复动作；
不得只显示 `dynamic version`、建议 `--force`，或把 report evidence 描述成无效：

```text
Cannot apply <requirement>: workspace member <name> declares its version dynamically.
PF cannot verify offline that the local member satisfies the intended requirement.
Next: apply the requirement manually and run pf smoke, or declare a static [project].version.
```

授权失败发生在任何文件编辑之前，`--force` 不可 waiver。用户手工修改并运行 `pf smoke` 只是
独立验证当前 development checkout，不为原报告补造 apply authorization。

## 9. Failure semantics

- root 不可安装、selector 不唯一、遗留 `packages` / `exclude-packages` / package `path`、
  managed search source 不合格或 registry route 不唯一，属于
  configuration/selection failure，不启动 Verification Run。
- registry/source/artifact/tool failure 为 Indeterminate，不得构造 Rejection。
- exact graph 在 workspace-backed managed coordinate 上选择 workspace/editable artifact、
  请求版本/source不闭合或无法复证 artifact 时为 prepare Indeterminate，不建立 Proposal。
- 普通 resolver contradiction 只有满足 D012 的 qualification profile 才能成为 Rejection。
- member dependency 的失败只属于当前 target Attempt；PF 不把它提升为该 member 的独立报告。
- 静态 member version 不能满足 intended requirement，或 workspace-backed managed member 使用
  动态版本时，apply 授权失败；两者均发生在文件编辑前且不可由 `--force` waiver。

FailureRecord 投影固定为：

| 检测点 | Scope | Cause @ stage | Authority 与 plan digests |
| --- | --- | --- | --- |
| registry candidate query 失败 | Cell | `SOURCE_FAILURE @ candidate-discovery` | stable structured detail；无新增 plan digest |
| uv source/artifact/metadata operation 失败 | Attempt | D012 的 `SOURCE_FAILURE \| BUILD_FAILURE \| TOOL_FAILURE @ resolve-project \| resolve-environment` | 原 ProcessObservation；只保存失败前已取得的 plan digests |
| project plan 中 managed workspace coordinate 仍为 local/editable，或 registry version/source/artifact 与 request 不闭合 | Attempt | `INTERNAL_INVARIANT @ resolve-project` | structured `managed-source-leakage` / `managed-source-mismatch`；保存 project plan digest |
| environment plan 没有精确保留 project selection | Attempt | `INTERNAL_INVARIANT @ resolve-environment` | structured `managed-source-mismatch`；保存 project 与 environment plan digests |
| installed graph 与最终 plan/managed vector 不一致 | Attempt | `INTERNAL_INVARIANT @ inspect-environment-plan` | inspection ProcessObservation；保存两个 plan digests |

表中全部结果都是 Indeterminate。结构化 detail 只保存稳定 code/message，不保存 locator、动态
stderr 或凭据；D005 按完整 scope、cause、stage、authority 与已有 digests 生成 Failure ID。

## 10. 非目标

- `--all-packages`、workspace 批处理、递归 member invocation 或一个命令写多份报告；
- 接受 package 路径、glob、`pyproject.toml` 路径或任意目录作为 selector；
- 搜索 target 的 transitive dependencies、成员自己的直接依赖或 workspace 的版本组合；
- 把 workspace HEAD、member version、Git tag 或本地 wheel 当作 registry release evidence；
- 自动发布、构建或比较 workspace member artifact；
- 支持同一 distribution 的多个或 marker-conditional `tool.uv.sources`；
- 用全局 `--no-sources` 实现 SEARCH mode；
- 兼容旧 report 布局，或把 workspace identity 解释为 registry search evidence；
- registry 失败时回退到 local source；
- 改变 D003 的搜索算法、D005 的 disposition authority 或 D012 的 resolver ownership；
- 让 `smoke` 的 development PASS 授权 registry floor。

## 11. 迁移与验收

实施必须以一个显式 Plan 完成下列行为和 evidence；不能只修改 CLI parser：

1. 所有 package-scoped help/handler/request 都只有 `--package` selector，并选择恰好一个
   target；public CLI tests 以合法 root/member invocation 正向证明新 grammar，不保留旧 CLI
   形状的拒绝测试。
2. installable root 默认、non-package root、合法 member、未知/重复 canonical name 均有真实
   workspace discovery tests；CLI grammar error 为 `1 + Usage`，project selection/config error 为
   `3 + no Usage`。
3. ProjectPlan 为单 target；VerificationRun 携带一个 package 与 `source_mode`；report
   update、explain/diagnose 为单数。Apply 仍返回 `AuthorizedWorkspaceApply`，
   只保存单数 `package_apply`，owned path CAS 保留。一次 invocation 从 workflow 到
   terminal 没有多 package aggregation。
4. `packages`、`exclude-packages` 与 package patch `path` 出现即为配置错误；集中 package
   patch 不能改变 target selection。
5. target project/optional dependencies 中的 searchable workspace-backed declaration 使用与普通
   registry declaration 相同的 managed/unmanaged 规则；workspace membership 不创建 declaration。
   smoke 选择 local source，check/search baseline 与 probes 对 managed coordinate 选择 registry
   artifact；resolution graph 与 report identity 对 `(source_routes, source_mode)` 闭合。
6. fixed/unmanaged workspace dependency 仍为 development source；无 registry candidate、source
   failure 和 managed coordinate 上的 local leakage 都 fail closed，且按 §9 形成完整 evidence，
   不误分为兼容性 Rejection。
7. member 自身依赖只参与 uv resolution；测试证明它们不会出现在 CandidateSnapshot、坐标、
   boundary 或 projection 中。
8. apply 只修改 target requirement，保留 `[tool.uv.sources]`，不修改 member metadata；对
   静态 PEP 621 member version、source route、report generation 与 owned pyproject drift
   fail closed。动态 member 可以 smoke/check/search；apply 的 public CLI test 证明它在任何编辑前
   以 `3 + stderr + no Usage` 失败，输出 dependency/member、intended requirement、离线验证限制与
   恢复动作，不建议 `--force`。
9. 固定 uv 版本上，`UvAdapter` 对两次 resolution 从 route/mode 生成相同、排序去重的重复
   `--no-sources-package` 参数；资格测试覆盖 root/member/equal source declarations、一个/多个受管
   suppression、未受管 source 保留、candidate、两阶段 resolution、installation 与 artifact
   inspection。生产实现不得为 source mode 编辑 Proposal `tool.uv.sources`，不得接受外部
   `UV_NO_SOURCES_PACKAGE`，且全局 `--no-sources` 不得出现在生产 argv。
10. 当前 CLI 生成的 diagnose/apply 等建议命令全部使用 `--package`。CLI、领域、报告、真实临时
    workspace 与发布工件测试均通过；旧批处理测试和文案被删除。修改后的 Schema 1 是唯一
    writer/reader/schema/example 形状，不保留旧布局 fixture 或兼容测试。

实现完成时，必须在同一变更中完成契约归并：

| 现行 owner | 吸收内容 |
| --- | --- |
| D001 | 单 target 产品/命令/配置、统一 direct managed 范围、selection/config error 与 apply 规则 |
| D002 | selector、单 PackagePlan、`source_routes` 与 member version classification owner、VerificationRun.source_mode、单 package workspace grant |
| D003 | direct-coordinate 与 source-bound Slice 不变量；算法本身不改 |
| D005 | registry search source/graph failure 的 disposition、authority 与 plan-digest 投影 |
| D006 | `--package` help/建议命令、调用与配置错误、动态版本 apply reason/action、单 report/singular summary；保留 Cell 级 active/pinned 计数 |
| D008 | 单 package Verification Run、source_mode、Journal 与命令终态 |
| D012 | source mode、有效 source、逐 package suppression 资格化与 exact graph/artifact 复证 |
| D014 | 最新 Schema 1、route/mode generation identity 与唯一 reader/writer/generated artifacts |

归并、实现和验证完成后，将 D017 与实施 Plan 移入归档并更新文档索引。归档后的 D017 只保留
迁移理由与验收历史，不再拥有任何现行条款。

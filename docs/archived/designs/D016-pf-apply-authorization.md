# PF Apply 授权与平台作用域

- **状态：** 已归档（已归并）
- **最后核对：** 2026-08-29
- **当前产品契约：** [D001](../../designs/D001-pf.md)
- **当前模块边界：** [D002](../../designs/D002-pf-implementation.md)
- **当前终端契约：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **当前报告 wire：** [D014](../../designs/D014-pf-report-schema.md)

本文保留已实施的apply授权决策来源，不再拥有现行条款。产品、模块、终端和报告wire契约已经分别归并到D001、D002、D006与D014；以下内容作为设计与验收历史保留。

## 1. 决策

公开 CLI 只增加 `--force`：

```text
pf apply [package]
pf apply [package] --force
```

不增加 `--platform`、`--partial`、`--ignore-source` 或可组合 waiver flags。用户不能挑选 Cell 或伪造覆盖。

核心规则：

1. Apply scope 由**当前平台声明**与**报告证据**共同决定，不由 `--force` 决定。
2. 省略 `platform` 或只显式声明一个 uv triple 时，写入声明矩阵，不生成平台 marker；报告范围外 OS 的兼容性属于项目部署语义。
3. 显式声明覆盖**多个 ApplySelector**，且其中存在 MissingSelector 时，默认 `PLATFORM_SCOPED`：按 Cell 意图重投影整组 requirement——已授权 selector 写 floor，未授权 selector 在那些 Cell 上保持 original 的有效约束。gnu+musl 是同一 selector：只搜 gnu 且无失败时走 `DECLARED_MATRIX`，不写平台补集。EXPLICIT_MULTI 指多个 uv triple，不等于多个 ApplySelector。
4. GNU 与 musl 是同一 ApplySelector 的编译环境变体。一个完整 libc triple 的唯一 floor 可以外推到同 selector 的未搜索 libc 变体；多个已观察变体 floor 不同则不可表示。
5. `--force` 只 waiver 结构化 identity 检查后剩余的 source-layer drift；不改变 scope，不跳过失败证据、缺 Cell、依赖漂移或不可表示 projection。
6. `pyproject.toml` 分别记录 remainder 与 dependency-array identity。选中 package 的依赖只允许授权状态迁移；未选中 workspace/path package 的依赖必须精确保持。
7. Apply 后增量验证开始新 generation，不跨 generation merge 或继承 evidence。

## 2. 平台授权

### 2.1 平台声明与证据

`PlatformDeclaration` 只看当前 `EffectiveConfig.platform`，不看 apply 时宿主：

```text
OMITTED          空元组；search 时按宿主推断 Cells
EXPLICIT_SINGLE  恰好一个规范 uv triple
EXPLICIT_MULTI   两个或更多规范 uv triples
```

报告术语：

- **TargetPlatform**：`report.inputs.target_cells` 中的规范 uv target。
- **EvidencePlatform**：至少有一个最终 `CellResult` root 的 TargetPlatform。
- **PlatformComplete(platform)**：该平台声明的每个 Python minor × extra surface 都恰有一个最终 `CellSuccess` root。
- **ObservedSearchSuccess**：至少有一个 EvidencePlatform；每个 EvidencePlatform 都 PlatformComplete；全部已有 roots 都是 `CellSuccess`。

候选搜索中的 `REJECTED` 是边界证据，不是最终 Cell failure；授权只看最终 `cell_results` roots。

### 2.2 ApplySelector

Search 按完整 uv triple 执行；apply 按 PEP 508 可表达维度分组：

```text
ApplySelector = (sys_platform, platform_machine)
```

同一 selector 中：

- **SelectorAuthorized**：至少一个 TargetPlatform 是 PlatformComplete；其它 TargetPlatform 要么完全没有 root，要么也 PlatformComplete；全部已有 roots 都成功。
- **MissingSelector**：声明集合中的 selector 没有任何 PlatformComplete。
- **MissingCellWithinSelector**：某个 TargetPlatform 已有 root，但未完整覆盖，或存在非成功 root。

MissingSelector 只允许 `EXPLICIT_MULTI` 自动 scoped apply；MissingCellWithinSelector 在默认和 force 下都阻止。

GNU 与 musl 映射到同一 selector。libc 不进入 marker：

- `EXPLICIT_SINGLE` 的声明一致性仍按 uv triple 精确比较，gnu 报告不能替代 musl 声明；
- 当前声明与报告 triple 集合一致后，同一 `(python_minor, extra_surface, ApplySelector)` 中已观察到的唯一 floor 展开到该 selector 的全部 TargetCells；
- 若两个已观察 libc 变体在同一坐标得到不同 floor，projection 不可表示。

### 2.3 ApplyScope

```text
ApplyScope = DECLARED_MATRIX | PLATFORM_SCOPED
```

| 当前声明 | 必要条件 | scope |
| --- | --- | --- |
| `OMITTED` | 报告恰好一个 TargetPlatform；ObservedSearchSuccess | `DECLARED_MATRIX` |
| `EXPLICIT_SINGLE` | TargetPlatforms 与声明的单一 triple 相等；PlatformComplete | `DECLARED_MATRIX` |
| `EXPLICIT_MULTI` | TargetPlatforms 与声明集合相等；ObservedSearchSuccess | 全部 selectors 授权时 `DECLARED_MATRIX`，否则 `PLATFORM_SCOPED` |

显式声明与报告的 uv triple 集合不一致一律阻止，`--force` 不能放宽。`OMITTED` 不比较 apply 宿主；配置仍省略 platform 且其它 identity 匹配时，可以在另一台机器 apply 该单平台报告。

`DECLARED_MATRIX` 不为未声明 OS 生成补集。`PLATFORM_SCOPED` 只为已授权 selector 生成平台条件；MissingSelector 在对应 Cell 上与 original 的有效约束等价，终端展示为 preserved/unverified。补集是已授权 selector 的 De Morgan，不按未搜平台逐条展开，见 §4。

## 3. 依赖与 source identity

### 3.1 PyprojectIdentity

SourceSnapshot 路径成员不变。`ProjectPlan` 拥有的 root、workspace 与递归 path package 元数据使用 `PyprojectIdentity`；其它文件（包括不属于项目计划的 `pyproject.toml`）继续使用 blob identity：

```text
remainder = parsed TOML 去掉 project.dependencies
                         与 project.optional-dependencies
dependency_arrays = {
    project.dependencies,
    project.optional-dependencies,
}

PyprojectIdentity = (path, mode, remainder_digest, dependency_arrays_digest)
```

`dependency_arrays` 保留两个字段是否存在；缺失字段不补为空数组或空表。完整 SourceSnapshot digest 同时绑定普通 entries 与全部 `PyprojectIdentity`。digest 预映像前缀与现行 `pf:snapshot:v1` 不同（由 D014 定名）；缺 `PyprojectIdentity` 的旧报告 reader fail closed，不能 apply。合法 apply 会改变选中 package 的 `dependency_arrays_digest`、完整 snapshot 和 generation，但这是授权状态迁移，不登记 source waiver。

`[dependency-groups]`、`[project.scripts]`、`requires-python`、`[tool.pf]`、`[tool.uv]`、`[build-system]` 及其它键都属于 remainder。注释、空白和排版不进入 parsed identity。

### 3.2 Canonical TOML encoding

摘要只要求确定、无歧义、类型保真的 canonical bytes，不要求使用 JSON。v1 复用 `canonical_identity_json`：先把 TOML 值转为 type-tagged tree，再做 canonical JSON 编码。

```text
table             ["table", [[sorted_key, tagged(value)], ...]]
array             ["array", [tagged(value), ...]]
string/bool/int   [type_tag, canonical_scalar]
float             ["float", finite hex | inf | -inf | nan]
offset datetime   ["offset-datetime", canonical ISO 8601]
local datetime    ["local-datetime", canonical ISO 8601]
local date/time   ["local-date" | "local-time", canonical ISO 8601]
```

Array 顺序保留，table key 排序；finite float 保留 `-0.0`，所有 NaN 归一为同一 token。不得把 `tomli` 返回的原生 Python 对象直接交给 `json.dumps`。

```text
sha256("pf:pyproject-remainder:v1\0" + canonical_identity_json(tagged(remainder)))
sha256("pf:pyproject-dependencies:v1\0" + canonical_identity_json(tagged(dependency_arrays)))
```

`SnapshotBuilder` 独占该编码和 digest 算法，并从 `ProjectPlan` 接收 owned pyproject paths；search、apply 与事务前置检查复用同一实现。

### 3.3 Dependency state

选中 package 的依赖按规范化语义比较，不按整份 TOML 字符串或 search-time `raw` 比较：

```text
DependencyGroupKey = (pyproject_path, location, optional_group | None, canonical_name)
```

一个 key 可以包含多个 marker declarations；该组是无序 multiset。每个 declaration 规范化为：

```text
RequirementSemantics
    requested_extras
    specifier_set
    marker_identity
    source_identity
```

`requested_extras` 与 specifier set 规范排序；marker identity 使用规范化 AST，并同时绑定全部报告 TargetCells 上的 activation partition，因此空白变化不漂移，报告矩阵外的 marker 语义也不会被静默丢弃。location/group/name 由 key 固定。managed/fixed ownership、SourcePlan 与 policy 另做不可 waiver 的 identity 检查。

对受管 declaration，每个 TargetCell 的 intended 约束是：ApplySelector 已授权则把 original 的 lower-bound 换成该坐标的精确 `>=floor`，否则等于该 Cell 上 original 的有效约束。上界、排除项、requested extras、source、位置与原 marker 语义保留。固定和非受管 declaration 完整保留。`intended` 的 group map 是 `PackageReportBuilder.project` 按这张 Cell 图**重生成的整组** requirement，不是 original 行与新 floor 行拼接。

依赖状态是全部 group maps 的整体比较：

```text
current   当前 DependencyGroupKey → declaration multiset
original  报告输入的 group map
intended  project() 重投影的整组结果

WRITABLE  current == original
NOOP      current == intended
DRIFTED   其它状态，包括既不等于 original 也不等于 intended，或 key 增删
```

base 与 optional group 的同名声明在共同活跃时仍按 D001 求交。数组内无语义影响的排列不构成 selected-package drift。`project.dynamic` 提供依赖时仍按 D001 失败。

未由本次 reports 拥有的 pyproject dependency-array identity 必须与报告 snapshot 精确相等。这样 A 的报告不能在 workspace/path package B 的依赖已变化时 apply。

### 3.4 `--force`

`--force` 只在其它默认授权条件成立时 waiver：

```text
SOURCE_SNAPSHOT_DRIFT
```

先复算不可 waiver 的结构化 identity，再判断剩余 source-layer drift：

| 当前变化 | 主导判定 | `--force` |
| --- | --- | --- |
| 普通源码、测试、文档或其它 source path | blob drift | 可 waiver |
| `[project.scripts]`、`[dependency-groups]`、普通 `[tool.*]` 或其它无独立 identity 的键 | remainder drift | 可 waiver |
| `test-command`、Python/platform/extra policy | policy / target identity 不匹配 | 阻止 |
| `requires-python` | package / target semantics 不匹配 | 阻止 |
| `[tool.uv.sources]`、index 或 declaration source | SourcePlan / declaration identity 不匹配 | 阻止 |
| 选中 package 的 PEP 621 依赖数组 | Dependency state | 只接受 WRITABLE/NOOP |
| 未选中 workspace/path package 的 PEP 621 依赖数组 | dependency-array identity drift | 阻止 |

source-layer 比对（结构化 identity 通过之后）按下面做，避免合法 apply / NOOP 被当成 drift：

- 选中 package 的 `dependency_arrays_digest` **不参与** source drift，只走 Dependency state（WRITABLE/NOOP）；
- 该 pyproject 的 `path`/`mode`/`remainder_digest` 仍必须与报告 snapshot 相等，否则 remainder drift；
- 未选中 owned pyproject 的完整 `PyprojectIdentity` 必须与报告 snapshot 精确相等；
- 其余路径比 blob digest。

`expected_snapshot` 是这次比对用的当前身份（选中包的 dependency-array 可以是 original 或 intended）。同一改动命中多类时，不可 waiver 的 identity 优先。`--force` 不改变报告 result、identity、scope 或证据归属。

分界不取决于配置写在哪个 TOML section，而取决于是否已有独立、不可 waiver 的结构化 identity。源码、scripts 与 dependency groups 只由 SourceSnapshot 绑定，可与普通 source drift 一并 waiver；test command、target/policy、package semantics 与 dependency source 另有当前授权 identity，必须阻止。

Source waiver 只展示 changed path 数量和最多 8 条脱敏后的规范相对路径；不输出内容、diff、digest、未脱敏路径或凭据。

## 4. Projection

D014 的 report projections 只证明 complete target coverage。Incomplete report 的空 projection 不授权 apply；`ApplyAuthorizer` 从 final `CellSuccess` 与报告 declarations 请求一次 apply-time projection。

`PackageReportBuilder.project` 是 Cell→PEP 508 projection 与重求值的唯一 owner。它按 Cell 意图**重生成整组** requirement，不把 original 行与新 floor 行拼接：

1. 按 `(python_minor, extra_surface, ApplySelector)` 收集唯一 ExactFloor；同 selector 未搜索 libc Cell 继承该值。
2. 每个 TargetCell 的 intended 约束：已授权 selector 为该坐标 floor，MissingSelector 为 original 在该 Cell 上的有效约束。
3. `DECLARED_MATRIX` 投影全部 TargetCells；`PLATFORM_SCOPED` 为已授权 selector 生成正选条件，并为 MissingSelector 生成一条补集（若还有未授权 selector）。
4. Floor 使用精确 `>=version`，保留上界、排除项、requested extras、source、位置与原 marker 语义。已有 marker 与 selector 做布尔交集；不生成 `extra == ...` marker。
5. 在全部报告 TargetCells 上重求值：已授权 selector 必须命中预期 floor，未授权 selector 必须等于 original 的有效约束，同名共同活跃不得重叠。

PEP 508 没有 otherwise，也没有一元 `not`。无 marker 的兜底行会在已授权平台上重叠，禁止。补集是已授权 selector 的 De Morgan，**不**按 MissingSelector 逐条展开；未搜平台自然落入补集。补集长度随已授权 selector 增加，不随声明了但未搜的平台增加。规范化器拥有空白、括号与条件顺序，语义必须与 De Morgan 等价。

不可精确表示时默认和 force 都阻止；不得用 host 猜测、更宽 marker 或 requirement 顺序掩盖冲突。

省略 platform 时不生成平台 marker：

```text
before    httpx<1
after     httpx>=0.27.2,<1
```

显式声明 Linux、Windows、macOS，只有 Linux 有证据：

```text
httpx>=0.27,<1 ; sys_platform == "linux" and platform_machine == "x86_64"
httpx<1        ; sys_platform != "linux" or platform_machine != "x86_64"
```

Windows 与 macOS 共用补集。随后在新 generation 搜完 Windows 再 apply，整组重投影（旧补集删除，否则 Windows 重叠）：

```text
httpx>=0.27,<1 ; sys_platform == "linux" and platform_machine == "x86_64"
httpx>=0.28,<1 ; sys_platform == "win32" and platform_machine == "AMD64"
httpx<1        ; (sys_platform != "linux" or platform_machine != "x86_64")
             and (sys_platform != "win32" or platform_machine != "AMD64")
```

三个 selector 都有 floor 之后补集消失。顺序 apply 即使 floor 相同也保留分 selector 的行；要写成一条无条件 floor，必须在同一次 dependency-array identity 上搜齐再 merge，一次 `DECLARED_MATRIX` apply。

## 5. Module interface 与事务

```text
ApplyAuthorizer.authorize(
    reports: tuple[ValidatedReport, ...],
    project: ProjectPlan,
    current_snapshot: SourceSnapshot,
    force: bool,
) -> AuthorizedWorkspaceApply
```

返回值是一次 workspace 事务的 strict/frozen 授权；任一 package 失败就不返回该对象，也不开始写入。

```text
AuthorizedWorkspaceApply
    mode                  DEFAULT | FORCE
    waivers_used          SOURCE_SNAPSHOT_DRIFT | ()
    expected_snapshot     authorize 时的当前 source-layer 身份（选中包 dependency-array 按 §3.4 排除或取 original/intended）
    package_applies       AuthorizedPackageApply 列表
    presentation_facts

AuthorizedPackageApply
    package
    scope                 DECLARED_MATRIX | PLATFORM_SCOPED
    declared_platforms    显式 uv triples；OMITTED 为空
    selected_selectors
    preserved_selectors
    dependency_state      WRITABLE | NOOP
    authorized_edits      AuthorizedProjectEdit 列表

AuthorizedProjectEdit
    pyproject_path
    expected_pyproject_identity
    group_edits           DependencyGroupKey → replacement requirements
```

`ApplyCommandWorkflow` 先取得整份授权。`ProjectEditor` 在事务准备时按 §3.4 复算 source-layer `expected_snapshot`，按 `AuthorizedProjectEdit` 验证目标语义并渲染；内部 prepared edit 记录本次读取的原始 bytes digest。替换每个 pyproject 前以该 raw digest 做 compare-and-swap，目标文件的并发改动在覆盖前失败。随后执行写后验证、原子替换及现行恢复日志/回滚。`group_edits` 是该 key 的整组 replacement，不是在旧补集后追加行。

ProjectEditor 不读取 report internals、不重新推导授权、不按 search-time `raw` 查找。`presentation_facts` 只含 scope、selected/preserved selectors、waiver 与有界 changed-path facts，不含渲染文案。

| Owner | 唯一负责 | 不负责 |
| --- | --- | --- |
| `PackageReportBuilder.project` | Cell 子集 → PEP 508 projection 与重求值 | TOML I/O、授权、waiver |
| `ApplyAuthorizer` | 前置条件、平台/scope、Dependency state、source drift、waiver、authorized edits | TOML I/O、终端措辞、wire join |
| `SnapshotBuilder` | 路径成员、普通 blob、PyprojectIdentity、canonical TOML encoding | apply 授权、PEP 508 求值 |
| `ProjectEditor` | snapshot/semantic 前置比较、raw CAS、group edits、写后验证、恢复与回滚 | 授权、report internals、search-time raw 匹配 |
| `ApplyCommandWorkflow` | planning/snapshot/report → authorize → workspace transaction | 复制授权规则或打印 |
| `ReportStore` | D014 reader/merge/write | 根据 force 改 result/projection |
| `TerminalPresenter` | presentation facts → D006 输出 | 决定授权 |

## 6. Report、merge 与增量验证

- Report wire 不保存 force、waiver、scope 或 apply history；它们是 apply-time facts。
- D014 必须让 SourceSnapshot identity 承载 `PyprojectIdentity`。缺该字段的报告 fail closed。digest 预映像前缀与现行 `pf:snapshot:v1` 不同；v1 未发布，不做旧文件迁移。
- `pf explain` 仍然离线：它可以描述 report-intrinsic MissingSelector，但只能说“若当前声明仍匹配，默认 apply 将 scoped”，不能判断当前树或 force 是否授权。
- `pf merge` 只合并 identity 兼容的同 generation reports；不接受 `--force`。
- `pf minimize` 只复用默认 apply；MissingSelector 可 scoped，失败或局部缺 Cell 仍停止。

Apply 后增量验证不做 report rebase：

1. scoped apply 改变 dependency-array identity、完整 snapshot 与 generation；
2. 下一平台从更新后的项目 search，已有 selector floor 成为新 generation 的 original；
3. 新 report 只授权本 generation 已观察 selector。旧 selector 在那些 Cell 上保持当前 TOML 的有效约束（已有 floor），MissingSelector 继续走补集；`project()` 重生成整组，不拼接上一次的补集行。终端对未在本 generation 认证的 selector 说 preserved/unverified。
4. 新 report 不与 apply 前 report merge，也不继承旧 evidence。

因此可以顺序执行 Linux search/apply → Windows search/apply。最终 TOML 可以包含不同 generation 分别授权的 selector floors，但每次操作只声明本 generation 的证据。

若需要一份全部 selectors complete 的报告，各平台必须基于同一个当前 dependency-array identity 搜索，并在下一次 apply 前 merge。

## 7. CLI 事实

无 waiver 的成功走 stdout、`✓`、退出 `0`：

```text
✓ Applied floors · 1 project updated
✓ Applied floors · 1 project updated · platform-scoped to linux/x86_64 · preserved windows/x86_64
```

实际使用 source waiver 时，warning/report 走 stderr，final summary 使用 `⚠`，退出 `0`：

```text
evidence  6/6 observed cells passed · linux/x86_64
preserved windows/x86_64, macos/arm64
waived    source drift (31 paths)
⚠ Applied floors with operator override · 1 project updated
```

- 没有 final `CellSuccess`、因而没有 applicable floor：退出 `2`。
- 有 applicable floor，但 ObservedSearchSuccess 不成立或其它授权条件失败：退出 `3`。
- CLI 用法错误：退出 `1`。
- 不输出完整 JSON、digest、全部 path、候选失败历史或重复 search summary。

具体词句、颜色、TTY/non-TTY 降级和宽度适配由 D006 归并。

## 8. 不变量与非目标

不变量：

1. 没有 final PASS Proposal 的 Cell 不能产生新 floor；未搜索 libc 只继承同 selector 的唯一 observed floor。
2. `--force` 不改变 scope，也不把失败、indeterminate、局部缺 Cell 或 MissingSelector 解释为成功。
3. MissingSelector 在对应 Cell 上保持 original 的有效约束；终端说 preserved/unverified，不说 passed。补集是已授权 selector 的 De Morgan，不是逐平台 otherwise。
4. `DECLARED_MATRIX` 不添加未声明 OS 的平台补集。
5. Dependency drift 与未选中 workspace/path package dependency-array drift 不可 waiver。
6. 每次新写 floor 都可追溯到该 report generation、同一 ApplySelector 的 `CellSuccess`；preserved floor 不冒充本 generation evidence。
7. Workspace 授权、事务和回滚 all-or-nothing；ProjectEditor 对 prepared raw content 做 compare-and-swap；重复 apply 幂等。
8. Apply 后开始新 generation；跨 generation evidence 不 merge。

非目标：

- 手工挑选 platform、Python minor、extra 或单个 Cell；
- 对失败/indeterminate evidence 做尽量 apply；
- 通过 force 跳过 schema、policy、dependency 或 projection safety；
- 在 PEP 508 中表达 libc、otherwise/一元 `not`，或用无 marker 兜底行冒充补集；
- 为未声明 OS 生成保护补集（`DECLARED_MATRIX` 本来就不写补集）；
- 跨 generation merge/rebase evidence，或写 TOML lineage/provenance；
- 修改搜索算法、候选资格或 runtime authority；
- 在 report wire 中记录 apply-time facts；
- 改变 SourceSnapshot 路径成员集合；
- 把 force 或 scoped apply 表述为新的兼容性认证。

## 9. 归并现行契约

落地同一变更必须同步修改：

| 所有者 | 归并内容 |
| --- | --- |
| D001 §4 | PyprojectIdentity、完整 snapshot identity 与路径成员不变规则 |
| D001 §5/§6 | CLI `--force`、ObservedSearchSuccess、ApplySelector authority、scope、dependency/source authorization |
| D001 §8/§9 | 退出码与删除“禁止 incomplete partial apply”旧非目标 |
| D002 | workspace/package authorization records、authorized/prepared edit 边界、ProjectEditor/SnapshotBuilder/PackageReportBuilder 职责 |
| D006 | scoped/preserved facts、source waiver 通道、explain 条件式授权措辞 |
| D014 | SourceSnapshot 承载 PyprojectIdentity；新 digest 预映像前缀；缺字段 fail closed；同 generation merge；incomplete 空 projection 仍不授权 apply |

D016 归并后移入归档。

## 10. 资格场景

资格必须覆盖 public CLI 与真实 TOML round-trip；只测 authorizer helper 不算完成。

平台与证据：

- OMITTED、EXPLICIT_SINGLE 写入无平台 marker；single triple 不一致时默认和 force 都阻止。
- EXPLICIT_MULTI 全部 ApplySelector 授权时 `DECLARED_MATRIX`（含 gnu+musl 只搜 gnu 且无失败，无平台补集）；存在 MissingSelector 时默认 `PLATFORM_SCOPED`。
- gnu 完整、musl 无 root 时外推唯一 selector floor；两者 observed floor 不同则不可表示。
- MissingCellWithinSelector、任一非成功 root、non-monotonic 都阻止；历史候选 `REJECTED` 不误判为最终失败。

依赖与 source：

- 合法 apply 改选中包 dependency-array 与 generation，但不登记 source waiver；NOOP 重复 apply 不必 `--force`。
- scripts、dependency-groups 或普通 source path drift 可 force；test-command、requires-python、platform/policy、tool.uv.sources 不可 force。
- 固定 pin、requested extras、source、marker partition、保留 specifier 或 unmanaged group 增删都判为 DRIFTED；数组无语义重排可写。
- 未选中 workspace/path package 依赖变化在默认和 force 下都阻止。
- Canonical encoding 覆盖 datetime/date/time、inf/nan 与 `-0.0`，相同 parsed TOML 得到稳定 digest。
- 缺 PyprojectIdentity 的报告不能 apply。

Projection、增量与事务：

- 已有 marker、optional group、多 Python minors、不同 floors 与 De Morgan 补集重求值准确；无 marker 兜底行必须拒绝重叠。
- Linux scoped apply 后可在新 generation 做 Windows search/apply：整组重投影，旧补集删除，Linux floor 保留，Windows 写入新 floor；不与 apply 前 report merge。
- 三平台只缺 macOS 时补集只否定已授权 selector，不出现 `darwin` 字面量。
- Complete report 只由相同 dependency-array identity 的跨平台 reports 在 apply 前 merge。
- Authorize 后、事务 snapshot 检查前的 source drift 会失败；目标 pyproject 在 prepared read 后发生 raw drift 也会在覆盖前失败。
- 多 package 任一 blocker、写入异常或恢复异常保持 all-or-nothing/rollback。
- TTY/non-TTY 都只有一个 final summary，stdout/stderr 与退出码符合 §7。

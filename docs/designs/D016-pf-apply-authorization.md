# PF Apply 授权与平台作用域

- **状态：** 草案
- **最后核对：** 2026-08-28
- **当前产品契约：** [D001](D001-pf.md)
- **当前模块边界：** [D002](D002-pf-implementation.md)
- **当前终端契约：** [D006](D006-pf-cli-enhancement.md)
- **当前报告 wire：** [D014](D014-pf-report-schema.md)

本文提出 `pf apply` 的目标授权模型：默认模式在单平台报告上自动做平台作用域 apply；显式 `--force` 在搜索证据全部成功且依赖声明未漂移时，可以接受 source drift 和缺失的整个平台覆盖。它不把未验证平台声明为通过，也不允许跳过失败证据、依赖漂移或不可表示的 projection。

D016 尚未替代 D001、D002、D006 或 D014。落地变更必须同时把本文的产品、模块和展示决策分别归并到这些唯一所有者，再把 D016 归档；在此之前，现行实现仍按 D001 的 complete/current report 规则执行。

## 1. 决策

公开 CLI 只增加一个布尔选项：

```text
pf apply [package]
pf apply [package] --force
```

不增加 `--platform`、`--partial`、`--ignore-source` 或可组合 waiver flags。平台作用域由报告证据自动推导，用户不能手工挑选 Cell 或伪造覆盖。

两种模式的承诺是：

| 模式 | 授权来源 | 允许的放宽 | 仍然禁止 |
| --- | --- | --- | --- |
| 默认 | 报告证据 | 单平台报告自动做平台作用域 projection | source/policy drift、多平台缺失、失败 Cell、依赖漂移、不可表示 projection |
| `--force` | 报告证据 + 操作者确认 | source snapshot drift、缺失的整个平台覆盖 | 失败或局部缺失 Cell、依赖漂移、policy/声明语义漂移、坏报告、不可表示 projection |

`--force` 是 apply-time 授权，不提升报告状态、不修改报告 identity，也不把当前 source 认证为搜索时的 source。

## 2. 领域术语

### 2.1 Platform 与 Cell 完整性

- **TargetPlatform**：`report.inputs.target_cells` 中出现的规范 uv target。
- **EvidencePlatform**：至少有一个最终 `CellResult` root 的 TargetPlatform。
- **PlatformComplete(platform)**：该平台在报告 target Cells 中声明的每个 Python minor × extra surface 都恰有一个最终 `CellSuccess` root。
- **ObservedSearchSuccess**：至少有一个 EvidencePlatform；每个 EvidencePlatform 都满足 `PlatformComplete`；报告中不存在任何非 `CellSuccess` root。

候选搜索过程中的 `REJECTED` 是边界证据，不是最终 Cell failure。只有最终 `cell_results` roots 参与 `ObservedSearchSuccess` 判定。

缺失可以发生在两个不同层级：

- **MissingPlatform**：某个 TargetPlatform 完全没有最终 root；`--force` 可以放宽。
- **MissingCellWithinPlatform**：EvidencePlatform 内有 Python/extra Cell 缺失；任何模式都禁止 apply。

因此 `--force` 允许“Windows 尚未搜索”，不允许“Linux 的 Python 3.12 尚未搜索”或“Linux 有一个 Cell 搜索失败”。

### 2.2 Apply scope

```text
ApplyScope = FULL_TARGET_SET | PLATFORM_SCOPED
```

- `FULL_TARGET_SET`：报告中有多个 TargetPlatform，全部平台完整成功。
- `PLATFORM_SCOPED`：只把所有完整成功 EvidencePlatforms 的 floor 写入其可表达的平台选择器；选择器补集继续使用原始 requirement。

`PLATFORM_SCOPED` 是声明投影范围，不是新的报告 completion 状态。

### 2.3 DependencyListIdentity

对每个待编辑 `pyproject.toml`，PF 从解析后的 TOML 计算：

```text
DependencyListIdentity = canonical({
  project.dependencies: ordered requirement strings,
  project.optional-dependencies: {
    group name sorted: ordered requirement strings,
  },
})
```

它覆盖 base/optional 位置、group、顺序和完整 PEP 508 requirement；注释、空白和 TOML 排版不进入 identity。`project.dynamic` 提供依赖时仍按 D001 失败。

Apply 前的依赖状态只有：

```text
ORIGINAL   current identity == report input identity
PROJECTED  current identity == 本次授权 projection 的精确结果
DRIFTED    其他状态
```

`ORIGINAL` 可以写入；`PROJECTED` 只产生幂等 no-op；`DRIFTED` 在默认和 force 模式都失败。这样 `--force` 不能覆盖 search 后的人工作依赖编辑。

## 3. 判定矩阵

每个选中 package 独立判定，workspace 事务只有在全部 package 都获授权后才开始。

| 报告事实 | 默认 `apply` | `apply --force` |
| --- | --- | --- |
| 一个 TargetPlatform，PlatformComplete，source/current | 自动 `PLATFORM_SCOPED` | 允许；通常无 waiver |
| 多个 TargetPlatforms，全部 PlatformComplete，source/current | `FULL_TARGET_SET` | 允许；通常无 waiver |
| 多个平台，仅部分整个平台有完整成功证据 | 阻止 | `PLATFORM_SCOPED`，记录 coverage waiver |
| EvidencePlatform 内缺 Cell | 阻止 | 阻止 |
| 任一最终 root 非 `CellSuccess` | 阻止 | 阻止 |
| source snapshot drift，依赖为 `ORIGINAL` | 阻止 | 允许，记录 source waiver |
| 依赖为 `DRIFTED` | 阻止 | 阻止 |
| policy、package 或声明语义 identity 不匹配 | 阻止 | 阻止 |
| scoped projection 不可精确表示 | 阻止 | 阻止 |

默认模式的特殊规则只适用于“报告恰有一个 TargetPlatform”。多平台报告缺少任一 TargetPlatform 时仍要求显式 `--force`，即使当前开发机只运行其中一个平台。

## 4. 默认 apply

默认模式保持 fail-closed，只新增单平台自动作用域：

1. Reader 完整验证 Schema、refs、identity 和 final PASS authority。
2. 当前 package、evaluation policy、dependency list 和 source snapshot 必须匹配报告。
3. 一个 TargetPlatform 且 PlatformComplete 时，选择该平台并生成 `PLATFORM_SCOPED` projection。
4. 多个 TargetPlatforms 全部 PlatformComplete 时，按完整 target set 生成 projection。
5. 多平台覆盖不完整或任一 Cell 非成功时阻止。

单平台报告的 floor 只对该平台生效，不能外推为所有平台兼容；平台选择器之外保留原 requirement。

## 5. `--force` 授权

`--force` 只有同时满足以下条件才可以进入编辑事务：

1. 报告通过 D014 的完整 reader 验证，package 与 generation 内部一致。
2. `ObservedSearchSuccess` 为真。
3. 每个被选 EvidencePlatform 都是完整平台，不存在局部缺 Cell。
4. 当前 DependencyListIdentity 为 `ORIGINAL` 或幂等的 `PROJECTED`。
5. 当前 evaluation policy、package identity、declaration ownership/marker 语义及 workspace generation 兼容性仍满足现行检查。
6. 所有 scoped projections 都能精确表示并通过重求值验证。
7. workspace 中所有 package 都先获授权，恢复日志、原子替换与回滚仍可用。

它只可以登记两种 waiver：

```text
SOURCE_SNAPSHOT_DRIFT
MISSING_PLATFORM_COVERAGE
```

其中 coverage waiver 只覆盖完全没有结果的 TargetPlatform。`CellIndeterminate`、baseline rejection/indeterminate、search failure、non-monotonic result 或同平台缺 Cell 都不是 waiver 候选。

Source waiver 也不掩盖会改变 apply 解释的输入。依赖列表、当前 policy、package/path、`requires-python`、target/extra surface 语义或声明 marker 发生不兼容变化时，相关 identity 检查先失败，不能借 source waiver 越过。

## 6. 平台作用域 projection

现行 D014 `projections` 证明的是完整 target coverage；incomplete report 可能没有可消费的 projection。D016 因此要求 apply owner 从经过验证的 final `CellSuccess.final_proposal` 与原始 declarations 生成一次 apply-time scoped projection，不能把 incomplete report 的空 projection 当成授权。

对每条 declaration：

1. 从选中 EvidencePlatforms 的成功 Cells 收集 `Cell -> ExactFloor`。
2. 生成只匹配这些平台的 PEP 508 selector，并与原 marker 做逻辑交集。
3. 在选中平台写入验证过的精确 `>=version`，保留原上界、排除项、extras、source 与位置。
4. 为 selector 的补集保留原始 requirement，不增加搜索得到的下界。
5. 在全部报告 TargetCells 上重求值：选中 Cell 必须恰好命中一个预期 floor；未选 TargetPlatform 必须与原 declaration 等价。

示意：

```text
before
  httpx<1

after a Linux/x86_64 scoped apply
  httpx>=0.27,<1 ; sys_platform == "linux" and platform_machine == "x86_64"
  httpx<1       ; sys_platform != "linux" or platform_machine != "x86_64"
```

真实输出由 marker 规范化器生成，不承诺上述空白或条件顺序。已有 marker 必须做布尔组合，optional dependency 仍留在原 group；不得生成 `extra == ...` marker。

uv target 到 PEP 508 selector 的映射必须一一可辨识。GNU/musl 等 target 若落到相同 `(sys_platform, platform_machine)` 而 floor 或保留语义不同，projection 不可表示，默认和 force 都阻止。不得用更宽 marker、host 猜测或 requirement 顺序掩盖冲突。

## 7. Source identity 边界

D016 不改变 D001 的 SourceSnapshot 成员规则。源码、测试、受跟踪文档及其他当前纳入的项目文件继续形成同一 source identity；`.git`、虚拟环境、常见 cache、`.pf`、PF 临时目录和报告等现有排除也保持不变。

是否把文档等类别永久排除属于 search authority 与可复现性决策，应另行修改 D001/SnapshotBuilder，不在 apply 中用扩展名启发式实现。D016 只提供显式、受约束且可审计的 apply-time source waiver。

Source drift 报告只包含安全、有限的摘要，例如 changed path 数量和最多若干规范相对路径；不打印文件内容、未脱敏路径、diff 或凭据。

## 8. Module interface 与所有权

新增一个深模块 owner，把授权、作用域选择和 projection 验证藏在一个 interface 后：

```text
ApplyAuthorizer.authorize(
    reports: tuple[ValidatedReport, ...],
    project: ProjectPlan,
    current_snapshot: SourceSnapshot,
    force: bool,
) -> AuthorizedApply
```

`AuthorizedApply` 是 strict/frozen 领域记录，至少包含：

```text
mode                  DEFAULT | FORCE
scope                 FULL_TARGET_SET | PLATFORM_SCOPED
selected_platforms    完整成功的 EvidencePlatforms
preserved_platforms   未获 floor 授权的 TargetPlatforms/selector complement
dependency_state      ORIGINAL | PROJECTED
waivers_used          SOURCE_SNAPSHOT_DRIFT / MISSING_PLATFORM_COVERAGE
prepared_edits        已重求值验证的 declaration edits
presentation_facts    bounded counts/paths；不含渲染文案
```

职责调整为：

| Owner | 唯一负责 | 不负责 |
| --- | --- | --- |
| `ApplyAuthorizer` | apply 前置条件、ObservedSearchSuccess、DependencyListIdentity、scope、waiver、scoped projection 与重求值 | TOML I/O、终端措辞、report wire join |
| `ProjectEditor` | 消费 `AuthorizedApply.prepared_edits`，执行 TOML 保格式事务、恢复日志、原子替换与回滚 | 再次推导授权、读取 report internals |
| `ApplyCommandWorkflow` | planning/snapshot/report → authorizer → workspace transaction | 复制授权规则或打印 |
| `ReportStore` | D014 reader/merge/write 与 resolved facade | 根据 `--force` 改写 result/projection |
| `TerminalPresenter` | 将 presentation facts 渲染为 D006 风格的简洁报告 | 决定是否获授权 |

Workflow 必须先为所有选中 package 构造 `AuthorizedApply`，之后才允许任何文件写入；一处 blocker 使整个 workspace 保持原样。

## 9. CLI 输出与退出码

正常单平台 apply 只增加一个作用域事实：

```text
✓ Applied floors · 1 project updated · platform-scoped to linux/x86_64
```

实际使用 waiver 的 force apply 输出一个有界报告，并保持一个 final summary：

```text
evidence  6/6 observed cells passed · linux/x86_64
preserved windows/x86_64, macos/arm64
waived    source drift (31 paths) · missing platform coverage
⚠ Applied floors with operator override · 1 project updated
```

- 默认成功和没有实际 waiver 的 `--force` 成功走 stdout，final summary 使用 `✓`。
- 使用 waiver 的成功结果退出 `0`，warning/report 走 stderr，final summary 使用 `⚠`。
- 授权、coverage、dependency drift 或 projection blocker 退出 `3`；CLI 用法错误仍为 `1`，没有 applicable floor 仍为 `2`。
- 不输出完整 JSON、digest、全部 path、候选失败历史或重复的 search summary。

具体词句、颜色、TTY/non-TTY 降级和宽度适配在落地时归并到 D006；D016 只固定必须展示的语义事实。

## 10. Report、merge 与 minimize

- Schema 1 不新增 force、waiver 或 partial-apply 字段。它们是当前项目与操作者共同决定的 apply-time facts。
- `pf explain` 仍离线描述 report 自身是否 complete，不读取当前 source 推测 force 是否可用。
- `pf merge` 不接受 `--force`；identity 不兼容和 Cell 冲突继续 fail closed。
- `pf minimize` 不隐式开启 force，也不新增 force 选项；它只复用默认 apply。单平台成功报告可以自动 scoped apply，多平台不完整则停止。
- apply 后的项目已不是报告 source snapshot。若要获得当前 source 的跨平台认证，必须开始新的 search generation。

## 11. 不变量

1. 没有 final PASS Proposal 的 Cell 永远不能贡献 floor。
2. `--force` 不能把失败、indeterminate 或同平台不完整解释为成功。
3. 未选平台的 requirement 语义保持不变，且终端明确说“preserved/unverified”，不能说“passed”。
4. DependencyListIdentity 漂移永远不可 waiver。
5. Source waiver 不改变报告 identity、result 或证据归属。
6. 每个写入 floor 都能追溯到相同 generation 的 final `CellSuccess`。
7. 不可表达的 target distinction 永远不近似。
8. workspace 授权与编辑保持 all-or-nothing，重复 apply 幂等。

## 12. 资格场景

落地按纵向 RED→GREEN 场景验证，至少覆盖：

- 默认单平台完整报告生成 scoped floor 与原 requirement fallback；
- 默认多平台全部完整成功生成完整 target projection；
- 默认多平台缺失整个平台时阻止；
- force 对完整 EvidencePlatforms 应用、对缺失平台保留原 requirement；
- source drift 且依赖 `ORIGINAL` 时 force 成功并报告 waiver；
- 依赖 `DRIFTED` 时 force 阻止且零写入；
- EvidencePlatform 内缺 Cell、任一非成功 root 或 non-monotonic result 时 force 阻止；
- `CellSuccess` 历史候选 rejection 不误判为搜索失败；
- existing marker、optional group、多 Python minor 与不同 floor 的重求值；
- GNU/musl selector collision 等不可表示情形阻止；
- `PROJECTED` 状态重复 apply 为 no-op；
- 多 package 中任一 blocker、写入异常与恢复异常都保持事务/回滚契约；
- TTY/non-TTY 的单一 final summary、stdout/stderr 与退出码。

资格必须覆盖原始 CLI 路径和真实 TOML round-trip；只测 authorizer helper 不足以宣布完成。

## 13. 非目标

- 任意选择 platform、Python minor、extra 或单个 Cell；
- 对失败/indeterminate 证据进行“尽量 apply”；
- 通过 `--force` 跳过 schema、policy、dependency 或 projection safety；
- 修改搜索算法、候选资格、runtime authority 或报告 wire；
- 在本设计中重新定义 SourceSnapshot 排除模式；
- 把 operator override 表述为新的兼容性认证。

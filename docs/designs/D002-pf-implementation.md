# PF 实现设计

- **状态：** 草案
- **产品契约：** [D001](D001-pf.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)

本文定义 PF v1 的代码结构、模块接口、Schema、外部工具适配、终端交互和测试策略。用户可见行为以 D001 为准，搜索不变量和 probe 顺序以 D003 为准；本文只决定这些契约如何实现。

## 1. 设计原则

PF 按深模块组织：每个模块用尽量小的接口隐藏一组完整行为，调用方不需要理解它的内部步骤。

具体约束如下：

- 搜索逻辑必须与项目发现、环境构建、`uv`、`ty`、测试命令、终端输出和报告写入解耦。
- 跨模块数据必须使用具名 Pydantic Schema，不允许用结构不明的 `dict[str, Any]` 传递领域状态。
- 依赖方向从 CLI 和外部 adapter 指向应用模块，再指向 Schema；Schema 不反向导入业务或基础设施模块。
- 只有真正变化的行为才建立 seam。v1 明确存在的 seam 是 Evaluator、外部进程执行和进度事件消费。
- 文件系统使用 `pathlib` 和真实临时目录测试，不为每个文件操作建立抽象层。
- 业务规则只允许有一个所有者。候选过滤、状态分类、报告规范化和 apply 授权不能在多个调用方重复实现。
- 只出现一次的辅助逻辑留在所属函数或方法中；出现至少两个真实调用方，或本身维护独立不变量时，才提取为同模块私有函数或具名模块。
- 禁止创建 `utils.py`、`helpers.py`、`common.py` 之类按“暂时放不下”分类的模块。共享代码必须按它维护的概念命名。
- 类只用于维护状态、资源生命周期或不变量；一次性的纯转换不包装成只有一个方法的类。
- 不引入依赖注入框架、通用 repository 层或一一对应数据表的 manager。composition root 显式构造依赖。

`CoordinateSearch` 是深模块：调用方只提供起始向量、冻结候选和 Evaluator；模块内部负责 probe、线性/二分定界、坐标提交、不动点和非单调检测。

只有 Evaluator 可以产生兼容性证据。搜索模块不得解析 stderr、选择测试用例或把基础设施错误解释为版本失败。

Apply 与搜索完全分离。`ProjectEditor` 只消费验证通过的公共报告，不调用候选发现、解析器、`ty` 或测试。

## 2. 技术选型

| 领域 | 选择 | 实现约束 |
| --- | --- | --- |
| CLI | Cyclopts 4.x | 命令、参数、版本和 shell completion 由 Cyclopts 提供；help 文本直接从 command docstring 解析，不在 typing annotation 中重复定义 |
| 终端展示 | Rich | 帮助、错误、表格、状态和 TTY 进度统一经 `TerminalPresenter` 输出；不硬编码运行时宽度或高度 |
| Schema | Pydantic 2.x | 配置、跨模块记录、Evaluation、事件和公共报告全部由 Pydantic 建模和校验 |
| 版本与需求解析 | `packaging` | PEP 440、PEP 508 和规范化名称的唯一实现来源 |
| TOML 读写 | `tomllib` + round-trip TOML 库 | 读取配置可用 `tomllib`；apply 必须使用能保留注释与格式的 round-trip 实现 |
| 外部解析与安装 | `uv` 可执行文件 | 只通过 `UvAdapter` 调用，不依赖 uv 的私有 Python 接口 |

生产依赖必须显式声明这些直接依赖。当前 `pyproject.toml` 中的 `msgspec` 应在实现 Schema 时替换为 `pydantic>=2`；不能同时维护 msgspec 和 Pydantic 两套模型或序列化路径。`packaging` 和选定的 round-trip TOML 库也必须是直接依赖，不能依赖传递安装。

## 3. 包布局与依赖方向

v1 从扁平、按职责命名的包开始，只有 Schema 和外部 adapter 使用子包。建议布局如下：

```text
src/pf/
├── __init__.py          # 版本和稳定导出，不放启动逻辑
├── __main__.py          # python -m pf
├── cli.py               # Cyclopts app、命令 handler、composition root
├── terminal.py          # Rich TerminalPresenter
├── errors.py            # PfError、错误类别与退出码映射
├── config.py            # ConfigLoader 和配置层合并
├── project.py           # 项目发现、声明解析、matrix 和 SourcePlan
├── snapshot.py          # 不可变源快照
├── candidates.py        # CandidateBuilder
├── search.py            # SearchCoordinator 和内部 CoordinateSearch
├── evaluation.py        # StaticEvaluator、FullEvaluator、EvaluationCache
├── environment.py       # EnvironmentFactory 和测试支撑环境
├── report.py            # ReportStore、merge 和 explain 查询
├── editing.py           # ProjectEditor 和恢复日志
├── scheduling.py        # 全局并发、deadline 和进度事件
├── schemas/
│   ├── __init__.py      # 仅导出稳定 Schema 名称
│   ├── base.py          # 复用的严格类型与 Schema 基类
│   ├── config.py        # 原始配置、覆盖层和 EffectiveConfig
│   ├── project.py       # declaration、cell、source、candidate、proposal
│   ├── evaluation.py    # 进程、static/full evaluation 和状态 union
│   └── report.py        # FloorResult、投影证据和 PackageFloorReportV1
└── adapters/
    ├── process.py       # ProcessRunner seam 和 SubprocessRunner
    ├── uv.py            # uv 命令构造、解析和状态分类
    ├── ty.py            # ty 命令与诊断分类
    └── test_command.py  # 完整测试命令与退出码分类
```

这些文件是初始所有权，不是强制“一类一文件”。只有当一个文件出现两个可以独立描述、独立测试且独立演进的深模块时才拆分。不得按 `models/`、`services/`、`managers/`、`repositories/` 再建立平行抽象层。

依赖方向必须保持为：

```text
Cyclopts CLI ───────┐
Rich presenter ─────┼─> command handlers
                    │          │
                    │          v
                    │   project / search / report / editing / scheduling
                    │          │
                    │          v
                    └──── adapters / environment
                               │
                               v
                         Pydantic schemas
```

额外规则：

- `schemas` 只能依赖标准库、Pydantic 和用于字段类型的 `packaging`。
- `search.py` 不得导入 Cyclopts、Rich、`subprocess` 或 TOML 库。
- `terminal.py` 可以读取结果 Schema，但任何业务模块不得导入 Rich。
- `adapters` 不得导入 CLI 或 terminal；adapter 返回分类后的 Schema，不直接打印。
- `cli.py` 是唯一 composition root，可以导入所有需要装配的模块。
- 跨层循环依赖视为模块所有权错误，不能用延迟导入掩盖。

## 4. Pydantic Schema 设计

### 4.1 基础策略

所有跨模块记录继承同一个只读基类：

```python
from pydantic import BaseModel, ConfigDict


class FrozenSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )
```

实现还必须遵守：

- 身份记录和报告记录使用 tuple，不使用可原地修改的 list；需要确定顺序的 mapping 序列化前转换为按规范 key 排序的记录 tuple。
- 配置、报告和外部命令结果在进入业务模块时完成一次校验；内部不得反复在 dict 与 model 之间转换。
- `field_validator` 只负责单字段规范化，`model_validator` 负责同一 Schema 内的跨字段不变量。Validator 必须是纯函数，不读取文件、不查询 index、不启动进程。
- 公共 JSON 不允许 `Path`、`Version`、异常对象或任意 Python 类型。路径写成相对 snapshot root 的 POSIX 字符串，版本写成验证并规范化后的字符串。
- `Enum`/`Literal` 表达有限状态；带状态差异的结果使用 discriminator union，不用大量 `None` 字段表达隐含状态机。
- 不在 v1 使用 `model_construct()` 绕过校验。来自外部或 `model_copy(update=...)` 的变更必须重新经过对应 Schema 校验。
- Pydantic 负责字段与结构正确性，领域模块负责需要 I/O 或多个记录共同参与的规则。例如“specifier 合法”属于字段校验，“测试依赖是否改变目标图”属于 Evaluation。
- Python 字段名和 JSON 字段名统一使用 `snake_case`；v1 不使用全局 alias generator，避免公共 Schema 因重命名悄然变化。

### 4.2 Schema 分组

`schemas/config.py` 包含：

- `RootConfigPatch`：root `[tool.pf]` 中实际出现的字段；保留“未设置”和显式值的差异。
- `PackageConfigPatch`：root package override 和包内 `[tool.pf]` 可覆盖的字段，不包含 root-only 包选择字段。
- `EffectiveConfig`：三层配置合并后的完整、无缺省运行配置。
- `SearchPolicyIdentity`：只包含会改变证据语义的字段和算法常量，不包含日志样式、jobs 或总调度时长。

配置先按原始 key 合并，再只构造一次 `EffectiveConfig`。列表执行替换，不做通用 deep merge。每个允许覆盖的字段都在 `ConfigLoader` 的显式表中，新增配置项必须同时声明默认值、覆盖权限和是否进入策略身份。

`schemas/project.py` 包含：

| Schema | 必要字段 | 关键不变量 |
| --- | --- | --- |
| `RequirementDeclaration` | `declaration_id`、package、base/extra 位置、规范化名称、requested extras、specifier、marker、source、源码位置 | ID 不随下界更新或 TOML 重排变化；同一位置的活跃声明不重叠 |
| `Cell` | package、精确 target triple、CPython minor、完整 interpreter identity、规范化 extra surface | extra 排序且去重；target 不能是粗粒度平台名 |
| `SourceIdentity` | source kind、无凭据 locator、index/name、commit/hash | secret 永不进入 model；可变 VCS ref 不能成为固定来源 |
| `Candidate` | 精确规范版本、series key、artifact identity、yanked/prerelease 信息 | 已通过来源、构件、上界和排除项过滤 |
| `CandidateSnapshot` | dependency、cell、policy、升序候选、series representatives、source、digest | 非空、严格升序、无重复，baseline 后不再变化 |
| `ResolvedNode` | 规范化名称、精确版本、source、artifact、依赖边 | 完整图按名称和 edge 规范排序 |
| `Proposal` | proposal ID、source snapshot、cell、受管向量、固定/非受管声明、完整解析图、实际构件、policy identity | Proposal ID 覆盖所有可能改变 Evaluation 的输入 |

稳定声明 ID 使用以下规范字段计算摘要：包内 `pyproject.toml` 相对路径、base/extra 位置、规范化名称、requested extras、规范化 marker 和 source identity。它不包含数组下标、行号或可被 apply 改写的 specifier，因此重排和下界更新不会改变 ID。源码位置只用于诊断和编辑时二次确认。

`schemas/evaluation.py` 包含：

- `ProcessSpec`：完整 argv、cwd、环境增量、timeout、进程组和脱敏策略身份。
- `ProcessResult`：退出码或 signal、耗时、脱敏 stdout/stderr 摘要、有界尾部和 timeout 标志。
- `StaticPass`、`StaticFail` 和各非证据结果组成的 `StaticEvaluation` discriminator union。
- `PassEvaluation`、`StaticFailEvaluation`、`TestFailEvaluation` 和各非证据结果组成的 `Evaluation` discriminator union。
- `ProgressEvent`：package、cell、阶段、已完成/总任务和不含 secret 的短消息。

状态 union 必须让非法组合无法构造。例如 `PASS` 必须包含 static 与完整测试证据，`STATIC_FAIL` 不能包含伪造的测试结果，`TIMEOUT` 必须记录超时阶段。业务代码使用模式匹配处理具体结果，不把状态折叠为 `bool`。

`schemas/report.py` 包含：

- `ProbeObservation`、`CoordinateBoundary` 和 `DependencyFloor`；
- 成功、失败和不完整三类 `CellResult` discriminator union；
- `ProjectionEvidence`，记录 declaration 到各 cell floor 的可表示投影；
- `CompleteReportResult` 与 `IncompleteReportResult`；
- 顶层 `PackageFloorReportV1`。

顶层报告至少包含：

```text
schema_version = 1
generator identity
package identity
source snapshot identity
policy identity
requirement declarations
candidate snapshots
cell results
projection evidence
report result: complete | incomplete
```

`complete`、最终向量、apply 授权和错误原因不得用互相独立的布尔值表达。使用 union 和 validator 保证：只有所有目标 cell 成功、覆盖完整且投影可表示时，报告结果才能是 `complete`。

### 4.3 身份与规范化序列化

所有 digest 使用 Schema 的 JSON-mode dump 作为输入，并由所属模块添加类型和版本前缀，例如：

```text
pf:proposal:v1\0<canonical-json>
```

`ReportStore` 是规范化 JSON 的唯一所有者。它执行：

1. `model_dump(mode="json", exclude_none=False)`；
2. 对 mapping key 排序，使用固定 separators 和 UTF-8；
3. 追加单个换行；
4. 写临时文件、flush、fsync，再原子 rename。

Pydantic 负责校验和 JSON-compatible 转换，但不能把库默认输出顺序当成公共规范。读取时先检查顶层 `schema_version`，再选择 `PackageFloorReportV1.model_validate_json()`；未知版本直接失败，不尝试 best-effort 解析。

运行时环境路径、进程 PID、临时文件和可恢复资源句柄使用独立的内部记录，不进入公共报告，也不参与跨运行身份。

## 5. Cyclopts CLI

### 5.1 应用结构

`cli.py` 定义一个根 `cyclopts.App(name="pf")`，直接注册 D001 中的平级命令：

```text
check
search
apply
minimize
explain
merge
```

没有 default command；裸运行 `pf` 显示帮助。命令函数的类型标注只定义解析类型和约束，命令简介、长说明和每个参数的 help 文本全部写在 Cyclopts 能直接解析的函数 docstring 中。不得使用 `Annotated[..., Parameter(help=...)]`、独立 help 常量或 decorator 的 `help=` 重复维护说明文本。参数别名、是否显示和展示分组等非文案元数据仍可使用 Cyclopts `Parameter`。不手写 `sys.argv`、usage、suggestion 或 completion。

所有 command docstring 使用仓库统一的一种受 Cyclopts 支持的参数章节格式。help 文本测试直接渲染 `pf <command> --help`，确认函数摘要、长说明和参数说明均来自 docstring；测试不得绕过 Cyclopts 单独断言 docstring 内容。

CLI 数据流固定为：

```text
argv
  ↓ Cyclopts 解析与基础类型转换
CommandRequest Pydantic Schema
  ↓ ConfigLoader / command workflow
Result Pydantic Schema
  ↓ TerminalPresenter
Rich output + D001 退出码
```

command handler 只允许：

1. 将 Cyclopts 参数构造成 command request；
2. 调用一个顶层工作流；
3. 把结果交给 `TerminalPresenter`；
4. 返回 D001 定义的整数退出码。

命令 handler 不解析 TOML、不拼 `uv` argv、不遍历依赖、不写 JSON，也不捕获并重新分类底层进程结果。

`search` 和 `minimize` 共用的 jobs/deadline 参数可以建模为一个 Pydantic 参数组；只被单个命令使用的参数就地保留在该 command 函数中，不创建一次性 Options 类。Cyclopts 的拼写、默认值和 D001 必须逐项一致。

### 5.2 Composition root 与入口点

`cli.py` 提供 `create_app(context: CliContext) -> App`，生产入口使用真实 adapter，CLI 测试传入 fake/recording adapter。`CliContext` 只保存已装配模块，不包含业务逻辑。

```text
main
  ├── stdout/stderr Console + TerminalPresenter
  ├── SubprocessRunner
  ├── UvAdapter / TyAdapter / TestAdapter
  ├── EnvironmentFactory + Evaluators + cache
  ├── ProjectLoader + CandidateBuilder + SearchCoordinator
  ├── ReportStore + ProjectEditor + Scheduler
  └── create_app(context)()
```

包入口统一为：

```toml
[project.scripts]
pf = "pf.cli:main"
```

`pf/__main__.py` 只调用同一个 `main()`，保证 `pf` 和 `python -m pf` 行为相同。`pf/__init__.py` 不导入 CLI，以免普通库导入产生 Console 或命令注册副作用。

Cyclopts 负责用法错误和命令返回值的基础退出行为。PF 领域错误统一继承 `PfError`，只在 CLI 最外层转换为 D001 的退出码；内部模块不得调用 `sys.exit()`。

### 5.3 命令所有权

| 命令 | 顶层模块 | 写入范围 |
| --- | --- | --- |
| `check` | `CompatibilityChecker` + `FullEvaluator` | 只写临时环境和诊断 |
| `search` | `SearchCoordinator` + `ReportStore` | `package-floor.json` 和 `.pf` 临时状态 |
| `apply` | `ReportStore` + `ProjectEditor` | 授权的 `pyproject.toml` 与恢复日志 |
| `minimize` | handler 顺序调用 search、确认成功、再 apply | 与前两者相同，不另写一套逻辑 |
| `explain` | `ReportStore` 查询 + presenter | 无 |
| `merge` | `ReportStore.merge` | 指定输出报告 |

`minimize` 的组合只出现一次，因此顺序留在该 handler；安全规则仍由 `ReportStore` 和 `ProjectEditor` 自己强制，不能依赖 CLI 调用正确。

## 6. Rich 终端输出

`terminal.py` 是 Rich 的唯一业务使用点。Cyclopts 可以使用同一组 Console 渲染帮助和用法错误，其他模块不得调用 `print()`、创建 `Console` 或输出 ANSI 控制符。

`TerminalPresenter` 拥有：

- stdout Console：最终摘要、explain 和 merge 结果；
- stderr Console：错误、警告、进度和外部工具诊断摘要；
- 单一 Theme：成功、失败、警告、dim、路径和版本的语义样式；
- `Progress` 生命周期：只在交互终端启用动态刷新。

输出策略：

- 让 Rich 自动检测 terminal、颜色和 `NO_COLOR`；非 TTY 不强制 ANSI。
- 生产代码构造 `Console`、`Table`、`Panel`、`Progress` 等 Rich 对象时，不传固定 `width`、`height`、`min_width`、`max_width` 或等价尺寸；由 Rich 根据当前终端动态排版。
- 动态进度写 stderr，stdout 保持为最终结果。非交互环境禁用动画，改为每个 package/cell 完成时输出一行稳定文本。
- 并发 worker 只产生 `ProgressEvent`，由主线程 Presenter 消费；worker 不直接打印，避免输出交错。
- 表格不硬编码列宽、总宽或高度；只声明语义上的对齐、换行和 overflow 行为，让 Rich 的排版引擎适应不同终端。完整证据始终写入报告。
- 外部命令 stdout/stderr 先由 adapter 脱敏和截断，再传给 Presenter；绝不直接透传可能含凭据的原始输出。
- 错误首先显示稳定类别和一句可行动摘要，`--verbose` 若未来加入，只能增加已脱敏上下文，不改变退出码。

Presenter 可以为不同结果提供 `render_check`、`render_search`、`render_explanation`、`render_error` 等具名方法。格式化细节留在这些方法内；仅当同一 renderable 在多个方法复用时才提取私有构造函数。

## 7. 项目规划模块

`ConfigLoader` 和 `ProjectLoader` 把工作树转成搜索可消费的不可变计划：

```text
ProjectLoader.load(root, package_selection)
  -> ProjectPlan
```

`ProjectPlan` 包含选定 package、稳定声明、有效配置、cell、SourcePlan 和源快照身份。调用方不分别调用 `PackageDiscovery`、`RequirementParser`、`MatrixNormalizer`；这些是 `ProjectLoader` 内部职责，不是平级公共接口。

### 7.1 配置合并

更具体的配置优先级与 D001 一致：包内 `[tool.pf]`，其次 root package override，最后 root `[tool.pf]`。

`ConfigLoader` 负责：

- 识别未知 key 和错误层级；
- 区分未设置与显式空列表；
- 按字段表执行 scalar/list 替换；
- 规范化 package name、路径、duration、target triple 和 extra surface；
- 验证 `managed-deps`/`unmanaged-deps`、extras/extra-surfaces 等互斥条件；
- 生成 `EffectiveConfig` 和 `SearchPolicyIdentity`。

其他模块只能消费 `EffectiveConfig`，不得再次读取 `[tool.pf]` 或填默认值。

### 7.2 声明与 cell

`ProjectLoader` 使用 `packaging.Requirement` 解析 PEP 508，统一完成名称、extras、specifier 和 marker 规范化。原始文本和源码位置保留用于诊断/round-trip 编辑，但业务判断只使用规范字段。

marker 投影、固定/可搜索依赖分类和 base/extra 交集都只在这里发生。`CandidateBuilder` 与 `ProjectEditor` 消费分类结果，不各自重新解释 PEP 508。

`MatrixNormalizer` 是 `ProjectLoader` 的内部步骤。它输出有序、去重的 `Cell` tuple；规范顺序为 target triple、Python minor、extra surface，且该顺序进入策略身份。

### 7.3 SourcePlan

`SourcePlan` 是候选查询和安装共享的唯一来源解释结果。它包含 registry/index、snapshot 内 path/workspace 映射、固定 Git commit、URL/hash 以及凭据引用，但可序列化身份中不得包含 secret。

`CandidateBuilder` 和 `UvAdapter` 必须消费同一个 `SourcePlan`。任何 adapter 都不得直接重新读取 `[tool.uv.sources]` 或自行决定 index 优先级。

## 8. 候选模块

`CandidateBuilder` 的外部接口为：

```text
build(package_plan, cell, baseline) -> tuple[CandidateSnapshot, ...]
```

它查询 index，并在一个所有者内应用：

- source/index；
- artifact 与 distribution 策略；
- prerelease 和 yanked；
- 当前上界和排除项；
- search-space；
- release-granularity；
- 不高于 baseline；
- PEP 440 规范排序与系列代表选择。

输出是每个受管 dependency/cell 的不可变 `CandidateSnapshot`。digest 计算完成后，搜索只能读取 snapshot；不得懒加载更多候选、刷新 index 或替换系列代表。

CandidateBuilder 不读取 Evaluation，不决定 probe 顺序，也不因为构建失败而跳过版本。构件不可用的分类来自 `UvAdapter`，候选策略只决定某个构件是否符合资格。

## 9. 搜索模块

### 9.1 外部接口

`SearchCoordinator` 拥有单个 package/cell 的完整状态机：

```text
search(package_plan, cell, deadline) -> CellResult
```

它内部完成 baseline、候选冻结、静态坐标搜索、联合测试 fast path、必要时的动态坐标搜索和 `FloorResult` 组装。调用方看不到一串可被错误重排的步骤。

`CompatibilityChecker` 复用 Proposal 构建与 `FullEvaluator` 验证当前声明，但不建立候选或进入坐标搜索。它与 SearchCoordinator 共享具名的 Proposal builder；不得复制 baseline 解析逻辑。

### 9.2 CoordinateSearch

`CoordinateSearch` 位于 `search.py` 内部，只处理有序离散坐标。它通过内部 Evaluator seam 观察 Proposal，不知道 uv、`ty`、pytest 或文件系统。

静态和动态阶段复用同一个实现：

```text
CoordinateSearch + StaticEvaluator
CoordinateSearch + FullEvaluator
```

两种 Evaluator 是同一内部 seam 上的真实 adapter。该 seam 的最小接口是：

```python
class Evaluator(Protocol):
    def evaluate(self, proposal: Proposal) -> EvaluationEvidence: ...
```

`EvaluationEvidence` 是分类后的 discriminator union。CoordinateSearch 不接受异常文本或裸退出码。

D003 中的首次 probe、small threshold、中点规则、非单调检查、提交和 sweep 都由 CoordinateSearch 唯一实现。SearchCoordinator 不复制一维定界；静态/动态差异只通过 Evaluator 和 hint 参数表达。

## 10. Evaluation 与外部 adapter

### 10.1 进程 seam

`ProcessRunner` 是所有外部进程的唯一 seam：

```text
run(ProcessSpec) -> ProcessResult
```

生产使用 `SubprocessRunner`，测试使用 scripted/recording adapter。它负责：

- `shell=False` 的完整 argv；
- cwd 和最小环境增量；
- 独立进程组；
- stdout/stderr 捕获上限；
- timeout 后温和终止、宽限期和整组强制终止；
- signal、启动失败和 timeout 的机械事实记录。

ProcessRunner 不知道 `uv`、`ty` 或测试退出码语义。`UvAdapter`、`TyAdapter` 和 `TestAdapter` 各自把 `ProcessResult` 分类为领域状态。

### 10.2 Adapter 所有权

`UvAdapter` 是所有 uv 命令构造和输出解析的唯一所有者，覆盖：解释器定位、候选查询、baseline resolve、精确 Proposal resolve/install、图冻结和构件身份。业务模块不得拼接 `uv pip` argv。

`TyAdapter` 只负责运行 `ty`、规范诊断摘要并区分正常不兼容与工具错误。它不创建环境，也不决定是否继续测试。

`TestAdapter` 只运行完整 `test-command` argv，应用配置的 cwd、timeout 和 `test-failure-exit-codes`。v1 不提供 test selector、failure parser 或 partial test seam。

所有 adapter 必须在返回前完成凭据脱敏。脱敏规则由 `adapters/process.py` 内的一个具名策略维护，覆盖 argv、URL、环境变量和输出；不得在 Presenter 或 ReportStore 临时补救。

### 10.3 Evaluator

`EnvironmentFactory` 根据完整 Proposal 创建或定位隔离环境。

`StaticEvaluator` 执行 resolve/install 和 `TyAdapter`，返回 `STATIC_PASS`、`STATIC_FAIL` 或非证据状态。

`FullEvaluator` 先查 static cache。精确 Proposal 未检查时调用 StaticEvaluator；静态通过后再调用 TestAdapter。

状态只能由最靠近事实的 adapter 分类。向上传递时保持原状态和证据，不折叠成布尔值，不用 catch-all `except Exception` 转换为版本失败。未预期的程序错误保留 traceback，属于 PF bug；已知外部失败转换为具名非证据状态。

## 11. 源快照

Git 仓库的快照清单由 tracked 文件和以下命令共同产生：

```text
git ls-files --others --exclude-standard
```

因此 staged、unstaged 和未忽略的 untracked 源文件都会进入快照。

非 Git 目录使用等价排除规则遍历。存在 `.gitignore` 时遵循其中规则。

必须排除：

- `.git`；
- `.venv`、`venv`；
- `.cache`、`__pycache__`；
- `.pytest_cache`、`.mypy_cache`、`.ruff_cache`、`.ty_cache`；
- `.tox`、`.nox`、`.pf`；
- PF 临时目录和 `package-floor.json`。

复制时保留相对路径、文件模式和符号链接。摘要覆盖规范路径、条目类型、模式、内容或链接目标。路径越过 snapshot root、绝对符号链接或无法安全表示的特殊文件必须保守失败。

一次 search 使用一个不可变源快照。每个新 Proposal 从该快照获得独立可写副本。`SnapshotBuilder` 是遍历、排除、摘要和 materialize 的唯一所有者；EnvironmentFactory 不再次实现复制规则。

## 12. 环境构建

PF 使用显式解释器和底层 `uv pip`。它不调用 `uv sync`、`uv run`，也不读取 workspace lock 或操作者 `.venv`。

安装前，将活跃受管声明物化为 Proposal 的精确版本，同时保留已有 `<`、`<=` 和 `!=`。

包和 extra surface 以 editable 方式安装。path/workspace 来源映射到源快照中的对应位置。

测试依赖安装前冻结目标依赖图。测试支撑环境使用精确约束安装；若改变目标图，返回 `HARNESS_ERROR`。

### 12.1 环境状态

```text
CREATED
  ↓ ty 通过
STATIC_CLEAN
  ↓ 完整测试
TESTED
```

环境状态由 `EnvironmentFactory` 维护，Evaluator 只能请求合法转换。

`STATIC_CLEAN` 环境可被同一精确 Proposal 的 FullEvaluator 晋升一次。

`TESTED` 环境视为可能污染，只保留诊断，不再作为干净环境使用。

不同 Proposal 不通过原地升级或降级依赖复用。复用范围只包括 uv 下载、wheel、build cache 和不可变源快照。

`ty` 产生的缓存和临时文件必须位于 Evaluator 管理目录，不能改变测试可观察的源码快照。

## 13. 测试支撑环境

分别解析 workspace root 和包中的 `test-group`。`include-group` 在声明文件内展开。

两处同时存在时先 root、后 package 合并。完全重复项删除，同来源兼容约束取交集。

来源冲突、交集为空或测试依赖改变目标图时返回 `HARNESS_ERROR`。

路径保持相对于声明文件。命令、cwd、退出码策略、工具版本和阶段 timeout 进入策略身份。

`test-command` 作为 argv 直接执行，不经过 shell。v1 不提供 test selector、failure parser 或 partial test seam。

合并 test group、冻结目标图和比较安装前后图全部属于 `EnvironmentFactory`；TestAdapter 只执行已经决定的命令。

## 14. 状态、错误与退出码

兼容性证据：

```text
PASS
STATIC_FAIL
TEST_FAIL
```

非证据状态：

```text
UNAVAILABLE
BUILD_UNAVAILABLE
UNRESOLVABLE
HARNESS_ERROR
SOURCE_ERROR
TOOL_ERROR
TIMEOUT
```

Adapter 完成 Evaluation 状态分类，CoordinateSearch 只接收分类后的结果。

非证据状态终止当前 cell，不能推进边界。状态从底层向上传递时不得折叠成布尔值。

同一 Proposal 若出现相互冲突的完整结果，EvaluationCache 返回 `NONDETERMINISTIC`，SearchCoordinator 停止该 cell。

SearchCoordinator 还拥有 `BASELINE_FAILED`、`NON_MONOTONIC`、`NO_PASS_IN_SEARCH_SPACE` 和 `INDETERMINATE`。这些是搜索终态，不是 Evaluation 状态。

`errors.py` 只定义稳定错误类别和 D001 退出码映射：

```text
0 success
1 current declaration or baseline compatibility failure
2 no applicable floor
3 usage/config/schema/coverage/drift error
4 infrastructure/timeout/indeterminate error
```

带证据的预期失败优先作为 Result Schema 返回；异常用于调用契约被破坏、配置无法建立或 I/O 操作本身无法继续。内部模块不直接选择进程退出码。

## 15. Evaluation 缓存

缓存仅在当前 search 进程内有效。公共报告不是缓存。

缓存 key 至少包含：

- 包快照摘要和 cell；
- 完整 Proposal 与解析图；
- 来源、构件 hash 和 distribution 策略；
- 解释器、ABI 和平台；
- `ty`、测试命令、测试依赖和工具版本；
- cwd、退出码策略和阶段 timeout。

StaticEvaluation 与 Evaluation 分开存储。`STATIC_PASS` 可以被 FullEvaluator 复用，但不能当作完整 `PASS`。

`EvaluationCache` 暴露按 Proposal identity 的 `get_static/evaluate_static/get_full/evaluate_full` 语义，调用方不能直接修改底层 mapping。它还负责检测同一 key 的冲突写入。

环境引用属于运行时缓存值，不进入公共报告。Search 结束后默认清理 Proposal 环境。

## 16. 报告、merge 与 apply

`ReportStore` 使用第 4 节的规范化序列化和版本化 Schema。读取未知 Schema 时失败。

写入先刷新临时文件，再原子 rename。报告保留脱敏命令身份、状态、耗时、输出摘要和有界尾部。

`ReportStore.merge(reports)` 自己验证 package、Schema、source 和 policy identity；CLI 不预筛选。结果按规范 cell key 排序，相同 cell 必须完全一致，不能按时间戳选胜者。

`ProjectEditor` 通过稳定 declaration ID、项目摘要和策略身份授权编辑。

编辑后重新解析依赖，验证语义 diff 只包含授权的精确 `>=floor` 变更。已有上界、排除项、marker、来源和无关字段必须保持。

TOML 和报告写入使用临时文件、fsync、原子 rename 和 `.pf` 恢复日志。重复 apply 必须幂等。

恢复日志状态机至少包含：

```text
PREPARED -> PROJECT_REPLACED -> REPORT_CONFIRMED -> COMMITTED
```

每次启动 apply 先检查未完成日志：能够证明目标和备份身份时恢复到一致状态；身份不匹配时停止并给出人工恢复路径，不覆盖未知用户修改。

## 17. 调度与进程控制

单个 cell 的 SearchCoordinator 串行执行 Proposal。独立 package/cell 可以在全局并发限制器下运行。

Scheduler 不得改变单个 cell 的规范依赖顺序。序列化前按规范 key 排列结果。

Scheduler 只调度 `Callable[[], CellResult]` 和收集 `ProgressEvent`，不了解搜索规则。jobs 影响吞吐但不进入搜索策略身份。

每个外部阶段拥有独立进程组。timeout 时先温和终止，经过短暂宽限后终止整个进程组。

达到总时限后停止调度新任务，等待或终止在途任务，写入不完整报告并禁止 apply。

进度事件必须有界；慢终端不能阻塞 worker 或导致无限内存增长。最终结果不依赖事件是否被展示。

## 18. 测试策略

测试以模块接口为表面，不依赖内部调用次数或私有容器形状。

### 18.1 Schema 测试

- 每个 Schema 的合法最小样例和非法边界；
- `extra="forbid"`、冻结性、默认值校验和 union discriminator；
- report JSON round-trip 和未知 `schema_version`；
- 同一语义输入的 canonical JSON/digest 稳定；
- Pydantic JSON Schema 生成和公共报告 golden fixture。

### 18.2 CLI 与 Presenter 测试

- 使用 `create_app(fake_context)` 和 Cyclopts 的 return-value 测试模式直接传 argv；
- 对六个命令的 help、默认值、别名、缺失参数和退出码做 smoke test；
- 用测试专用 Console 分别模拟窄、常规和宽终端并关闭颜色，验证布局可读且不截断关键状态；模拟尺寸只存在于测试，不进入生产 Presenter；
- 分别覆盖 TTY progress 和 non-TTY 稳定行输出；
- 确认 stdout、stderr 分流以及原始 secret 不出现在任何输出。

### 18.3 核心模块测试

- `ProjectLoader` 使用 `tmp_path` 中的真实 pyproject/workspace fixture；
- `CandidateBuilder` 使用 fake UvAdapter 返回的 index/artifact 数据，断言唯一过滤结果；
- `SearchCoordinator` 通过 scripted Evaluator 覆盖 D003 的线性、二分、hint、反复 sweep、非单调和非证据状态；
- `EvaluationCache` 覆盖 static/full 分离、一次晋升和冲突结果；
- `ProjectEditor` 用 round-trip TOML fixture 验证注释保留、语义 diff、幂等和恢复日志。

CoordinateSearch 是 `search.py` 的内部 seam，可以有集中算法测试，但最终还必须通过 SearchCoordinator 接口验证同样的不变量；不得为每个搜索私有函数保留一层脆弱的镜像单元测试。

### 18.4 Adapter 与端到端测试

- `SubprocessRunner` 测进程组、signal、timeout、输出上限和脱敏；
- Uv/Ty/Test adapter 使用 recording ProcessRunner 断言完整 argv、cwd、env 和状态分类；
- 最小本地包端到端覆盖 `check -> search -> explain -> apply`；
- CLI 端到端必须直接运行安装后的 `pf` 命令，不能只调用 Python 函数；
- 需要网络、多个解释器或真实 index 的测试显式标记，不把环境缺失误报为功能回归。

## 19. 实施顺序与完成条件

按可运行的纵向切片实施：

1. 将生产 Schema 依赖切换为 Pydantic，建立 Schema 基类、错误类别、Cyclopts 入口和 Rich Presenter；
2. 实现 ConfigLoader、ProjectLoader、稳定 declaration ID 和源快照；
3. 实现 ProcessRunner、Uv/Ty/Test adapter、EnvironmentFactory 和 baseline `pf check`；
4. 实现 CandidateBuilder、EvaluationCache、CoordinateSearch 和单 cell `pf search`；
5. 实现多 cell Scheduler、进度事件、公共报告和 `pf explain/merge`；
6. 实现 ProjectEditor、恢复日志、`pf apply/minimize`；
7. 补齐安装后 CLI、真实 uv/ty 和最小项目端到端验证。

每个切片都必须同时包含：Pydantic Schema、深模块接口、adapter fake、核心测试和对应 CLI smoke。不能先堆一组 helper，最后再决定所有权。

v1 实现完成必须满足：

- `pf` 与 `python -m pf` 命令一致，帮助中包含 D001 的六个命令；
- 除 Cyclopts/terminal 外无 Rich 导入，除 CLI 外无 Cyclopts 导入；
- 公共报告只能由 `PackageFloorReportV1` 生成和读取；
- 不存在 msgspec Schema、裸跨模块 dict、`utils.py` 或重复的候选/状态/apply 规则；
- D003 搜索不变量拥有 focused test；
- 非 TTY、timeout、secret 脱敏、原子写入和重复 apply 有验证；
- 实际安装后的 CLI 能完成最小项目的 `check/search/explain/apply` 流程。

## 20. 框架参考

- [Cyclopts commands](https://cyclopts.readthedocs.io/en/stable/commands.html)
- [Cyclopts help](https://cyclopts.readthedocs.io/en/stable/help.html)
- [Cyclopts and Pydantic](https://cyclopts.readthedocs.io/en/stable/pydantic.html)
- [Cyclopts packaging and result actions](https://cyclopts.readthedocs.io/en/stable/packaging.html)
- [Rich Console](https://rich.readthedocs.io/en/stable/console.html)
- [Rich Progress](https://rich.readthedocs.io/en/stable/progress.html)
- [Pydantic model configuration](https://docs.pydantic.dev/latest/api/config/)
- [Pydantic validators](https://docs.pydantic.dev/latest/concepts/validators/)
- [Pydantic discriminated unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions)
- [Pydantic serialization](https://docs.pydantic.dev/latest/concepts/serialization/)

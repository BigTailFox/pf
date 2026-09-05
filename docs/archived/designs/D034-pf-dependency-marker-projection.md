# D034 — PF 可移植依赖 marker 投影

- **状态：** 已完成，归档；稳定规则已由 D001/D002/D012/D014 接管，本文保存历史决策
- **实施计划：** [P039](../plans/P039-pf-dependency-marker-projection.md)
- **日期：** 2026-09-06
- **核对基线：** `3d747fd`；PF worktree 起草前干净
- **来源：** `experiments/mkdocs` dogfood 中受管声明
  `colorama >=0.4; platform_system == 'Windows'` 在 project load 阶段被拒绝
- **稳定 owner：** [D001](../../designs/D001-pf.md)、[D002](../../designs/D002-pf-implementation.md)、
  [D012](../../designs/D012-pf-harness-relaxation.md)、[D014](../../designs/D014-pf-report-schema.md)
- **历史依据：** [D028](D028-pf-validation-contract-surfaces.md)、
  [D029](D029-pf-conditional-resolution-projection.md)

本文定义一次临时性的 project planning、Cell marker、report projection 与 apply authorization
契约迁移。目标是让 PF 对可由既有 Cell identity 完整、可移植地确定的
`platform_system` 与 `os_name` 提供受管依赖投影支持，同时保持跨宿主 evidence、merge 与 apply
等价性。本文在被接受前不定义目标契约；接受后仍须先建立实施 Plan，才能修改生产代码。

## 1. 问题与决策摘要

现行 `ProjectLoader` 对参与 search/apply 的受管 `project.dependencies` 与
`project.optional-dependencies` 只允许：

```text
python_version
sys_platform
platform_machine
```

test-group 中 target 自引用的 marker 使用同一限制。与此同时，`marker_applies()` 从当前进程的
`packaging.markers.default_environment()` 起步，只覆盖 planned Python minor、`sys_platform` 与
`platform_machine`。如果仅把 `platform_system` 或 `os_name` 加入允许集合，非宿主 Cell 仍可能读取
运行 PF 的 host 值；例如 Linux 进程规划 Windows Cell 时把 `platform_system` 误判为 `Linux`。

2026-09-06 从 `experiments/mkdocs` 执行 `/home/llh/pf/.venv/bin/pf smoke`，run ID
`20260905T193408.439353Z-1191435-e2da78ab` 在 `loading project` 阶段以 exit `3` 返回
`unsupported managed marker dimension: platform_system`。没有建立 SourceSnapshot、Attempt、resolution
或 verifier 进程；该运行只证明 declaration admission 缺口，不证明 unittest 或其它 MkDocs contract 失败。

D029 已让 native `pylock.toml` 条件节点在创建空环境并观察实际解释器后，使用 actual Python patch
与 exact target 投影 `platform_system`、`os_name` 等字段。该修复不能直接代表 project declaration
planning：后者在 environment prepare 前建立完整 Cell matrix，还要承担 declaration overlap、候选、
report generation、跨宿主 merge 与 apply 写回语义。

本设计作出以下决定：

1. 建立唯一的 **portable Cell marker profile**，将可投影字段扩为
   `python_version`、`sys_platform`、`platform_machine`、`platform_system`、`os_name`。
2. 五个字段全部从 `Cell.python_minor` 与 exact uv target triple 确定；不得从 PF host 的默认 marker
   environment 补值。
3. `platform_system` 与 `os_name` 是既有 target triple 的派生别名，不增加 Cell 维度、配置、
   ApplySelector 或执行平台。
4. 受管 project declaration 与 test-group target self-reference 都采用新 profile；base、optional、
   required extra、overlap、active declaration 与 report/apply 重投影使用同一求值语义。
5. apply 继续只生成 canonical `python_version` / `sys_platform` / `platform_machine` selector；原始
   `platform_system` / `os_name` marker 保留，并与生成 selector 取 conjunction。
6. native pylock 继续使用 D029 的 actual-interpreter profile；portable Cell profile 不伪造
   `python_full_version` 或 implementation patch。
7. 通过固定 policy fact 隔离旧、新 marker evaluation evidence；不升级 Schema 1，不保留旧语义
   fallback、dual evaluation 或兼容 reader。
8. `ProjectLoader` 独占声明用途与 project admission 决策；marker module 独占 portable 表达式资格、
   target facts 与求值语义。资格检查不依赖 Cell，也不因声明不活跃而跳过。
9. 所有合法声明完整记录，portable 资格按整条 marker 表达式判断；不忽略未知条件，不静默降为非受管。
   fixed/unmanaged 与 external harness 保持现行准入，经显式区分的求值入口处理。

## 2. 两个 marker 投影阶段

PF 必须区分两个输入、时点和 authority 不同的投影阶段。

| 投影 | 输入时点 | 可用事实 | 用途 | owner |
| --- | --- | --- | --- | --- |
| portable Cell projection | project load / report / apply | target triple、CPython minor；optional group 另由 extra surface 控制 | portable declaration/self-reference activation、overlap、floor projection equivalence | project marker module；D001/D002 |
| actual resolution projection | environment prepare 内，inspect interpreter 后 | target triple、实际 CPython patch/implementation | native pylock active package graph | `adapters.uv_lock`；D012 |

portable profile 不读取 `InterpreterIdentity`，因此相同 Cell 在不同宿主、不同 invocation 或同一 minor
的不同 patch 上必须得到相同结果。actual profile 不反向改变 Cell、declaration 或 report target identity。
上述确定性只覆盖 portable 表达式；保留其他字段准入的既有求值路径见 §2.3，不属于新增的 portable
projection，也不借用尚未观察到的 actual interpreter facts。

### 2.1 Portable Cell marker profile

目标 profile 精确支持：

```text
M_portable = {
  python_version,
  sys_platform,
  platform_machine,
  platform_system,
  os_name,
}
```

值域固定为：

| uv target family | `sys_platform` | `platform_machine` | `platform_system` | `os_name` |
| --- | --- | --- | --- | --- |
| `*-unknown-linux-gnu` / `*-unknown-linux-musl` | `linux` | target architecture | `Linux` | `posix` |
| `*-apple-darwin` | `darwin` | OS-facing architecture | `Darwin` | `posix` |
| `*-pc-windows-msvc` | `win32` | OS-facing architecture | `Windows` | `nt` |

现行 architecture normalization 保持：Darwin `aarch64 -> arm64`；Windows
`x86_64 -> AMD64`、`aarch64 -> ARM64`；Linux 保留 uv target architecture。
`python_version` 精确等于 `Cell.python_minor`，例如实际解释器 `3.11.15` 的 portable 值仍为 `3.11`。

这张映射是 PF target semantics，不读取运行进程的 `sys.platform`、`os.name`、
`platform.system()` 或 `platform.machine()`。未知 target family 继续作为 ConfigurationError fail closed。

### 2.2 本次范围与整条表达式资格

五字段是本次迁移的明确范围，不是 PF 或 uv 的技术上限。以下字段留待后续设计：

| 字段 | 本次不纳入的依据 |
| --- | --- |
| `python_full_version` | 当前 Cell 只记录 minor；本次不引入 planning 阶段的精确 patch 选择与冻结 |
| `implementation_version` | 本次不引入实际 implementation version/patch 的 portable 绑定 |
| `platform_release` | target triple 不包含 kernel/OS release |
| `platform_version` | target triple 不包含 host system version |
| `implementation_name` | 可由 v1 的 CPython 限定确定，但本次不扩大五字段 profile |
| `platform_python_implementation` | 同上；actual resolution profile 的既有支持不变 |
| `extra` / `extras` / `dependency_groups` | 含义由 containing context 决定，不是 target platform projection |

uv 支持例如 `uv venv --python 3.11.10` 的精确版本请求，见
[uv Python version requests](https://docs.astral.sh/uv/concepts/python-versions/#requesting-a-version)。
PF 当前先建立 minor Cell，再创建环境并观察实际 patch；支持精确 patch planning 需要另行定义版本
选择与冻结、Cell/generation evidence identity、跨宿主一致性和写回覆盖。本次不改变这一流程，也不把
单次实际 patch 的 PASS 宣称为该 minor 全部 patch 的验证。

ProjectLoader 收集所有语法合法的 PEP 508 声明，保留原文、规范 marker identity 与 provenance，
再按声明用途决定是否要求 portable 资格；完整记录不意味着一定准入，也不包括非法语法或 PEP 508
未定义的变量。支持与不支持的字段均不从原始表达式中删除。

受管 project declaration 或 target self-reference 引用任一不支持字段，仍在 project load 阶段失败。
检查覆盖整个表达式，不能因逻辑分支短路、optional group 未启用或没有活跃 Cell 而跳过；不静默降为
非受管，也不因 source kind 猜测未知字段的值。managed/fixed/source 分类仍按现行规则决定。

例如 `os_name == "posix" and python_full_version >= "3.11.5"` 在 Linux Cell 上仍无法仅由 minor
确定整条表达式的真假。保留后半段文本不补齐 activation 或 floor evidence；本次不引入子表达式
投影、符号化 residual marker 或 unknown activation 状态。

### 2.3 Fixed/unmanaged 与 external harness

本次不收紧 D001 对 fixed/unmanaged project declaration 的现行准入，也不改变 D012 external harness
marker 的拥有范围。它们通过 marker module 的独立 contextual 求值入口处理，调用方按既有声明用途
选择该入口，不能把 portable 资格失败作为重试该入口的理由。

该入口完整求值原 marker，五个 `M_portable` 字段一律使用 §2.1 的显式 Cell 值；其余字段沿用现行
context/default environment 行为，`extra` 保持对空 extra 与 `cell.extra_surface` 逐项求值后取 any
的规则。它不扩大 `extras` / `dependency_groups` 等字段的 context 支持，也不伪造实际 interpreter
patch。host 默认值获取封装在此入口内，不泄漏给 Loader、harness、report 或 authorization。

只引用五字段的表达式在两个入口上必须等价；混合表达式整体仍不取得跨宿主、跨 PF interpreter patch
的确定性承诺。此入口承接当前仍支持的使用场景，不是旧 policy reader、portable fallback 或双重求值。

fixed/unmanaged project declaration 原样写回，不产生该声明的 floor 修改；但其 activation 仍参与
现有 overlap、active IDs 与整组 projection 等价检查，不能以“交给 uv”为由跳过。external harness 的
筛选与 relaxation 仍由 D012 拥有。后续若要消除剩余 host 依赖，须另行设计缺失事实、求值时点与
evidence 表达，本次不借固定 policy fact 宣称已经解决。

## 3. Project declaration 与 validation surface

### 3.1 受管 project declaration

`project.dependencies` 与 `project.optional-dependencies` 中受管声明的 marker 变量集合必须是
`M_portable` 的子集。`ProjectLoader` 独占 managed/fixed/source 分类与 project admission 决策，并在
建立 Cells 前调用 marker module 检查全部受管表达式的 portable 资格，包括未启用的 optional group。
支持字段集合、变量发现与资格规则由 marker module 独占，Loader 不自行解析或复制规则。

report reader、report builder 与 authorization 可以复用相同解析/资格入口校验表达式，但不能重新
分类声明、改写 managed ownership，或把 portable 失败转成另一种准入。ConfigLoader、CandidateBuilder、
resolver 与 editor 不建立第二套 marker 资格规则。

对每个 Cell：

```text
active(portable_declaration, cell) =
  (base declaration or optional group 已由 cell.extra_surface 启用)
  and qualified portable marker evaluates true for cell
```

对上述 portable 声明，该结果唯一决定其 `Cell.active_declaration_ids` 成员资格、同位置同名声明
overlap、managed coordinate presence、有效 declaration lower-bound anchor 与后续 floor projection。
不能让 resolver 的 host evaluation 修补或覆盖 planning 结果。

例如：

```toml
dependencies = [
  'colorama>=0.4; platform_system == "Windows"',
  'posix-ipc>=1; os_name == "posix"',
]
```

Windows Cell 只激活 colorama，Linux/macOS Cell 只激活 posix-ipc；同一完整 matrix 在任一宿主上规划
必须得到相同 active declaration IDs。

### 3.2 Test-group target self-reference

test-group 中 canonical distribution name 等于 target 的 self-reference marker 同样接受
`M_portable`。required extra `R(target, python)` 的现行含义不变，但其活跃性从 portable Cell facts 求值：

```toml
[dependency-groups]
test = [
  'demo[windows-tests]; platform_system == "Windows"',
  'demo[posix-tests]; os_name == "posix"',
]
```

Windows 与 POSIX target 可以形成不同 required base surface；最终 surface、Cell identity、active
declarations、SourcePlan、Attempt 与 report 继续通过现有字段闭合，不增加 hidden surface。
self-reference 仍不成为 external harness 或额外 source route；specifier、dynamic version 与 source
资格继续由 D001/D012 现行契约拥有。

### 3.3 Declaration overlap 与等价别名

PF 按实际 Cell activation 判断 overlap，不按 marker 文本是否相同判断。以下声明在 Windows Cell 上
重叠，必须继续作为配置错误：

```toml
'demo-lib>=1; sys_platform == "win32"'
'demo-lib>=2; platform_system == "Windows"'
```

同理，`os_name == "posix"` 与 `sys_platform == "linux"` 在 Linux Cell 上重叠，但前者还覆盖 Darwin。
PF 不建立 marker AST 等价证明器，也不把用户 marker 改写成另一字段；Cell matrix 上的直接求值是
overlap 与投影等价性的唯一语义判据。

## 4. Marker module、interface 与依赖方向

### 4.1 深 module seam

目标实现应把 portable qualification、target facts 与 Cell evaluation 集中在一个 in-process marker
module，提供纯计算的 portable interface，并显式隔离 §2.3 的 contextual 求值。外部 interface 为：

1. exact target triple 到具名、不可变 platform marker facts 的投影；
2. 不依赖 Cell 的 portable marker 解析/资格检查，返回已资格化的不可变表达式；
3. 已资格化表达式在一个 Cell 上的确定性求值；
4. fixed/unmanaged 与 external harness 的 `evaluate_contextual_marker(raw, cell)` 求值入口，语义限于 §2.3。

portable 使用形态为：

```python
marker = PortableMarker.parse(raw)  # 检查语法与整条表达式的支持字段，不需要 Cell
active = marker.evaluate(cell)     # 只使用 Cell minor 与明确 target facts
```

无 marker 表示恒真。parse 返回的对象只存在于进程内；report 继续保存现有 marker 字符串，reader
经同一入口重建，不增加 wire type 或序列化 AST。缓存可放在 module 内，调用方无需管理其生命周期。
解析/资格检查和求值分别报告结构化 marker 错误；使用场景、文件/group/item provenance 与命令错误
映射由调用方补充，见 §6。

调用方不接触 `packaging` marker AST、正则变量提取、`default_environment()`、architecture alias 表或
dict value 顺序。无需 adapter、Protocol、service locator 或 test-only port；测试与调用方穿过同一
interface。portable 路径不依赖 host 字段的值，contextual 路径的 host 依赖明确限定于其余字段。

`packaging.Marker.evaluate(environment=...)` 会把传入字段覆盖到默认 environment 上，见
[packaging marker evaluation](https://packaging.pypa.io/en/stable/markers.html#packaging.markers.Marker.evaluate)。
仅传入五字段不构成 portable 资格检查；必须先验证整个表达式，并保证每个可引用字段都来自显式 facts。
不要求重写 PEP 508 求值器，也不将依赖内部读取未使用默认字段等同于 PF 使用该字段作判断。

删除该 module 后，变量资格和 target 映射会重新散回 `ProjectLoader`、`harness`、`report`、
`authorization`、`terminal` 与 `adapters.uv_lock`，因此它满足 deletion test。底层 parser、AST 与 cache
保持私有；调用方只使用上述 interface。

### 4.2 Owner 与消费者

| module | 唯一负责 | 不负责 |
| --- | --- | --- |
| marker module | portable/contextual 入口的解析、表达式资格与求值、具名 target facts、结构化 marker 错误 | declaration 用途与 managed/fixed 分类、provenance、floor、wire、resolver graph |
| `ProjectLoader` | 声明用途与 project admission、选择求值入口、补充错误 provenance；建立 declarations、required surface、Cells 与 active IDs | 支持字段集合、变量解析、target value alias、report selector 生成 |
| `report` | 从 Cell floors 生成 canonical selector并在完整 TargetCells 上复证等价 | marker 值来源、host 探测 |
| `authorization` | current/report declaration semantics 与 activation partition 比较 | 重定义 marker、target facts 或 managed ownership |
| `harness` | external harness activation 与 D012 relaxation，使用 contextual 入口 | portable admission、host 值获取、target alias |
| `adapters.uv_lock` | actual interpreter + target 的 native package marker projection | project declaration planning、apply selector |
| terminal | 展示已结构化 selector/coverage 结论 | 从 mapping 顺序猜 selector identity |

现行 `marker_platform()` 返回无类型 dict，并有消费者通过 `.values()` 隐含依赖字段数与顺序。迁移必须改为
具名 facts/selector 消费；增加 `platform_system`/`os_name` 时不能让 terminal、authorization 或 report
的 ApplySelector 从二元组意外变为四元组。

`adapters.uv_lock` 复用 marker module 的 target-derived platform facts，再追加实际
`python_full_version`、implementation 与 patch facts；它继续独占 native profile 的变量资格、active
package filtering 与图闭合。不得把 actual facts 填回 `Cell` 或 portable project evaluator。

## 5. Report、merge 与 apply

### 5.1 原 marker 保留

RequirementDeclaration 已保存原始/规范 marker identity。搜索得到 floor 后，projected requirement 必须
保留原 marker，只替换 eligible lower-bound：

```text
before: colorama>=0.4; platform_system == "Windows"
after:  colorama>=0.4.6; platform_system == "Windows"
```

PF 不把它规范化为 `sys_platform == "win32"`，因为原始 marker 是用户的声明语义和 drift identity。

### 5.2 不增加 ApplySelector 维度

`platform_system` 和 `os_name` 都由 `(sys_platform, platform_machine)` 所在 target family 推导，不能区分
既有 ApplySelector 无法区分的新环境。因此：

- ApplySelector 继续是 `(sys_platform, platform_machine)`；
- GNU/musl 继续属于同一 selector；
- 跨 Python 或 selector floor 不同，仍用 canonical
  `python_version/sys_platform/platform_machine` 条件拆分；
- 每条生成条件与原 marker 取 conjunction；
- platform-scoped apply 的 complement 继续只针对 canonical selectors 生成。

例如 Windows 两种 architecture 得到不同 floor 时，可以生成：

```toml
'colorama>=0.4.6; (platform_system == "Windows") and (sys_platform == "win32" and platform_machine == "AMD64")'
'colorama>=0.4.5; (platform_system == "Windows") and (sys_platform == "win32" and platform_machine == "ARM64")'
```

具体括号与 canonical ordering 继续由 report projection owner 定义；示例只说明语义，不建立第二种
renderer contract。

### 5.3 完整 matrix 等价性

report builder 与 ApplyAuthorizer 对受管原声明和生成表达式必须使用同一 portable Cell evaluator；
对原样保留的 fixed/unmanaged 声明使用 §2.3 的同一 contextual 入口。入口选择依据现有声明 ownership
与生成来源，不依靠求值异常 fallback，不增加 report profile 字段。在完整 report TargetCells 上证明：

```text
projected active requirement multiset per Cell
==
intended original/floor requirement multiset per Cell
```

`os_name == "posix"` 跨 Linux 与 Darwin 的情况必须分别覆盖；不能只用运行 apply 的宿主验证。
证据缺失仍按现行 declared/platform-scoped 与 GNU/musl 规则处理；同 selector 内 observed floor 冲突、
投影覆盖不完整或重复活跃 requirement 继续 `UNREPRESENTABLE_PROJECTION` / apply blocked，不因新 marker
alias 放宽。若 contextual 求值与冻结 active IDs 不一致，仍按现行等价性/授权检查阻止，不能删除该声明
或猜测其值来凑齐投影；完整 matrix 检查不扩大 §2.3 对混合表达式的承诺。

### 5.4 Identity 与 Schema

portable marker 求值从 host-derived 改为 target-derived，会改变声明 activation、required surface 或
projection equivalence 的可能结果。`validation_contract_policy` 增加固定事实：

```json
{
  "project_marker_projection": "portable-cell-platform-v1"
}
```

它进入现行 evaluation policy/generation preimage，使旧报告不能与新语义混合 evidence 或取得当前 apply
authority。merge 拒绝跨 generation 合并；update_path 对不同 generation 整体替换，不能继承旧 Cells，
保持 D014 的现行规则。该值不是用户配置，不重复保存 marker environment 或 activation table。

RequirementDeclaration、TargetCell、active declaration refs 与 projection 已能表达目标结果，因此：

- `schema_version = 1` 不变；
- v1 digest prefix 不变；
- JSON Schema 不增加字段；
- examples 只因 policy preimage/digest 和已有引用变化而重生成；
- 不提供旧 policy fallback、dual reader、report migrator 或跨 generation 合并。

PF 未发布且新受管 marker 过去在 project load 即失败，不需要为其建立兼容入口。固定 policy fact 仍用于
隔离过去可能由 fixed/unmanaged marker 的 host-derived evaluation 形成的 evidence。

## 6. Failure 与命令行为

新增字段不改变 disposition、FailureRecord 或退出码：

- 合法 `platform_system` / `os_name` 受管声明正常进入 smoke/check/search/minimize planning；
- project planning 中 marker 语法错误、portable unsupported variable、unknown target family 或不可求值
  比较均为 ConfigurationError、退出 `3`；
- 上述 project planning 配置失败发生在 SourceSnapshot、Attempt、resolution、installation、ty 与 verifier 之前；
- resolver/build/install/tool 失败继续按 D005/D012 分类，不能因 marker 新支持而改称 Rejection；
- apply 对 policy、declaration activation 或 projection mismatch 的阻止不受 `--force` waiver。

marker module 统一把解析失败、portable 资格失败、不可求值比较与缺失求值事实表达为结构化错误，不向
调用方泄漏 `InvalidMarker`、`UndefinedComparison`、`UndefinedEnvironmentName` 等依赖异常。
例如 `platform_system ~= "Windows"` 或 `os_name ~= "posix"` 可以通过语法解析，但比较无法求值，
必须报错而非返回 False、忽略分支或换用 contextual 入口。parse 不需要 Cell；依赖具体值的错误在
evaluate 时报告。

ProjectLoader 将 marker 错误映射为 ConfigurationError，指出 usage（managed dependency、target
self-reference 或 preserved declaration）与声明定位；unsupported-variable 错误指出首个排序后的变量，
self-reference 保留 root/member/group/item provenance。report reader、authorization 与 harness 在各自
公共入口补充相应语境，保留现行配置/报告/授权错误分类，不能转成候选 Rejection。native pylock 仍由
`adapters.uv_lock` 转换为现行 UvLockError 与 D005/D012 disposition，不套用 project planning 退出规则。
错误不得泄漏绝对路径或求值取得的 host marker values，也不固化第三方完整异常文案。

## 7. 测试与资格策略

测试以 module interface 和现有公共消费者 seam 为主，不固化私有 helper 或 dict ordering。

### 7.1 Marker module

- Linux glibc/musl、Darwin、Windows × x86_64/aarch64 的完整五字段矩阵；
- `python_version` 只取 Cell minor；
- 在 Linux host 上投影 Windows/Darwin Cell，证明结果不读取 host；
- 独立于 Cell 的 portable parse 对非法 marker 与不支持变量 fail closed，变量只出现在字符串值中时
  不误报；unknown target 在 facts/evaluation interface fail closed；
- `platform_system`/`os_name` 与 canonical fields 的 conjunction/disjunction、大小写和值不匹配；
- 可解析但不可求值的比较返回结构化错误，不冒泡依赖异常或变成 False；
- contextual 入口完整求值混合表达式、显式覆盖五字段并保留现行 extra 行为；相同五字段表达式与
  portable 入口等价，改变剩余 host facts 不影响 portable 结果。

### 7.2 ProjectLoader 与 validation surface

- base/optional managed declarations 在 Linux/Darwin/Windows Cells 上产生正确 active IDs；
- `platform_system` 与 `sys_platform`、`os_name` 与 `sys_platform` 的 overlap 矩阵；
- self-reference 按 target 激活 required extras并生成确定性、去重的 Cell surfaces；
- fixed/unmanaged 完整收集与原样保留、source routes、external harness 与现行 `extra` 规则无回归；
- 受管声明/self-reference 的 `python_full_version`、`platform_release`、`platform_version`、implementation
  与 context fields 仍被拒绝；覆盖未启用 optional group、不可达逻辑分支与无活跃 Cell，不静默降为非受管；
- 公共 ProjectLoader 与 CLI 入口覆盖非法比较，证明配置错误、usage/provenance 和退出 `3`，且没有
  SourceSnapshot、Attempt 或 verifier 执行；活跃 specifier 的 self-reference 资格保持现行规则。

### 7.3 Report、authorization 与 terminal

- 单一 floor 保留原 marker；跨 Python/architecture floor 使用 canonical selector；
- `os_name == "posix"` 同时覆盖 Linux/Darwin，Windows 不活跃；
- platform-scoped complement、GNU/musl selector 等价和同 selector floor 冲突保持；
- projected group 完整 Cell multiset 等价、重复/缺失/额外 activation 被拒绝；
- portable 受管声明与 contextual preserved 声明混合的 group 使用正确入口；原样保留的声明仍参与
  等价性与 drift 检查，不能因不产生 floor 而跳过；
- policy identity、generation、merge/update/apply 隔离旧 profile；Schema/examples 无漂移；
- terminal 只消费具名 ApplySelector，不依赖 platform facts 数量或 iteration order。

### 7.4 Resolution 与端到端

- native pylock actual profile 的现行 Python patch、implementation、platform marker qualification 全部保持；
- public EnvironmentFactory 路径证明同一 target 的五字段 activation 在 project planning 与 native active
  graph 中一致；native patch/implementation 条件仍按实际 interpreter 独立求值；
- 真实或最小可发布 fixture 的 smoke/check/search 各至少一个 host Cell 通过
  `platform_system` managed declaration planning 并运行到其既有 command-specific terminal；必须至少
  有新增字段声明在该 host Cell 实际活跃，不能仅以 Windows 声明在 Linux 上被跳过作为端到端证据；
- MkDocs dogfood 在补齐独立的 test-group/verifier 配置后，不再因 colorama 的 `platform_system`
  在 project load 失败；其后结果按真实阶段单独记录，不把其它配置或测试失败归因于本设计。

## 8. 验收标准

1. portable Cell profile 精确包含五个字段，所有值从 Cell minor/exact target 得出；portable 表达式跨宿主
   规划相同，不读取同名 host default。其余字段与精确 patch planning 明确留待后续。
2. managed base/optional declarations 接受 `platform_system` 与 `os_name`，正确建立 active IDs、overlap、
   candidate coordinate 与 lower-bound anchor；旧三字段行为无回归。
3. target self-reference 接受新增字段并正确形成 required extras/surfaces；两类 portable 用途均在建 Cell
   前检查完整表达式，unsupported fields 不因不活跃而漏检，不静默降级；活跃 specifier、dynamic version
   与 source 资格保持 D001/D012 规则。不可求值比较得到配置错误、provenance 与 CLI 退出 `3`。
4. ProjectLoader 独占声明用途与 admission，marker module 独占 portable 表达式资格与求值，提供无 Cell
   的资格入口和纯计算 evaluator；contextual 入口明确分离，fixed/unmanaged 与 harness 准入不收紧，
   五字段显式覆盖、其他字段无新增可移植承诺。消费者复用具名 facts，不复制 alias 表或依赖 mapping order。
5. report/apply 保留原 marker，只生成 canonical 三字段 selector，并在完整 TargetCells 上证明
   projected 与 intended original/floor multiset 等价；preserved 声明仍参与检查，partial/scoped/libc
   冲突规则不变。
6. 新固定 policy fact 隔离旧、新 generation；Schema 1 不扩形，reader、merge、update、apply、生成 Schema
   与 examples 全部通过。
7. D029 actual-interpreter pylock profile、resolution graph、Failure/disposition、search algorithm、CLI 数值
   退出码与 verifier contract 不变。
8. focused marker/project/report/authorization/uv tests、Ruff、ty、Python 3.10–3.12 全套、coverage、build、
   schema/example no-drift、Markdown links 与 whitespace checks 全部通过；Plan 记录精确命令和结果。

## 9. 非目标

- 不支持五字段之外的 portable declaration projection，包括 `python_full_version`、implementation
  name/version、`platform_python_implementation`、OS release/version 与 extra/context fields；
- 不引入精确 Python patch 配置、planning 版本选择/冻结、对应 evidence identity 或 patch selector；
- 不引入混合 marker 的子表达式投影、unknown activation、自动降为非受管或依赖失败后的宽松重试；
- 不增加 OS release、kernel、libc、Python patch 或 implementation Cell axis；
- 不把 GNU/musl 拆成新的 PEP 508 ApplySelector；
- 不把用户的 `platform_system` / `os_name` marker 自动改写为 canonical marker；
- 不改变 candidate、coordinate search、predecessor、artifact、harness relaxation 或 verifier 算法；
- 不增加 marker 配置开关、alias registry、fallback profile 或 universal PEP 508 evaluator；不为 portable
  字段猜测 host 值，§2.3 的既有非 portable context 行为是明确保留的范围；
- 不让本机执行不匹配 host target 的 Cell；跨宿主覆盖仍由 report/merge 协议取得；
- 不在本 Design 中修复 MkDocs 的 test dependency group、unit/integration oracle 或其它实验配置。

## 10. 实施与 owner 归并

本 Design 获得接受后，先建立 durable Plan，逐项映射 §8 到 ordered implementation slices、interface/
ownership migration、owner docs、Schema/examples、测试与证据槽位。建议切片顺序为：

1. marker module、portable facts、无 Cell 资格入口、contextual 入口分离与结构化错误；
2. ProjectLoader managed declaration/self-reference 与 active Cell migration；
3. report/authorization/terminal 的具名 selector 与等价性迁移；
4. uv-lock target facts 复用及 policy/Schema/examples；
5. 公共行为、MkDocs dogfood、全套验证与逐 AC 审计。

完成实施后，把稳定规则分别吸收到：

- D001：五字段范围、整条表达式资格、Cell activation、preserved declaration 准入、原 marker 保留与
  ApplySelector 不变；
- D002：admission/marker 资格职责、无 Cell parse 与 evaluation interface、contextual 入口、错误语境
  映射、消费者与依赖方向；
- D012：portable project profile 与 actual pylock profile 的时点/authority 分离、external harness 的
  contextual 求值范围及既有 native 错误分类；
- D014：固定 policy fact、generation/merge/apply 隔离及 Schema 1 不扩形。

同一完成变更中归档 D034 与对应 Plan，并更新文档索引。Design 未接受或 Plan 未建立时，不开始上述
生产、schema、owner 文档或实验配置修改。

实施结论：五字段 profile、消费者与错误契约迁移已完成；P039 保存逐项验收和三版本全套证据。
MkDocs 已通过 marker admission，独立缺失 test group 仍按非目标保留，不声明其 verifier PASS。

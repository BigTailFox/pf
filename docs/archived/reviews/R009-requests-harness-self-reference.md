# R009 — requests dogfood 与 harness 自引用评审

- **状态：** 已解决并归档
- **日期：** 2026-09-05
- **性质：** 非规范性产品与投影评审；不定义命令、算法、Schema 或 module interface，不授权实施
- **对照：** PF `0.1.0`，工作树 `684df373070fab081e8db195d434c722c731cc0b`
- **输入：** [E003](../../experiments/E003-requests-dependency-validation.md) 的运行事实，以及随后对
  test-group 自引用、required extras、extra 依赖与 `HARNESS_CONFLICT` 的讨论
- **现行契约所有者：** [D001](../../designs/D001-pf.md)、
  [D003](../../designs/D003-pf-search-algorithm.md)、
  [D005](../../designs/D005-pf-failure-and-diagnose.md)、
  [D012](../../designs/D012-pf-harness-relaxation.md)
- **与既有文档的关系：** E003 保存三次命令的 journal、snapshot 与 incomplete report；E001 保存
  floor 相对 validation contract 的自举证据。本文接收 E003 的投影缺口，并把讨论收敛成产品判断。
  本文不把 E003 的 incomplete 终态改写成 floor，也不跟踪实施状态。

> **归档说明：** 本评审由 [D028](../designs/D028-pf-validation-contract-surfaces.md) 收敛并实施，
> 实施与验收见 [P034](../plans/P034-pf-validation-contract-surfaces.md)。Marker 资格、static Rejection
> 与 satisfaction 以现行 owner 为准；下文保留评审时的判断，不再跟踪实施状态。

## 1. 最终结论

PF 求的是 **configured validation contract 下经过验证的最低依赖向量** `floor_C(P)`，不是脱离
oracle 的 intrinsic compatibility floor。`C` 包含 Python、extra surface、artifact policy、harness
（含 D012 规范化后的 oracle）、ty、test-command 与 environment policy。换 suite 或换 harness
得到不同 floor，不是缺陷。

设计原则：

```text
A self-reference to the current project in the validation dependency group
defines a required project surface, not an external harness dependency.
Required extras are included in every Cell generated for that validation contract.
```

test group 里对当前项目的自引用 extras 不是 harness，而是 validation contract 对 project
surface 的最低要求。Discovery 先抽出 `required extras` `R`，policy 只探索 `E \ R`。每个可执行
Cell 的 surface 是 `R ∪ explored`。这不是「test group 改写了 extra-policy」，而是两层：

```text
oracle_required_extras = R
exploration_extras     = declared_extras - R
effective_surface      = R ∪ explored_surface
```

E003 的失败因此从模型上消失：`requests[socks]` 进入 `R = {socks}`，不再进入 `_direct_harness`。
裸 `[]` surface 在该 oracle 下不具备执行条件；声称测 base、实际装 `requests[socks]` 会让 Cell
identity 与验证对象不一致。`none` 的含义是「不主动探索 oracle 要求之外的 extras」，不是
「什么 extra 都不启用」。

所有权与否决权分开：

```text
project owns the version; harness owns the right to reject the resulting environment.
```

`A ∈ G(P)` 时，`E(P)` 必须精确保留 project-selected A。Harness 不能改写该版本。若规范化后的
**外部** harness 仍无法接受这个 pin，`HARNESS_CONFLICT` 是 **REJECTED**，搜索继续。这与
`VERIFIER_EXITED_NONZERO` 在搜索层等价消费。`INDETERMINATE` 只留给测量本身不可用或不可解释。

Direct / transitive 重叠只剩真正的外部 harness。v1 只能安全放宽显式 direct harness 下限；传递
metadata 不 rewrite。保守 floor 可以接受，predecessor 的 cause 不能伪装成 runtime 不兼容。

现行 D005 已把 `HARNESS_CONFLICT` 列为 Rejection 资格 cause，D003 已按 `REJECTED` 继续搜索。
要改的是 Cell surface 生成、REJECTED 的定义、D012 的原则句，以及 overlap 节点的 provenance。
开箱默认值另见 §8。本文不授权实施。

## 2. E003 接收的事实

在 `expirements/requests`（psf/requests `dae7ef63`）上依次执行 `pf smoke`、`pf check`、
`pf search`。20 个 Cell、6 个受管依赖；三次命令均在 baseline `resolve-environment` 以
`TOOL_FAILURE` / `resolution-plan-invalid` 退出 4。uv `pip compile` 约 20ms 退出 0；
`project_plan_digest` 非空，`environment_plan_digest` 为空。search 写出 incomplete
`package-floor.json`，`UNREPRESENTABLE_PROJECTION`，无 floor。

失败点是 `_direct_harness`：test group 的 `requests[socks]` 被收成名为 `requests` 的 harness；
path 源项目被滤掉后，harness 找不到带版本的 `requests`。diagnose 只展示 process exit 0 与
`TOOL_FAILURE`，不展示被吞掉的 `ValueError`。这是投影缺口，不是 requests 测试失败，也不是
声明下界已被证伪。完整计数与 run-id 只由 E003 保存。

## 3. Required extras

### 3.1 归一化

```text
dependency group
        ↓
self-reference extraction
        ├─ current_project[extras]  →  required Cell extras R
        └─ everything else          →  external harness
```

三类东西分开：project surface requirements（`project[A]`）、project dependencies（A 的依赖）、
external validation harness（pytest / 插件 / httpbin 等）。不要用 FirstPartyHarnessReference、
environment-only extras 或 shared-node 特判去补同一件事。

requests 的 `test = ["requests[socks]", "pytest", ...]` 归一化为
`R = {socks}`，外部 harness 只剩 pytest 族。`requests[socks]` 不进入 `_direct_harness`。
`PySocks` 在每个可执行 Cell 的 `G(P)` 里，由搜索拥有，不再同时是「harness 间接引入」。

Discovery 记录来源，例如：

```text
required_extra: socks
source: dependency-group test / declaration requests[socks]
```

### 3.2 与 extra-policy 合成

设声明 extras `E = {A, B, C}`，`R = {A}`。policy 只处理 `selectable = E \ R`。
`effective_surface = R ∪ explored`。现行 D001 的 `all` 已是「base + 各 extra + 全集」，不是
「只测全部 extras 叠在一起」；引入 `R` 后保持该覆盖：

| extra-policy | 可执行 surfaces |
| --- | --- |
| `none` | `[A]` |
| `each` | `[A]`，`[A, B]`，`[A, C]` |
| `all` | `[A]`，`[A, B]`，`[A, C]`，`[A, B, C]` |

requests：`R = {socks}`，`selectable = {security, use_chardet_on_py3}`。默认 `each` 从
`5 × 4 = 20` 个 Cell 变成 `5 × 3 = 15` 个可执行 Cell，且每个 Cell 的 identity 与实际验证对象一致。

`extra-surfaces` 的自定义组合同样先并上 `R`，再与 policy 展开取规范并集。未知 extra 仍失败。

### 3.3 边界

1. **多个自引用取 union。** `project[A]` 与 `project[B]`，或 `project[A,B]`，都是
   `R = {A, B}`，不生成两套 oracle。
2. **marker 按 Python Cell 求值。** `project[A]; python_version < "3.13"` 在 3.12 得
   `R = {A}`，在 3.13 得 `R = {}`。surface 生成对 Python minor 敏感；先按 Python 求
   `R`，再展开，或展开后 canonicalize / 去重。
3. **未声明 extra fail closed。** `project[does_not_exist]` 是 invalid validation surface，
   在 discovery/qualification 失败，不是 `HARNESS_CONFLICT`，也不等 uv。
4. **裸自引用可消去。** `project` 无 extras / specifier / marker 时，对 surface 无增量，
   `R` 不变，且不进入 harness。
5. **specifier 不得静默丢失。** `project[A]>=2` 的 extras 进入 `R`；`>=2` 用当前项目
   identity/version 做 qualification，不满足则 validation contract conflict。requests 的
   `requests[socks]` 无 specifier，v1 实现简单。URL / Git 自引用仍 fail closed。

不要：把自引用留在 harness 再投影例外；只把 extras 并进 environment、Cell 仍声称 `[]`；允许
`requests.version is None` 的假 harness。

## 4. 外部 harness 重叠

自引用被吸收进 project surface 之后，真正剩下的 overlap 只有外部直接 harness（`A>=5`）或
外部传递（`foo → A>=5`）。

`A ∈ G(P)` 时，environment 用 `A==G(P)[A]` 钉死。`G(P) ⊆exact E(P)` 是硬约束。Harness 只能
接受或否决这个 pin，不能把它升级到 5.0。A 不因出现在外部 harness 图里就不再是受管坐标。

Direct overlap：D012 可 relax eligible 下限。resolve 生效的是 `A==1.2`，不是
`A==1.2 ∧ A>=5`。原文留作 observer provenance。不要把 project highest 选中的 `A==10` 记成
独立 `HarnessSelection` 再当 `U_B[A]`。更干净的是
`HarnessRequirementObservation(declaration=A>=5, satisfied_by=ProjectNode(A==10))`。

Transitive overlap：v1 不 rewrite 传递边。与 pin 冲突则 `HARNESS_CONFLICT` / `REJECTED`，
搜索继续。不能把「写成直接 harness 才不挡 floor」说成产品语义；那是 v1 对 oracle 的可放宽
能力边界。

extra 带进的依赖仍是 `project.optional-dependencies` 的发布契约，不按 harness relax。若希望
某依赖「只为测试、不约束 `P`」，应写成外部 test group 的直接 harness。

## 5. 三态与搜索消费

REJECTED 不再定义为「已证明项目自身不兼容」，而定义为：

> The Attempt cannot satisfy the configured validation contract.

| 态 | 含义 | 例子 |
| --- | --- | --- |
| PASS | Attempt 满足 configured validation contract | 环境建立且 verifier 正常退出 0 |
| REJECTED | 有确定证据：Attempt 不满足该契约 | project UNSAT、harness UNSAT、ty hard regression、pytest nonzero |
| INDETERMINATE | 测量本身不可用或不可解释 | tool crash、timeout、无法解释的输出、投影失败、基础设施故障 |

`HARNESS_CONFLICT` 与 `VERIFIER_EXITED_NONZERO` 在搜索层等价：都是 deterministic contract
failure。PF 不必猜「换一套测试工具也许能过」。用户配置的 test group 就是 oracle 的一部分。

拒绝「harness conflict → CellIndeterminate」。1–4 为 harness conflict、5–20 通过时，floor=5、
predecessor `4 → HARNESS_CONFLICT` 比「我不知道」有用。即使 `A==3` 在另一套工具下能跑，用户
当前也无法用他的 oracle 验证它；把 5 当作保守 floor 合理。

报告不得把该边界伪装成 runtime 破了。用户可以看见 `A>=5`，但 predecessor 必须是
`HARNESS_CONFLICT`，不是 verifier failure。保守可以，丢 provenance 不行。

E003 的 `resolution-plan-invalid` 仍是测量失败：PF 无法投影 environment plan，不是契约
否决。它保持 `INDETERMINATE`，直到自引用被吸收为 required extras。

## 6. 现行契约与待写入

已对齐、不必为 disposition 改搜索算法：

- D005 已将 `HARNESS_CONFLICT` 列为 Rejection 资格 cause
- D003 已将 `REJECTED` 当负向边界并继续搜索
- D005 diagnose 文案已说「测试依赖装不上，应调整测试依赖」
- D005 已规定 Rejection 不声称单点根因
- D001 的 `all` 已是 base + 各 extra + 全集；与 `R ∪ explored` 相容

尚未写成规范的是：

- D001：floor 是 `floor_C(P)`；required extras 是每个可执行 Cell 的 mandatory base；
  `none` 表示不探索 `R` 之外的 extras；`resolve-artifact` 默认 `any`；`test-command` 省略时
  默认 `["pytest"]`
- D005：REJECTED / INDETERMINATE 按契约成败 vs 测量失败切开
- D012：自引用不是 harness；版本归项目、否决权归外部 harness；direct relax 是 oracle 规范化
- overlap 节点的 observer provenance，以及 `A ∈ G(P)` 时不要伪造独立 `HarnessSelection`

若后续建立 Design，稳定规则只由上述 owner 接收。R007 的优先级表不因本文自动重排。

## 7. 非目标

本文不要求、不授权：

- 重写传递 harness metadata，或把 PF 做成通用 dependency-graph rewriting engine
- FirstPartyHarnessReference、environment-only extras，或把自引用留在 harness graph
- 生成 oracle 无法执行的裸 `[]` Cell，再在 environment 里偷偷启用 `R`
- 因 E003 修改 D003 坐标下降、ty 基线或 configured verifier authority
- 删除 requests 的 `requests[socks]`，或把动态版本改成静态 `project.version`
- 把 `HARNESS_CONFLICT` 改成 `INDETERMINATE` 并停止 Cell
- 把 `all` 收成「只测全部 extras 叠在一起」
- 在投影修复前重跑 E003 三条命令并声称新的兼容性证据

修复 required-extras 归一化之后重跑 requests，记录口径仍由 E003 §7 约束；并核对 Cell 矩阵
是否变为 Python × 3 个可执行 surface。

## 8. 开箱默认值

E003 的 requests 树必须写 `resolve-artifact = "any"` 才能进入解析。现行默认 `wheel` 会拒绝只提供
sdist 的包，也会挡住需要本地编译的路径。从开箱即用看，默认应是 `any`：能解析就先解析，用户要
收窄再显式设 `wheel` 或 `sdist`。这是 D001 默认值变更，不是 requests 特例。

`test-command` 今天没有静态默认，Quick Start 被迫先写 `[tool.pf] test-command = ["pytest"]`。
短期省略时应默认 `["pytest"]`，与 Python 生态的常见 oracle 对齐。后续可以再加自动扫描（例如
探测 `pytest` / `nox` / `tox`），但扫描是增强，不能替代一个可预测的省略默认。默认 `pytest`
仍是 configured contract 的一部分：用户换命令就会得到不同的 `floor_C(P)`。

`test-group` 已经默认 `"test"`，group 本身可为空。README 必须把「省略即用名为 `test` 的
dependency group」写成开箱规则，避免读成还要再配一次 group 名。本文不把该默认改成别的名字。

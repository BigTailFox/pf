# D035 — PF 可选 Test Group 与空 Harness 快路

- **状态：** 已实施并归档；稳定规则已由下列现行 owner 接管
- **实施与验收：** [P040](../plans/P040-pf-optional-test-group.md)
- **日期：** 2026-09-06
- **核对基线：** `cdeb37b`；PF worktree 起草前干净
- **来源：** 用户要求 `test-group` 可以不存在；省略配置时自动查找 `dev` 或 `test`，都不存在时按空 test group 执行并跳过 harness resolution/install augmentation
- **现行 owner：** [D001](../../designs/D001-pf.md)、[D002](../../designs/D002-pf-implementation.md)、
  [D005](../../designs/D005-pf-failure-and-diagnose.md)、[D006](../../designs/D006-pf-cli-enhancement.md)、
  [D012](../../designs/D012-pf-harness-relaxation.md)、[D014](../../designs/D014-pf-report-schema.md)
- **历史依据：** [D023](D023-pf-configuration-model.md)、
  [D028](D028-pf-validation-contract-surfaces.md)

本文定义一次临时性的配置默认、project planning、environment preparation 与报告证据迁移。
目标是让不声明 test dependency group 的项目仍可执行配置的 verifier，并让没有活跃 external harness
的 Cell 不再做无意义的第二次 resolution。本文按本次用户授权确立目标契约；先建立 durable
Plan，再修改生产代码、Schema、README 或 owner Design。

## 1. 问题与决策摘要

迁移前 `ConfigLoader` 把省略的 `test-group` 物化为 `"test"`。`ProjectLoader` 记录该名称是否在 workspace
root 或目标 member 中存在；`smoke`、`check`、`search` 与 `minimize` 在 SourceSnapshot、Attempt、
resolution、installation 和 verifier 前统一拒绝不存在的 group。即使 group 存在但为空，
`EnvironmentFactory` 仍固定执行：

```text
ResolveProject -> ResolveEnvironment(exact project + empty harness) -> Install(environment plan)
```

因此，没有 test group 的项目无法让自带或显式配置的 test command 成为实际证据；空 group 还支付一次
不增加约束的 environment resolution。现行实现没有独立的“install harness”进程；所谓跳过 harness
安装，是不构造 harness-augmented environment plan，并直接安装已经通过资格检查的 project plan。

本设计作出以下决定：

1. 显式 `test-group = "name"` 继续表示唯一指定名称；该名称不存在时得到空 test group，不再配置失败，
   也不自动回退到其他名称。
2. 省略 `test-group` 时，`ProjectLoader` 按固定优先级 `dev`、`test` 查找；候选名称在 workspace root
   或目标 member 任一处出现即算存在，选择第一个存在的名称。两者都不存在时选择结果为 `None`，表示
   空 test group。
3. 一个名称一旦选中，root/member 同名 group 的组合、`include-group` 展开、target self-reference 与
   external harness 分离继续遵守 D001/D012。不得把 `dev` 与 `test` 自动合并。
4. 删除 full-evaluation 对 group existence 的直接准入；有效的 test command 仍为必需，并继续由
   `TestConfig` 在 project load 时校验。
5. 对每个 Cell 计算活跃 external harness。若为空，`EnvironmentFactory` 不调用 harness normalization
   或 `resolve_environment`，直接安装 project plan；若非空，保留现行“两次 resolution、一次 install”。
6. test group 中只有 target self-reference 时，required extras 仍进入 `Cell.extra_surface`，但它不构成
   external harness；相应 Cell 走 project-only 快路。
7. project-only 与 harness-augmented 两条成功路径必须在 Attempt、Proposal、EnvironmentIdentity、report
   与失败 stage 中可区分，不虚构一次没有发生的 environment resolution。
8. 新固定 policy fact 隔离迁移前后的 evaluation evidence。PF 是 pre-release，不提供旧默认、group
   existence gate、双分支 reader 或兼容 alias。

## 2. Test group 选择语义

### 2.1 显式配置

root/member 的 `[tool.pf]` 仍按普通字段合并；较高 layer 的显式 `test-group` 整值替换较低 layer。
值必须是非空字符串。合并后若得到显式名称 `G`：

```text
selected_test_group = G    if G exists in root or target member
selected_test_group = None otherwise
```

显式缺失与确实为空的同名 group 在 harness 内容上等价：两者都没有 self-reference 或 external harness。
二者的 SourceSnapshot 仍保留各自原始 TOML，不能借此复用不同源码快照的证据。显式缺失不触发 `dev` /
`test` 搜索，也不产生警告或单独退出码。

### 2.2 省略配置

省略值不再由 `ConfigLoader` 猜成一个实际 group name。`ProjectLoader` 看到 root 与目标 member 的 group
keys 后按以下唯一算法选择：

```text
for candidate in ("dev", "test"):
    if candidate exists in root or target member:
        selected_test_group = candidate
        break
else:
    selected_test_group = None
```

“存在”只检查 group key，不检查展开后是否有 requirement；空数组也会选中该名称。如果 `dev` 与 `test`
都存在，固定选择 `dev`。如果 root 只有 `test`、member 只有 `dev`，仍选择 `dev`，只组合 root/member
中名为 `dev` 的声明。用户要选择 `test` 时可以显式写 `test-group = "test"`。

该优先级是产品语义和 identity policy 的一部分，不读取 uv `default-groups`，不扫描任意 group name，
也不根据 test command、文件名、已安装 distribution 或 group 内容打分。

### 2.3 展开与空结果

`ProjectLoader` 只把选中的名称作为 PF test group 展开。`selected_test_group = None` 时不调用 PF 的 group
expander，不解析 harness requirement，不建立 target self-reference、external harness declaration 或
harness source route。未选中的 group 不属于 PF harness contract；底层 uv 对整个项目配置的独立资格检查
不因此被绕过。

选中名称存在时继续完整展开 root/member 与 `include-group`。展开为空、全部条目都是当前 Cell 不活跃的
external harness，或展开后只有 target self-reference，都可能令某个 Cell 的活跃 external harness 为空；
环境快路按 Cell 的实际活跃 external declarations 决定，而不是只看 group 是否存在或全局 tuple 是否为空。

## 3. Config 与 Project interface

`ConfigLoader` 继续独占 raw `[tool.pf]` 校验和 root/member merge，但不读取 dependency-group inventory。
目标 `TestConfig` 保存用户选择意图：

```text
TestConfig.group: str | None
```

`None` 只表示 raw/effective 配置省略，不能同时表示 `ProjectLoader` 已决定没有 group。`ProjectLoader` 在
唯一持有 root/member group inventory 的位置完成 §2 选择，并让 `PackagePlan` 暴露：

```text
PackagePlan.selected_test_group: str | None
PackagePlan.harness_requirements: tuple[HarnessRequirement, ...]
```

删除 `PackagePlan.test_group_present`；调用方不再从 bool 与 `config.test.group` 拼回实际选择。
`selected_test_group` 是 planning 事实，`harness_requirements` 仍只含 external direct harness。
required project extras 继续只通过 `Cell.extra_surface` 暴露。

`require_full_evaluation_contract` 的 group-existence 规则删除。`smoke`、`check`、`search` 与 `minimize`
不得在 snapshot/Attempt 前因 `selected_test_group is None` 失败。显式或默认 `test-command` 的非空、无 shell、
禁止 `uv run` 与 configured-verifier authority 保持不变；没有 group 不会自动安装 pytest，也不会替换命令。
若 project graph 没有提供命令所需工具，实际 start/exit 事实继续按 D005/D007/D013 分类。

## 4. Project-only environment preparation

### 4.1 分支条件与执行序列

`EnvironmentFactory.prepare` 仍是上层唯一环境准备 interface。创建并检查解释器、建立 ResolutionContext /
Attempt、执行 project resolution 和 artifact/source 复证后，按当前 Cell 的
`AttemptIdentity.harness_declaration_ids` 分支。

有活跃 external harness 时保留现行序列：

```text
ResolveProject(P, attempt strategy) -> G(P)
ResolveEnvironment(P + Exact(G(P)) + active harness, highest) -> E(P)
Install(E(P))
Inspect installed graph == E(P), with G(P) exact-preserved
```

没有活跃 external harness 时采用：

```text
ResolveProject(P, attempt strategy) -> G(P)
Install(G(P))
Inspect installed graph == G(P)
```

project-only 分支不得调用 `original_harness`、`relax_harness` 或 `UvOperations.resolve_environment`，不得生成
environment resolution request/cache entry，也不发出 `resolving environment` activity。它发出
`installing project plan`，安装 failure 的 stage 为 `install-project`；installed graph 与 project plan
不一致时使用 `inspect-project-plan`。有 harness 的既有 `install-environment`、
`inspect-environment-plan` 与活动文案保持。

`install_resolution` 仍只安装已资格化 native `pylock.toml`，不重新开放 resolution。project-only 分支
仍必须安装 target project 及 project graph，随后才可运行 ty、runtime witness 或 configured verifier；
“空 test group”不表示跳过整个环境安装。

### 4.2 Baseline 与后续请求

Highest project-only prepare 成功后建立与当前 Cell 绑定的 empty `HarnessBaseline`：declaration IDs 与
observations 都为空。`LowestDirectResolution` 与 `ExactSelection` 继续携带该 baseline，因此 request
interface、search baseline handoff 与 Cell-match invariant 不新增 optional 分支。后续 project-only Attempt
仍使用现行 original/relaxation harness policy identity 和 empty active declaration IDs；实现可以绕过纯
harness 变换，但不能省略 baseline digest 对 lower/exact request 的绑定。

baseline 的 Cell 与 active declaration IDs 必须在分支前独立复证；空分支也拒绝非空 observations，
不能依赖被跳过的 `relax_harness` 完成这些准入检查。配置错误应在创建运行资源前失败。

只要当前 Cell 至少有一条活跃 external harness declaration，就必须执行完整 D012 路径；不得因该
requirement 已由 project graph 同名节点满足而走快路，因为 environment resolution 仍需证明 specifier、
source 与 exact ownership 的共同可满足性。

### 4.3 Failure 边界

project-only 分支没有 environment resolution，故不能产生 `HARNESS_CONFLICT @ resolve-environment`。
Project resolution 的 certified UNSAT 仍是 `RESOLUTION_CONFLICT @ resolve-project`；project plan 安装、
graph inspection、test command 缺失或 verifier 启动失败继续是各自的 Indeterminate/terminal 事实，不能
反推 harness 或 dependency conflict。

## 5. Identity、report 与 Schema 1

`EnvironmentIdentity` 与成功 `Proposal` 必须准确表达第二次 resolution 是否存在：

```text
project_plan_digest: str
environment_plan_digest: str | None
resolved_graph: tuple[ResolvedNode, ...]
```

有活跃 external harness 时 `environment_plan_digest` 必填，installed graph 来自 environment plan；没有
活跃 external harness 时该字段为 `None`，installed graph 来自 project plan。identity digest 的 canonical
payload 显式保留 JSON `null`，不得把 project digest 复制到 environment 字段来伪造第二份 evidence。
`PreparedEnvironment` 的计划 interface 同样让 environment plan 为 optional，并为内部 installation 选择
唯一实际 final plan；check/search/baseline/report 调用方消费 digest facts，不自行按 group 名称重做分支。

Schema 1 的 `ProposalV1.environment_plan_digest` 改为 required-nullable：key 必须存在，值为 digest 或
`null`。Reader 通过 Proposal 引用的 Attempt 复证：

```text
environment_plan_digest is None
iff Attempt.harness_declaration_ids == ()
```

FailureRecord 已允许两个 plan digest 独立为空，继续只保存失败前实际取得的 evidence。生成 JSON Schema
与 minimal examples 必须同步更新。PF pre-release，reader 只接受新目标形状；不接受缺 key 的旧 proposal，
不提供默认填充、双读或 schema-version 分支，`schema_version = 1` 保持。

现行 wire serialization 使用 `exclude_none=True`，Schema generator 默认移除 null，reader 也有 null-path allowlist；ProposalV1 必须
显式保留该字段的 null serialization 与 null schema 分支，reader 只为此路径增补准入。generation、reader、write/read、merge/update
和 examples 都穿过同一 wire 契约，不扩大其他可省略字段的序列化范围。

`evaluation_policy_identity` 的 `validation_contract_policy` 新增固定事实，例如：

```json
{
  "test_group_selection": "explicit-or-dev-then-test-else-empty-v1",
  "empty_harness_prepare": "install-project-plan-without-environment-resolution-v1"
}
```

具体选择名称、group 原文、Cell 与 external declaration IDs 继续由 SourceSnapshot、Cell 与 Attempt evidence
绑定；固定 facts 只隔离迁移前后的语义。D014 的 generation、merge/update 与 apply policy comparison
继续负责跨语义拒绝，`--force` 不绕过 policy mismatch。

## 6. Module ownership 与依赖方向

| Module | 目标职责 |
| --- | --- |
| `ConfigLoader` | 校验/合并 optional raw `test-group`；不读取 group inventory |
| `ProjectLoader` | 按 §2 选择一个有效名称或空结果；只展开选中 group；分离 required surface 与 external harness |
| `PackagePlan` | 暴露 resolved `selected_test_group`、external harness 与 Cells；不保留 existence bool |
| `harness.py` | 只处理有活跃 external declarations 的 original/relaxed requirement；不决定 group 名称 |
| `EnvironmentFactory` | 以 active declaration IDs 选择 project-only 或 harness-augmented 路径，并隐藏安装与复证细节 |
| `UvOperations` adapter | resolve project/environment、安装收到的唯一 plan；按 plan kind 给出准确 install stage |
| `policy.py` | 物化固定选择/快路 policy facts |
| `ReportStore` / `ApplyAuthorizer` | 校验 nullable environment evidence 与现行 generation/policy authority |

这条 seam 保持 `ConfigLoader` 不解释 project inventory、workflow 不解释 harness，且让
`EnvironmentFactory.prepare` 的调用方无需了解何时需要第二次 resolution。

## 7. 测试与实施证据要求

测试穿过 public interface 并断言语义 facts，不锁定 private helper 或整份 CLI 文案。

1. Config/Project 参数矩阵覆盖 root/member 省略、显式覆盖、显式缺失、只有 `dev`、只有 `test`、两者
   同时存在、跨 root/member 分布、空 group 与 include-group；证明 `dev` 优先、只组合选中名称，
   selected name 是唯一 planning 选择事实；不保留枚举旧 interface 的永久负向测试。
2. Project planning 证明无 group 不解析 harness、不建立 harness source route；self-reference-only group
   仍产生 required Cell surface，但 external harness 为空。
3. Workflow 正向用例证明 `smoke`、`check`、`search` 在无 group 时越过旧 admission；显式独立
   `test-command` 实际运行并按现行 terminal authority 得到结果。另保留 command 不可启动的真实失败分类。
4. EnvironmentFactory public seam 以记录型 fake 证明每种 resolution role 在 active harness 为空时恰好一次
   project resolution、零次 environment resolution、一次 project-plan install 与一次 graph inspection；
   active harness 分支继续两次 resolution、一次 environment-plan install。
5. 条件 external harness 覆盖同一 package 的不同 Cells：不活跃 Cell 走快路，活跃 Cell 走 D012；
   project-overlap harness 即使由 `G(P)` 满足也不得走快路。
6. Baseline/check/search 证明 empty baseline 可用于 lowest-direct/exact requests，Attempt IDs、cache、失败
   evidence 与 proposal graph 保持闭合。
7. Report/reader/merge/update/apply 覆盖 required-nullable environment digest、null 与 harness IDs 双向一致、
   identity tamper、缺 key fail closed、新 policy generation 隔离及 `--force` 拒绝。
8. 真实最小 fixture 不声明 dependency group，使用不依赖额外 harness 的 test command，证明 public CLI
   project-only 路径确实安装 project graph并执行 verifier；process/activity evidence 证明没有
   `resolve-environment`。
9. 完整 regression 包含 Ruff、ty、Python 3.10/3.11/3.12 suites、coverage、build、schema/example
   no-drift、Markdown links 与 `git diff --check`。按 AGENTS.md 从 PF repo root 在沙箱外运行 CLI 和测试，
   精确命令与结果写入 Plan。

## 8. 验收标准

1. 显式 test-group 存在时只选择该名称并组合 root/member；不存在时得到空 group 且不 fallback、不在
   full-evaluation admission 失败。显式值继续要求非空字符串。
2. 省略配置时按 `dev`、`test` 顺序在 root/member 有效 keys 中选择；两者都不存在时得到空 group；
   两者都存在时稳定选择 `dev`，不自动 union。
3. `ConfigLoader` 不读取 group inventory；`ProjectLoader` 独占选择/展开；`PackagePlan` 以 optional selected
   name 替换 presence bool，workflow 不重做选择。
4. 空选择不进入 PF harness parsing/source planning；选中 group 的 include/self-reference/external 分离与
   D001/D012 保持。self-reference-only Cell 保留 required extras 且按无 external harness 处理。
5. 无活跃 external harness 的每个 Cell 只 resolve project、安装 project plan 并复证相同 graph；不调用
   harness normalization/resolve_environment，不发出相应 activity，也不能产生 `HARNESS_CONFLICT`。
6. 有活跃 external harness 的 Cell 保持两次 resolution、一次 final environment install、project graph
   exact preservation、satisfaction/ceiling 与 certified conflict 语义；project overlap 不错误走快路。
7. Highest project-only 成功建立 empty HarnessBaseline；lowest-direct/exact 继续绑定其 digest，现行 search
   request interface 和算法不增加 optional baseline。
8. project-only install/inspect 使用准确 stage；缺失 pytest 或其他 test tool 由实际 configured verifier/
   process evidence决定，不恢复 group gate、不自动发现命令或安装默认 harness。
9. EnvironmentIdentity、Proposal 与 Schema 1 以 required-nullable `environment_plan_digest` 表示分支；null
   当且仅当 Attempt 没有 active harness IDs。Reader 拒绝缺 key、不一致与 identity 漂移，不虚构第二份 plan。
10. 固定 test-group selection / empty-harness policy facts 进入 evaluation-policy preimage；新旧 generation
    不可 merge/apply，`update_path` 按现行规则整体替换，`--force` 不绕过；其余具体输入继续由现有 evidence
    绑定。
11. D003 coordinate/predecessor、D004 static/witness、D008 scheduling、D013 pytest observer、artifact/source
    资格与数值退出码除本文明确 stage 外无行为变化。
12. §7 的 public planning/workflow/environment/report/真实 CLI 与三版本全套证据通过；owner docs、README、
    生成物同步，逐项 AC 审计没有未决项。

## 9. 非目标

- 不自动合并 `dev` 与 `test`，不扫描 `qa`、`testing`、tox/nox 或任意其他名称；
- 不从 test command 推断 group，也不因 pytest 缺失切换命令或临时安装 pytest；
- 不读取或改写 uv `default-groups`，不改变 uv 对整体 pyproject 的独立资格；
- 不跳过 project resolution、project plan installation、installed graph inspection、ty 或 configured verifier；
- 不让包含 external harness 的 Cell 绕过 D012 satisfaction、ceiling、source 或 conflict 证明；
- 不改变 project direct dependency search space、harness/transitive 非坐标性质或 floor 定义；
- 不新增 CLI flag、多个 test-group 列表、warning mode、兼容 alias、旧 report migration 或 Schema 2。

## 10. 实施与 owner 归并

本 Design 获得接受后，先建立 durable Plan，逐项映射 §8 到 ordered slices、interface/ownership migration、
Schema/examples、文档、测试和证据槽。建议切片顺序为：

1. Config/Project group-selection interface 与 public planning tests；
2. EnvironmentFactory project-only 分支、stage 与 baseline/search handoff；
3. EnvironmentIdentity/Proposal/report nullable evidence、policy 隔离与生成物；
4. workflow/CLI 正向 evidence、README 与完整回归；
5. 逐项 AC 审计、owner 吸收及 Design/Plan 同步归档。

完成时把稳定规则分别吸收到：

- D001：显式/省略选择、缺失/空语义、command 与命令准入；
- D002：ConfigLoader/ProjectLoader/PackagePlan ownership、EnvironmentFactory 两分支 interface；
- D005：project-only stage 与不能产生 HARNESS_CONFLICT 的 failure 资格；
- D006：两条 preparation 路径的 activity/failure 展示；
- D012：active external harness 分支条件、project-only install、empty baseline 与既有完整路径；
- D014：optional environment plan evidence、Schema 1 reader invariants 与 policy generation 隔离。

同一完成变更中归档 D035 与对应 Plan，并更新现行/归档索引。Design 未接受或 Plan 未建立时，不开始
生产、Schema、owner Design、README 或 generated artifact 修改。

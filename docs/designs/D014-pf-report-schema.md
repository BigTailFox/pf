# PF 报告 Schema 2

- **状态：** 已批准，待实现
- **日期：** 2026-08-25
- **适用范围：** `package-floor.json` 的公共 JSON 布局、引用完整性、规范编码，以及用 Schema 2 一次性替换现行内联报告
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **静态证据：** [D004](D004-pf-ty-enhancement.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)
- **架构接口：** [D010](D010-pf-v1-architecture.md)
- **runtime-backed 搜索：** [D011](D011-pf-runtime-backed-static-search.md)
- **harness resolution：** [D012](D012-pf-harness-relaxation.md)
- **pytest failure evidence：** [D013](D013-pf-pytest-failure-evidence.md)

本文定义 PF 如何把报告证据表达为单一所有者的引用图。它不删除 Attempt、Proposal、Evaluation、CandidateSnapshot、FailureRecord、static region、坐标边界或 projection 证据，只消除重复内联和多重权威。

目标是让公共报告更小、更容易审计，并继续保守拒绝缺失、冲突或不可表示的证据。

本文只拥有持久化报告的 wire interface：顶层分组、实体表、引用、规范编码和跨引用验证。D001 继续拥有报告的产品作用、命令和 apply 条件；D003–D005、D008、D011–D013 继续拥有被保存证据的领域含义。本文不得通过重排 JSON 改写这些语义。

本文已批准、待实现。落地前现行内联报告仍是唯一有效行为。项目尚未发布；落地时直接以 `schema_version = 2` 取代现行内联报告，不保留 reader、migrator 或 dual-write。开发期报告由新的 search 重生。

## 1. 背景与目标

Schema 1 把本质上是引用图的证据按值展开为一棵 JSON 树。一个 `Cell` 会嵌入 CandidateSnapshot、Attempt、Proposal、static region、FailureScope 和 Projection；一个 Proposal 又同时嵌入 Full Evaluation 与其 Static Evaluation；一个失败既出现在 Probe evidence 中，又以 FailureRecord 保存诊断事实。

这种布局让 validator 可以直接比较两棵子树是否相等，却产生三个问题。

### 1.1 同一事实存在多个所有者

现行 Builder 从每个 CellResult 的 `candidate_snapshots` 再复制一份顶层 `candidate_snapshots`。CellResult 内副本用于完整性验证，顶层副本用于 merge；`PackageFloorReportV1` 没有要求两者完全相等。因此，一个 Schema-valid 文档可以同时携带两套不同的候选证据。

同类重复还包括：

- `ProbeObservation.vector == AttemptIdentity.requested_managed_vector`；
- `AttemptIdentity.active_declaration_ids == Cell.active_declaration_ids`；
- Full Evaluation 的 `proposal == static.proposal`；
- `CellSuccess.final_vector == search.vector == final_evaluation.proposal.managed_vector`；
- Probe 的 Attempt、cause、Evaluation 与其 FailureRecord 中的 scope、cause、process facts；
- `StaticOnlyEvidence.region_slice == StaticRegion.slice`；
- FloorProjection 中的 Cell 与 `target_cells` / `cell_results` 中的 Cell。

这些相等关系是必要不变量，但不要求在 wire interface 中保存多份完整对象。保存一次并验证引用关系可以表达同一约束。

### 1.2 体积主要来自高扇出证据

2026-08-25 的 PF 自搜索报告包含 3 个 Cell、24 个 CandidateSnapshot、162 条 ProbeObservation 和 117 个 FailureRecord。实测如下：

| 指标 | Pretty JSON | 紧凑 JSON |
| --- | ---: | ---: |
| 总体积 | 7,682,528 bytes | 4,084,111 bytes |
| 总行数 | 168,682 | 1 |
| `cell_results` | — | 3,655,329 bytes / 89.5% |
| `search.observations` | — | 2,641,759 bytes / 64.7% |
| 顶层 CandidateSnapshot 副本 | — | 380,202 bytes / 9.3% |
| `source_snapshot` | — | 25,119 bytes / 0.6% |

递归实体计数进一步显示：

| 实体 | 出现次数 | 唯一值 | 重复紧凑字节估计 |
| --- | ---: | ---: | ---: |
| Cell | 747 | 3 | 488,064 |
| Attempt | 282 | 144 | 390,108 |
| Proposal | 262 | 128 | 662,818 |
| resolved graph | 262 | 84 | 621,081 |
| CandidateSnapshot | 48 | 24 | 380,177 |

表中的重复字节包含嵌套重叠，不能相加；它们说明主要成本不是 source manifest、缩进或少量 summary 字段，而是 Observation 对整个证据上下文的反复内联。

### 1.3 机器规范与人类结构都不清楚

D001 当前只枚举 Schema 1 包含的概念。完整结构分散在 `schemas/project.py`、`schemas/evaluation.py` 和 `schemas/report.py` 的多个判别 union 与交叉 validator 中，没有提交的 JSON Schema、最小完整示例或字段所有权表。

`ReportStore` 的紧凑、排序 key 输出适合确定性持久化，不适合直接阅读。Pretty-print 只能增加行数，不能恢复实体关系。`pf explain` 是主要人类 interface，但公共 JSON 仍应能沿稳定引用被审计。

### 1.4 目标

1. 每个 Cell、CandidateSnapshot、Attempt、Proposal、resolution graph、StaticEvaluation、terminal Evaluation 和 FailureRecord 在报告中最多定义一次；
2. Observation、Region、CellResult、FailureScope 和 Projection 只使用稳定、本报告内引用；
3. 现行证据闭环、failure disposition、static region、边界、最终 PASS、coverage 和 projection 不变量全部保留；
4. 缺失引用、重复 ID、错误类型引用、跨 Cell 引用、冲突实体和循环引用均保守失败；
5. `apply`、`explain`、`diagnose`、`merge` 和 search update 不各自实现引用解析；
6. 报告继续是一个可移植、原子写入、自包含的 JSON 文件；
7. reader 只接受 `schema_version = 2`，未知或缺失版本保守失败；
8. 提交机器可读 JSON Schema、最小完整示例和篡改测试矩阵；
9. 对同一语义报告，Schema 2 的紧凑编码显著小于现行内联编码，并记录可复现的前后对比。

### 1.5 非目标

- 不删除 Observation、失败、构件 hash、resolved graph、static region 或 runtime witness 以换取体积；
- 不改变坐标搜索、candidate order、static guidance、Rejection/Indeterminate 或 apply 语义；
- 不把 `package-floor.json` 变成跨运行 Evaluation cache；
- 不把完整证据移入 `.pf/logs` 或其他本机目录；
- 不在 Schema 2 首版引入 sidecar、外部 JSON Pointer、远程引用、数据库或二进制主格式；
- 不依赖 gzip 证明 schema 已经变清楚；压缩可以是传输选择，不是 wire interface；
- 不用数组下标作为持久引用；merge、规范排序或插入实体不得改变引用；
- 不把短随机别名当作领域 identity；
- 不在同一 `schema_version` 下同时接受内联树和 refs；
- 不把 report hash 变成签名或可信来源证明。

## 2. 文档模型

### 2.1 规范化原则

PF 引入 `schema_version = 2`，使用一个自包含 JSON document 表达规范化引用图：

```text
PackageFloorReportV2
├── identity
├── inputs
│   ├── requirement_declarations[]
│   ├── target_cells[]
│   └── candidate_snapshots[]
├── evidence
│   ├── resolution_graphs[]
│   ├── attempts[]
│   ├── proposals[]
│   ├── static_evaluations[]
│   ├── evaluations[]
│   └── failures[]
├── cell_results[]
├── projections[]
└── result
```

实体定义只出现在其所属表中；其他位置保存 `*_ref`。引用值继续使用现有 identity，或使用本文定义的内容 digest。Report validator 在内存中建立私有索引、解析引用并复证现行领域不变量，调用方不直接维护索引。

Wire normalization 不等于把所有小 value object 都拆成表。`VersionPin`、`ProcessResult`、`TyDiagnostic`、`RuntimeWitnessPlan` 和 artifact 仍可在唯一拥有它们的实体中按值保存。只有满足以下任一条件的记录需要独立 identity：

- 被两个或以上领域记录引用；
- 参与 merge、dedup 或跨引用完整性；
- 当前已经存在稳定 ID/digest；
- 内联会制造两个可独立修改的权威副本。

### 2.2 顶层结构

Schema 2 的说明性结构如下。省略号不是合法 JSON；具体判别变体见后续章节。

```json
{
  "schema_version": 2,
  "identity": {
    "report_generation_id": "...",
    "generator": {
      "name": "pf",
      "version": "0.1.0",
      "algorithm": "v1"
    },
    "package": {
      "name": "demo",
      "pyproject_path": "pyproject.toml"
    },
    "source_snapshot": {
      "digest": "...",
      "entries": []
    },
    "policy_identity": "..."
  },
  "inputs": {
    "requirement_declarations": [],
    "target_cells": [],
    "candidate_snapshots": []
  },
  "evidence": {
    "resolution_graphs": [],
    "attempts": [],
    "proposals": [],
    "static_evaluations": [],
    "evaluations": [],
    "failures": []
  },
  "cell_results": [],
  "projections": [],
  "result": {
    "status": "incomplete",
    "reasons": ["MISSING_CELL"]
  }
}
```

顶层对象按 identity、输入实体、过程证据和产品结果分组。`identity`、`requirement_declarations` 与 `target_cells` 定义报告世代；CandidateSnapshot 是 generation 内冻结的 search input，但不进入 `report_generation_id`。

`evidence` 是共享证据池；`cell_results` 和 `projections` 是产品结果 roots。表的物理分组不决定可达性：CandidateSnapshot 必须从 CellResult 或 Region 被引用，不能仅因位于 `inputs` 而成为 root。

## 3. Generation 与输入实体

### 3.1 `identity`

`identity` 保存：

- `report_generation_id`；
- `generator`；
- `package`；
- `source_snapshot`；
- `policy_identity`。

Schema 2 首版保留完整 `SourceSnapshotIdentity.entries`。它在样本中只占 0.6%，却能支持 generation identity、merge 相等性和 source drift 复证。若未来只保存 digest，必须单独提升 source identity 策略。

Schema 2 validator 必须按现行 `pf:snapshot:v1` 算法从规范 entries 重算 `source_snapshot.digest`，拒绝不一致值。

### 3.2 `report_generation_id`

Schema 2 使用新的 `pf:report-generation:v2` identity，不继承内联报告的数组顺序。preimage 精确为：

```text
payload = {
  "generator": generator,
  "package": package,
  "source_snapshot": source_snapshot,
  "policy_identity": policy_identity,
  "requirement_declarations": declarations_sorted_by_declaration_id,
  "target_cells": cells_sorted_by_cell_order
}
report_generation_id = sha256(
  b"pf:report-generation:v2\0" + canonical_identity_json(payload)
).hexdigest()
```

`cell_order` 是 `(package, target, python_minor, extra_surface)`。Cell 在 generation preimage 中使用领域字段和 `active_declaration_ids`，不使用 `cell_id` 或 refs。

CandidateSnapshot、CellResult、Projection、Failure 和 Evaluation 不进入 generation preimage。未来若改变这些输入或排序，必须提升 report-generation identity 版本，不能借 codec 隐式改变。

### 3.3 隐式 generation scope

以下字段不再在每个嵌套实体重复：

- `source_snapshot_digest`；
- `evaluation_policy_identity` / `policy_identity`；
- 完整 `Cell`；
- `active_declaration_ids`。

Attempt、Proposal、CandidateSnapshot 和 static region 都属于一个 PackageFloorReport generation。它们通过 `cell_ref` 取得 Cell，通过顶层 `identity` 取得 source snapshot 与 policy，通过 Cell 取得 active declarations。

validator 必须先解析这些引用，再按现行完整语义重建 identity 输入并校验 ID。省略 wire 字段不省略 identity 事实。

### 3.4 RequirementDeclaration

`inputs.requirement_declarations` 保留 `RequirementDeclaration` 字段和 `declaration_id`。每个 ID 只能定义一次；表按 `declaration_id` 排序，与 §3.2 的 generation preimage 一致。

其他实体使用 `declaration_ref` 或 `declaration_refs`。引用必须存在；需要规范顺序的集合必须排序且唯一。

### 3.5 TargetCell

```json
{
  "cell_id": "cell-...",
  "package": "demo",
  "target": "x86_64-unknown-linux-gnu",
  "python_minor": "3.12",
  "extra_surface": [],
  "active_declaration_refs": ["..."]
}
```

`cell_id` 是以下规范事实的内容 digest：

```text
(package, exact target, CPython minor, sorted extra surface)
```

它沿用现行 `cell_identity` 的 lookup 语义，不包含 `active_declaration_refs`。后者属于当前报告世代的 Cell record；同一 generation 中不得出现相同 `cell_id` 和不同 active declarations。跨 generation 引用不合法，因此声明变化不要求重命名 Cell lookup identity。

规范编码为：

```text
payload = {
  "package": package,
  "target": exact_target,
  "python_minor": cpython_minor,
  "extra_surface": sorted_unique_extra_surface
}
cell_id = "cell-" + sha256(
  b"pf:cell:v1\0" + canonical_identity_json(payload)
).hexdigest()
```

`canonical_identity_json` 的精确定义见 §6.3。`cell_id`、`active_declaration_refs` 和任何 Schema 2 `*_ref` 都不进入 payload。`target_cells` 按 §3.2 的 `cell_order` 排序。

`target_cells` 必须继续显式存在。Incomplete report 不能从已有 `cell_results` 反推出尚未运行的目标 Cell。

### 3.6 CandidateSnapshot

`inputs.candidate_snapshots` 是 CandidateSnapshot 的唯一所有者：

```json
{
  "candidate_snapshot_id": "...",
  "dependency": "packaging",
  "cell_ref": "cell-...",
  "source": {},
  "candidates": [],
  "series_representatives": []
}
```

`candidate_snapshot_id` 使用现行 CandidateSnapshot digest。validator 解析 `cell_ref` 并注入顶层 policy 后，按现行完整 identity 重算 digest。CellResult 只保存 `candidate_snapshot_refs`，不得再内联 CandidateSnapshot。

同一 `(cell_ref, dependency)` 只能对应一个 CandidateSnapshot。所有候选、artifact locator/hash、series representative、prerelease 与构件可安装性规则保持不变。

## 4. Evidence entities

### 4.1 ResolutionGraph

```json
{
  "resolution_graph_id": "resolution-...",
  "nodes": [
    {
      "name": "packaging",
      "version": "26.3",
      "dependencies": []
    }
  ]
}
```

`resolution_graph_id` 是规范排序 `ResolvedNode` 列表的内容 digest。Package name 必须等于现行 PEP 503 canonicalization 的结果；Node 按 canonical package name 排序且唯一，每个 `dependencies` 按 canonical name 排序且唯一；payload 是这些 node object 的 JSON array：

```text
resolution_graph_id = "resolution-" + sha256(
  b"pf:resolution-graph:v1\0" + canonical_identity_json(nodes)
).hexdigest()
```

相同 resolved graph 在报告中只定义一次。Proposal 使用 `resolution_graph_ref`。Proposal ID 校验仍对解析后的完整 graph 计算，不把新 ref 字符串冒充原有 Proposal identity。

Environment producer 必须先规范化 graph，再计算 Environment identity 和 `resolution_graph_id`。Reader 要求 wire 已是规范顺序；它不通过重排一个非规范文档来“修复”Proposal identity。

### 4.2 Attempt

```json
{
  "attempt_id": "...",
  "cell_ref": "cell-...",
  "requested_resolution": "exact-vector",
  "requested_managed_vector": [],
  "source_plan_identity": "...",
  "resolution_context_digest": "...",
  "harness_policy_identity": "harness-relaxation-v1",
  "harness_declaration_ids": [],
  "harness_baseline_digest": "...",
  "selected_candidate_evidence_digest": "..."
}
```

Attempt 不再保存 source snapshot digest、policy identity、完整 Cell、重复的 active declarations 或 `identity_version`。

Schema 2 只接受 `attempt-v2`。validator 从 generation 与 `cell_ref` 展开事实，注入 `identity_version="attempt-v2"` 后重算 `attempt_id`；Builder 遇到其他 Attempt identity version 必须失败。

`harness_declaration_ids` 是排序唯一的 opaque IDs，不是 `*_ref`。公共报告不拥有 `HarnessRequirement` 实体表；Attempt identity 的 harness facts 只保存这些 IDs、baseline digest 和 selected-candidate digest。

运行期 planning 继续消费 D012 的结构化 HarnessRequirement。未来若报告要保存完整声明，必须单独增加实体表并版本化 Attempt identity。

`requested_resolution` 的互斥条件保持不变：highest / lowest-direct 不携带 exact vector，exact-vector 必须携带排序唯一的 vector；harness 与 selected candidate 约束保持不变。

### 4.3 Proposal

```json
{
  "proposal_id": "...",
  "attempt_ref": "...",
  "managed_vector": [],
  "fixed_declaration_refs": [],
  "resolution_graph_ref": "resolution-...",
  "project_plan_digest": "...",
  "environment_plan_digest": "...",
  "interpreter": {
    "implementation": "cpython",
    "version": "3.12.11",
    "abi": "cpython-312-x86_64-linux-gnu"
  }
}
```

Proposal 不再重复 `snapshot_digest`、Cell、policy 或 resolved graph。一个 Attempt 最多产生一个 Proposal；prepare failure 不得虚构 Proposal。

`proposal_id` 等于 `EnvironmentIdentity.digest`。成功 Proposal 必须保存 `project_plan_digest` 与 `environment_plan_digest`。validator 解析 `resolution_graph_ref` 后，按 D012 的 Environment identity 重算 `proposal_id`。

Environment producer 必须把两个 plan digest 放入领域 Proposal，再交给 report builder。Builder 不得从当前项目、缓存或 graph 猜测缺失值。

任一 digest 缺失或为空，或者用两个 digest 与 graph 重建的 Environment identity 不等于 `proposal_id` 时，报告无效。

`managed_vector` 仍保留在 Proposal 中。它是解析后的实际向量；对于 exact Attempt，validator 要求它与 requested vector 一致，但 request 和 realized result 是不同阶段的事实，不合并为一个字段。

### 4.4 StaticEvaluation

`evidence.static_evaluations` 每个 Proposal 最多一条：

```json
{
  "proposal_ref": "...",
  "status": "STATIC_UNCHANGED",
  "ty": {},
  "baseline_digest": "...",
  "incremental": [],
  "static_fingerprint": "..."
}
```

`STATIC_REGRESSION` 变体额外保存 `classifications`。`static_evaluations` 只拥有已经产生 `TyCheck` 的 `STATIC_UNCHANGED | STATIC_REGRESSION`；static tool failure 是 terminal `INDETERMINATE`，只定义在 §4.5 的 `evaluations` 表中，不在两张表重复定义。不存在的字段不以 `null` 占位。

Terminal Evaluation 不再内联 StaticEvaluation，只保存 `static_evaluation_ref`，其值为同一 Proposal ref。static baseline 也不复制 `TyCheck`；CellResult 只保存 baseline Proposal ref 与 diagnostic digest，validator 从本表取得唯一 TyCheck。

### 4.5 Terminal Evaluation

`evidence.evaluations` 每个 Proposal 最多一条 terminal Evaluation，继续使用 `PASS | TEST_FAIL | RUNTIME_INTERFACE_MISSING | INDETERMINATE` discriminator：

```json
{
  "proposal_ref": "...",
  "status": "PASS",
  "static_evaluation_ref": "...",
  "witnesses": [],
  "test": {
    "status": "TEST_PASS",
    "process": {}
  }
}
```

正向 stage facts 由 Evaluation 唯一拥有。负向或不确定 stage 使用 `failure_ref` 指向 FailureRecord，不再在 Probe、Evaluation 和 FailureRecord 三处复制 cause/process：

```json
{
  "proposal_ref": "...",
  "status": "TEST_FAIL",
  "static_evaluation_ref": "...",
  "witnesses": [],
  "failure_ref": "failure-..."
}
```

Runtime witness 的计划、顺序与状态仍由 D011 拥有。若 terminal witness 产生 FailureRecord，该 witness 保存 `failure_ref`；FailureRecord 保存一次 report-portable process facts。validator 复证 failure stage/cause 与 Evaluation 变体匹配。

`INDETERMINATE` 可以发生在 static、witness 或 test stage。static stage 尚未产生合法 StaticEvaluation 时，该变体省略 `static_evaluation_ref`；witness/test stage 必须引用同 Proposal 已有的 StaticEvaluation。

`IndeterminateEvaluation` 在 wire 上只由 terminal Evaluation table 拥有。

### 4.6 FailureRecord

```json
{
  "failure_id": "failure-...",
  "scope": {
    "kind": "attempt",
    "attempt_ref": "..."
  },
  "disposition": "REJECTED",
  "cause": "TEST_FAILURE",
  "stage": "test",
  "process": {},
  "summary_code": "..."
}
```

Cell scope 使用 `cell_ref`，不再内联 Cell、package、source snapshot 和 policy：

```json
{
  "kind": "cell",
  "cell_ref": "cell-..."
}
```

FailureRecord 继续是 disposition、cause、stage 和可移植诊断事实的唯一所有者。`failure_id` 在解析 scope ref 后按现行完整语义重算。报告仍不得保存 run ID、本地日志 locator、绝对路径、credential 或无界输出。

## 5. 结果 roots

### 5.1 CellResult 判别变体

CellResult 继续保留现行终态：

```text
SUCCESS
BASELINE_REJECTION
BASELINE_INDETERMINATE
CELL_INDETERMINATE
SEARCH_FAILED
```

Schema 2 首版使用以上精确字符串；不得把“落地时现行实现”作为可变 wire 定义。未来重命名必须提升 Schema 或提供显式版本映射，不能借 normalization 静默改变。

所有变体使用 `cell_ref`。FailureRecord 统一位于 `evidence.failures`；CellResult 只列出属于当前 Cell 的 `failure_refs`。内联 Attempt、Proposal、Evaluation、CandidateSnapshot 和 Cell 改为 refs；现行机械字段保留：

| status | 保留字段 | 主要 refs |
| --- | --- | --- |
| `SUCCESS` | `search`（observations / regions / boundaries / sweeps）、`static_baseline_digest` | `baseline.attempt_ref` / `proposal_ref`、`candidate_snapshot_refs`、`final_proposal_ref`、`failure_refs` |
| `BASELINE_REJECTION` | 无独立 search | `attempt_ref`、精确一条 `failure_refs`；若已产生 Evaluation 则另有 `proposal_ref` 与 `static_baseline_digest` |
| `BASELINE_INDETERMINATE` | 无独立 search | 同 `BASELINE_REJECTION` |
| `CELL_INDETERMINATE` | `phase`；可选 `search` / `coordinate_failure` | 终端 `failure_ref` 必须属于 `failure_refs`；若已进入 search 则要求完整 baseline refs 与 `candidate_snapshot_refs` |
| `SEARCH_FAILED` | `phase`、`reason`；可选 `coordinate_failure` | 完整 baseline refs、`candidate_snapshot_refs`、`failure_refs`；`reason` 必须与 coordinate outcome 一致 |

Baseline 变体的 Cell 仍由 `cell_ref` 显式给出，并必须等于其 highest `attempt_ref` 的 Cell。`SEARCH_FAILED.reason` 继续使用现行 `NON_MONOTONIC | NONDETERMINISTIC | NO_PASS_IN_SEARCH_SPACE`。

### 5.2 Success

```json
{
  "status": "SUCCESS",
  "cell_ref": "cell-...",
  "baseline": {
    "attempt_ref": "...",
    "proposal_ref": "...",
    "static_baseline_digest": "..."
  },
  "candidate_snapshot_refs": [],
  "search": {},
  "final_proposal_ref": "...",
  "failure_refs": []
}
```

以下现行内联字段被删除，改为引用或派生：

- `observed_upper`：其类型固定为 `None`，没有独立语义；
- `final_vector`：从 final Proposal 的 `managed_vector` 唯一取得；
- `final_evaluation`：从 `final_proposal_ref` 唯一取得；
- 内联 `candidate_snapshots`、`baseline_attempt`、`static_baseline` 和 `baseline`。

validator 必须证明：

1. baseline Attempt 是当前 Cell 的 highest Attempt；
2. baseline Proposal 属于该 Attempt；
3. baseline StaticEvaluation 的 TyCheck 产生 `static_baseline_digest`；
4. baseline terminal Evaluation 为 PASS；
5. final Proposal 属于当前 Cell，且 terminal Evaluation 为 PASS；
6. search boundaries 与 final Proposal managed vector 完全一致；
7. final vector 每个 pin 唯一选择当前 Cell 的 CandidateSnapshot artifact；
8. 不是 baseline 的 final Proposal 必须由 reported direct ProbePass 授权。

### 5.3 ProbeObservation

Direct observation：

```json
{
  "dependency": "packaging",
  "candidate_version": "19.2",
  "evidence": {
    "kind": "DIRECT",
    "attempt_ref": "...",
    "status": "PASS"
  }
}
```

Direct Rejection 额外保存 `failure_ref`；PASS 不得出现该字段：

```json
{
  "kind": "DIRECT",
  "attempt_ref": "...",
  "status": "REJECTED",
  "failure_ref": "failure-..."
}
```

Static-only observation：

```json
{
  "dependency": "packaging",
  "candidate_version": "20.0",
  "evidence": {
    "kind": "STATIC_ONLY",
    "attempt_ref": "...",
    "guidance": "REJECTED",
    "region_ref": "region-...",
    "representative_proposal_ref": "..."
  }
}
```

Observation 不再保存完整 vector、Attempt、Proposal、StaticEvaluation 或 terminal Evaluation。所有 Direct 与 Static-only Observation 都必须引用当前 Cell 的 `exact-vector` Attempt。

vector 恒从 Attempt 的 `requested_managed_vector` 取得；`dependency/candidate_version` 必须命中该 vector。`highest` 只用于 Cell baseline，`lowest-direct` 不进入 coordinate observations。

Direct status 必须与引用 Evaluation / FailureRecord 一致：

- `PASS`：Attempt 必须有 Proposal 与 PASS Evaluation，不得有 failure ref；
- `REJECTED`：必须有 REJECTED FailureRecord；若已产生 Proposal，还必须有匹配的负向 Evaluation；
- `INDETERMINATE`：必须有 INDETERMINATE FailureRecord；若已产生 Proposal，还必须有匹配的 Indeterminate Evaluation。

### 5.4 StaticRegion

Region 只保存当前 Cell search 中不能从引用推导的事实：

```json
{
  "region_id": "region-...",
  "candidate_snapshot_ref": "...",
  "baseline_digest": "...",
  "other_coordinates": [],
  "static_fingerprint": "...",
  "observed_versions": [],
  "runtime_references": [
    {
      "proposal_ref": "..."
    }
  ]
}
```

现行 `StaticRegionSlice` 中的 Cell、source snapshot、policy 和 candidate order 不再内联：

- Cell 来自所属 CellResult；
- source/policy 来自 generation；
- `candidate_snapshot_ref` 必须属于同一 CellResult 的 `candidate_snapshot_refs`；active dependency 与 candidate order 来自该 CandidateSnapshot；
- other coordinates、baseline digest 和 fingerprint 仍由 Region 拥有。

Runtime reference 只选择 region 的 direct runtime representative，不重复保存其 status。`PASS | REJECTED | INDETERMINATE` 从同 Proposal 的 direct Observation、terminal Evaluation 与 FailureRecord 唯一推导；三者不一致时整个报告失败。

`region_id` 对规范化后的完整 region 语义计算：

```text
payload = {
  "slice": expanded_static_region_slice,
  "static_fingerprint": static_fingerprint,
  "observed_versions": versions_in_candidate_order,
  "runtime_proposal_ids": sorted_unique_proposal_ids
}
region_id = "region-" + sha256(
  b"pf:static-region:v1\0" + canonical_identity_json(payload)
).hexdigest()
```

展开的 Slice 使用领域 `StaticRegionSlice` 的字段名与 value shape。完整 Cell 使用 `active_declaration_ids`；source/policy 来自 generation；active dependency/candidate order 来自 CandidateSnapshot；other coordinates 按 dependency 排序且唯一。

派生 runtime status、`region_id` 和 refs 都不进入 payload。Static-only observation 使用所属 CellResult 的 local `region_ref`。Region 不是全局实体，不能被其他 CellResult 引用。

每个 runtime ref 必须对应同 Region 内的 direct Observation，且派生 status 相同。每条 Static-only Observation 必须恰好命中一个 Region；其 representative 必须属于该 Region 的 runtime refs，派生 status 等于 guidance，且不能是自身 Proposal。

Static-only Proposal 的 StaticEvaluation 必须与 Region 使用相同 baseline digest 和 static fingerprint。

### 5.5 Coordinate outcome

Coordinate status、`boundaries`、`regions`、`sweeps`、counterexample 和 terminal failure ref 保持现行语义。成功 outcome 不再单独保存 `vector`；它由 `final_proposal_ref` 唯一取得，boundaries 必须与该 vector 相等。

`CoordinateBoundary.predecessor_failure_id` 改名为 `predecessor_failure_ref`，但仍必须引用同 dependency、同 predecessor、同 Slice 的 direct ProbeRejection。

### 5.6 ProjectionEvidence

```json
{
  "declaration_ref": "...",
  "floors": [
    {
      "cell_ref": "cell-...",
      "version": "2.0"
    }
  ],
  "projected_requirements": ["demo>=2.0"],
  "representable": true
}
```

Projection 不再内联 Cell。`floors` 是产品结果，不因可以从 final Proposal 派生就删除：它明确保存要投影的 `Cell -> ExactFloor` 映射，并由 validator 对 final Proposal 复证。`projected_requirements` 与 `representable` 同样保留为面向 apply/explain 的授权摘要。

### 5.7 `result`

`complete | incomplete` 保持判别 union。`complete` 虽可由覆盖与 projection 推导，仍作为公共结果摘要保留；validator 必须重新推导并拒绝不一致值。Incomplete reasons 必须规范排序且与 CellResult、缺失覆盖和不可表示 projection 相符。

这些摘要字段属于有意、低成本的派生信息，不是第二份底层证据。

## 6. 引用与 identity

### 6.1 引用只在本报告内有效

所有 `*_ref` 都是 opaque string，只能解析到当前 JSON document 中对应类型的唯一实体。禁止：

- URI、文件路径或 JSON Pointer；
- 跨报告或跨 generation 引用；
- 依赖数组位置；
- 大小写、名称 canonicalization 或截断后的模糊匹配。

### 6.2 定义唯一，引用可重复

每个实体 ID 在对应类型表中必须恰好定义一次。相同 ID 即使 payload 相同也不能重复出现；输入重复说明 producer 没有完成 normalization。引用字段本身携带目标类型，不要求不同 identity namespace 的字符串在全报告内互不相同。未知、悬空或类型错误引用直接使整个报告无效。

### 6.3 ID 对展开后的语义计算

Schema 2 不把物理 `*_ref` 字符串替换成领域 identity 输入。本文新增 ID 使用下列统一编码：

```text
canonical_identity_json(value) = json.dumps(
  value,
  sort_keys=True,
  separators=(",", ":"),
  ensure_ascii=True
).encode("utf-8")
```

这一定义只用于 identity preimage；§7.3 的 wire JSON 继续不转义非 ASCII 字符。各 ID 的展开 record 与算法如下：

| Wire identity | 展开的语义 record | 算法与约束 |
| --- | --- | --- |
| `report_generation_id` | §3.2 的 generator/package/source/policy/canonical declarations/canonical target Cells | `pf:report-generation:v2`；CandidateSnapshot 与 Schema 2 refs 不进入 |
| `source_snapshot.digest` | 按 path 排序的完整现行 `SnapshotEntry[]` | 现行 `pf:snapshot:v1` |
| `candidate_snapshot_id` | 注入完整 Cell 与顶层 policy 的现行 CandidateSnapshot identity | 现行 `pf:candidate-snapshot:v1` |
| `attempt_id` | 注入 `identity_version="attempt-v2"`、source digest、完整 Cell、active declaration IDs 与 policy 的 `AttemptIdentity` | 现行 `pf:attempt:v2` |
| `failure_id` | 把 scope ref 展开为完整 `AttemptFailureScope | CellFailureScope` 的现行 FailureRecord facts | 现行 `pf:failure:v1`，保留现行截断长度 |
| `proposal_id` | `project_plan_digest`、`environment_plan_digest` 与解析后的 graph | 现行 `environment_identity_digest`；wire 必须保存两个 plan digest |
| `cell_id` | §3.5 的精确 payload | `pf:cell:v1`，最终 ref 为 `cell-` 加完整 SHA-256 hex |
| `resolution_graph_id` | §4.1 的规范 node array | `pf:resolution-graph:v1`，最终 ref 为 `resolution-` 加完整 SHA-256 hex |
| `region_id` | §5.4 的规范 region payload | `pf:static-region:v1`，最终 ref 为 `region-` 加完整 SHA-256 hex |

校验可重建 identity 时，validator 必须：

1. 解析引用；
2. 展开顶层 generation facts；
3. 重建对应现行语义 record；
4. 使用该领域 identity 的版本和前缀计算 digest；
5. 与 wire ID 比较。

Report validator 必须调用各领域 identity 函数，不能在 codec 中复制哈希算法。本节只拥有 ref 展开规则，以及 Schema 2 新增的 report-generation、Cell、ResolutionGraph 和 Region identity。

任何 `*_ref`、表位置或派生字段都不得进入未明确列出的 identity preimage。

### 6.4 引用图必须无环

Schema 2 的依赖方向固定为：

```text
generation
  -> declarations / cells
  -> candidate snapshots / resolution graphs
  -> attempts
  -> proposals
  -> static evaluations
  -> terminal evaluations / failures
  -> cell results
  -> projections / result
```

FailureScope 可以向 Attempt 或 Cell 回引，但 Attempt/Cell 不引用 Failure；Region 与 Observation 只在所属 CellResult 内形成从 summary 到既有证据的引用。不得增加可产生循环的通用 ref。

## 7. 验证与规范编码

### 7.1 引用作用域

“cross-cell ref”按下表判定，不表示所有被多个 Cell 使用的实体都非法：

| Ref | 目标 | 作用域 |
| --- | --- | --- |
| `declaration_ref` | RequirementDeclaration | generation-global；可被多个 Cell/Projection 共享 |
| `cell_ref` | TargetCell | generation-global；必须属于本报告 target Cells |
| `candidate_snapshot_ref` | CandidateSnapshot | cell-scoped；必须等于所属 CellResult 的 Cell |
| `resolution_graph_ref` | ResolutionGraph | generation-global；允许多个 Cell 的 Proposal 共享 |
| `attempt_ref` | Attempt | cell-scoped；baseline/Observation/Failure 必须与所属 CellResult 相同 |
| `proposal_ref` | Proposal | 继承其 Attempt 的 Cell；baseline/final/Observation/Region 不得跨 Cell |
| `static_evaluation_ref` | StaticEvaluation | 必须与当前 terminal Evaluation 使用同一 Proposal |
| `failure_ref` | FailureRecord | 继承 scope Cell；CellResult、Observation、Evaluation 与 witness 不得跨 Cell |
| `region_ref` | 当前 CellResult 内的 StaticRegion | local；不得解析到其他 CellResult，即使 payload 相同 |

Projection floor 可以引用本 generation 的任一 target Cell；它仍必须属于该 declaration 的 active Cell 集合。相同 ResolutionGraph 被多个 Cell 使用是预期 dedup，不是 cross-cell 违规。

### 7.2 验证顺序

Report validator 按以下顺序执行：

1. 检查 64 MiB 读取上限、JSON 语法、顶层 object 和精确 `schema_version`；
2. 用 `extra="forbid"` 校验每个判别 record 的局部结构；
3. 建立所有实体的私有 typed index，拒绝重复 ID；
4. 解析全部引用，拒绝 unknown、wrong-kind 和 cross-cell refs；
5. 校验 Cell、source snapshot、CandidateSnapshot、resolution graph、Attempt、Proposal、FailureRecord 与 report generation identity；
6. 校验 StaticEvaluation、terminal Evaluation、witness、FailureRecord cause/stage/disposition；
7. 校验 Observation、static region、coordinate boundary 与 terminal outcome；
8. 校验 CellResult baseline/final authority 和 failure ref 精确集合；
9. 校验 target Cell coverage、projection 等价性和 complete/incomplete result；
10. 只有全部通过才返回可供命令消费的 `ValidatedReport`。

任何阶段都不得通过丢弃未知实体、选择第一份冲突记录、忽略未引用 FailureRecord 或把悬空引用降级为 warning 来恢复。

可达性从 identity inputs（declarations 与 target Cells）、`cell_results`、`projections` 和 `result` 计算固定闭包。CandidateSnapshot table 不是 root。

1. CellResult 的 baseline/final、candidate snapshot、Observation、Region、boundary 与 failure refs 进入闭包；
2. 可达 Attempt 使满足 `Proposal.attempt_ref == Attempt.attempt_id` 的至多一个 Proposal 可达；只有 prepare failure 可以没有 Proposal；
3. 可达 Proposal 使其 ResolutionGraph，以及存在的至多一条 StaticEvaluation 和至多一条 terminal Evaluation 可达；
4. Evaluation 与 witness 使其 `failure_ref` 可达；FailureScope 使其 Attempt/Cell 可达；
5. Region 使其 CandidateSnapshot 与 runtime representative Proposals 可达；
6. Projection 使其 declaration 与 floor Cells 可达；
7. 所有表定义必须被闭包访问，且每条 cell-scoped 边通过 §7.1。

Static-only Observation 的 `attempt_ref` 足以使其 Attempt、Proposal、StaticEvaluation 与 ResolutionGraph 可达。它不需要虚构 Terminal Evaluation，也不要求自己的 Proposal 成为 Region runtime representative。

Direct status、Static-only guidance、Region representative status 与 FailureRecord 仍必须由同一闭环复证。

不可达实体使报告无效，避免证据池成为未受约束的附加数据区。

### 7.3 规范 JSON 编码

Schema 2 继续使用：

- UTF-8；
- key 字典序；
- separators `(",", ":")`；
- 非 ASCII 字符不转义；
- 单个末尾换行；
- 临时文件、flush、`fsync` 和原子 replace。

Schema 2 的可空事实使用判别变体表达。不存在的 optional 字段在 wire JSON 中省略，不写 `null`；语义上存在的空集合写 `[]`。

Wire 中建模的 discriminator、ID/ref、status、cause、stage 和 identity 字段不得依赖默认省略。由 Schema 固定或从 generation/ref 展开的 facts 不重复进入 wire。

规范化实体表使用稳定排序：

| 表 | 顺序 |
| --- | --- |
| declarations | `declaration_id` |
| cells | `(package, target, python_minor, extra_surface)` |
| candidate snapshots | `(cell_ref, dependency)` |
| resolution graphs | `resolution_graph_id` |
| attempts | `attempt_id` |
| proposals | `proposal_id` |
| static/terminal evaluations | `proposal_ref` |
| failures | `failure_id` |
| cell results | `(package, target, python_minor, extra_surface)` |
| projections | `declaration_ref` |

`active_declaration_refs`、`fixed_declaration_refs`、`harness_declaration_ids` 按 ID 排序且唯一；`candidate_snapshot_refs` 按 dependency 排序且唯一。

Region runtime refs 按 `proposal_ref` 排序；Projection floors 按 cell order 排序。

`failure_refs` 必须唯一，但保留 D003/D005 的 evidence 顺序。Candidate order、observed versions、witness 顺序和其他领域序列也保留其所有者定义的顺序。

Pretty JSON 不是规范写入格式。人类展示继续由 `pf explain` / `pf diagnose` 拥有；文档示例可以 pretty-print，但不能作为 golden byte fixture。

## 8. Report module interface 与所有权

Schema 2 应加深 report module，而不是让调用方学习多个表的 join 规则。

本节固定落地验收所需的 resolved facade 与 persistence seam，消除 hydrate-vs-query 分叉。D014 仍只拥有 wire 后置条件；具体 Python interface 落地后由 D002 拥有，diagnosis association 由 D005 拥有。

必须满足的后置条件是：wire refs 不泄漏；只有一种 resolved view；持久化更新后的实体图继续满足 identity、冲突和可达性规则。

外部 interface 保持以工作流为单位：

```text
ReportStore.read(path) -> ValidatedReport
ReportStore.write(path, report) -> None
ReportStore.merge(reports) -> ValidatedReport
ReportStore.update(existing, replacement) -> ValidatedReport
ReportStore.update_path(path, replacement) -> ReportUpdate
PackageReportBuilder.build(...) -> ValidatedReport
```

`ValidatedReport` 是 immutable、resolved facade，不是 wire model 的别名。首版 public interface 精确为：

```text
report_generation_id
generator
package
source_snapshot
policy_identity
requirement_declarations        # §3.2 declaration_id order
target_cells                    # §3.2 cell order
cell_results                    # §7.3 report order；返回 resolved CellResult views
projection_evidence             # declaration_id order
result
failure_records                 # cell-result order + each CellResult.failure_refs order
cell_result(cell_id) -> ResolvedCellResult | None
failure(failure_id) -> FailureRecord | None
failure_context(failure_id) -> FailureContext | None
```

`FailureContext` 精确包含所属 resolved Cell、可选 Proposal identity，以及 `predecessor | None` boundary role；它不包含本地日志 locator。日志 lookup 继续由 D005 的 `(report_generation_id, failure_id)` association 拥有。

Resolved CellResult/Observation/Region views 暴露命令所需的 typed 机械事实；所有 `*_ref` 已由 report module 解析。它们共享 immutable interned 实体，不为每次查询深复制完整树。

下列 wire 已删除的字段可以作为只读派生属性。它们必须从 interned 实体计算，调用方不能赋值或构造第二份权威副本：

- `final_vector` / 成功 `search.vector` ← final Proposal `managed_vector`；
- Observation `vector` ← exact Attempt `requested_managed_vector`；
- Region representative `status` ← 同 Proposal 的 direct Observation / terminal Evaluation / FailureRecord。

内部选择 eager hydration、lazy table lookup 或两者组合属于 implementation，只要不重新制造可独立修改的权威副本，也不让调用方学习 join 规则。

`PackageReportBuilder.build(...)` 是生产路径的 intern seam。Search 产生领域 CellResult，不产生 wire refs；Proposal producer 必须已经附带 §4.3 的两个 plan digests。

Builder 只省略可由 generation / `cell_ref` 恢复的字段并写入 typed refs。它不得从当前项目补证，也不得改写 Attempt、CandidateSnapshot、Failure 或 Environment identity。`ReportStore.read/write` 只处理 Schema 2。

`ReportStore.update(existing, replacement)` 是不写磁盘的纯合并：两份报告必须属于同一 generation；replacement 的每个 CellResult root 明确替换 existing 的同 Cell root。replacement 不含任何 CellResult 时保留 `existing`（本机 0 cell 的 other-host 保留路径），不失败。不同 generation 调用本方法直接失败。

`update_path` 拥有 search 的持久化更新事务：

1. 路径不存在时写 replacement；
2. 现有文档无法解析、超过读取上限或 `schema_version != 2` 时保守失败；
3. 现有 Schema 2 合法且 generation 不同时写 replacement，不 intern 旧证据池；
4. generation 相同时调用 `update` 替换本次 Cell；
5. 成功路径只原子写一次 Schema 2。

Workflow 不自行比较 raw generation fields 或清理 refs。

`ReportUpdate` 只向 D005 的本地 diagnosis association seam 返回持久化事务已经确定的最小 delta：

```text
report: ValidatedReport
replace_generation: bool
removed_failure_ids: tuple[str, ...]  # 排序唯一；仅同 generation 替换的旧 Cell failures
```

路径不存在或 generation 改变时，`replace_generation=true` 且 `removed_failure_ids=()`。同 generation 更新时为 `false`，并列出被替换旧 Cell roots 的 Failure IDs。

报告写入成功后，Workflow 把该 delta 与新 FailureRecord process facts 交给 `RunLogStore.replace_associations(...)`。两个 store 不互相依赖；association 更新失败按 D002/D005 作为基础设施错误上报。

`merge` / `update` 的规范算法为：

1. 每个输入先独立完成局部、引用、identity 与可达性验证；
2. 比较 §3.2 展开 generation facts；merge 与纯 `update` 要求相同，`update_path` 决定调用 `update` 还是以 replacement 开始新 generation；
3. 只合并最终 CellResult roots；merge 中相同 Cell 的 result 必须完全一致，update 中 replacement 明确替换同 Cell 的旧 root；
4. 从最终 roots 重新 intern 全部 CandidateSnapshot、Attempt、Proposal、Evaluation、Failure、Region 与 graph；
5. 跨输入相同 ID/相同 payload 只定义一次，相同 ID/不同 payload 失败；同 Proposal 的 StaticEvaluation、terminal Evaluation 与 witness 序列同样适用；
6. 删除不再被最终 roots 访问的旧实体，仍被其他 Cell Proposal 使用的共享 ResolutionGraph 保留；
7. projection、coverage、incomplete reasons 与 result 全部从最终 Cell roots 重建，不 union 输入摘要。

模块所有权如下：

| 规则 | 唯一所有者 |
| --- | --- |
| Wire Pydantic models、`schema_version` 校验、canonical codec | report module |
| ID/ref index、展开与可达性 | report module |
| CellResult / projection / completion 跨引用验证 | report module |
| Search 产生哪些 Observation/Region/Boundary | D003 / CoordinateSearch |
| Attempt、Evaluation、Failure cause/disposition | D005 / D008 |
| static fingerprint / runtime witness | D004 / D011 |
| CandidateSnapshot 内容与选择 | CandidateBuilder / D003 / D012 |
| Apply 修改与事务 | ProjectEditor / D001 / D009 |
| 人类展示 | TerminalPresenter / D006 |

调用方不得手写 `dict[id]` join、重复 ID 校验或 Schema 版本分支。`ReportStore` 只接受 `schema_version = 2`。

## 9. 切换策略

项目尚未发布。Schema 2 落地时直接删除现行内联 writer、reader 和 wire models，不提供 migrator、dual-read、dual-write、隐式备份或 sidecar。

`apply`、`explain`、`diagnose`、merge 和 search update 只消费 Schema 2 `ValidatedReport`。缺失或不支持的版本直接失败；开发期旧报告由新的 `pf search` 重生。

拒绝错误必须指出实体类型与稳定 ID，但不得转储完整证据或 process output。

## 10. 实施工件与验收

### 10.1 JSON Schema 与文档工件

Schema 2 实现必须同时提交：

```text
docs/schemas/package-floor-v2.schema.json
docs/examples/package-floor-v2-minimal-complete.json
docs/examples/package-floor-v2-minimal-incomplete.json
```

JSON Schema 从唯一 Pydantic wire models 确定性生成并在测试中检查无 diff，不维护第二套手写字段定义。它负责局部类型、required、discriminator、enum、`additionalProperties: false` 和基本格式；跨引用、内容 digest、可达性、coverage 与 projection 仍由 report validator 负责。

D014 解释字段所有权和跨引用不变量；示例只展示最小结构，不复制完整产品规则。真实大报告不得作为文档示例提交。

### 10.2 安全与资源边界

Schema 2 延续现行公共报告限制：

- `ProcessResult.stdout` / `stderr` 不进入报告；
- 不保存 run ID、本地 diagnosis locator、临时目录或绝对路径；
- SourceIdentity 与 artifact locator 只保存 public locator；
- FailureDetail、start error 和其他字符串在进入报告前已经脱敏；
- ref 不能触发文件、网络或动态 import；
- `pf explain` / `pf diagnose` 解析报告时保持离线、只读；
- 读取上限继续为 64 MiB。

Normalized tables 不能成为 reference amplification。validator 只能按固定有向关系解析本地实体，并使用线性 index；不得递归展开任意用户提供的通用图。目标复杂度为报告字节数和引用数的线性或近线性函数。

### 10.3 结构测试

- 每类实体定义唯一；重复 ID 即使 payload 相同也失败；
- unknown、wrong-kind、cross-cell、cross-generation 和循环 ref 失败；
- 同一 ResolutionGraph 被多个 Cell 的 Proposal 引用合法，cell-scoped refs 跨 Cell 失败；
- 不可达实体失败；
- 每个表的规范排序在 round trip 后稳定；
- optional 字段不输出 `null`；
- `observed_upper`、Observation vector、内联 Cell/Attempt/Proposal/Evaluation 不出现在 Schema 2；
- 顶层只有一份 CandidateSnapshot table；
- static-stage Indeterminate 只在 terminal Evaluation table 定义一次；
- `harness_declaration_ids` 是 opaque IDs，不要求或允许解析为 refs；
- Attempt wire 不保存 `identity_version`；Builder 拒绝非 `attempt-v2` 领域对象；
- JSON Schema 与 Pydantic wire models 同步；
- 缺失或不支持的 `schema_version` 读取失败，不进入 intern。

### 10.4 语义测试

现行证据篡改矩阵必须在 Schema 2 中继续失败，并增加 Proposal plan digest 校验：

- Proposal 缺少 plan digest，或重算 `proposal_id` 与 `EnvironmentIdentity` 不符；
- generation identity 漂移；
- target Cell 缺失、重复或多余；
- CandidateSnapshot digest、candidate order 或 artifact 变化；
- Attempt requested resolution/vector/harness identity 不一致；
- Proposal graph、interpreter、vector 或 policy scope 不一致；
- static baseline、increment、fingerprint 或 witness 不一致；
- Probe status 与 Evaluation / FailureRecord 不一致；
- Static-only Observation 只靠 exact Attempt/Proposal/StaticEvaluation 闭包仍合法；
- Region `candidate_snapshot_ref` 不属于所属 CellResult 的 `candidate_snapshot_refs`；
- Region runtime representative 的派生 status 与 direct evidence 不一致；
- boundary predecessor 没有 direct Rejection；
- final Proposal 不是 PASS 或不属于 reported search；
- failure ref 未被 CellResult 精确拥有；
- projection floor 与 final vector 不一致；
- complete report 覆盖不全或 projection 不可表示。

### 10.5 持久化与 intern 测试

- Builder intern 后 CandidateSnapshot、Attempt、Failure 和 Proposal ID 与领域对象一致；report generation ID 按 §3.2 的 canonical tables 计算；
- 非空 harness declaration IDs 作为 opaque `harness_declaration_ids` 写入；
- 重复实体在一致时只产生一个定义；ID 相同、payload 不同则失败；
- 领域 graph 在计算 identity 前由 producer 规范化；wire 顺序不规范、name 非 canonical、重复 Node/dependency 或同名冲突均读取失败；
- `explain` 可见结果、`diagnose` Failure 顺序和 apply patch 与 intern 前领域证据等价；
- Schema 2 round trip 保留每个 CellResult 的 `failure_refs` evidence 顺序，且 byte-for-byte 稳定；
- 相同 generation 的 merge 得到同一规范结果；
- search update 只读写 Schema 2；
- update 在 replacement 为零 CellResult 时保留 existing；
- update 替换 Cell 后删除仅由旧 Cell 可达的实体，并保留仍被其他 Cell 引用的 ResolutionGraph；
- `update_path` 遇到缺失或不支持的 `schema_version` 失败，不覆盖；
- `ReportUpdate` 在新路径/新 generation 时要求替换 diagnosis generation，在同 generation 时只列出被替换旧 Cell 的 Failure IDs。

### 10.6 体积与性能

实现必须把 §1.2 的 PF 自搜索内联报告提交为固定体积对照样本，而不是生产 reader 输入：

```text
tests/fixtures/report-schema/pf-self-search-inline.json
bytes: 7,682,528
sha256: 29dd927eea928d63a555203f35304bea1f927f5e81963bac1b163e2e209af034
inline_report_generation_id: cf37b403df8eceb0060afaf95ce30effd2a699b28ce84f551c432aa5ed91342b
```

`inline_report_generation_id` 只用于确认对照样本来源，不是 Schema 2 generation ID 的 golden value。

fixture 相邻 README 必须记录生成命令、PF/uv/ty/Python 版本、source snapshot digest 和候选来源；仓库根未跟踪的 `package-floor.json` 不算 qualification fixture。该样本只用于量体积基线，测试不得通过 `ReportStore.read` 解码它。Qualification 记录：

- 内联紧凑 bytes（固定为 4,084,111）；
- Schema 2 紧凑 bytes；
- read + validate 峰值内存和耗时；
- merge 两份互补 Schema 2 报告的峰值内存和耗时。

硬性条件：

1. Schema 2 必须小于同语义内联紧凑编码；
2. 24 个 CandidateSnapshot 只能定义 24 次；
3. 3 个 Cell 只能定义 3 次；
4. 每个 Attempt、Proposal 和 resolution graph 只能定义一次；
5. 体积改善不得依赖删除证据、gzip 或降低 validator 强度。

Schema 2 紧凑编码目标不超过 **2,042,055 bytes**，即比 4,084,111-byte 内联编码至少减少 50%。“约 2.0 MiB”只描述量级，不是另一个 2,097,152-byte 门槛。

若原型超过门槛，必须按实体分类说明剩余体积，并通过 D014 修订显式批准例外。不能靠放宽证据契约达标。峰值内存和耗时首版只记录，不构成新 SLA。

## 11. 契约同步

本文已批准、待实现，落地前不取代任何现行条款。落地后：

- D001 §7 保留报告的产品作用、命令消费者、完整/不完整含义和保守失败，只把 wire layout、版本与规范编码指向 D014；
- D002 的 Schema / ReportStore 章节采纳 §8 的 ValidatedReport、persistence interface 与唯一 Schema 2 codec，删除内联报告模型，不复制引用规则；
- D005 继续拥有 FailureRecord 与 `(report_generation_id, failure_id)` association 的产品含义；wire generation 的规范输入、排序和 `pf:report-generation:v2` 前缀以 §3.2 为准，并同步采纳 `ReportUpdate` 的 diagnosis association delta；
- D003–D005、D008、D011–D013 继续定义各自证据语义，只把持久化字段改为 D014 refs；D002/D012 同步让成功 Proposal 携带两个 plan digest；D012 继续拥有 Environment identity 与 graph 规范化；
- `docs/README.md` 把 D014 状态更新为“现行”，并记录相应实施计划；
- 现行内联报告从代码与产品契约中删除。

落地必须在同一变更中同步上述所有者文档，不能出现代码写 Schema 2 而 D001 仍声明内联 Schema 1 是唯一输出的中间状态。

## 12. 被拒绝的方案与决策摘要

### 12.1 只恢复紧凑 JSON

从 pretty JSON 改回现行 canonical encoding 可把样本从 7.68 MB 降到 4.08 MB，但 162 条 Observation 仍各自内联完整证据；磁盘改善不能解决多重权威或理解成本。

### 12.2 只删除顶层 CandidateSnapshot

这能立即删除约 380 KB 和一处不一致来源，但 Cell、Attempt、Proposal、Evaluation 与 Failure 的高扇出重复仍占主要体积。Schema 2 应一次建立一致的 ref 规则，不为每种实体补零散特例。

### 12.3 删除搜索过程，只保留 final floor

这会让 report 无法复证坐标边界、static-only guidance、FailureRecord、非单调性和 final PASS authority，并削弱 explain/diagnose/merge。报告不是只有 apply patch 的摘要。

### 12.4 主报告 + 本机 evidence sidecar

把大证据移入 `.pf/` 会破坏报告可移植性，使 apply/merge 的授权依赖可丢失本机状态，并引入多文件事务。Schema 2 首版保持一个自包含文档；若未来需要 bundle，必须单独设计 manifest、content hash 和原子发布。

### 12.5 通用 JSON Pointer / `$ref`

通用指针把类型、可达性和允许依赖方向交给字符串路径，数组重排也可能改变引用。Schema 2 使用 typed opaque refs 和固定实体表，不建立通用 graph language。

### 12.6 数组下标或短别名

`attempts[17]`、`c3` 等引用在 merge、排序和增量更新中不稳定，也不能独立校验内容。持久引用使用现有 domain ID 或明确的内容 digest；人类标签由 `pf explain` 生成。

### 12.7 把所有 value object 都放进实体表

为每个 VersionPin、TyDiagnostic 或 ProcessResult 建表会扩大调用者需要理解的 interface，且节省有限。Schema 2 只规范化具有共享 identity、merge 作用或多重权威风险的高扇出实体。

### 12.8 在同一 `schema_version` 下同时接受内联树和 refs

同一版本号同时允许内联对象和 refs 会制造更大的 union、模糊 canonical writer，并让 reader 无法可靠拒绝错误文档。Schema 2 只接受 refs 布局。

### 12.9 为 harness declaration ID 虚构 typed ref

Attempt identity 的 harness facts 只保存 declaration IDs，公共报告没有 `HarnessRequirement` 实体。把这些 ID 改名为 `*_ref` 会要求解析不存在的 target，或从当前项目重新 planning。

这会破坏报告自包含、离线读取和 source-drift 语义。Schema 2 保留 opaque `harness_declaration_ids`；未来若要保存完整声明，必须增加实体表并版本化 Attempt identity。

### 12.10 决策摘要

- `package-floor.json` 继续是版本化公共接口，不降级为实现缓存；
- 保留一个自包含 JSON 文件，先规范化再考虑压缩或 bundle；
- 以稳定 typed refs 替代高扇出按值内联；
- 每类实体有一个定义所有者，其他记录只能引用；
- ID 对展开后的领域事实计算，wire refs 不进入领域 identity；
- `pf:report-generation:v2` 对 canonical declarations/target Cells 计算，不继承内联数组顺序；
- CandidateSnapshot 属于 generation-scoped search input，但不进入 `report_generation_id`；
- harness declaration IDs 保持 opaque，不伪装成缺少 target 的 refs；
- Attempt identity 在 Schema 2 固定为 `attempt-v2`，不保留旧 identity union；
- static-stage Indeterminate 只由 terminal Evaluation table 拥有；
- merge/update 从最终 CellResult roots 重建可达图并清理旧实体，共享 ResolutionGraph 可以跨 Cell 保留；
- `ValidatedReport` 是 resolved immutable facade，wire table 与 typed index 不泄漏给调用方；
- `ReportUpdate` 只把 generation replacement 与旧 Failure ID delta 交给 diagnosis association seam，不让 ReportStore 依赖 RunLogStore；
- 成功 Proposal 保存并重算 `project_plan_digest` / `environment_plan_digest`；
- 空 CellResult replacement 保留 existing；generation 不同时覆盖，不 intern 旧证据池；
- 保留完整 source manifest，因为它体积很小且属于 generation identity；
- 保留 projection/result 等低成本产品摘要，并由底层证据复证；
- 删除 `observed_upper`、重复 final vector 和无语义 `null`；
- 项目未发布：落地直接替换内联报告，不保留 reader 或 migrator；
- D014 只拥有报告 wire interface，不接管搜索、失败、静态证据或 apply 的领域语义。

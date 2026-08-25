# PF 报告 Schema 2

- **状态：** 草案
- **日期：** 2026-08-25
- **适用范围：** `package-floor.json` 的公共 JSON 布局、引用完整性、规范编码与 Schema 1 迁移
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

本文定义 PF 如何把现行报告中的证据树规范化为单一所有者的引用图。目标是在不删除 Attempt、Proposal、Evaluation、CandidateSnapshot、FailureRecord、static region、坐标边界或 projection 证据的前提下，消除同一事实的重复内联和多重权威，使公共报告更小、更容易审计，并继续保守拒绝缺失、冲突或不可表示的证据。

本文只拥有持久化报告的 wire interface：顶层分组、实体表、引用、规范编码、跨引用验证和版本迁移。D001 继续拥有报告的产品作用、命令和 apply 条件；D003–D005、D008、D011–D013 继续拥有被保存证据的领域含义。本文不得通过重排 JSON 改写这些语义。

本文尚未批准或实现。落地前，`schema_version = 1`、`PackageFloorReportV1` 和现行 `ReportStore` 仍是唯一有效行为。

## 1. 问题

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

## 2. 目标与非目标

### 2.1 目标

1. 每个 Cell、CandidateSnapshot、Attempt、Proposal、resolution graph、StaticEvaluation、terminal Evaluation 和 FailureRecord 在报告中最多定义一次；
2. Observation、Region、CellResult、FailureScope 和 Projection 只使用稳定、本报告内引用；
3. Schema 1 的证据闭环、failure disposition、static region、边界、最终 PASS、coverage 和 projection 不变量全部保留；
4. 缺失引用、重复 ID、错误类型引用、跨 Cell 引用、冲突实体和循环引用均保守失败；
5. `apply`、`explain`、`diagnose`、`merge` 和 search update 不各自实现引用解析；
6. 报告继续是一个可移植、原子写入、自包含的 JSON 文件；
7. Schema 1 可以在持久化 seam 被读取和规范化，不要求用户手工迁移；
8. 提交机器可读 JSON Schema、最小完整示例和篡改测试矩阵；
9. 对同一语义报告，Schema 2 的紧凑编码显著小于 Schema 1，并记录可复现的前后对比。

### 2.2 非目标

- 不删除 Observation、失败、构件 hash、resolved graph、static region 或 runtime witness 以换取体积；
- 不改变坐标搜索、candidate order、static guidance、Rejection/Indeterminate 或 apply 语义；
- 不把 `package-floor.json` 变成跨运行 Evaluation cache；
- 不把完整证据移入 `.pf/logs` 或其他本机目录；
- 不在 Schema 2 首版引入 sidecar、外部 JSON Pointer、远程引用、数据库或二进制主格式；
- 不依赖 gzip 证明 schema 已经变清楚；压缩可以是传输选择，不是 wire interface；
- 不用数组下标作为持久引用；merge、规范排序或插入实体不得改变引用；
- 不把短随机别名当作领域 identity；
- 不在 `schema_version = 1` 下静默改变字段含义或布局；
- 不把 report hash 变成签名或可信来源证明。

## 3. 核心决策

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

## 4. 顶层结构

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

顶层对象使用语义分组，而不是把输入、过程证据和最终结果混在同一层。`identity`、有序 `requirement_declarations` 与有序 `target_cells` 共同定义报告世代；`candidate_snapshots` 是该 generation 内冻结的 search input，但不进入 `report_generation_id`。`evidence` 是可被多个 CellResult 引用的证据池；`cell_results` 和 `projections` 是面向产品结果的索引。

## 5. Generation identity

### 5.1 `identity`

`identity` 保存：

- `report_generation_id`；
- `generator`；
- `package`；
- `source_snapshot`；
- `policy_identity`。

Schema 2 首版保留完整 `SourceSnapshotIdentity.entries`。它在样本中只占 0.6%，不是主要体积来源；保留它可以维持现行 generation identity、merge 相等性和 source drift 证据。若未来要只保存 digest，必须单独提升 source identity 策略，不能作为本次去重的顺带修改。

Schema 2 validator 必须按现行 `pf:snapshot:v1` 算法从规范 entries 重算 `source_snapshot.digest`。Schema 1 reader 过去只把 digest 与 entries 作为一组 generation facts，没有复算二者关系；legacy normalization 遇到不一致时必须保守拒绝。

### 5.2 `report_generation_id` preimage

Schema 2 继续使用现行 `pf:report-generation:v1` identity。其展开 preimage 精确为：

```text
generator
package
source_snapshot
policy_identity
requirement_declarations  # 保留 Schema 1 数组顺序
target_cells              # 保留 Schema 1 数组顺序；Cell 使用 active_declaration_ids
```

`requirement_declarations` 和 `target_cells` 的数组顺序是现行 generation identity 的一部分。Schema 2 writer 不按 `declaration_id` 或 `cell_id` 重排这两个表；legacy normalization 原样保留已经通过 Schema 1 identity 校验的顺序。CandidateSnapshot、CellResult、projection、failure 和其他 evaluation evidence 不进入 generation preimage。

因此，`inputs` 是 wire interface 的语义分组，不表示其中每个表都属于 generation identity。若未来要让 generation identity 忽略输入数组顺序或吸收 CandidateSnapshot，必须提升为新的领域 identity 版本，并单独迁移 diagnosis association；不能借 Schema 2 normalization 隐式改变。

### 5.3 隐式 generation scope

以下字段不再在每个嵌套实体重复：

- `source_snapshot_digest`；
- `evaluation_policy_identity` / `policy_identity`；
- 完整 `Cell`；
- `active_declaration_ids`。

Attempt、Proposal、CandidateSnapshot 和 static region 都属于一个 PackageFloorReport generation。它们通过 `cell_ref` 取得 Cell，通过顶层 `identity` 取得 source snapshot 与 policy，通过 Cell 取得 active declarations。

validator 必须先解析这些引用，再按现行完整语义重建 identity 输入并校验 ID。省略 wire 字段不省略 identity 事实。

## 6. Input tables

### 6.1 RequirementDeclaration

`inputs.requirement_declarations` 保留现行 `RequirementDeclaration` 字段和 `declaration_id`。每个 ID 只能定义一次；表顺序保留 §5.2 的 generation preimage 顺序，不按 `declaration_id` 重排。

其他实体使用 `declaration_ref` 或 `declaration_refs`。引用必须存在；需要规范顺序的集合必须排序且唯一。

### 6.2 TargetCell

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

它沿用现行 `cell_identity` 的 lookup 语义，不包含 `active_declaration_refs`。后者属于当前报告世代的 Cell record；同一 generation 中不得出现相同 `cell_id` 和不同 active declarations。跨 generation 引用不合法，因此声明变化不要求重命名兼容性 Cell。

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

`canonical_identity_json` 的精确定义见 §10.3。`cell_id`、`active_declaration_refs` 和任何 Schema 2 `*_ref` 都不进入 payload。`target_cells` 表顺序保留 §5.2 的 generation preimage 顺序，不按 `cell_id` 排序。

`target_cells` 必须继续显式存在。Incomplete report 不能从已有 `cell_results` 反推出尚未运行的目标 Cell。

### 6.3 CandidateSnapshot

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

## 7. Evidence tables

### 7.1 ResolutionGraph

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

### 7.2 Attempt

```json
{
  "attempt_id": "...",
  "identity_version": "attempt-v2",
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

Attempt 不再保存 source snapshot digest、policy identity、完整 Cell 或重复的 active declarations。validator 从 generation 与 `cell_ref` 展开这些事实，按对应 `identity_version` 重算现行 `attempt_id`。

`harness_declaration_ids` 保留现行 Attempt identity 中排序唯一的 opaque IDs，不是 `*_ref`。Schema 1 没有保存完整 `HarnessRequirement`，legacy reader 不能离线补出 ref target；Schema 2 不伪造该缺失 preimage。运行期 planning 继续消费 D012 的结构化 HarnessRequirement，公共报告只保存现行 Attempt identity 已经拥有的 declaration IDs、baseline digest 和 selected-candidate digest。

`requested_resolution` 的互斥条件保持不变：highest / lowest-direct 不携带 exact vector，exact-vector 必须携带排序唯一的 vector；attempt-v2 的 harness 与 selected candidate 约束保持不变。

### 7.3 Proposal

```json
{
  "proposal_id": "...",
  "attempt_ref": "...",
  "managed_vector": [],
  "fixed_declaration_refs": [],
  "resolution_graph_ref": "resolution-...",
  "interpreter": {
    "implementation": "cpython",
    "version": "3.12.11",
    "abi": "cpython-312-x86_64-linux-gnu"
  }
}
```

Proposal 不再重复 `snapshot_digest`、Cell、policy 或 resolved graph。一个 Attempt 最多产生一个 Proposal；prepare failure 不得虚构 Proposal。

现行 `proposal_id` 实际等于 `EnvironmentIdentity.digest`，其 preimage 还包含 project plan digest 与 environment plan digest；Schema 1 的成功 Proposal 没有保存这两个 digest。因此，仅凭公共 Schema 1 不能重算合法的现行 `proposal_id`。Schema 2 不伪造缺失证据，也不把 graph digest 错当成 environment identity：

- `proposal_id` 保持现行 opaque environment identity，并成为 Proposal 的稳定 ref；
- validator 要求它非空、在 Proposal table 中唯一，并复证 Attempt、Cell、vector、graph、interpreter 与 Evaluation 的全部公开关系；
- legacy normalization 原样保留已经通过 Schema 1 交叉验证的 `proposal_id`；
- 若未来要让公共报告独立重算 Proposal ID，必须保存两个 plan digest 并版本化 Proposal identity；该增强不作为 Schema 2 normalization 的隐式前提。

`managed_vector` 仍保留在 Proposal 中。它是解析后的实际向量；对于 exact Attempt，validator 要求它与 requested vector 一致，但 request 和 realized result 是不同阶段的事实，不合并为一个字段。

### 7.4 StaticEvaluation

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

`STATIC_REGRESSION` 变体额外保存 `classifications`。`static_evaluations` 只拥有已经产生 `TyCheck` 的 `STATIC_UNCHANGED | STATIC_REGRESSION`；static tool failure 是 terminal `INDETERMINATE`，只定义在 §7.5 的 `evaluations` 表中，不在两张表重复定义。不存在的字段不以 `null` 占位。

Terminal Evaluation 不再内联 StaticEvaluation，只保存 `static_evaluation_ref`，其值为同一 Proposal ref。static baseline 也不复制 `TyCheck`；CellResult 只保存 baseline Proposal ref 与 diagnostic digest，validator 从本表取得唯一 TyCheck。

### 7.5 Terminal Evaluation

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

`INDETERMINATE` 可以发生在 static、witness 或 test stage。static stage 尚未产生合法 StaticEvaluation 时，该变体省略 `static_evaluation_ref`；witness/test stage 必须引用同 Proposal 已有的 StaticEvaluation。这样现行同时属于 `StaticEvaluation` 与 `Evaluation` union 的 `IndeterminateEvaluation` 在 wire 上仍只有一个所有者。

### 7.6 FailureRecord

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

## 8. CellResult 与搜索引用

### 8.1 判别变体

CellResult 继续保留现行终态：

```text
SUCCESS
BASELINE_REJECTION
BASELINE_INDETERMINATE
CELL_INDETERMINATE
SEARCH_FAILED
```

Schema 2 首版使用以上精确字符串；不得把“落地时现行实现”作为可变的 wire 定义。未来领域状态重命名必须提升相应 Schema 或提供显式迁移，不能借 normalization 静默改变。

所有变体使用 `cell_ref`，并通过 `attempt_ref`、`proposal_ref`、`candidate_snapshot_refs` 和 `failure_refs` 连接证据表。FailureRecord 统一位于 `evidence.failures`；CellResult 只列出属于当前 Cell 的 refs。

### 8.2 Success

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

以下 Schema 1 字段被删除：

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

### 8.3 ProbeObservation

Direct observation：

```json
{
  "dependency": "packaging",
  "candidate_version": "19.2",
  "evidence": {
    "kind": "DIRECT",
    "attempt_ref": "...",
    "status": "REJECTED",
    "failure_ref": "failure-..."
  }
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

Observation 不再保存完整 vector、Attempt、Proposal、StaticEvaluation 或 terminal Evaluation。所有 Direct 与 Static-only Observation 都必须引用当前 Cell 的 `exact-vector` Attempt；vector 恒从 Attempt 的 `requested_managed_vector` 取得，validator 继续要求 `dependency/candidate_version` 命中该 vector。`highest` 只用于 Cell baseline，`lowest-direct` 不进入 coordinate observations；二者不得借缺失 vector 混入本结构。

Direct status 必须与引用 Evaluation / FailureRecord 一致：

- `PASS`：Attempt 必须有 Proposal 与 PASS Evaluation，不得有 failure ref；
- `REJECTED`：必须有 REJECTED FailureRecord；若已产生 Proposal，还必须有匹配的负向 Evaluation；
- `INDETERMINATE`：必须有 INDETERMINATE FailureRecord；若已产生 Proposal，还必须有匹配的 Indeterminate Evaluation。

### 8.4 StaticRegion

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

Schema 1 `StaticRegionSlice` 中的 Cell、source snapshot、policy 和 candidate order 不再内联：

- Cell 来自所属 CellResult；
- source/policy 来自 generation；
- active dependency 与 candidate order 来自 CandidateSnapshot；
- other coordinates、baseline digest 和 fingerprint 仍由 Region 拥有。

Runtime reference 只选择 region 的 direct runtime representative，不重复保存其 status。`PASS | REJECTED | INDETERMINATE` 从同 Proposal 的 direct Observation、terminal Evaluation 与 FailureRecord 唯一推导；三者不一致时整个报告失败。

`region_id` 对规范化后的完整 region 语义计算：

```text
payload = {
  "slice": expanded_schema_1_static_region_slice,
  "static_fingerprint": static_fingerprint,
  "observed_versions": versions_in_candidate_order,
  "runtime_proposal_ids": sorted_unique_proposal_ids
}
region_id = "region-" + sha256(
  b"pf:static-region:v1\0" + canonical_identity_json(payload)
).hexdigest()
```

展开的 Slice 使用 Schema 1 字段名与 value shape：完整 Cell 使用 `active_declaration_ids`，source/policy 来自 generation，active dependency/candidate order 来自 CandidateSnapshot，other coordinates 按 dependency 排序且唯一。Derived runtime status、`region_id` 和所有 Schema 2 refs 都不进入 payload。Static-only observation 使用 local `region_ref`；引用必须属于同一个 CellResult。虽然 digest 覆盖完整 Cell context，Region 仍不是全局实体表，不能被其他 CellResult 引用。

### 8.5 Coordinate outcome

Coordinate status、`boundaries`、`regions`、`sweeps`、counterexample 和 terminal failure ref 保持现行语义。成功 outcome 不再单独保存 `vector`；它由 `final_proposal_ref` 唯一取得，boundaries 必须与该 vector 相等。

`CoordinateBoundary.predecessor_failure_id` 改名为 `predecessor_failure_ref`，但仍必须引用同 dependency、同 predecessor、同 Slice 的 direct ProbeRejection。

## 9. Projection 与报告结果

### 9.1 ProjectionEvidence

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

### 9.2 `result`

`complete | incomplete` 保持判别 union。`complete` 虽可由覆盖与 projection 推导，仍作为公共结果摘要保留；validator 必须重新推导并拒绝不一致值。Incomplete reasons 必须规范排序且与 CellResult、缺失覆盖和不可表示 projection 相符。

这些摘要字段属于有意、低成本的派生信息，不是第二份底层证据。

## 10. 引用与 ID 规则

### 10.1 引用只在本报告内有效

所有 `*_ref` 都是 opaque string，只能解析到当前 JSON document 中对应类型的唯一实体。禁止：

- URI、文件路径或 JSON Pointer；
- 跨报告或跨 generation 引用；
- 依赖数组位置；
- 大小写、名称 canonicalization 或截断后的模糊匹配。

### 10.2 定义唯一，引用可重复

每个实体 ID 在对应类型表中必须恰好定义一次。相同 ID 即使 payload 相同也不能重复出现；输入重复说明 producer 没有完成 normalization。引用字段本身携带目标类型，不要求不同 identity namespace 的字符串在全报告内互不相同。未知、悬空或类型错误引用直接使整个报告无效。

### 10.3 ID 对展开后的语义计算

Schema 2 不把物理 `*_ref` 字符串替换成领域 identity 输入。本文新增 ID 使用下列统一编码：

```text
canonical_identity_json(value) = json.dumps(
  value,
  sort_keys=True,
  separators=(",", ":"),
  ensure_ascii=True
).encode("utf-8")
```

这一定义只用于 identity preimage；§12 的 wire JSON 继续不转义非 ASCII 字符。各 ID 的展开 record 与算法如下：

| Wire identity | 展开的语义 record | 算法与约束 |
| --- | --- | --- |
| `report_generation_id` | §5.2 的 generator/package/source/policy/ordered declarations/ordered target Cells | 现行 `pf:report-generation:v1`；CandidateSnapshot 与 Schema 2 refs 不进入 |
| `source_snapshot.digest` | 按 path 排序的完整 Schema 1 `SnapshotEntry[]` | 现行 `pf:snapshot:v1` |
| `candidate_snapshot_id` | 注入完整 Cell 与顶层 policy 的 Schema 1 CandidateSnapshot identity | 现行 `pf:candidate-snapshot:v1` |
| `attempt_id` | 注入 source digest、完整 Cell、active declaration IDs 与 policy 的 `AttemptIdentity` | 按 `identity_version` 使用现行 `pf:attempt:v1|v2` |
| `failure_id` | 把 scope ref 展开为完整 `AttemptFailureScope | CellFailureScope` 的 Schema 1 FailureRecord facts | 现行 `pf:failure:v1`，保留现行截断长度 |
| `proposal_id` | 不展开缺失的 project/environment plan digests | §7.3 的 opaque ID，只校验唯一性与公开关系 |
| `cell_id` | §6.2 的精确 payload | `pf:cell:v1`，最终 ref 为 `cell-` 加完整 SHA-256 hex |
| `resolution_graph_id` | §7.1 的规范 node array | `pf:resolution-graph:v1`，最终 ref 为 `resolution-` 加完整 SHA-256 hex |
| `region_id` | §8.4 的规范 region payload | `pf:static-region:v1`，最终 ref 为 `region-` 加完整 SHA-256 hex |

校验可重建 identity 时，validator 必须：

1. 解析引用；
2. 展开顶层 generation facts；
3. 重建对应现行语义 record；
4. 使用该领域 identity 的版本和前缀计算 digest；
5. 与 wire ID 比较。

因此，规范化布局不会使“同一证据”得到另一个 generation、CandidateSnapshot、Attempt 或 Failure ID。现行 identity 函数仍是各自领域算法的唯一实现所有者；本表拥有的只是 Schema 2 ref 到既有 preimage 的展开规则。Proposal ID 按 §7.3 作为现行 opaque environment identity 保留。

### 10.4 引用图必须无环

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

## 11. 验证顺序与保守失败

### 11.1 引用作用域

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

### 11.2 验证顺序

Report validator 按以下顺序执行：

1. 检查 64 MiB 读取上限、JSON 语法、顶层 object 和精确 `schema_version`；
2. 用 `extra="forbid"` 校验每个判别 record 的局部结构；
3. 建立所有实体的私有 typed index，拒绝重复 ID；
4. 解析全部引用，拒绝 unknown、wrong-kind 和 cross-cell refs；
5. 校验 Cell、source snapshot、CandidateSnapshot、resolution graph、Attempt、FailureRecord 与 report generation identity，并校验 Proposal ID 唯一性；
6. 校验 StaticEvaluation、terminal Evaluation、witness、FailureRecord cause/stage/disposition；
7. 校验 Observation、static region、coordinate boundary 与 terminal outcome；
8. 校验 CellResult baseline/final authority 和 failure ref 精确集合；
9. 校验 target Cell coverage、projection 等价性和 complete/incomplete result；
10. 只有全部通过才返回可供命令消费的 `ValidatedReport`。

任何阶段都不得通过丢弃未知实体、选择第一份冲突记录、忽略未引用 FailureRecord 或把悬空引用降级为 warning 来恢复。

可达性从 generation inputs、`cell_results`、`projections` 与 `result` 这些 roots 计算固定闭包：

1. CellResult 的 baseline/final、candidate snapshot、Observation、Region、boundary 与 failure refs 进入闭包；
2. 可达 Attempt 使满足 `Proposal.attempt_ref == Attempt.attempt_id` 的至多一个 Proposal 可达；只有 prepare failure 可以没有 Proposal；
3. 可达 Proposal 使其 ResolutionGraph，以及存在的至多一条 StaticEvaluation 和至多一条 terminal Evaluation 可达；
4. Evaluation 与 witness 使其 `failure_ref` 可达；FailureScope 使其 Attempt/Cell 可达；
5. Region 使其 CandidateSnapshot 与 runtime representative Proposals 可达；
6. Projection 使其 declaration 与 floor Cells 可达；
7. 所有表定义必须被闭包访问，且每条 cell-scoped 边通过 §11.1。

因此，Static-only Observation 的 `attempt_ref` 足以使该 Attempt、其唯一 Proposal、StaticEvaluation 与 ResolutionGraph 可达；它不需要虚构 Terminal Evaluation，也不要求自己的 Proposal 同时成为 Region runtime representative。Direct status、Static-only guidance、Region representative status 与 FailureRecord 仍必须由同一闭环复证。

不可达实体使报告无效，避免证据池成为未受约束的附加数据区。

## 12. 规范 JSON 编码

Schema 2 继续使用：

- UTF-8；
- key 字典序；
- separators `(",", ":")`；
- 非 ASCII 字符不转义；
- 单个末尾换行；
- 临时文件、flush、`fsync` 和原子 replace。

Schema 2 的可空事实使用判别变体表达。不存在的 optional 字段在 wire JSON 中省略，不写 `null`；语义上存在的空集合写 `[]`。Discriminator、ID/ref、status、cause、stage 和影响 identity 的字段不得依赖默认省略。

规范化实体表使用稳定排序：

| 表 | 顺序 |
| --- | --- |
| declarations | §5.2 的 generation preimage 顺序 |
| cells | §5.2 的 generation preimage 顺序 |
| candidate snapshots | `(cell_ref, dependency)` |
| resolution graphs | `resolution_graph_id` |
| attempts | `attempt_id` |
| proposals | `proposal_id` |
| static/terminal evaluations | `proposal_ref` |
| failures | `failure_id` |
| cell results | `(package, target, python_minor, extra_surface)` |
| projections | `declaration_ref` |

嵌套集合也必须有唯一顺序：`active_declaration_refs`、`harness_declaration_ids` 按 ID 排序且唯一；`candidate_snapshot_refs` 按 dependency 排序且唯一；Region runtime refs 按 `proposal_ref` 排序且唯一；Projection floors 按上述 cell-result order key 排序且唯一。`failure_refs` 必须唯一，但保留 D003/D005 产生的 CellResult evidence 顺序，不按 ID 重排。Candidate order、observed versions、witness 顺序和其他由 D003/D005/D011 拥有的领域序列同样保留其领域顺序。

Pretty JSON 不是规范写入格式。人类展示继续由 `pf explain` / `pf diagnose` 拥有；文档示例可以 pretty-print，但不能作为 golden byte fixture。

## 13. Report module interface 与所有权

Schema 2 应加深 report module，而不是让调用方学习多个表的 join 规则。

本节是 D014 草案为了消除 hydrate-vs-query 分叉而固定的落地验收 interface，不声明现行实现已经改变，也不成为第二个长期所有者。D014 处于草案期间，现行 module interface 仍只由 D002 定义；批准落地时，D002 必须在同一变更中采纳这里的 resolved facade 与 persistence seam，D005 必须采纳 diagnosis association delta，此后具体 Python 名称与签名仍分别只由 D002/D005 拥有。D014 继续拥有的后置条件只有：wire refs 不泄漏、Schema 1/2 返回同一 resolved view、持久化更新后实体图满足本文的 identity/冲突/可达性规则。

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
requirement_declarations        # §5.2 顺序
target_cells                    # §5.2 顺序
cell_results                    # §12 report order；返回 resolved CellResult views
projection_evidence             # declaration generation order
result
failure_records                 # cell-result order + each CellResult.failure_refs order
cell_result(cell_id) -> ResolvedCellResult | None
failure(failure_id) -> FailureRecord | None
failure_context(failure_id) -> FailureContext | None
```

`FailureContext` 精确包含所属 resolved Cell、可选 Proposal identity，以及 `predecessor | None` boundary role；它不包含本地日志 locator。日志 lookup 继续由 D005 的 `(report_generation_id, failure_id)` association 拥有。

Resolved CellResult/Observation/Region views 暴露现行命令所需的完整 typed 机械事实，包括 static baseline、incremental diagnostics、boundary role 和 Proposal identity；所有 `*_ref` 已由 report module 解析。它们共享 immutable interned 实体，不为每次查询深复制完整树。内部选择 eager hydration、lazy table lookup 或两者组合属于 implementation，只要不重新制造可独立修改的权威副本，也不让调用方学习 join 规则。

`PackageReportBuilder.build(...)` 是生产路径的 intern seam：Search 继续产生 D003/D005/D011 的领域 CellResult，不产生 wire refs；Builder 把这些领域事实收敛为 `ValidatedReport`。`ReportStore.read(...)` 对 Schema 1/2 产生同一个 facade，`write(...)` 只编码 Schema 2。

`ReportStore.update(existing, replacement)` 是不写磁盘的纯合并：两份报告必须属于同一 generation；replacement 的每个 CellResult root 明确替换 existing 的同 Cell root，空 replacement 失败。不同 generation 调用本方法直接失败。

`update_path` 拥有 search 的持久化更新事务：路径不存在时写 replacement；现有报告合法但 generation 不同时写 replacement；generation 相同时调用 `update` 替换本次 Cell；任何未知或非法现有报告保守失败；成功路径只原子写一次 Schema 2。Workflow 不自行读取 schema version、比较 raw generation fields 或清理 refs。

`ReportUpdate` 只向 D005 的本地 diagnosis association seam 返回持久化事务已经确定的最小 delta：

```text
report: ValidatedReport
replace_generation: bool
removed_failure_ids: tuple[str, ...]  # 排序唯一；仅同 generation 替换的旧 Cell failures
```

路径不存在或 generation 改变时 `replace_generation=true` 且 `removed_failure_ids=()`；同 generation 更新时为 `false`，并列出所有只属于被替换旧 Cell roots 的 Failure IDs。Workflow 在报告写入成功后，把该 delta 与 replacement Cell 的新 FailureRecord process facts 交给 `RunLogStore.replace_associations(...)`。ReportStore 不依赖 RunLogStore，RunLogStore 也不解析报告表；association 更新失败继续按 D002/D005 作为基础设施错误上报。

`merge` / `update` 的规范算法为：

1. 每个输入先独立完成局部、引用、identity 与可达性验证；
2. 比较 §5.2 展开 generation facts；merge 与纯 `update` 要求相同，`update_path` 决定调用 `update` 还是以 replacement 开始新 generation；
3. 只合并最终 CellResult roots；merge 中相同 Cell 的 result 必须完全一致，update 中 replacement 明确替换同 Cell 的旧 root；
4. 从最终 roots 重新 intern 全部 CandidateSnapshot、Attempt、Proposal、Evaluation、Failure、Region 与 graph；
5. 跨输入相同 ID/相同 payload 只定义一次，相同 ID/不同 payload 失败；同 Proposal 的 StaticEvaluation、terminal Evaluation 与 witness 序列同样适用；
6. 删除不再被最终 roots 访问的旧实体，仍被其他 Cell Proposal 使用的共享 ResolutionGraph 保留；
7. projection、coverage、incomplete reasons 与 result 全部从最终 Cell roots 重建，不 union 输入摘要。

模块所有权如下：

| 规则 | 唯一所有者 |
| --- | --- |
| Wire Pydantic models、version dispatch、canonical codec | report module |
| ID/ref index、展开与可达性 | report module |
| CellResult / projection / completion 跨引用验证 | report module |
| Search 产生哪些 Observation/Region/Boundary | D003 / CoordinateSearch |
| Attempt、Evaluation、Failure cause/disposition | D005 / D008 |
| static fingerprint / runtime witness | D004 / D011 |
| CandidateSnapshot 内容与选择 | CandidateBuilder / D003 / D012 |
| Apply 修改与事务 | ProjectEditor / D001 / D009 |
| 人类展示 | TerminalPresenter / D006 |

调用方不得手写 `dict[id]` join、重复 ID 校验或 Schema 1/2 分支。版本兼容只存在于 ReportStore persistence seam。

## 14. Schema 1 迁移

### 14.1 读取与写入策略

落地后的 ReportStore：

- 读取 Schema 1 和 Schema 2；
- 写入只产生 Schema 2；
- 读到 Schema 1 时先执行完整 `PackageFloorReportV1` 校验，再规范化为 ValidatedReport；
- `apply`、`explain` 和 `diagnose` 可以消费规范化后的旧报告；
- search update 统一调用 `update_path`：generation 相同则替换 Cell，不同则开始新 generation，并以 Schema 2 原子写回；
- merge 可以接受语义 generation 相同的 Schema 1/2 输入，输出 Schema 2；
- 不 dual-write，不在原文件旁生成隐式备份或 sidecar。

未知 Schema 继续失败。Schema 1 兼容期的删除需要单独产品决策，不在本文预设时间。

### 14.2 Schema 1 normalization

Legacy adapter 按以下规则收敛重复实体：

1. 按 `pf:snapshot:v1` 重算 source digest；RequirementDeclaration ID 必须唯一；declarations 与 target Cells 保留现行 generation preimage 顺序；
2. `target_cells` 建立 Cell table；所有嵌套 Cell 必须解析到完全相同的目标 Cell record，包括 active declaration IDs；
3. 顶层 CandidateSnapshot 集合必须与所有 CellResult 内集合按 `(cell, dependency, digest)` 完全一致；
4. 同一 Attempt ID 的展开 identity 必须完全一致，且同一 Attempt 最多映射一个 Proposal；
5. 同一 Proposal ID 的公开 payload 必须完全一致；legacy adapter 不声称重算缺失 preimage 的 environment identity；
6. 同一 Proposal 的 StaticEvaluation、terminal Evaluation、TyCheck、witness 序列和 FailureRecord 关系必须完全一致；static-stage Indeterminate 只进入 terminal Evaluation table；
7. legacy resolved graph 只允许规范化数组顺序：Node name 与 dependency name 必须已经是现行 PEP 503 canonical form；Node 或单个 Node 内 dependency 即使 payload 相同也不得重复；非规范名称、重复名称或同名冲突 payload 全部拒绝。通过后以 §7.1 内容 digest 合并；相同 region preimage 得到相同 local ID，不同 payload 的 ID 碰撞失败；
8. Probe 与 FailureRecord 的 scope/cause/disposition/process 必须通过现行交叉校验后改为 refs；
9. final vector、search vector 和 final Proposal vector 必须相等后只保留 final Proposal ref；
10. Projection Cell 必须解析到 target Cell；
11. 任何冲突都失败，不选择“顶层优先”“嵌套优先”、first 或 last。

Legacy normalization 有意比现行 `PackageFloorReportV1` reader 更严格：

| 现行 reader 可接受的歧义 | Schema 2 normalization |
| --- | --- |
| source digest 与 entries 未复算 | 拒绝 |
| 重复 declaration ID | 拒绝 |
| target Cell 与嵌套 Cell lookup key 相同但 active declarations 不同 | 拒绝 |
| 顶层/CellResult CandidateSnapshot 副本漂移 | 拒绝 |
| 同一 opaque Proposal ID 对应不同公开 payload | 拒绝 |
| 同一 Proposal 的 static/terminal Evaluation 或 witness 出现冲突副本 | 拒绝 |
| resolution graph 仅顺序非规范 | 排序后规范化 |
| resolution graph name 非 canonical、Node/dependency 重复或同名冲突 | 拒绝 |

这些输入不具备无歧义迁移条件，应要求重新 search。拒绝条件必须在错误中指出实体类型与稳定 ID，不转储完整证据或 process output。

### 14.3 Identity 保持

Normalization 对可重建的展开语义计算 ID，因此通过 §14.2 legacy normalization 的 Schema 1 报告保持原有 generation、CandidateSnapshot、Attempt 和 Failure ID。尤其是 declarations/target Cells 使用原数组顺序重建 generation preimage。Proposal ID 按 §7.3 原样保留并统一引用；Schema 2 新增的 Cell、resolution graph 和 region refs 不进入这些旧 identity 的 hash 输入。`harness_declaration_ids` 原样保留，不解析为不存在的实体。

若未来领域 identity 自身升级，必须由对应所有者定义新版本；不能把 wire layout 变化伪装成领域 identity 变化。

## 15. JSON Schema 与文档工件

Schema 2 实现必须同时提交：

```text
docs/schemas/package-floor-v2.schema.json
docs/examples/package-floor-v2-minimal-complete.json
docs/examples/package-floor-v2-minimal-incomplete.json
```

JSON Schema 从唯一 Pydantic wire models 确定性生成并在测试中检查无 diff，不维护第二套手写字段定义。它负责局部类型、required、discriminator、enum、`additionalProperties: false` 和基本格式；跨引用、内容 digest、可达性、coverage 与 projection 仍由 report validator 负责。

D014 解释字段所有权和跨引用不变量；示例只展示最小结构，不复制完整产品规则。真实大报告不得作为文档示例提交。

## 16. 安全与资源边界

Schema 2 延续现行公共报告限制：

- `ProcessResult.stdout` / `stderr` 不进入报告；
- 不保存 run ID、本地 diagnosis locator、临时目录或绝对路径；
- SourceIdentity 与 artifact locator 只保存 public locator；
- FailureDetail、start error 和其他字符串在进入报告前已经脱敏；
- ref 不能触发文件、网络或动态 import；
- `pf explain` / `pf diagnose` 解析报告时保持离线、只读；
- 读取上限继续为 64 MiB。

Normalized tables 不能成为 reference amplification。validator 只能按固定有向关系解析本地实体，并使用线性 index；不得递归展开任意用户提供的通用图。目标复杂度为报告字节数和引用数的线性或近线性函数。

## 17. 测试与验收

### 17.1 结构测试

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
- JSON Schema 与 Pydantic wire models 同步。

### 17.2 语义等价测试

现行 Schema 1 篡改矩阵必须在 Schema 2 中继续失败，包括：

- generation identity 漂移；
- target Cell 缺失、重复或多余；
- CandidateSnapshot digest、candidate order 或 artifact 变化；
- Attempt requested resolution/vector/harness identity 不一致；
- Proposal graph、interpreter、vector 或 policy scope 不一致；
- static baseline、increment、fingerprint 或 witness 不一致；
- Probe status 与 Evaluation / FailureRecord 不一致；
- Static-only Observation 只靠 exact Attempt/Proposal/StaticEvaluation 闭包仍合法；
- Region runtime representative 的派生 status 与 direct evidence 不一致；
- boundary predecessor 没有 direct Rejection；
- final Proposal 不是 PASS 或不属于 reported search；
- failure ref 未被 CellResult 精确拥有；
- projection floor 与 final vector 不一致；
- complete report 覆盖不全或 projection 不可表示。

### 17.3 迁移测试

- 真实 Schema 1 complete / incomplete fixture 能规范化为 Schema 2；
- v1→v2 后 generation、CandidateSnapshot、Attempt、Failure ID byte-for-byte 不变，ordered declarations/target Cells 不被 ID 排序；
- 非空 harness declaration IDs 原样迁移为 opaque `harness_declaration_ids`；
- Schema 1 重复实体在一致时只产生一个 Schema 2 定义；
- 顶层/嵌套 CandidateSnapshot 冲突保守失败；
- source digest、nested Cell declarations、Proposal payload、static/terminal Evaluation 或 witness 冲突保守失败；
- legacy resolution graph 的 Node/dependency 数组只重排顺序，非 canonical name、重复项与同名冲突失败；
- v1→v2 后 `explain` 可见结果、`diagnose` Failure 顺序和 apply patch 完全等价；
- v1→v2 与 Schema 2 round trip 都保留每个 CellResult 的 `failure_refs` evidence 顺序；
- v1/v2 merge 对相同 generation 得到同一规范结果；
- search update 原子地把旧报告升级为 Schema 2；
- update 替换 Cell 后删除仅由旧 Cell 可达的实体，并保留仍被其他 Cell 引用的 ResolutionGraph；
- `ReportUpdate` 在新路径/新 generation 时要求替换 diagnosis generation，在同 generation 时只列出被替换旧 Cell 的 Failure IDs；
- Schema 2 round trip byte-for-byte 稳定。

### 17.4 体积与性能

实现必须把 §1.2 的 PF 自搜索报告提交为固定 qualification fixture：

```text
tests/fixtures/report-schema/pf-self-search-v1.json
bytes: 7,682,528
sha256: 29dd927eea928d63a555203f35304bea1f927f5e81963bac1b163e2e209af034
report_generation_id: cf37b403df8eceb0060afaf95ce30effd2a699b28ce84f551c432aa5ed91342b
```

fixture 相邻 README 必须记录生成命令、PF/uv/ty/Python 版本、source snapshot digest 和候选来源；仓库根未跟踪的 `package-floor.json` 不算 qualification fixture。Qualification 记录：

- Schema 1 紧凑 bytes；
- Schema 2 紧凑 bytes；
- read + validate 峰值内存和耗时；
- merge 两份互补报告的峰值内存和耗时。

硬性条件：

1. Schema 2 必须小于同语义 Schema 1 紧凑编码；
2. 24 个 CandidateSnapshot 只能定义 24 次；
3. 3 个 Cell 只能定义 3 次；
4. 每个 Attempt、Proposal 和 resolution graph 只能定义一次；
5. 体积改善不得依赖删除证据、gzip 或降低 validator 强度。

设计目标的精确门槛为 Schema 2 紧凑编码不超过 **2,042,055 bytes**，即相对 4,084,111-byte Schema 1 至少减少 50%；口语中的“约 2.0 MiB”只描述量级，不是另一个 2,097,152-byte 门槛。若原型超过 2,042,055 bytes，不能仅以“仍比 Schema 1 小”宣布 D014 已实施；必须给出按实体分类的剩余体积与原因，并以 D014 修订显式批准例外。不能通过放宽证据契约达标。峰值内存和耗时首版只作为记录项，不构成新的产品 SLA。

## 18. 对现行契约的取代

本文处于草案状态，不取代任何现行条款。批准并落地后：

- D001 §7 保留报告的产品作用、命令消费者、完整/不完整含义和保守失败，只把 wire layout、版本与规范编码指向 D014；
- D002 的 Schema / ReportStore 章节采纳 §13 的 ValidatedReport 与 persistence interface、Schema 1 legacy adapter 和 Schema 2 codec，不复制引用规则；D005 同步采纳 `ReportUpdate` 的 diagnosis association delta；
- D003–D005、D008、D011–D013 继续定义各自证据语义，只把持久化字段改为 D014 refs；D012 同步澄清结构化 HarnessRequirement 属于 planning/runtime，而报告持久化其 opaque declaration IDs；
- `docs/README.md` 把 D014 状态更新为“现行”，并记录相应实施计划；
- Schema 1 从“现行 writer”变为“legacy read format”。

落地必须在同一变更中同步上述所有者文档，不能出现代码写 Schema 2 而 D001 仍声明 Schema 1 是唯一输出的中间状态。

## 19. 被拒绝的方案

### 19.1 只恢复紧凑 JSON

从 pretty JSON 改回现行 canonical encoding 可把样本从 7.68 MB 降到 4.08 MB，但 162 条 Observation 仍各自内联完整证据；磁盘改善不能解决多重权威或理解成本。

### 19.2 只删除顶层 CandidateSnapshot

这能立即删除约 380 KB 和一处不一致来源，但 Cell、Attempt、Proposal、Evaluation 与 Failure 的高扇出重复仍占主要体积。Schema 2 应一次建立一致的 ref 规则，不为每种实体补零散特例。

### 19.3 删除搜索过程，只保留 final floor

这会让 report 无法复证坐标边界、static-only guidance、FailureRecord、非单调性和 final PASS authority，并削弱 explain/diagnose/merge。报告不是只有 apply patch 的摘要。

### 19.4 主报告 + 本机 evidence sidecar

把大证据移入 `.pf/` 会破坏报告可移植性，使 apply/merge 的授权依赖可丢失本机状态，并引入多文件事务。Schema 2 首版保持一个自包含文档；若未来需要 bundle，必须单独设计 manifest、content hash 和原子发布。

### 19.5 通用 JSON Pointer / `$ref`

通用指针把类型、可达性和允许依赖方向交给字符串路径，数组重排也可能改变引用。Schema 2 使用 typed opaque refs 和固定实体表，不建立通用 graph language。

### 19.6 数组下标或短别名

`attempts[17]`、`c3` 等引用在 merge、排序和增量更新中不稳定，也不能独立校验内容。持久引用使用现有 domain ID 或明确的内容 digest；人类标签由 `pf explain` 生成。

### 19.7 把所有 value object 都放进实体表

为每个 VersionPin、TyDiagnostic 或 ProcessResult 建表会扩大调用者需要理解的 interface，且节省有限。Schema 2 只规范化具有共享 identity、merge 作用或多重权威风险的高扇出实体。

### 19.8 在 Schema 1 原地接受两种布局

同一 `schema_version` 同时允许内联对象和 refs 会制造更大的 union、模糊 canonical writer，并让旧 reader 无法可靠拒绝新文档。布局变化必须提升为 Schema 2。

### 19.9 为 legacy harness declaration ID 虚构 typed ref

Schema 1 Attempt 只保存 harness declaration IDs，没有完整 HarnessRequirement target。把它们改名为 `*_ref` 会让真实旧报告产生无法解析的引用；从当前项目重新 planning 又会破坏报告自包含、离线读取和 source-drift 语义。Schema 2 保留 opaque `harness_declaration_ids`；未来若公共报告要保存结构化 harness declarations，必须单独增加实体表与迁移策略。

## 20. 决策记录

- `package-floor.json` 继续是版本化公共接口，不降级为实现缓存；
- 保留一个自包含 JSON 文件，先规范化再考虑压缩或 bundle；
- 以稳定 typed refs 替代高扇出按值内联；
- 每类实体有一个定义所有者，其他记录只能引用；
- ID 对展开后的领域事实计算，wire normalization 不改变既有证据 identity；
- generation-sensitive declarations/target Cells 保留现行 preimage 顺序，不按新实体 ID 重排；
- CandidateSnapshot 属于 generation-scoped search input，但不进入 `report_generation_id`；
- harness declaration IDs 保持 opaque，不伪装成缺少 target 的 refs；
- static-stage Indeterminate 只由 terminal Evaluation table 拥有；
- merge/update 从最终 CellResult roots 重建可达图并清理旧实体，共享 ResolutionGraph 可以跨 Cell 保留；
- `ValidatedReport` 是 resolved immutable facade，wire table、typed index 与版本分发不泄漏给调用方；
- `ReportUpdate` 只把 generation replacement 与旧 Failure ID delta 交给 diagnosis association seam，不让 ReportStore 依赖 RunLogStore；
- 保留完整 source manifest，因为它体积很小且属于现行 generation identity；
- 保留 projection/result 等低成本产品摘要，并由底层证据复证；
- 删除 `observed_upper`、重复 final vector 和无语义 `null`；
- Schema 1 只在 persistence seam 兼容，生产 writer 只输出 Schema 2；
- D014 只拥有报告 wire interface，不接管搜索、失败、静态证据或 apply 的领域语义。

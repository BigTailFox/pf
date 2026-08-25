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

1. 每个 Cell、CandidateSnapshot、Attempt、Proposal、resolution graph、StaticEvaluation、FullEvaluation 和 FailureRecord 在报告中最多定义一次；
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

顶层对象使用语义分组，而不是把输入、过程证据和最终结果混在同一层。`identity` 与 `inputs` 共同定义报告世代；`evidence` 是可被多个 CellResult 引用的证据池；`cell_results` 和 `projections` 是面向产品结果的索引。

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

### 5.2 隐式 generation scope

以下字段不再在每个嵌套实体重复：

- `source_snapshot_digest`；
- `evaluation_policy_identity` / `policy_identity`；
- 完整 `Cell`；
- `active_declaration_ids`。

Attempt、Proposal、CandidateSnapshot 和 static region 都属于一个 PackageFloorReport generation。它们通过 `cell_ref` 取得 Cell，通过顶层 `identity` 取得 source snapshot 与 policy，通过 Cell 取得 active declarations。

validator 必须先解析这些引用，再按现行完整语义重建 identity 输入并校验 ID。省略 wire 字段不省略 identity 事实。

## 6. Input tables

### 6.1 RequirementDeclaration

`inputs.requirement_declarations` 保留现行 `RequirementDeclaration` 字段和 `declaration_id`。每个 ID 只能定义一次；规范顺序按 `declaration_id`。

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

建议编码为：

```text
cell- + sha256("pf:cell:v2\0" + canonical cell identity).hexdigest()
```

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

`resolution_graph_id` 是规范排序 `ResolvedNode` 列表的内容 digest：

```text
resolution- + sha256("pf:resolution-graph:v1\0" + canonical nodes).hexdigest()
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
  "harness_declaration_refs": [],
  "harness_baseline_digest": "...",
  "selected_candidate_evidence_digest": "..."
}
```

Attempt 不再保存 source snapshot digest、policy identity、完整 Cell 或重复的 active declarations。validator 从 generation 与 `cell_ref` 展开这些事实，按对应 `identity_version` 重算现行 `attempt_id`。

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

`STATIC_REGRESSION` 变体额外保存 `classifications`；static tool failure 使用明确的 indeterminate 变体和 `failure_ref`。不存在的字段不以 `null` 占位。

Full Evaluation 不再内联 StaticEvaluation，只保存 `static_evaluation_ref`，其值为同一 Proposal ref。static baseline 也不复制 `TyCheck`；CellResult 只保存 baseline Proposal ref 与 diagnostic digest，validator 从本表取得唯一 TyCheck。

### 7.5 FullEvaluation

`evidence.evaluations` 每个 Proposal 最多一条，继续使用 `PASS | TEST_FAIL | RUNTIME_INTERFACE_MISSING | INDETERMINATE` discriminator：

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

具体字符串以落地时现行 Schema 为准；Schema 2 不借迁移重命名领域状态。

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
4. baseline FullEvaluation 为 PASS；
5. final Proposal 属于当前 Cell，且 FullEvaluation 为 PASS；
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

Observation 不再保存完整 vector、Attempt、Proposal、StaticEvaluation 或 FullEvaluation。对于 exact Attempt，vector 从 Attempt 的 `requested_managed_vector` 取得；validator 继续要求 `dependency/candidate_version` 命中该 vector。

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
      "proposal_ref": "...",
      "status": "REJECTED"
    }
  ]
}
```

Schema 1 `StaticRegionSlice` 中的 Cell、source snapshot、policy 和 candidate order 不再内联：

- Cell 来自所属 CellResult；
- source/policy 来自 generation；
- active dependency 与 candidate order 来自 CandidateSnapshot；
- other coordinates、baseline digest 和 fingerprint 仍由 Region 拥有。

`region_id` 对解析后的完整 Slice、fingerprint、observed versions 和 runtime refs 计算内容 digest。Static-only observation 使用 local `region_ref`；引用必须属于同一个 CellResult。

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

Schema 2 不把物理 `*_ref` 字符串替换成领域 identity 输入。校验 Cell、source snapshot、Attempt、CandidateSnapshot、FailureRecord、resolution graph、region 和 report generation ID 时，validator 必须：

1. 解析引用；
2. 展开顶层 generation facts；
3. 重建对应现行语义 record；
4. 使用该领域 identity 的版本和前缀计算 digest；
5. 与 wire ID 比较。

因此，规范化布局不会使“同一证据”得到另一个 Attempt/Failure ID。Proposal ID 按 §7.3 作为现行 opaque environment identity 保留；新增的 Cell、resolution graph 和 region ID 使用本文明确的新前缀。

### 10.4 引用图必须无环

Schema 2 的依赖方向固定为：

```text
generation
  -> declarations / cells
  -> candidate snapshots / resolution graphs
  -> attempts
  -> proposals
  -> static evaluations
  -> full evaluations / failures
  -> cell results
  -> projections / result
```

FailureScope 可以向 Attempt 或 Cell 回引，但 Attempt/Cell 不引用 Failure；Region 与 Observation 只在所属 CellResult 内形成从 summary 到既有证据的引用。不得增加可产生循环的通用 ref。

## 11. 验证顺序与保守失败

Report validator 按以下顺序执行：

1. 检查 64 MiB 读取上限、JSON 语法、顶层 object 和精确 `schema_version`；
2. 用 `extra="forbid"` 校验每个判别 record 的局部结构；
3. 建立所有实体的私有 typed index，拒绝重复 ID；
4. 解析全部引用，拒绝 unknown、wrong-kind 和 cross-cell refs；
5. 校验 Cell、source snapshot、CandidateSnapshot、resolution graph、Attempt、FailureRecord 与 report generation identity，并校验 Proposal ID 唯一性；
6. 校验 StaticEvaluation、FullEvaluation、witness、FailureRecord cause/stage/disposition；
7. 校验 Observation、static region、coordinate boundary 与 terminal outcome；
8. 校验 CellResult baseline/final authority 和 failure ref 精确集合；
9. 校验 target Cell coverage、projection 等价性和 complete/incomplete result；
10. 只有全部通过才返回可供命令消费的 `ValidatedReport`。

任何阶段都不得通过丢弃未知实体、选择第一份冲突记录、忽略未引用 FailureRecord 或把悬空引用降级为 warning 来恢复。

所有定义实体都必须可达：

- CandidateSnapshot 必须被某个 CellResult 引用；
- Attempt 必须被 baseline、Observation 或 FailureScope 引用；
- Proposal 必须属于被引用 Attempt，并被 Evaluation、Region 或 final/baseline 引用；
- Evaluation 与 FailureRecord 必须进入一个 CellResult 的闭环；
- resolution graph 必须被 Proposal 引用。

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
| declarations | `declaration_id` |
| cells | `cell_id` |
| candidate snapshots | `(cell_ref, dependency)` |
| resolution graphs | `resolution_graph_id` |
| attempts | `attempt_id` |
| proposals | `proposal_id` |
| static/full evaluations | `proposal_ref` |
| failures | `failure_id` |
| cell results | 现行 report cell order |
| projections | `declaration_ref` |

Pretty JSON 不是规范写入格式。人类展示继续由 `pf explain` / `pf diagnose` 拥有；文档示例可以 pretty-print，但不能作为 golden byte fixture。

## 13. Report module interface 与所有权

Schema 2 应加深 report module，而不是让调用方学习多个表的 join 规则。

外部 interface 保持以工作流为单位：

```text
ReportStore.read(path) -> ValidatedReport
ReportStore.write(path, report) -> None
ReportStore.merge(reports) -> ValidatedReport
ReportStore.update(existing, replacement) -> ValidatedReport
PackageReportBuilder.build(...) -> ValidatedReport
```

`ValidatedReport` 提供现有命令需要的 typed 查询，例如 package/generation identity、规范 CellResult、FailureRecord、projection 和 complete status。它不把可变 dict index 暴露给 `ProjectEditor`、TerminalPresenter 或 workflow。

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
- search update 若 generation 相同，可以读取 Schema 1、替换 Cell 并以 Schema 2 原子写回；
- merge 可以接受语义 generation 相同的 Schema 1/2 输入，输出 Schema 2；
- 不 dual-write，不在原文件旁生成隐式备份或 sidecar。

未知 Schema 继续失败。Schema 1 兼容期的删除需要单独产品决策，不在本文预设时间。

### 14.2 Schema 1 normalization

Legacy adapter 按以下规则收敛重复实体：

1. `target_cells` 建立 Cell table；所有嵌套 Cell 必须解析到完全相同的目标 Cell record；
2. 顶层 CandidateSnapshot 集合必须与所有 CellResult 内集合按 `(cell, dependency, digest)` 完全一致；
3. 同一 Attempt ID 的展开 identity 必须完全一致；
4. 同一 Proposal ID 的公开 payload 必须完全一致；legacy adapter 不声称重算缺失 preimage 的 environment identity；
5. 相同 resolved graph 以内容 digest 合并；
6. Probe 与 FailureRecord 的 scope/cause/disposition/process 必须通过现行交叉校验后改为 refs；
7. final vector、search vector 和 final Proposal vector 必须相等后只保留 final Proposal ref；
8. Projection Cell 必须解析到 target Cell；
9. 任何冲突都失败，不选择“顶层优先”或“嵌套优先”。

第 2 条比现行 `PackageFloorReportV1` 更严格，专门关闭顶层 CandidateSnapshot 与 CellResult 副本可以独立漂移的问题。一个现行 reader 能接受、但两套候选证据不一致的 Schema 1 报告，不具备无歧义迁移条件，应保守拒绝并要求重新 search。

### 14.3 Identity 保持

Normalization 对可重建的展开语义计算 ID，因此 Schema 1 中合法的 generation、CandidateSnapshot、Attempt 和 Failure ID 保持不变。Proposal ID 按 §7.3 原样保留并统一引用；Schema 2 新增的 Cell、resolution graph 和 region refs 不进入这些旧 identity 的 hash 输入。

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
- 不可达实体失败；
- 每个表的规范排序在 round trip 后稳定；
- optional 字段不输出 `null`；
- `observed_upper`、Observation vector、内联 Cell/Attempt/Proposal/Evaluation 不出现在 Schema 2；
- 顶层只有一份 CandidateSnapshot table；
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
- boundary predecessor 没有 direct Rejection；
- final Proposal 不是 PASS 或不属于 reported search；
- failure ref 未被 CellResult 精确拥有；
- projection floor 与 final vector 不一致；
- complete report 覆盖不全或 projection 不可表示。

### 17.3 迁移测试

- 真实 Schema 1 complete / incomplete fixture 能规范化为 Schema 2；
- Schema 1 重复实体在一致时只产生一个 Schema 2 定义；
- 顶层/嵌套 CandidateSnapshot 冲突保守失败；
- v1→v2 后 `explain` 可见结果、`diagnose` Failure 顺序和 apply patch 完全等价；
- v1/v2 merge 对相同 generation 得到同一规范结果；
- search update 原子地把旧报告升级为 Schema 2；
- Schema 2 round trip byte-for-byte 稳定。

### 17.4 体积与性能

使用 §1.2 的 PF 自搜索报告作为固定 qualification fixture，记录：

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

设计目标为该 fixture 不超过约 2.0 MiB，即相对 4,084,111-byte Schema 1 减少至少 50%。若原型无法达到目标，实施计划必须给出按实体分类的剩余体积与原因；不能通过放宽证据契约达标。

## 18. 对现行契约的取代

本文处于草案状态，不取代任何现行条款。批准并落地后：

- D001 §7 保留报告的产品作用、命令消费者、完整/不完整含义和保守失败，只把 wire layout、版本与规范编码指向 D014；
- D002 的 Schema / ReportStore 章节改为 ValidatedReport、Schema 1 legacy adapter 与 Schema 2 codec，不复制引用规则；
- D003–D005、D008、D011–D013 继续定义各自证据语义，只把持久化字段改为 D014 refs；
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

## 20. 决策记录

- `package-floor.json` 继续是版本化公共接口，不降级为实现缓存；
- 保留一个自包含 JSON 文件，先规范化再考虑压缩或 bundle；
- 以稳定 typed refs 替代高扇出按值内联；
- 每类实体有一个定义所有者，其他记录只能引用；
- ID 对展开后的领域事实计算，wire normalization 不改变既有证据 identity；
- 保留完整 source manifest，因为它体积很小且属于现行 generation identity；
- 保留 projection/result 等低成本产品摘要，并由底层证据复证；
- 删除 `observed_upper`、重复 final vector 和无语义 `null`；
- Schema 1 只在 persistence seam 兼容，生产 writer 只输出 Schema 2；
- D014 只拥有报告 wire interface，不接管搜索、失败、静态证据或 apply 的领域语义。

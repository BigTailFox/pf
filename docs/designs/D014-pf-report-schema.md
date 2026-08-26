# PF 报告 Schema 2

- **状态：** 现行
- **版本：** `schema_version = 2`
- **最后核对：** 2026-08-26
- **产品语义：** [D001](D001-pf.md)
- **领域模型：** [D002](D002-pf-implementation.md)–[D005](D005-pf-failure-and-diagnose.md)、[D008](D008-pf-verification-run.md)、[D012](D012-pf-harness-relaxation.md)、[D013](D013-pf-pytest-failure-evidence.md)
- **机器结构：** [package-floor-v2.schema.json](../schemas/package-floor-v2.schema.json)
- **最小示例：** [complete](../examples/package-floor-v2-minimal-complete.json)、[incomplete](../examples/package-floor-v2-minimal-incomplete.json)
- **实施记录：** [P013](../plans/P013-pf-report-schema.md)

本文是 `package-floor.json` wire interface、typed refs、规范编码和跨引用验证的唯一所有者。JSON Schema 是由同一 Pydantic wire model 生成的机器可读结构投影；搜索、failure、static/runtime evidence、harness 和 apply 的领域含义仍由上列文档拥有。

Schema 2 已一次性替换开发期内联 Schema 1。Reader 只接受版本 2，不提供旧 reader、migrator、alias、dual-read 或 dual-write。

## 1. 文档模型

报告是一个自包含、规范化的有向引用图：每类高扇出实体只定义一次，CellResult 与其他证据只保存 typed ref。顶层固定为：

```text
schema_version = 2
identity        generation identity 与 source/policy
inputs          declarations、target Cells、CandidateSnapshots
evidence        graphs、Attempts、Proposals、static/terminal Evaluations、Failures
cell_results    每个已观察 Cell 的唯一 root
projections     apply 所需的声明投影证据
result          complete | incomplete
```

字段、判别值、required/optional 形状以生成的 JSON Schema 为准。可选事实不存在时必须省略；显式 `null`、额外字段、Pydantic coercion 或由默认值补出的 wire facts 都无效。

### 1.1 Identity

`identity` 保存：

- `report_generation_id`；
- generator name/version/algorithm；
- canonical package name 与项目相对 `pyproject.toml` 路径；
- 完整 SourceSnapshot identity；
- evaluation policy identity。

Generation ID 的唯一算法是：

```text
sha256(
  "pf:report-generation:v2\0" + canonical_identity_json({
    generator,
    package,
    source_snapshot,
    policy_identity,
    requirement_declarations sorted by declaration_id,
    target_cells sorted by cell_identity,
  })
)
```

CandidateSnapshot、CellResult、Projection、Failure、Evaluation 和 wire refs 不进入 generation ID。它们仍必须属于该 generation，并由 reader 复证。

### 1.2 Inputs

`inputs` 是 generation 的声明与搜索输入：

- `requirement_declarations` 以 `declaration_id` 唯一、排序；
- `target_cells` 以内容寻址 `cell_id` 唯一、排序，并只引用本表声明；
- `candidate_snapshots` 以 `candidate_snapshot_id` 唯一、排序，每个 `(cell_ref, dependency)` 最多一条。

CandidateSnapshot 的 selection policy 与顶层 evaluation policy 是不同事实，因此 record 自带 `policy_identity`。Reader 以 record 自身 policy、完整 Cell、source、候选和 series representatives 重算现行 CandidateSnapshot digest。

### 1.3 Evidence

`evidence` 包含六张定义表：

| 表 | 稳定 owner/key | 主要依赖 |
| --- | --- | --- |
| `resolution_graphs` | `resolution_graph_id` | canonical nodes |
| `attempts` | `attempt_id` | Cell、source/policy、resolution context、harness facts、request |
| `proposals` | `proposal_id` | Attempt、managed vector、fixed declarations、graph、两个 plan digest、interpreter |
| `static_evaluations` | `proposal_ref` | Proposal、TyCheck、baseline digest、increment/fingerprint/classification |
| `evaluations` | `proposal_ref` | Proposal、static evaluation、witnesses、test/failure terminal |
| `failures` | `failure_id` | Cell/Attempt scope、disposition、cause、stage、portable facts、已取得 plan digests |

Proposal 只有在 prepare 成功并复证实际 graph 后才能存在；prepare failure 可以引用 Attempt，但不能虚构 Proposal。成功 Proposal 的 `project_plan_digest` 与 `environment_plan_digest` 必须非空，interpreter Python minor 必须匹配 Attempt Cell。

Static Evaluation 只能是 `STATIC_UNCHANGED | STATIC_REGRESSION`。Terminal Evaluation 只能是现行领域 union `PASS | TEST_FAIL | RUNTIME_INTERFACE_MISSING | INDETERMINATE`。D015 是草案，不改变这些 Schema 2 值。

### 1.4 Roots 与 projection

`cell_results` 的判别变体是：

```text
SUCCESS
CELL_INDETERMINATE
BASELINE_REJECTION
BASELINE_INDETERMINATE
SEARCH_FAILED
```

每个结果只通过 ref 连接 baseline、candidate snapshots、observations、regions、boundaries、FailureRecords 和 final Proposal。`SUCCESS.final_proposal_ref` 是 final vector 与 final PASS Evaluation 的唯一 authority；wire 不保存第二份 `final_vector` 或 `observed_upper`。

Direct observation 必须引用当前 Attempt，并闭合到同一 Proposal/Evaluation/Failure。Static-only observation 只能引用同 Cell、baseline、Slice 和 fingerprint 的 region 与 runtime representative；它不能成为 boundary 或 final authority。Boundary predecessor failure、failure disposition、region runtime reference、non-monotonic counterexample 和 coordinate outcome 必须与 D003–D005 的展开语义一致。

`projections` 只保存 declaration ref、Cell ref、exact floor、生成 requirements 与 `representable`。Reader 展开后复证 D001 的 Cell→floor 映射和 complete authority。`result.status = complete` 当且仅当全部 target Cells 有成功 root 且全部投影可表示；否则为 `incomplete` 并保存规范 reason 集合。

## 2. 引用规则

1. Ref 只在同一报告内有效；不得引用路径、外部文件、另一个 generation 或数组下标。
2. 每个定义 ID 唯一；同一 ref 可重复使用，但重复定义即使 payload 相同也无效。
3. ID 对展开后的领域事实计算，wire ref 字符串本身不替代 identity 输入。
4. RequirementDeclaration、Cell、CandidateSnapshot、Attempt、Proposal、Static/Terminal Evaluation 与 Failure 的 Cell scope 必须一致。ResolutionGraph 可以在 Cells 间按内容共享。
5. 图必须符合固定依赖方向，不允许任意 JSON Pointer、反向 owner 或循环引用。
6. 从 `cell_results` roots 出发不可达的 CandidateSnapshot、Attempt、Proposal、Evaluation、Failure 或 graph 是附加数据池，必须拒绝。Generation inputs 中声明和 target Cells 仍需完整保留。
7. `harness_declaration_ids` 是 Attempt identity 中的 opaque declaration IDs；公共报告没有 HarnessRequirement table，不能伪装成 typed refs。

## 3. Reader 验证

`ReportStore.read` 按以下顺序 fail closed：

1. `stat` 预拒绝超过 64 MiB 的输入，读取后再次检查以覆盖竞态；
2. 只按 UTF-8 解析 JSON，拒绝非法编码、语法、递归深度、非对象根和非版本 2；
3. 以严格 wire model 验证字段、类型、判别 union、无额外字段和无显式 null；
4. 要求输入与 `model_dump(exclude_none=True)` 完全一致，禁止 coercion/default 补事实；
5. 建立线性的 typed indexes，拒绝重复、未知或错误种类的 ref；
6. 复算 source、generation、Cell、CandidateSnapshot、ResolutionGraph、Attempt、Proposal、region 与 Failure identity；
7. 验证 cross-cell scope、Evaluation/Failure/Proposal 闭环、搜索边界、projection 与 result；
8. 从 roots 检查全图可达性和规范顺序；
9. 返回 immutable、resolved `ValidatedReport`，不向调用方泄漏 wire refs 或 join 规则。

任何失败都映射为不含输入正文、凭据、路径或不可信动态 ID 的 `ConfigurationError`。Reader 不访问网络、不读取当前项目来补事实，也不根据本地日志改变报告 authority。

公共 locator 必须是规范、可移植且无凭据的相对路径或安全 source/artifact locator；绝对路径、file URL、credential/query 泄漏、Windows drive 路径和越界 `..` 均无效。Process output、run ID、临时路径和本地日志 locator 不进入报告。

## 4. 规范编码与持久化

Writer 的唯一编码是：

```python
json.dumps(
    wire.model_dump(mode="json", exclude_none=True),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
) + "\n"
```

编码为 UTF-8，只有一个末尾换行。实体表按稳定 ID 排序；CellResult 按 Cell identity 排序；projection 按 declaration ID 排序；领域内有序序列保持其所有者定义的 canonical order。一次 read→write 必须 byte-stable。

写入在目标目录创建临时文件，flush + file `fsync` 后原子 replace，再同步父目录；失败不得留下半写目标。

## 5. Module interface

```text
PackageReportBuilder.build(package, source_snapshot, cell_results)
    -> ValidatedReport

ReportStore.read(path) -> ValidatedReport
ReportStore.write(path, report) -> None
ReportStore.merge(reports) -> ValidatedReport
ReportStore.update(existing, replacement) -> ValidatedReport
ReportStore.update_path(path, replacement) -> ReportUpdate
```

`PackageReportBuilder` 把领域 `CellResult` intern 为规范图并计算 projection/result。`ReportStore` 独占 wire codec、typed index、ref 展开、完整验证、merge/update 和原子事务。Workflow、editor、explain 与 diagnose 只消费 `ValidatedReport`；不得 import wire records、读取 `_wire` 或自行 join refs。

同 generation merge/update 先展开为最终 CellResult roots，再重新 intern 整图；因此旧的不可达 evidence 被清理，共享 graph 只保留一次。相同 Cell 的冲突结果失败。不同 generation 的 `update_path` 整体替换；空 replacement 不删除 existing Cells。坏 existing report 必须在任何覆盖前失败。

`ReportUpdate` 只向 diagnosis association seam 暴露 `replace_generation` 与已移除 Failure IDs；ReportStore 不依赖 RunLogStore。

## 6. 已发布工件与验证状态

- JSON Schema 和两个最小示例只能由 `scripts/generate_report_schema.py` 从唯一 wire model 生成；`--check` 必须无漂移。
- 示例必须同时通过 Draft 2020-12 JSON Schema 与 `ReportStore.read`。
- `tests/fixtures/report-schema/README.md` 指定的固定 Schema 1/Schema 2 self-search 对照文件当前均未交付，因此 `scripts/qualify_report_schema.py --check` 严格失败，对应 pytest 明确 skip。
- 缺失固定输入意味着 D014 的体积、实体计数、read/merge 性能资格化尚未执行；它不是 Schema 2 行为失败，也不是资格通过证据。
- 恢复精确 fixture 后，资格脚本必须验证同 generation 与同搜索语义、Schema 2 不超过 2,042,055 bytes、实体唯一计数和记录工件，再解除 skip。

当前 wire contract 不因该验证缺口回退：`schema_version = 2` 仍是唯一可读写布局。

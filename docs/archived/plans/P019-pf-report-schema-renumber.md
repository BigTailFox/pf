# P019 — D014 报告 Schema 1 重编号实施记录

- **状态：** 已归档（已完成）
- **开始日期：** 2026-08-28
- **完成日期：** 2026-08-28
- **性质：** 非规范性实施计划与过程记录
- **设计来源：** [D014](../../designs/D014-pf-report-schema.md)
- **用户约束：** 项目尚未发布，不保留旧版本兼容层

本文记录把当前已实现的 report wire contract 从 `schema_version = 2` 原地重编号为
`schema_version = 1` 的实施顺序和验证证据。数据图、typed refs、identity 输入、验证语义与
领域 owner 均保持不变；D014 仍是该 wire contract 的唯一规范所有者。

## 1. 目标与非目标

目标：

- writer 只生成 `schema_version = 1`，reader 只接受版本 1；
- report generation identity 前缀同步改为 `pf:report-generation:v1`；
- 私有 wire type、JSON Schema、最小示例、生成脚本和测试统一使用 `V1` / `v1`；
- 现行设计与文档索引只把规范化引用图称为 Schema 1，不留下可被误解为兼容承诺的旧布局；
- 不增加 migrator、alias、dual-read、dual-write 或 release migration gate。

非目标：

- 不改变引用图字段、required/optional 规则、typed refs、identity 输入、可达性或 merge/update
  语义；
- 不重编号独立领域/本地协议：`attempt-v2`、`verification-journal-v2`、
  `pf-process-log-v2` 和 D015 草案的 `pf:failure:v2`；
- 不实现 D015 的 authoritative verifier outcome；
- 不覆盖或迁移根目录未跟踪的开发产物 `package-floor.json`。

开发期旧内联布局不再占用 “Schema 1” 名称；历史文字如必须提到，只称为“开发期旧内联
布局”，不把它描述为可读、可迁移或待兼容的 wire version。

## 2. Owner 与变更切面

| 切面 | 唯一 owner / 入口 | 本轮可观察结果 |
| --- | --- | --- |
| wire model | `pf.schemas.report` | `PackageFloorReportV1Wire` 严格声明 `Literal[1]` |
| codec / validation | `PackageReportBuilder`、`ReportStore` | 生成 1、拒绝 2、错误文案称 Schema 1 |
| generation identity | `report_generation_id` | 规范前缀为 `pf:report-generation:v1` |
| published artifacts | `scripts/generate_report_schema.py` | 只生成 `package-floor-v1.schema.json` 与 v1 examples |
| contract tests | report/schema/artifact/editor/explain tests | 只消费 Schema 1 工件和版本值 |
| current docs | D001–D015、docs index、相邻计划 | 当前 wire contract 统一称 Schema 1 |

## 3. 垂直实施顺序

### 切片 001 — writer、reader 与 generation identity

1. 先把公开 report store/builder 测试改为期望版本 1、拒绝版本 2，并增加 generation ID 的
   精确 v1 前缀断言；运行聚焦测试取得 RED。
2. 最小修改 wire model、builder、reader 与 identity 前缀；把私有 `V2` wire type 重命名为
   `V1`，不保留 alias；运行相同测试取得 GREEN。
3. 运行 report schema tamper/merge/update 测试，确认重编号没有改变图验证语义。

### 切片 002 — 生成工件与消费者

1. 把 artifact contract 测试改为 v1 路径、`const: 1`、v1 model title；运行取得 RED。
2. 修改唯一生成器并将 committed schema/examples 改名后重新生成；删除旧 v2 工件。
3. 更新 editor/explain fixtures 和其他直接 JSON fixture；运行 artifact、editor、explain 聚焦集合
   取得 GREEN，并执行 generator `--check`。

### 切片 003 — 文档唯一叙述与回归验证

1. 更新 D014、docs index、现行所有者文档和 D015 草案中的 report schema 名称；把旧内联
   Schema 1 叙述改为未命名的开发期旧布局。
2. 更新 P013/P014/P017 的当前事实，但保留它们的历史实施证据和原始数值结论；明确 P019
   是后续纯重编号。
3. 用 `rg` 检查 production owner、现行设计与发布工件不存在 report `Schema 2`、
   `package-floor-v2`、report wire `V2`、`schema_version = 2` 或
   `report-generation:v2` 遗留；本计划的迁移说明和 reader negative fixtures 除外，独立 v2
   协议必须仍存在。
4. 运行格式、lint、类型检查、报告相关测试与显式全量测试；记录环境限制和未执行门禁，
   不把缺失 qualification fixture 伪装为通过。

## 4. 测试计划

- RED/GREEN 快环：`tests/test_report.py`、`tests/test_report_schema.py`；
- artifact 快环：`tests/test_report_artifacts.py` + `scripts/generate_report_schema.py --check`；
- 消费者回归：`tests/test_editor.py`、`tests/test_explain_terminal.py`、
  `tests/test_report_workflows.py`；
- 静态门禁：Ruff format/check、ty；
- 最终门禁：显式 `--no-testmon` 全量 pytest；资格 fixture 缺失时只接受既有明确 skip。

## 5. 行动与证据账本

### 2026-08-28 — 基线审计

- **行动：** 检查工作树、D014/P013、wire model、codec、生成器、工件和全仓版本引用。
- **目标：** 区分 report schema 版本与独立领域/本地协议版本，避免无关 identity 降级。
- **结论：** report schema 的版本 owner 集中在 `schemas.report`、`report` 与 artifact generator；
  `attempt-v2`、`verification-journal-v2` 等有独立 owner，本轮保持不变。工作树已有修改的 D015
  与未跟踪 `package-floor.json`，实施必须保留前者的现有内容且不触碰后者。
- **证据：** `git status --short`；针对 `Schema 2`、`package-floor-v2`、report wire `V2`、
  `schema_version` 与 `report-generation` 的全仓 `rg` 审计。

### 2026-08-28 — 切片 001 RED

- **行动：** 先把 `ReportStore` 与 `PackageReportBuilder` 的公开行为测试改为 writer 输出版本 1、
  reader 拒绝版本 2，并按 `pf:report-generation:v1` 独立计算预期 generation ID。
- **目标：** 证明测试直接命中版本门禁和 identity owner。
- **结论：** RED 有效；writer 仍输出 2，reader 仍把 2 送入旧 wire validator，builder 仍生成版本 2。
- **证据：** 聚焦命令得到 `3 failed, 4 passed`；三个失败分别位于 canonical write、版本 2
  reader case 和 minimal builder round trip。

### 2026-08-28 — 切片 001 GREEN

- **行动：** 将 report wire model 的全部私有 `V2` 类型原地重命名为 `V1`，把版本 literal、
  builder 输出、reader 门禁、sanitized validation 文案和 generation identity 前缀统一改为 1。
- **目标：** 只重编号 wire owner，不增加 alias，也不改变引用图结构。
- **结论：** 三个公开行为均已转为 Schema 1，完整 report/store/schema 回归未发现图语义变化；
  `attempt-v2` 保持原值。
- **证据：** RED 快环转为 `7 passed`；`tests/test_report.py tests/test_report_schema.py`
  得到 `147 passed`。

### 2026-08-28 — 切片 002 RED / GREEN

- **行动：** artifact 测试先改为 v1 路径，并固定 JSON Schema title 与
  `schema_version.const = 1`；随后迁移生成器、重新生成 v1 schema/examples、删除旧 v2
  工件，并更新 explain 消费路径。
- **目标：** 确保机器工件没有第二套手写版本 owner，所有消费者只读取首发 Schema 1。
- **结论：** RED 在 collection 时精确失败于生成器继续 import `PackageFloorReportV2Wire`；
  GREEN 后 v1 schema title/const、两个 examples、ReportStore loading 和 generator no-drift
  全部通过。apply-recovery journal 的独立 `schema_version = 2` 保持不变。
- **证据：** RED 为 `ImportError: cannot import name 'PackageFloorReportV2Wire'`；artifact
  集合 `4 passed`，generator `--check` 成功；artifact/editor/explain/workflow 集合
  `41 passed`。

### 2026-08-28 — 切片 003 文档同步

- **行动：** 更新 D014、docs index、D001–D013 的相邻 owner、D015 草案和历史计划；旧内联
  格式统一称为“开发期旧内联布局”，P013 明确最终发布名由 P019 重编号。
- **目标：** 让 D014 继续作为唯一 wire 规范，避免把未发布的旧布局描述成兼容版本。
- **结论：** 现行文档、类型名、artifact 路径和 validator 名称均使用 Schema 1；保留的 v2
  命中只属于 rejection fixture、apply-recovery、Attempt、Journal、Process Log 或 Failure
  identity 等独立协议，以及本计划对旧名称的审计说明。
- **证据：** report/schema/artifact/editor/explain/workflow 聚焦集合 `188 passed`；全仓版本
  owner `rg` 已逐项分类。

### 2026-08-28 — 最终验证与结论

- **行动：** 执行 generator no-drift、Ruff、ty、diff whitespace、版本 owner 扫描、显式
  `--no-testmon` 全量 pytest，并对唯一包源失败在允许依赖访问的环境复跑原节点。
- **目标：** 同时证明 report v2 已清除、独立 v2 协议未被误改，并从环境噪声中分离产品
  回归。
- **结论：** Schema 1 重编号完成；writer/reader、identity、private wire types、JSON Schema、
  examples、消费者和现行文档只有一个版本 owner。根目录未跟踪 `package-floor.json` 未修改。
  全量测试唯一受限失败发生在安装环境读取 package source，允许访问后通过。全仓 Ruff
  format 仍报告 51 个既有漂移文件；本轮不批量重排无关代码，且四个被本轮触及但格式检查
  失败的文件在 `HEAD` 原版上同样失败。
- **证据：**
  - 聚焦回归：`188 passed`；
  - 全量：`1304 passed, 1 failed`，失败为
    `TestInstalledCli.test_installed_module_cli_completes_report_lifecycle` 的 package-source
    访问；原节点允许依赖访问后 `1 passed`；
  - `ruff check src tests scripts`、`ty check src tests scripts`、generator `--check`、
    `git diff --check` 全部通过；
  - `ruff format --check src tests scripts`：51 个既有文件漂移；`editor.py`、
    `resolution.py`、`test_explain_terminal.py`、`test_report_schema.py` 的 `HEAD` 内容均同样
    返回 exit 1；
  - production owner/现行设计 v2 report 扫描无输出；`attempt-v2`、
    `verification-journal-v2`、`pf-process-log-v2`、`pf:failure:v2` 扫描仍有预期命中；
  - `git status --short` 仅显示本轮 tracked changes、新 v1 工件与既有未跟踪
    `package-floor.json`。

无实现偏差：没有增加兼容层，没有修改独立协议版本，也没有实现 D015 草案行为。

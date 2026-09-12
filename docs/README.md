# PF 工程文档索引

- **状态：** 现行
- **最后核对：** 2026-09-12

本页拥有文档分类、生命周期、权威归属与导航。工程实施门槛与 agent 执行原则由 [AGENTS.md](../AGENTS.md) 拥有。

## 1. 负载分层

| 层 | 文件 | 允许写入 |
| --- | --- | --- |
| 始终加载 | [AGENTS.md](../AGENTS.md) | 工程门槛、执行原则与触发指针；不复制契约 |
| 地图 | 本页 | 分类、生命周期、owner 表、开放项、归档入口 |
| 词汇 | [CONTEXT.md](../CONTEXT.md) | 现行术语与 `_Avoid_`；不写行为或验收 |
| 测试 | [tests/README.md](../tests/README.md) | 本仓库测试种类、消费者、断言与验证命令；由 AGENTS.md 触发 |
| 契约 | `docs/designs/` 现行 owner | 唯一行为规范 |
| 过程 | `docs/plans/` | 切片、决定、证据；不另立契约 |
| 证据 | Review / Concept / Experiment / Investigation / `docs/archived/` | 问题、设想、运行事实、历史 |
| 用户摘要 | [README.md](../README.md)、[README.zh.md](../README.zh.md) | 可执行示例并指向 owner |
| 生成投影 | `docs/schemas/`、`docs/examples/` | 由 D014 脚本生成，不手改 |

从 AGENTS.md 出发，定位行为 owner、给工程文档分类或归档，都经本页一跳到达唯一文件。
本仓库测试经 AGENTS.md 进入 [tests/README.md](../tests/README.md)。

## 2. 文档类型与写入门槛

| 类型 | 用途与权威 | 状态与最小记录 |
| --- | --- | --- |
| owner Design（D） | 唯一现行行为契约 | 现行；范围、owner/关联、规则、不变量、验证边界、最后核对日期 |
| 临时 Design（D） | 已接受时定义唯一目标契约；完成吸收前不冒充已交付行为 | 草案 / 已接受待实施 / 实施中；目标 owner、验收标准、接受状态 |
| Plan（P） | 实施步骤、决定与验收证据；不另立行为契约 | 进行中 / 已完成；按 AGENTS.md 维护 Design 验收映射 |
| Review（R） | 问题证据与开放项；不授权实施 | 开放 / 已解决或已移交；基准 commit、owner、影响、证据、状态与去向 |
| Concept（C） | 待证构想；不授权实施、不要求 Plan | 开放 / 转入 Design / 关闭；来源、证据缺口、进入 Design 的条件；研究目录集中导航，暂缓项可明确移交目录后关闭独立文档 |
| Experiment（E） / Investigation（I） | 固定环境下的事实、调查与结论；非规范性 | 进行中 / 已完成；日期、源码/工具/配置、命令、结果、局限与固定证据位置 |
| README / CONTEXT | 使用摘要 / 领域词汇 | 引用 owner，不增加行为或验收义务 |
| schemas / examples | wire model 的生成投影 | 由 D014 指定脚本生成，不手改、不形成平行契约 |

何时新建或只改 owner：

- 改产品承诺、命令、配置、退出码、失败资格、wire 或模块边界等契约：写或改 Design；接受与实施门槛遵循 [AGENTS.md](../AGENTS.md#engineering-workflow)。临时 Design 逐项标明替代哪个 owner 的哪条规则。
- 需要留存的问题评审与待办（实现偏移、性能或展示候选）：记 Review。Review 文档本身不授权生产代码；用户已授权的轻量修复按 AGENTS.md 执行，无须为进入实施补建 Review。
- 尚未证明该做的产品、功能、契约或性能收益方向：记 Concept。纯架构优化（模块划分、私有抽象、去重、可维护性）留在 Review，明确观察、影响与待证判断；不因方案尚未验证就转入 Concept。涉及契约变更时仍按 Design 门槛处理。
- 固定环境下的命令、结果与局限：记 Experiment 或 Investigation。结论可被吸收，原文不回写。
- 用词、链接、去重、frontmatter 与历史归档：直接改文档，不另建产品 Design/Plan。改变产品承诺仍走 AGENTS.md。
- 纯文档拆分可迁到新的长期 owner，必须在同一变更中搬走原规范、更新全部现行引用并留下迁移对照。规范性附录属于其主文 owner，不占独立 D 编号。

## 3. Frontmatter 与编号

状态行只写受控词；完成摘要、迁移说明与开放细节放正文。`最后核对` 为 `YYYY-MM-DD`。

| 文件 | 状态词 | 最少字段 |
| --- | --- | --- |
| 现行 owner Design、其附录、本索引、CONTEXT | 现行 | 状态、最后核对 |
| 临时 Design | 草案 / 已接受待实施 / 实施中 | 状态、目标 owner、验收标准 |
| Plan | 进行中 / 已完成 | 状态、对应 Design（指向现行或归档 Design 的有效 Markdown 链接） |
| Review | 开放 / 已解决或已移交 / 已归档 | 状态、日期、性质 |
| Concept | 开放 / 转入 Design / 关闭 | 状态、日期、性质 |
| Experiment / Investigation | 进行中 / 已完成 | 状态、日期、性质、证据位置 |

编号永久保留、不复用。D009–D011、D015–D036、D038–D044 与已完成 Plan 在归档；原 D031 拆至 C001 后不再回到 Design 号。现行 D012–D014、D037 的空号是归档结果，不是缺失。进行中 Plan 在 `docs/plans/`。

## 4. 单一权威与冲突分流

每条规则只在一个 owner 中完整定义。消费方保留接口关系与链接；用户摘要、示例和生成投影须明确来源，不能独立改变规范。表中的“D002 + D008”式关联表示各自拥有不同边界，不表示共同定义同一规则。

临时 Design 的目标被接受不等于实现已验证。接受、Plan、实施、验收与 owner 吸收的工程门槛直接遵循 AGENTS.md，本页不再复制一套流程。

文档与代码不一致时先查现行 owner、已接受变更和公开 seam：契约残留/过时则修订 owner；实现偏移则保持应有契约，按 AGENTS.md 的授权与影响分流修复，需留存的问题按 §2 记 Review。无法确定意图时记录未决项，不以代码自动胜出或把 bug 写成新规范。

现行 owner 正文只保留范围、规则、不变量、验证边界与公开 seam。迁移故事、验收计数与当时测试规模留在归档 Plan。不按行数拆文档；只有新的所有权边界才拆分。

## 5. 归档规则

Plan 完成、临时 Design 被 owner 吸收、Review 问题解决或明确移交、Investigation 结论被吸收后归档。
开放项不能只因日期旧而关闭；移交须指定接收文档与事项。已完成 Experiment 可继续保留在
`experiments/` 作为稳定证据库，不在其中跟踪当前整改；完成不等于现行产品资格。

归档文件是历史记录，不再有规范性。归档完成后的文件不修改结论、命令、计数或当时证据；新归档时只处理
状态、交接与路径重定位。用户明确授权时，可改写已归档入链以完成路径重定位，仍不改历史事实。
归档索引可更新导航。不再为旧地址保留现行目录中的跳转页。修复现行入链，原编号永久保留、不复用。

索引只保留现行 owner、开放事项和简短历史入口；迁移过程、验收计数与逐项完成摘要留在归档。

Review/Plan/Experiment/Investigation 的命令、计数和当时结论是历史证据；后续判断追加带日期的状态说明，
不回写旧运行。实验产物使用固定 commit、不可变路径或保存的 identity；可变根 `package-floor.json` 不作历史证据链接。
静态检查、fixture 回放、宿主测试与真实资格运行必须分别标注，不能相互替代。

长期维护的开放 Review 在 frontmatter 后以 issue 状态表开始正文，至少列出事项、状态、证据或去向。
事项状态区分「开放」「已解决」「过时」「已移交」：已解决须指向修复证据；过时指问题前提或对应机制
已不存在，须注明失效原因，不能把证据不足当作过时；已移交须指定接收文档和具体事项。
表反映最近一次核对，并注明日期与固定基准 commit。开放项正文区分事实、影响和待证方案；
已解决或过时项可保留在状态表及明确标注日期的历史部分，不继续当作现行待办。
新判断更新状态表并追加带日期的说明，不覆盖历史命令、计数和当时结论。

## 6. 契约所有权

| 唯一所有者 | 负责的现行契约 |
| --- | --- |
| [D001](designs/D001-pf.md) | 产品承诺、声明/Cell 准入、命令/通用配置、统一 artifact policy、apply 条件、数值退出码 |
| [D002](designs/D002-pf-implementation.md) | 模块 interface、依赖方向、composition、资源/持久化 owner 与测试 seam |
| [D003](designs/D003-pf-search-algorithm.md) | 单 Cell 两阶段搜索、direct fast path、独立 static/oracle 窗口、执行复用、边界与终止 |
| [D004](designs/D004-pf-ty-enhancement.md) | 规范静态投影、原始 TyCheck/Unavailable、Run 缓存、S_hi/S_slice 比较准入与 GuidancePolicy |
| [D005](designs/D005-pf-failure-and-diagnose.md) | Attempt/failure scope、disposition/cause、执行事实资格、FailureRecord identity、诊断 title/next step |
| [D006](designs/D006-pf-cli-enhancement.md) | 通道、live/final Cell、summary、explain/diagnose 展示；[附录](designs/appendices/D006-visual-specification.md) 固定 help 文案与视觉细则 |
| [D007](designs/D007-pf-process-output.md) | ProcessObservation、Process Log、Output Cache、完整性、脱敏与安全读取 |
| [D008](designs/D008-pf-verification-run.md) | Verification Run/Role/Attempt 序列、跨 Cell 调度、命令聚合、Journal/Diagnosis Index、impact |
| [D012](designs/D012-pf-harness-relaxation.md) | structured harness、relaxation、resolution/install、解释器/plan 时序与 uv 资格 |
| [D013](designs/D013-pf-pytest-observer.md) | direct pytest observer、progress/detail/cases telemetry 与透明性资格 |
| [D014](designs/D014-pf-report-schema.md) | Schema 1 wire、typed refs、identity/编码/reader、merge/update 与生成投影 |
| [D037](designs/D037-pf-candidate-search-policy.md) | 候选观测/准入、系列 DSL、anchor、条件默认、采样与 baseline artifact 选择域 |

[JSON Schema](schemas/package-floor-v1.schema.json)、[complete](examples/package-floor-v1-minimal-complete.json) /
[incomplete](examples/package-floor-v1-minimal-incomplete.json) 示例从 D014 的 wire model 生成。
[英文 README](../README.md)、[中文 README](../README.zh.md) 是使用入口，[CONTEXT](../CONTEXT.md) 固定领域词汇。

## 7. 开放事项

| 文档 | 当前跟踪范围 |
| --- | --- |
| [R006](reviews/R006-pf-cli-system-review.md) | issue 表跟踪通用错误展示、card lifecycle 架构候选及非 TTY 活动；历史修复证据保留 |
| [R012](reviews/R012-pf-qualification-todo.md) | ty × Python、真实 Host 资格与 E015 完整报告留存缺口 |
| [研究目录](concepts/README.md) | 开放 C004/C006–C010、暂缓问题、已交付基础与实验入口；Concept 状态和优先级只在该目录导航 |
| [E016](experiments/E016-pf-linear-search-control.md) | 真实固定 Slice 扫描与整轮线性对照协议；进行中，仅记录协议，尚未执行 |

<a id="uv-resolution-output-completeness"></a>

成功解析日志完整性开放项已移交[研究目录](concepts/README.md#deferred-resolution-output)，
[C003](archived/concepts/C003-pf-resolution-output-completeness.md) 保留历史。
原 D031 拆至 C001；C001–C003、C005 已归档，开放问题交接见研究目录，编号不复用。

## 8. 历史证据与归档入口

| 稳定记录 | 记录范围（均非规范性） |
| --- | --- |
| [E001](experiments/E001-pf-self-bootstrap-validation-contract.md) | PF 自举 full-repository validation contract 与固定报告检查点 |
| [E002](experiments/E002-pf-search-performance.md) | 2026-08-28 搜索性能计数，不能代表当前 HEAD 性能 |
| [E003](experiments/E003-requests-dependency-validation.md) | requests required-surface 修复前的 baseline 失败 |
| [E004](experiments/E004-requests-validation-surfaces.md) | required-surface、条件节点与搜索范围修复前后证据 |
| [E005](experiments/E005-pf-multi-resolution-search-simulation.md) | 纯算法模拟；支持 predecessor 重验，不证明真实 evaluator 耗时 |
| [E006](experiments/E006-requests-complete-search.md) | requests 两阶段完整 search 与 smoke/check 记录 |
| [E007](experiments/E007-mkdocs-baseline-and-build-failures.md) | MkDocs 基线/build 调查；后续 D036/P041 已完成，不表示重跑了 MkDocs search |
| [E008](experiments/E008-mkdocs-complete-search.md) | MkDocs 5-Cell smoke/check/search 完整记录、最终 PASS 与 witness 目标错配发现 |
| [E009](experiments/E009-mkdocs-static-guidance.md) | 静态 guidance 后 MkDocs check/search 资格；check 声明下界进入原 unittest，intern 后 search 经 64 MiB reader 复证 |
| [E010](experiments/E010-windows-native-search-cleanup.md) | 2026-09-08 非 WSL Windows 自搜索：killpg 崩溃复现与 Cell 结束环境清理墙钟 |
| [E011](experiments/E011-pf-optimistic-search-exploration.md) | 2026-09-11 乐观二分与有限探索的纯算法模拟：oracle 调用成本、发现率及下界退化 |
| [E012](experiments/E012-flask-complete-search.md) | 2026-09-11 Flask 5-Cell smoke/check/search/apply；第二轮 all×minor 与 patch refine |
| [E013](experiments/E013-requests-complete-search.md) | 2026-09-11 requests 10-Cell smoke/check/search/apply；all×minor 与 patch refine |
| [E014](experiments/E014-mkdocs-complete-search.md) | 2026-09-11 MkDocs 5-Cell smoke/check/search；all×minor 成功；patch refine 暴露坐标下降的顺序依赖缺口 |
| [E015](experiments/E015-pf-self-bootstrap-complete-search.md) | 2026-09-11 PF 自举 3-Cell smoke/check/search；all×minor 等于声明，patch refine 下降四条 |
| [E017](experiments/E017-xarray-core-complete-search.md) | 2026-09-12 xarray `v2026.07.0` core：本地测试补丁后 3.11 all×minor complete/apply；patch refine incomplete；计划见 [E017 计划](experiments/data/E017/pf-xarray-core-search-experiment-plan.md) |
| [E018](experiments/E018-markitdown-complete-search.md) | 2026-09-12 MarkItDown `v0.1.7` extra `all`：3.10 all×minor complete/apply；patch refine incomplete；apply 后 check 因 `lowest-direct` 经 numpy 2 把 pandas 抬到 2.2.2、与 openpyxl 3.0.10 冲突；计划见 [E018 计划](experiments/data/E018/pf-markitdown-search-experiment-plan.md) |

已归档 Design/Plan/Review/Investigation 见[归档索引](archived/README.md)。现行 Investigation 现无未归档条目。
补充归档入口：[R007 历史优先级评审](archived/reviews/R007-pf-current-improvement-priorities.md)
（后续开放事项现由 R006/R012/C010 跟踪）；
[R011 架构评审](archived/reviews/R011-pf-architecture-review.md)
（§3–§6 已吸收，其余后续交接至 R006/R012/C010）；
[R008 性能评审](archived/reviews/R008-pf-search-performance-review.md)
（未证收益与分阶段基线交 C010）；
[R010 文档审计](archived/reviews/R010-pf-engineering-document-audit.md)
（整改完成，资格与产物缺口交 R012）。

## 9. 文档变更验证

文档变更后在仓库根、沙箱外运行：

```sh
uv run python scripts/validate.py docs
```

统一入口及日志规则见 [tests/README.md](../tests/README.md#验证)。`check_docs.py` 核对应指针、现行 owner 与索引表一致、frontmatter、相对链接与章节锚点、双语 README 配置示例、以及既有归档记录未被删除。授权的路径重定位可以改写入链。`git diff --check` 含在其中，但排除 `docs/experiments/data/`：该目录是冻结运行证据，终端、差分与诊断原文保留当时空白。生成投影仍由 D014 脚本检查。

提交范围检查使用 `uv run python scripts/validate.py docs --base REF`；也可直接给 `check_docs.py`
传 `--base REF`。REF 必须能解析为 commit，在本地工作区/暂存区检查之外增加 REF 到 HEAD 的
已提交归档删除与 whitespace 检查；无效或不可用基准明确失败。调用方选择比较基准，脚本不自动
改用 merge-base。CI 获取完整历史，PR 使用目标分支的 base SHA，push 使用事件的 before SHA。

核对现行 owner 与代码/公开 tests 的具体 seam 仍按改动范围进行。只改文档不宣称交付了行为修复。
资格与证据留存开放项见 R012，不在索引复制测试计数。

# PF 工程文档索引

- **状态：** 现行
- **最后核对：** 2026-08-23

本页定义工程文档的职责、位置和状态词。每类规范性契约只有一个所有者；其他文档只说明上下文并引用所有者，不复制规则。

**现行**文档描述已落地规则，必须与代码一致。**已批准，待实现**是已确认但尚未落地的替换所有者：落地前，现行文档最多用一句话指向它们，不得把待实现规则写成当前命令、Schema 或模块接口。待实现文档的「对现行契约的取代」列出将被替换的现行条款；那些条款在落地前仍以现行文档为准。

## 布局

```text
README.md                 # 使用入口：能力摘要，链接到本页与 D001
CONTEXT.md                # 领域词汇；不定义产品或实现规则
docs/
├── README.md             # 本页：所有权、状态、导航
├── designs/              # 规范性契约与设计草案 D001–D012
├── plans/                # 非规范性实施记录 P001–P011
└── reviews/              # 非规范性评审快照 R001–
```

实施记录只放在 `docs/plans/`。评审快照只放在 `docs/reviews/`。根目录不另存一份 `plans/` 或 `reviews/`。

## 状态词

| 状态 | 含义 |
| --- | --- |
| 草案 | 尚在讨论的设计，不取代现行契约，也不能作为实现依据 |
| 现行 | 规范性契约，已作为实现依据落地 |
| 已批准，待实现 | 替换契约已经批准，实现尚未完成；落地前运行行为仍由现行文档定义 |
| 已完成 | 非规范性实施记录，只提供历史证据 |
| 快照 | 非规范性评审，对照当时代码与现行契约；不定义行为 |

## 规范性文档

| 契约 | 唯一所有者 | 内容 | 状态 | 实施记录 |
| --- | --- | --- | --- | --- |
| 产品与命令 | [D001](designs/D001-pf.md) | floor 含义、支持范围、命令与参数语义、配置、报告、apply、退出码 | 现行 | [P001](plans/P001-pf-v1.md)、[P003](plans/P003-pf-smoke-observability.md) |
| 实现结构 | [D002](designs/D002-pf-implementation.md) | 模块接口、依赖方向、Schema、adapter、持久化和测试边界 | 现行 | [P001](plans/P001-pf-v1.md)、[P009](plans/P009-pf-v1-architecture.md) |
| 搜索算法 | [D003](designs/D003-pf-search-algorithm.md) | 单 cell 坐标搜索、probe 顺序、不变量、终止与非单调处理 | 现行 | [P001](plans/P001-pf-v1.md) |
| `ty` 静态证据 | [D004](designs/D004-pf-ty-enhancement.md) | `S_hi`、诊断身份、多重集增量、`TyAdapter` 与 `StaticEvaluator` 的职责 | 现行 | [P002](plans/P002-pf-ty-enhancement.md) |
| failure 与 diagnose | [D005](designs/D005-pf-failure-and-diagnose.md) | Attempt、Rejection/Indeterminate、安装失败的搜索处置、FailureRecord、用户文案和 `pf diagnose` 行为 | 现行 | [P004](plans/P004-pf-failure-and-diagnose.md) |
| CLI 交互与展示 | [D006](designs/D006-pf-cli-enhancement.md) | help 信息架构、调用错误、结果摘要、终端层级和 `pf explain` 展示 | 现行 | [P007](plans/P007-pf-cli-enhancement.md) |
| 进程输出与日志 | [D007](designs/D007-pf-process-output.md) | 磁盘日志原文、每进程 16 MiB 输出缓存、`stdout_complete` / `stderr_complete` | 现行 | [P005](plans/P005-pf-process-output.md) |
| 验证运行语义 | [D008](designs/D008-pf-verification-run.md) | smoke/check/search 如何实例化 Attempt、统一错误链路、Verification Journal、`diagnose` 读取面 | 现行 | [P006](plans/P006-pf-verification-run.md) |
| 契约修复、模块加深与内部 seam | [D009](designs/D009-pf-v1-refactor.md) | 日志保密、证据/apply 授权、离线 discovery、验证编排、search 拆分、测试面与全量门禁 | 现行 | [P008](plans/P008-pf-v1-refactor.md) |
| 架构加深 | [D010](designs/D010-pf-v1-architecture.md) | 判别 resolution/event、Runner 内部调度、平台日志 seam、终端私有视图与完整 composition | 现行 | [P009](plans/P009-pf-v1-architecture.md) |
| runtime-backed 静态引导搜索 | [D011](designs/D011-pf-runtime-backed-static-search.md) | static fingerprint/region、runtime witness、动态边界与最终直接验证 | 现行 | [P010](plans/P010-pf-runtime-backed-static-search.md)（已完成） |
| harness resolution | [D012](designs/D012-pf-harness-relaxation.md) | baseline 保持原始 harness；probe/check 只删除显式 minimum，以两次 uv resolution、一次 installation 保持 project graph | 现行 | [P011](plans/P011-pf-harness-relaxation.md)（已完成） |

职责交叠时按“被描述的规则”选择所有者，而不是按调用链选择。例如：D003 消费 `ProbeRejection`，但 Rejection 与 Indeterminate 的分类只在 D005；D004 产生 `STATIC_REGRESSION` 所需事实，但不决定搜索处置；D006 组织 failure 文案但不复制 D005 的 title/impact/next step；D008 拥有各命令的 Attempt 序列、Evaluation → cause/stage、Journal 条目语义和 diagnose 工件来源，但不复制 D005 的 cause 矩阵；D007 拥有进程输出、日志保密与完整性标志，但不复制 D005 的 disposition；D002 列出模块位置但不复制 D004–D009 的业务或展示规则；D001 承诺坐标最小结果和命令退出码，但 probe 顺序只在 D003，终端信息层级只在 D006，进程输出语义只在 D007，验证运行条目语义只在 D008，现行契约修复、cell identity、验证运行编排、Journal package identity / 写入时机只在 D009。

## 非规范性文档

- 根目录 [README](../README.md) 是使用入口，只摘要能力并链接 D001 与本页。
- 根目录 [CONTEXT.md](../CONTEXT.md) 是领域词汇表。它固定术语与避免用法，不定义命令、算法或模块接口。不收录字节上限、文件格式或命令规则。
- [P001](plans/P001-pf-v1.md)、[P002](plans/P002-pf-ty-enhancement.md)、[P003](plans/P003-pf-smoke-observability.md)、[P004](plans/P004-pf-failure-and-diagnose.md)、[P005](plans/P005-pf-process-output.md)、[P006](plans/P006-pf-verification-run.md)、[P007](plans/P007-pf-cli-enhancement.md)、[P008](plans/P008-pf-v1-refactor.md)、[P009](plans/P009-pf-v1-architecture.md)、[P010](plans/P010-pf-runtime-backed-static-search.md) 和 [P011](plans/P011-pf-harness-relaxation.md) 是已经完成的实施与验证记录。它们不定义现行行为；其中 P003 的运行时-only failure 诊断结构已经由 D005 取代，D005 的实施证据见 P004。D007/D008/D006/D009/D010/D011/D012 的实施证据分别见 P005、P006、P007、P008、P009、P010、P011。
- [R001](reviews/R001-pf-v1-review.md) 是 2026-08-22 对照 D009 落地前 `main` 的 v1 仓库评审。它提供契约缺口与首轮重构意见，不定义现行行为；对应整改契约见 [D009](designs/D009-pf-v1-refactor.md)。实施前终审补充了脱敏观察面：流式缺口在日志/终端，不在 `package-floor.json` 正文。
- [R002](reviews/R002-pf-v1-architecture-review.md) 是 2026-08-22 对照 D009 落地后 `af10d0c` 的架构评审。它记录剩余 module/interface/seam 优化机会，不定义现行行为；对应整改契约与实施记录是 [D010](designs/D010-pf-v1-architecture.md) / [P009](plans/P009-pf-v1-architecture.md)。

若实施记录、README 或代码注释与规范性文档冲突，以对应契约所有者为准，并在同一变更中修复实现或所有者文档。改变契约时只修改所有者；其他文档最多更新链接、状态或非规范性证据。

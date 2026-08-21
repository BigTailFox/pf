# PF 工程文档索引

- **状态：** 现行
- **最后核对：** 2026-08-21

本页定义工程文档的职责、位置和状态词。每类规范性契约只有一个所有者；其他文档只说明上下文并引用所有者，不复制规则。

## 布局

```text
README.md                 # 使用入口：能力摘要，链接到本页与 D001
CONTEXT.md                # 领域词汇；不定义产品或实现规则
docs/
├── README.md             # 本页：所有权、状态、导航
├── designs/              # 规范性契约 D001–D006
└── plans/                # 非规范性实施记录 P001–P004
```

实施记录只放在 `docs/plans/`。根目录不另存一份 `plans/`。

## 状态词

| 状态 | 含义 |
| --- | --- |
| 现行 | 规范性契约，已作为实现依据落地 |
| 现行契约，待实现 | 规范性契约已确认，实现尚未完成 |
| 已完成 | 非规范性实施记录，只提供历史证据 |

## 规范性文档

| 契约 | 唯一所有者 | 内容 | 状态 | 实施记录 |
| --- | --- | --- | --- | --- |
| 产品与命令 | [D001](designs/D001-pf.md) | floor 含义、支持范围、命令与参数语义、配置、报告、apply、退出码 | 现行 | [P001](plans/P001-pf-v1.md)、[P003](plans/P003-pf-smoke-observability.md) |
| 实现结构 | [D002](designs/D002-pf-implementation.md) | 模块接口、依赖方向、Schema、adapter、持久化和测试边界 | 现行 | [P001](plans/P001-pf-v1.md) |
| 搜索算法 | [D003](designs/D003-pf-search-algorithm.md) | 单 cell 坐标搜索、probe 顺序、不变量、终止与非单调处理 | 现行 | [P001](plans/P001-pf-v1.md) |
| `ty` 静态证据 | [D004](designs/D004-pf-ty-enhancement.md) | `S_hi`、诊断身份、多重集增量、`TyAdapter` 与 `StaticEvaluator` 的职责 | 现行 | [P002](plans/P002-pf-ty-enhancement.md) |
| failure 与 diagnose | [D005](designs/D005-pf-failure-and-diagnose.md) | Attempt、Rejection/Indeterminate、安装失败的搜索处置、FailureRecord、本地日志关联和 `pf diagnose` | 现行 | [P004](plans/P004-pf-failure-and-diagnose.md) |
| CLI 交互与展示 | [D006](designs/D006-pf-cli-enhancement.md) | help 信息架构、调用错误、结果摘要、终端层级和 `pf explain` 展示 | 现行契约，待实现 | — |

职责交叠时按“被描述的规则”选择所有者，而不是按调用链选择。例如：D003 消费 `ProbeRejection`，但 Rejection 与 Indeterminate 的分类只在 D005；D004 产生 `STATIC_REGRESSION` 所需事实，但不决定搜索处置；D006 组织 failure 文案但不复制 D005 的 title/impact/next step；D002 可以列出模块位置但不复制 D004–D006 的业务或展示规则；D001 可以承诺坐标最小结果和命令退出码，但 probe 顺序只在 D003，终端信息层级只在 D006。

## 非规范性文档

- 根目录 [README](../README.md) 是使用入口，只摘要能力并链接 D001 与本页。
- 根目录 [CONTEXT.md](../CONTEXT.md) 是领域词汇表。它固定术语与避免用法，不定义命令、算法或模块接口。
- [P001](plans/P001-pf-v1.md)、[P002](plans/P002-pf-ty-enhancement.md)、[P003](plans/P003-pf-smoke-observability.md) 和 [P004](plans/P004-pf-failure-and-diagnose.md) 是已经完成的实施与验证记录。它们提供历史证据，不定义现行行为；其中 P003 的运行时-only failure 诊断结构已经由 D005 取代，D005 的实施证据见 P004。

若实施记录、README 或代码注释与规范性文档冲突，以对应契约所有者为准，并在同一变更中修复实现或所有者文档。改变契约时只修改所有者；其他文档最多更新链接、状态或非规范性证据。

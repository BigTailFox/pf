# PF 工程文档索引

- **状态：** 现行
- **最后核对：** 2026-08-20

本页定义工程文档的职责。每类规范性契约只有一个所有者；其他文档只说明上下文并引用所有者，不复制规则。

## 规范性文档

| 契约 | 唯一所有者 | 内容 |
| --- | --- | --- |
| 产品与命令 | [D001](designs/D001-pf.md) | floor 含义、支持范围、CLI、配置、报告、apply、退出码 |
| 实现结构 | [D002](designs/D002-pf-implementation.md) | 模块接口、依赖方向、Schema、adapter、持久化和测试边界 |
| 搜索算法 | [D003](designs/D003-pf-search-algorithm.md) | 单 cell 坐标搜索、probe 顺序、不变量、终止与非单调处理 |
| `ty` 静态证据 | [D004](designs/D004-pf-ty-enhancement.md) | `S_hi`、诊断身份、多重集增量、`TyAdapter` 与 `StaticEvaluator` 的职责 |
| failure 与 diagnose | [D005](designs/D005-pf-failure-and-diagnose.md) | Attempt、Rejection/Indeterminate、安装失败的搜索处置、FailureRecord、本地日志关联和 `pf diagnose` |

职责交叠时按“被描述的规则”选择所有者，而不是按调用链选择。例如：D003 消费 `ProbeRejection`，但 Rejection 与 Indeterminate 的分类只在 D005；D004 产生 `STATIC_REGRESSION` 所需事实，但不决定搜索处置；D002 可以列出 `StaticEvaluator` 和 failure policy module，但不复制 D004/D005 的分类规则；D001 可以承诺坐标最小结果，但 probe 顺序只在 D003。

## 非规范性文档

- 根目录 [README](../README.md) 是使用入口，只摘要能力并链接 D001。
- [P001](../plans/P001-pf-v1.md)、[P002](../plans/P002-pf-ty-enhancement.md) 和 [P003](../plans/P003-pf-smoke-observability.md) 是已经完成的实施与验证记录。它们提供历史证据，不定义现行行为；其中 P003 的运行时-only failure 诊断结构已经由 D005 取代。

若实施记录、README 或代码注释与规范性文档冲突，以对应契约所有者为准，并在同一变更中修复实现或所有者文档。改变契约时只修改所有者；其他文档最多更新链接、状态或非规范性证据。

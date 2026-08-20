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

职责交叠时按“被描述的规则”选择所有者，而不是按调用链选择。例如：D003 可以使用 `STATIC_FAIL`，但其定义只在 D004；D002 可以列出 `StaticEvaluator`，但不重复诊断比较算法；D001 可以承诺坐标最小结果，但 probe 顺序只在 D003。

## 非规范性文档

- 根目录 [README](../README.md) 是使用入口，只摘要能力并链接 D001。
- [P001](../plans/P001-pf-v1.md) 和 [P002](../plans/P002-pf-ty-enhancement.md) 是已经完成的实施与验证记录。它们提供历史证据，不定义现行行为。

若实施记录、README 或代码注释与规范性文档冲突，以对应契约所有者为准，并在同一变更中修复实现或所有者文档。改变契约时只修改所有者；其他文档最多更新链接、状态或非规范性证据。

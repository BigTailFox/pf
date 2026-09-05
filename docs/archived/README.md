# PF 工程文档归档

- **状态：** 历史索引
- **最后整理：** 2026-09-06

归档只保存决策来源、实施过程、评审证据和实验记录，不解释当前行为。现行契约与所有权见 [工程文档索引](../README.md)。

| 目录 | 内容 | 归档原因 |
| --- | --- | --- |
| [designs](designs/) | D009–D011、D015–D030、D032–D035 | 已实施并由现行 Design 接管 |
| [plans](plans/) | P001–P040 | 实施与验证已完成 |
| [reviews](reviews/) | R001–R003、R005、R009 | 发现已由后续设计和实现解决，或已移交现行 Review |
| [investigations](investigations/) | I001 | 结论已纳入 D013 |

归档文档中的版本、命令、路径、测试计数和结论保持历史原貌；如需理解当前行为，必须回到现行所有者。

[D035](designs/D035-pf-optional-test-group.md) /
[P040](plans/P040-pf-optional-test-group.md)：可选 test-group、按 Cell active harness 分支准备、
project-only 安装、nullable environment evidence 与 policy 隔离已完成；
稳定规则由 D001/D002/D005/D006/D012/D014 接管，三版本全套各 1971 passed。

[D034](designs/D034-pf-dependency-marker-projection.md) /
[P039](plans/P039-pf-dependency-marker-projection.md)：portable 五字段 marker、独立资格与 contextual
求值、report/apply/terminal/native facts 迁移及 policy 隔离已完成；稳定规则由 D001/D002/D012/D014 接管。

[D033](designs/D033-pf-predecessor-revalidate.md) /
[P038](plans/P038-pf-predecessor-revalidate.md)：resolution 命名、predecessor 重验、evaluator 统一缓存、
highest baseline PASS 与窄搜索空间 baseline artifact 选择已完成；稳定规则由 D001/D002/D003/D006/D014 接管。

[D032](designs/D032-pf-runtime-witness-stderr.md) /
[P037](plans/P037-pf-adapter-evidence-admission.md)：adapter 证据准入与诊断边界迁移已完成，
稳定规则由 D003/D004/D013/D014 接管；§9 的 uv 日志完整性候选继续由
[现行 README 独立开放项](../README.md#uv-resolution-output-completeness) 跟踪。

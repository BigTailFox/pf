# PF 工程文档归档

- **状态：** 历史索引
- **最后整理：** 2026-09-09

归档只保存决策来源、实施过程、评审证据和实验记录，不解释当前行为。现行契约与所有权见 [工程文档索引](../README.md)。
编号永久保留、不复用；现行 owner 出现 D012–D014、D037 等空号是归档结果，不是缺失。原 D031 已拆至现行 [C001](../concepts/C001-pf-multi-resolution-coordinate-search.md)。

| 目录 | 内容 | 归档原因 |
| --- | --- | --- |
| [designs](designs/) | D009–D011、D015–D030、D032–D036、D038、D040、D041 | 已实施并由现行 Design 接管 |
| [plans](plans/) | P001–P044 | 实施与验证已完成 |
| [reviews](reviews/) | R001–R003、R005、R007、R009 | 发现已由后续设计和实现解决，或已移交现行 Review |
| [investigations](investigations/) | I001 | 结论已纳入 D013 |

归档文档中的版本、命令、路径、测试计数和结论保持历史原貌；如需理解当前行为，必须回到现行所有者。

[D041](designs/D041-pf-repository-test-conformance.md) /
[P044](plans/P044-pf-repository-test-conformance.md)：仓库测试符合性已完成；资格不再藏产品慢测，
e2e 为真实产品 CLI，公开缝与断言已收口。稳定规则由 D002 §11 与 `tests/README.md` 接管。

[D040](designs/D040-pf-test-lanes.md) /
[P043](plans/P043-pf-test-lanes.md)：仓库测试车道与自举 targeted-runtime `C` 已完成；
日常 / `pf search` / CI 分范围收集，机械漏标检查随日常与 PR pytest 运行。
稳定规则由 D002 §11、`tests/README.md`、`pyproject.toml` 与 CI 接管。

[D038](designs/D038-pf-static-guidance-authority.md) /
[P042](plans/P042-pf-static-guidance-authority.md)：静态事实可改探测顺序但不能排除候选或更新边界；
无 witness、两阶段搜索、Run 内 TyCheck cache、Journal v3 intern 与 64 MiB 内 MkDocs search 已完成；
稳定规则由 D001–D008/D012/D014/D037 接管，三版本各 2601 tests 通过，coverage 90.01%。

[D036](designs/D036-pf-execution-failure-contract.md) /
[P041](plans/P041-pf-execution-failure-contract.md)：统一 execution/operation-structured authority、
合格 UNSAT 与普通非零兜底、FailureRecord v3、report/policy/apply 隔离及三命令诊断已完成；
稳定规则由 D001–D008/D012–D014 接管，真实 sdist resolve/install 搜索通过，三版本各 2244 tests 通过。

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

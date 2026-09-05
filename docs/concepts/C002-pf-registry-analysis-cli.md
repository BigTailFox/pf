# C002 — PF Registry 发布分布分析 CLI

- **状态：** 开放构想；命令、数据和建议输出契约待探索
- **日期：** 2026-09-05
- **性质：** 非规范性 Concept，不授权生产实施
- **来源：** [D030 §12](../archived/designs/D030-pf-search-space-dsl.md#12-待办独立-registry-分析-cli-design)
  的独立待办；从工程索引抽出集中跟踪，D030/P036 已完成
- **相关 owner：** [D001](../designs/D001-pf.md)、[D002](../designs/D002-pf-implementation.md)、
  [D003](../designs/D003-pf-search-algorithm.md)、[D006](../designs/D006-pf-cli-enhancement.md)
- **相关构想与草案：** [C001](C001-pf-multi-resolution-coordinate-search.md)、
  [D033](../archived/designs/D033-pf-predecessor-revalidate.md)

## 1. 构想

增加独立的 registry 发布分布分析 CLI，帮助用户理解包的版本系列、稀疏程度和各系列 release 数，
比较不同搜索空间与系列代表粒度的覆盖范围、候选数量，给出带依据的配置建议。
命令名称、参数和输出形式尚未确定。

现行 search 为 DSL 位置切片采集系列事实；这个构想是在这些事实之上提供独立的解释与比较用途。
文中“粒度”对应现行 `search-resolution`，后续 Design 应采用届时
已接受的唯一配置契约。该命令的价值不依赖 C001 的树搜索是否实现。

## 2. 范围与证据边界

- 发布分布能证明版本形态和代表数量，不能证明兼容性边界；候选数也不是 verifier 次数或耗时。
- 建议辅助用户做决定，不自动改写 pyproject、调整搜索空间或代表粒度；普通 search 不隐式执行
  发布习惯推断。
- 过滤前的 release/series inventory 与经过 Cell、artifact、声明、baseline 和空间筛选的候选
  回答不同问题，输出需要明确口径，不能混称“可用版本”。
- 分析所用 registry 观测及其时间、source 和配置上下文需要能追溯；如何保存尚待决定。

## 3. 待验证问题

1. 用户最需要比较哪些配置，怎样的展示能帮助选择，而不诱导把“候选少”当成“搜索更好”？
2. 分析面向单个包还是项目/Cell；是否需要真实 baseline，哪些结论可以只靠发布观测得到？
3. 如何共享现有系列分组、DSL 求值和资格规则，避免新命令维护第二套筛选逻辑？
4. 输出怎样表达原始发布、空间覆盖与合格代表之间的差异；离线复查需要保留哪些最小证据？

## 4. 进入 Design 的条件

用少量真实发布观测展示有代表性的比较结果，确认独立命令能支持明确的配置决策，再确定
命令、数据来源、过滤口径、错误行为、输出与保存契约。随后另建 Design，接受后建立 Plan。
探索过程和实验事实留在独立记录中，不把本构想写成已实现功能或 D030 的未完成验收项。

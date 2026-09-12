# C010 — PF 搜索效率优化

- **状态：** 开放
- **日期：** 2026-09-12
- **性质：** 非规范性性能研究；不选择实现架构、不授权实施
- **来源：** [R008](../archived/reviews/R008-pf-search-performance-review.md) §5、§9 的未证实收益候选与分阶段基线缺口
- **核对基准：** `05dbf604812e37bafdf96bd6247a1665bc3e83c7`；源码静态核对，未执行新性能实验
- **现行 owner：** [D002](../designs/D002-pf-implementation.md)、[D003](../designs/D003-pf-search-algorithm.md)、[D008](../designs/D008-pf-verification-run.md)、[D012](../designs/D012-pf-harness-relaxation.md)、[D013](../designs/D013-pf-pytest-observer.md)

## 1. 独立问题与边界

在固定搜索空间、Cell、源码、验证命令和证据资格下，哪些工作实际主导搜索耗时，哪些候选能减少
verifier 次数或墙钟关键路径？本文集中跟踪收益假设，不把代码结构现象直接当作性能瓶颈。
纯架构优化仍记 Review；single-flight、CoW、overlay 等只是原 R008 的历史建议，不是本文选择的目标。

本文不接管 [C004](C004-pf-evidence-respecting-optimistic-monotone-search.md) 的单坐标搜索语义、
[C009](C009-pf-contextual-interaction-discovery.md) 的跨坐标发现、
[C008](C008-pf-cross-run-evidence-store.md) 的历史观察准入或
[C006](C006-pf-test-dependency-association.md) 的测试关联。
历史 floor 作为选点提示与历史证据获准复用是不同问题；这里仅研究前者。
更广泛工作调度仍由[研究目录](README.md#deferred-work-scheduling)跟踪，本文提供其可能需要的成本数据。

## 2. 接收的收益假设

| 项 | 已观察事实 | 待证问题与下一步 |
| --- | --- | --- |
| 旧 floor 选点提示 | `CoordinateSearch.minimize` 接受 hints；[SearchCoordinator](../../src/pf/search.py) 产品调用不提供 hints | 在同一固定 trace 上比较无提示与旧 floor 提示，记录 verifier 次数、最终结果和退化场景；当前 invocation 重新取得权威证据 |
| 不同 key 的 I/O 重叠 | [CandidateBuilder](../../src/pf/candidates.py)、[uv adapter](../../src/pf/adapters/uv.py)、[EnvironmentFactory](../../src/pf/environment.py) 各有锁包住 query/resolve 执行 | 先测锁等待与关键路径占比，再判断重叠是否值得；不以锁存在证明瓶颈，不预选并发抽象 |
| Proposal 源码物化成本 | [SourceSnapshot.materialize](../../src/pf/snapshot.py) 使用 copytree | 测量文件数、逻辑字节、materialize 耗时与整轮占比；只有占比显著才研究降低成本，保持可写环境隔离 |
| xdist failed-set 收益 | [ConfiguredVerifier](../../src/pf/adapters/test_command.py) 要求合格的 controller/serial collection；不足时回退原命令 | 在真实 xdist 套件测回退频率、failed-set 命中与墙钟收益；新的 collection 资格尚未定义，不以 worker 列表并集授权 Rejection |
| 当前分阶段基线 | R008 未保存同一当前实现下完整的 prepare/ty/verifier/锁等待分解 | 固定输入与缓存条件，补成本与关键路径归因后才排候选优先级 |

上述事项均待证，不沿用 R008 的 P1/P2 收益排序。没有使用 hints、保守回退或使用 copytree
本身不是产品缺陷。若调查只留下模块去重或私有接口改进，应回到 Review 跟踪架构问题。

## 3. 证据与失效边界

[E002](../experiments/E002-pf-search-performance.md)、[E006](../experiments/E006-requests-complete-search.md)
的计数属于旧算法；region/witness 权限已删除。[E009](../experiments/E009-mkdocs-static-guidance.md)
没有完整阶段分解。[I002](../archived/investigations/I002-pf-self-search-py310-static-collection.md)
记录的整树散列与公开静态 intern 已由 D043/P045 替换，不能用其 42 MB / 37s 描述当前 reader。

[E012](../experiments/E012-flask-complete-search.md)、[E013](../experiments/E013-requests-complete-search.md)、
[E014](../experiments/E014-mkdocs-complete-search.md)、[E015](../experiments/E015-pf-self-bootstrap-complete-search.md)
是更新的真实运行入口；它们仍不是优化前后的受控对照，不能跨仓库、配置或版本计算加速倍数。
当前不认定 verifier、静态工作或锁中的任何一项为一般主导瓶颈。

## 4. 进入实施前需要的证据

1. 固定源码 commit/dirty identity、候选 cutoff 与清单、Cell、完整 argv、工具版本、资源限制、缓存条件及报告 identity；运行产物保存到不可变位置。
2. 逐 Cell 记录候选/sweep/唯一向量、prepare/静态/oracle 次数与耗时，分出 materialize、resolve、锁等待和整轮关键路径；区分累计耗时与墙钟。
3. 每次只改变一个候选因素，比较结果语义、权威证据、调用成本与墙钟，记录重复运行的波动和负收益；不通过缩小空间或验证命令制造收益。
4. 无稳定净收益则暂缓或关闭对应候选；有收益后按影响进入现行 owner 的局部实现或 Design，不由本文指定模块拆分与接口。

[E016](../experiments/E016-pf-linear-search-control.md) 负责固定 Slice 与整轮算法对照；尚未执行，
也不自动覆盖本文的完整阶段测量。启动本研究时再建立固定实验记录，不把协议当作运行结果。

# R004 — PF 自搜索空间裁剪与运行成本评审

- **状态：** 开放（prepared promotion 复用已由 D022/P028 解决；其余性能候选未实施；CLI 非 TTY 搜索遥测已移交 R006）
- **日期：** 2026-08-28
- **性质：** 非规范性性能评审；不定义命令、算法、Schema 或 module interface
- **对照：** configured-verifier authority 落地工作树；运行 ID `20260828T063807.140981Z-999683-d803a27c`
- **契约所有者：** [D001](../designs/D001-pf.md)、[D003](../designs/D003-pf-search-algorithm.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)、[D012](../designs/D012-pf-harness-relaxation.md)
- **历史决策：** [D011](../archived/designs/D011-pf-runtime-backed-static-search.md)、[D015](../archived/designs/D015-pf-authoritative-verification-outcome.md)
- **CLI 项迁移：** 原 §5(3) 非交互搜索遥测由 [R006](R006-pf-cli-system-review.md) §5.2 完整接管，本文不再跟踪其 Design、Plan 或完成状态
- **已解决项：** [D022](../archived/designs/D022-pf-evaluation-seam.md) / [P028](../archived/plans/P028-pf-evaluation-seam.md) 已完成原 §5(1) static-to-runtime promotion 的同 Proposal prepared lifecycle 复用

本文回答一次 PF 自搜索为何持续约 37 分钟、是否进入死循环，以及现行裁剪是否有效。本文只记录性能与可观测性证据，不修改搜索算法、runtime authority 或环境生命周期。

## 1. 结论

搜索没有进入死循环。3.11 与 3.12 cell 均自然完成，3.10 cell 因用户级 uv 配置引入的外部 NVIDIA index 超时而形成 `INDETERMINATE`。`CoordinateSearch` 的有限候选、坐标单调下降、内层迭代上限和 invocation-local evidence cache 共同保证本次运行不会无限访问同一状态。

空间裁剪分成两个结论：

1. **组合空间裁剪有效。** 3.11/3.12 各只访问 54 个唯一向量；仅把本次已观察到的各坐标版本做笛卡尔积，就分别有 114,048 个组合。现实现没有枚举全组合。
2. **昂贵 runtime 裁剪有限。** 本次仍运行 106 次配置 verifier，累计 3,470.40 秒，中位 36.22 秒，P90 39.34 秒。静态路径只让 18 个唯一向量免于运行 pytest；另有 19 个向量先做 static-only probe，随后为 runtime promotion 重新准备同一环境。

因此，长耗时是有限但昂贵的搜索，不是失控循环。最大成本来自重复执行用户配置的完整 verifier，其次是 static-to-runtime promotion 的环境重建和一次 126 秒的外部 index 重试。

## 2. 运行证据

运行从 2026-08-28 14:38:07 持续到 15:15:24（Asia/Shanghai），共写入 1,093 个 Process Log。

| Cell | environment prepare | 唯一向量 | 重复 prepare | pytest | 无 pytest 的 prepare | 已观察坐标笛卡尔积 | 终态 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Python 3.10 | 19 | 16 | 3 | 14 | 5 | 128 | `INDETERMINATE`；source timeout |
| Python 3.11 | 62 | 54 | 8 | 46 | 16 | 114,048 | success；`tomlkit=0.5.11` |
| Python 3.12 | 62 | 54 | 8 | 46 | 16 | 114,048 | success；`tomlkit=0.5.11` |

配置 verifier 的 106 次终态分布为：exit 0 共 28 次、exit 1 共 68 次、exit 2 共 5 次、exit 4 共 5 次。按 D005，normal nonzero 都是权威 Rejection；退出码分布只解释运行成本，不改变 disposition。

37 个无 pytest 的 prepare 中，19 个属于同一向量的 static-only probe，随后各有一次不再运行 ty、但运行 pytest 的 promotion prepare。剩余 18 个唯一向量才是本次 static region guidance 真正免掉的完整 verifier。

3.10 的最后一次解析在 Process Log 423 中访问 `https://pypi.nvidia.com/coverage/`，三次重试后于 123.5 秒失败。该 index 来自用户级 `~/.config/uv/uv.toml`，不在项目 SourcePlan 中；这是配置隔离正确性问题，已在同一实现工作树中独立修复，不作为搜索性能优化。

## 3. 为什么不是死循环

`CoordinateSearch` 有四个终止约束：

- 每个 cell 的候选快照有限；
- 外层 sweep 只在某个坐标 floor 严格低于当前版本时继续，当前向量不会向上移动或振荡；
- `_find_floor` 最多迭代 `2 * len(versions) + 1` 次；
- 相同 `(active dependency, vector)` 的 evidence 在一次 invocation 内缓存。

本次轨迹也符合这些约束：3.10 在进程 423 后不再调度，3.11/3.12 继续并行并最终完成；不存在进程数持续增长但向量集合不增长的状态。

## 4. 现行裁剪的有效范围

### 4.1 有效：coordinate descent 与区间定位

搜索固定其他坐标后逐个定位 floor，大窗口使用二分、小窗口至多线扫 8 个候选。3.11/3.12 的 54 个唯一向量远小于 114,048 个已观察组合，证明主要裁剪来自 coordinate descent，而不是 static shortcut。

### 4.2 有限：static region guidance

Static region 以 active dependency、所有其他坐标、candidate order、snapshot、policy 和 baseline 组成精确 slice，并要求已观察的相邻点具有相同 fingerprint 和单一 runtime status。该边界保守且正确，但二分探针通常不相邻；其他坐标下降后还会形成新 slice。因此本次只有 18 个唯一向量免掉 pytest，约占 121 个 search-only 唯一向量的 14.9%。

### 4.3 固定开销：static-to-runtime promotion

当 static guidance 提议 floor 或 predecessor 时，最终边界仍必须由 runtime authority 直接认证。现实现会关闭 static probe 的 `PreparedEnvironment`，promotion 再为同一向量执行两次 compile、venv、sync 与 graph inspection；19 个重复 prepare 全部属于这一模式。它不重复 ty，也不重复已缓存的 evidence，但重复了环境物化。

以上是 2026-08-28 运行基线。D022/P028 后续让同一 Proposal 的 static-only probe 保留未污染的
`PreparedEnvironment`，promotion/full 复用该 lifecycle，并在 full 或 runner 终态关闭；不同 Proposal
仍不复用环境。真实 graph regression 已证明 static/full probe 只 prepare 一次。

### 4.4 必要隔离：跨 Cell 不共享 runtime result

Python 3.11 与 3.12 访问了相同数量和形状的向量，但 interpreter、marker、wheel、resolution graph 和 configured verifier terminal 都属于 Cell evidence，不能直接跨 Cell 复用。未来只能审查不可变 registry response、artifact 或确定性 plan 层面的共享，不能复用 compatibility disposition。

## 5. 后续优化候选

除第 1 项外，以下候选尚未实施，落地前需要独立设计与资格证明：

1. **已解决：保留 promotion 所需的 prepared environment。** D022/P028 已让同 Proposal 的 static probe 到 runtime promotion 在短生命周期内复用环境；不同 Proposal 不原地升级/降级，运行 verifier 后立即关闭并视为污染。
2. **P1：提高昂贵 verifier 的有效裁剪率。** 评估能否调整探针顺序或 static region 建立方式，使相同 fingerprint 的直接 runtime reference 更早服务后续点；任何 shortcut 都只能提供 guidance，最终 floor 和 predecessor 仍须满足 D003/D005 的 runtime certification。
3. **已移交 R006：非交互搜索遥测。** 问题、候选事实、identity 约束、验收与停止条件现由 [R006 §5.2](R006-pf-cli-system-review.md) 单独跟踪；本项只保留来源位置，不再作为 R004 的开放候选。
4. **P2：在昂贵工作前检查旧 report compatibility。** 本次完成 Cell 搜索后才发现旧开发期内联报告不可读取。D014 要求 reader 拒绝该布局；未来可以把同一校验前移，避免完成搜索后才失败。
5. **P3：审查 resolver 层的安全共享。** Candidate HTTP response 已跨 cell 缓存；可以继续量化相同 source/cutoff 下 metadata、artifact 与 plan 的可复用边界，但 Cell-specific resolution context 和终态不得被弱化。

不建议由 PF 隐式改写用户的 `test-command`、自动启用 testmon、跳过完整 verifier，或把未运行 runtime 的 static PASS 称为 floor。这些做法会改变验证契约，而不是单纯优化实现。

## 6. 后续验证口径

如果启动性能整改，应至少记录：

- 每个 Cell 的候选数、sweep 数、唯一向量、prepare、static-only、promotion 和 verifier 次数；
- configured verifier 累计时间与 wall-clock critical path；
- 相同输入下 final vector、boundary、FailureRecord 和 disposition 与整改前一致；
- packaging/pydantic dogfood、三 Python cell 和 source timeout case 不改变产品分类；
- 性能 qualification 不以减少测试覆盖或放松 runtime certification 换取通过。

本评审不要求当前实现同步完成上述优化。

# C007 — PF Search 生命周期 Presets

- **状态：** 开放
- **日期：** 2026-09-12
- **性质：** 非规范性 Concept；不授权实现，不新增现行 CLI 选项
- **来源：** check-first 周期之后的接入与修复意图；历史见 [C005](../archived/concepts/C005-pf-check-first-lifecycle.md)
- **相关 owner：** [D001](../designs/D001-pf.md)、[D037](../designs/D037-pf-candidate-search-policy.md)、[D003](../designs/D003-pf-search-algorithm.md)、[D014](../designs/D014-pf-report-schema.md)

## 1. 问题与候选意图

现行稳态是 `pf check`，接入与重定界仍使用普通 search。设想增加两个常用意图入口；以下命令
尚未实现，不是当前使用说明。Preset 组合候选策略，不引入新的搜索算法。

| 意图 | 候选窗口（示意，逐 dependency/Cell） | 周期 |
| --- | --- | --- |
| `pf search --init` | candidate ≤ verified baseline | 初次发现 → apply → check |
| `pf search --repair` | declaration ≤ candidate ≤ verified baseline | check 拒绝后主动修复 → apply → check |

Init 在没有用户显式限制时以 `search-space = all` 表达历史探索意图；仍受声明保留项、artifact、
Cell、prerelease 等候选资格约束，不承诺所有历史 release 都会被采样。Repair 寻找不降低当前声明的
可通过新下界，不保证修复代码、测试或环境问题；check 的 INDETERMINATE 不自动成为修复请求。

## 2. 已收敛的工作方向

DSL 是机制，preset 是 intent overlay。有效候选应同时满足用户显式策略、生命周期窗口和公共
候选资格；preset 不扩大逐依赖 search-space，不覆盖其显式 search-resolution 或其它 policy。
例如用户限制 numpy 的某些系列，repair 只在这些系列与 repair 窗口的交集中搜索。

现行名称是 `search-resolution`；不恢复旧 `search-step`。窗口以精确版本约束表达，不能把
`majors[declaration:]` 当成精确 `candidate >= declaration` 的同义词。空间与采样仍是不同维度。
当前 D037 已有条件默认；未来 Design 需明确 overlay 在 raw 显式配置、继承和默认展开之间的位置，
不能把 init 的意图默认偷偷变成所有 search 的新默认。

## 3. 进入 Design 前需闭合

1. **声明边界。** active base/extra、marker/Cell 的有效下界如何确定；无下界、严格下界、排除项、
   多条约束或缺少 declaration anchor 时如何处理。无下界不能未经定义就静默把 repair 改成 init。
2. **组合次序。** root/member/dep、条件默认、CLI 显式覆盖与 preset 的优先关系；是否允许同时选择
   init/repair；用具体配置矩阵证明用户显式约束不被放宽。
3. **资格与采样。** 精确窗口何时施加、系列代表如何选取及 baseline 是否在候选空间内；不得通过
   重选代表造成用户未选择的分辨率变化。空交集、无更低候选、无 PASS 与 baseline 失败分别表达。
4. **结果与授权。** 有效策略如何进入 report identity、reader 和 apply 复证，是否保存 preset 意图；
   apply 不自动放松 drift，不依赖回执或增量授权，不把单点 PASS 当成未测范围证明。
5. **验证证据。** 覆盖首次接入、check 拒绝后的修复、逐依赖限制、不同 Cell 下界、空窗口和无法验证
   baseline；证明完整 search/apply/check 周期，并与普通 search 在同有效策略下比较结果资格。

先用现行候选求值语义推演上述矩阵，确定最小命令与策略增量，再建立并接受 Design。
C004、C008、C009 均不是本构想前置；若目标无法在现行单坐标搜索的能力边界内完成，保留失败，
不在 preset 内隐式加入 interaction search 或非单调恢复。

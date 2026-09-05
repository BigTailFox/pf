# E005 — PF 多分辨率搜索纯算法模拟

- **状态：** 已完成；仅 D031 收益验证第一阶段
- **日期：** 2026-09-05
- **性质：** 非规范性合成算法实验，不定义产品契约，不授权生产实施
- **基线：** `85e195c`；Python `3.10.16`；固定 `small_threshold=8`
- **目标 Design：** [D031](../designs/D031-pf-multi-resolution-coordinate-search.md)
- **现行 owner：** [D003](../designs/D003-pf-search-algorithm.md)
- **关联 Review：** [R008](../reviews/R008-pf-search-performance-review.md)
- **复现脚本：** [simulate_d031_search.py](../../scripts/simulate_d031_search.py)
- **原始结果：** [summary.json](data/E005/summary.json)、[逐案 CSV，gzip](data/E005/cases.csv.gz)、
  [代表性完整 trace](data/E005/traces.json)

## 1. 结论

本矩阵支持优先继续验证 predecessor 重验；没有证据支持把分层搜索本身作为默认性能优化。
对 2,883 个单坐标边界场景，仅重验使直接 oracle 调用减少 36.75%；仅分层增加 2.93%。
两者组合比现行少 23.96%，但比仅重验多 20.22%。这些是本合成矩阵的累计探针计数，不能换算为
真实项目耗时百分比，也不表示实际 floor 在各位置等概率分布。

全部适用单调用例与独立穷举参考的向量、predecessor、sweep 和终态一致。模拟 A 还逐案调用未修改的
`CoordinateSearch.minimize()`，3,362 个对照的终态、成功边界/sweep 与唯一直接评价 trace 全部一致。
本次只执行纯 Python/合成 oracle，没有 registry、prepare、uv resolution、ty 或真实 verifier。

因此：可以继续投入重验的最小 evaluator 实测；分层搜索需先解释并改善退化场景，或以其他明确产品
收益支持其复杂度。实验不自动撤销 D031 的配置统一方向，也不把重验的收益算作树的收益。

## 2. 对照与计数方法

| 组 | 一维策略 | 后续 sweep |
| --- | --- | --- |
| A | 现行平面定位，最低目标候选快速路径 | 重新定位 |
| B | 同 A | 重验 predecessor；仍拒绝则完成该坐标 |
| C | major → minor → patch，保留最低目标候选快速路径 | 重新定位 |
| D | 同 C | 重验 predecessor；仍拒绝则完成该坐标 |

每组独立创建 invocation-local 结果表，baseline 已验证且预置，不计入新增 direct miss。
相同完整向量命中直接结果后不再调用 oracle；direct result 与按 active dependency/context 绑定的
synthetic static guidance 分开。缓存命中仍登记当前 Slice 的直接观测并参与非单调检测。

主性能矩阵使用 direct-only oracle；不模拟 static region 命中率，不模拟 FailedCaseSet 耗时。
报告中的 direct miss 是一次新的完整向量 oracle 求值，不是实际 configured verifier 次数。
所有策略使用相同终止层代表序列、baseline、规范坐标顺序和阈值；不优化一组的 threshold 再与另一组比较。
平面 locator 精确保留现行“入场时距离不超过 8 则线性，否则二分直到相邻”的选择，不在二分中途切换线性。

CSV 分别记录 logical requests、cache hits、direct misses、static misses、promotion requests、
含 baseline 的唯一直接向量数、sweep、重验结果和最终边界。逻辑请求的统计入口是模拟器 evaluator，
不等同于当前算法内部方法调用次数；生产差分对照的是去重后的直接执行 trace。

脚本是实验状态机，不进入产品依赖图。使用现行 public test fixture 构造合法 Probe evidence 与
CandidateSnapshot，再调用真实 `minimize`；不 monkeypatch 或 subclass 当前搜索器。
所有 fixture artifact/process/evaluation 均为合成事实，不是安装或测试进程的运行证据。

## 3. 输入矩阵与独立验证

### 3.1 性能形状

以下形状在脚本中预先固定。每种形状分别用 major/minor/patch resolution，并遍历目标候选中的
每一个真实 floor 位置，不只选择中点或有利边界。每个场景包含完整坐标搜索和最终无变化 sweep。
表内原始版本数量是合格 U 大小；每次仅探测终止层代表。

| 形状 | U 数量 | major / minor / patch 代表数 | 发布结构 |
| --- | ---: | --- | --- |
| uniform_10x10x10 | 1,000 | 10 / 100 / 1,000 | 均匀 10×10×10 |
| dense_old_major | 409 | 10 / 49 / 409 | 最旧 major 有 40×10 个版本，另 9 个 major 各 1 个 |
| dense_new_major | 409 | 10 / 49 / 409 | 最新 major 有 40×10 个版本，另 9 个 major 各 1 个 |
| single_major | 300 | 1 / 30 / 300 | 单 major，30×10 |
| single_minor | 300 | 1 / 1 / 300 | 单 minor，300 个 patch |
| sparse_series | 144 | 12 / 48 / 144 | major/minor 数字有间隔，patch 为 0/3/9 |

单坐标共 2,883 个场景。另有 36 个多坐标场景：三个 resolution × 2/3/5 个坐标，
每组分别设置 20%/50%/80% 位置的独立阈值，以及一个随后续坐标降低而放宽早先阈值的 context oracle。
候选形状为 4×5×5；所有实际访问的向下切片另经逐点检查，满足局部单调性。
context oracle 不声明整个多维空间全局单调，也不将未访问的更高切片视为已认证。

### 3.2 正确性与异常矩阵

| 检查 | 场景数 | 四策略运行数 | 结果 |
| --- | ---: | ---: | --- |
| 小空间、单候选、特殊版本、三 resolution、全部边界、空间外 sentinel、全拒绝、空空间 | 70 | 280 | 240 SUCCESS；40 NO_PASS_IN_SEARCH_SPACE；与穷举一致 |
| 全枚举向上闭合布尔表，3×3、4×4、2×2×2，固定最高点 PASS | 107 | 428 | 全部与独立穷举一致 |
| 9 个候选、最高点 PASS，其余 8 点的全部布尔组合 | 256 | 1,024 | 检查直接证据与终态合法性，不要求非单调空间中 floor 等价 |
| 12 个候选的 11 个非 baseline 位置逐一放置 Indeterminate | 11 | 44 | 28 次探测到后立即停止；16 次未访问该点而成功 |
| synthetic PASS guidance 被拒绝、REJECTED guidance 被 predecessor promotion 推翻 | 2 | 8 | 全部返回直接证据支持的正确边界 |
| 单坐标性能矩阵 | 2,883 | 11,532 | 全部与独立穷举一致 |
| 多坐标性能矩阵 | 36 | 144 | 全部与独立穷举一致 |

CSV 共 13,460 行、3,365 个四策略场景。最高点固定 PASS 的向上闭合表分别为 19、69、19 个；
不包含全拒绝表，全拒绝由 sentinel 矩阵单独覆盖。

另有六组定向断言，不计入上述 CSV：四策略各一次同 Slice 已有直接 PASS 后发现更高 Rejection 的
立即终止；一次通过 direct-cache hit 发现非单调；一次检查跨 active dependency 的 guidance 隔离、
直接结果优先和 invocation-local cache 隔离。代表性 trace 保存在 `traces.json`。

验证采用三条独立证据链：

1. 候选按 key 求最大值形成参考目标序列，再从完整 U 递归遍历树，核对每层连续 partition 和最终代表。
   包含 epoch、短版本、prerelease、post/local 与额外 release 段，不根据版本字符串字典序分组。
2. 不使用树、二分、缓存和重验的穷举坐标下降，独立计算预期 floor、predecessor、sweep 与终态。
3. 真实现行 `CoordinateSearch.minimize()` 对照模拟 A，逐点比较唯一直接执行的完整 vector 与 status。
   3,362 个场景均通过；排除现行 Snapshot 不接受的空输入，以及两个 synthetic guidance 专项场景。

另以独立 trace 回放检查：直接 fatal 或已观察非单调必须出现在最后一次逻辑调用；floor/final 有直接
PASS，predecessor 在最终 context 有直接 Rejection，直接 oracle 不重复执行，计数守恒。
最低候选即 PASS 的 72 个性能策略运行均至多一次新直接评价；baseline 已是唯一候选时为零次。

## 4. 单坐标结果

| 策略 | 累计 direct misses | 相对 A | 比 A 更少 / 相同 / 更多的场景 |
| --- | ---: | ---: | --- |
| A 平面 | 43,809 | 基线 | 0 / 2,883 / 0 |
| B 平面＋重验 | 27,709 | −36.75% | 2,773 / 110 / 0 |
| C 分层 | 45,091 | +2.93% | 940 / 688 / 1,255 |
| D 分层＋重验 | 33,311 | −23.96% | 2,515 / 184 / 184 |

直接比较 D 与 B：D 在 310 个场景更少、554 个相同、2,019 个更多，累计多 20.22%。
本矩阵里的重验没有增加任何单坐标场景的 direct misses；这是实测范围内的事实，不是所有 oracle 的普遍保证。

以下为每个形状中所有 floor 位置的平均 direct misses，完整 18 个形状/层级汇总见 JSON：

| 形状 / resolution | A | B | C | D |
| --- | ---: | ---: | ---: | ---: |
| 均匀 / minor | 11.23 | 7.64 | 11.89 | 8.69 |
| 最旧 major 密集 / minor | 9.27 | 6.55 | 9.18 | 6.84 |
| 最新 major 密集 / minor | 9.27 | 6.55 | 12.20 | 9.86 |
| 单 major / minor | 7.97 | 5.73 | 7.97 | 5.73 |
| 单 minor / minor | 0.00 | 0.00 | 0.00 | 0.00 |
| 稀疏系列 / minor | 9.19 | 6.52 | 8.98 | 7.40 |
| 均匀 / patch | 17.91 | 10.96 | 17.49 | 12.69 |
| 最旧 major 密集 / patch | 15.30 | 9.72 | 15.97 | 11.94 |
| 最新 major 密集 / patch | 15.30 | 9.72 | 18.90 | 14.88 |
| 单 major / patch | 14.43 | 9.26 | 14.43 | 10.60 |
| 单 minor / patch | 14.43 | 9.26 | 14.43 | 9.26 |
| 稀疏系列 / patch | 12.32 | 8.16 | 10.72 | 9.13 |

只有一个目标候选且它就是 baseline 时无需新增评价，因此单 minor / minor 一行为零。
相同 resolution 和候选数，桶形状不同也会产生不同树成本；最新 major 密集时退化尤其明显。

## 5. 可解释的收益与退化 trace

这些例子从完整矩阵中选出用于说明机制，不改变汇总样本或权重。trace 中 vector 使用 U 的零基索引，
同文件的 versions/targets 给出精确版本映射，`sweep` 和 cache source 标出每次调用。

### 5.1 同 context 重验也能省新探针

均匀 1,000 候选，patch floor 为索引 500，即 `5.0.0`，predecessor 为索引 499，即 `4.9.9`。
四组 direct misses 分别为 A=18、B=10、C=10、D=7。

A 第一轮已用 10 次新评价建立 499 REJECTED / 500 PASS。第二轮 current 变为 500，context 仍为空，
但平面定位以新的区间重新二分，额外探测此前未访问的 250、375、437、468、484、492、496、498。
结果缓存只能消除重复向量执行，不能自行阻止搜索产生这八个新向量。

B 第二轮只请求 predecessor 499，命中同 context 的直接 Rejection 即完成，新增执行为零。
所以重验的收益并不要求其他依赖先发生变化；它同时避免了已知边界被重新搜索。

### 5.2 树可以获益，但不是所有位置都获益

同一均匀空间，floor 为索引 900，即 `9.0.0`：A=20、B=11、C=8、D=8。
此时边界贴近 major/minor 的最低 child，分层探测能较快命中并向下复用 PASS。

最新 major 密集空间，floor 为最高索引 408，即 `9.39.9`：A=B=10，C=D=17。
这次 current 不下降，只需一轮；重验没有机会介入。树分别支付 major、minor、patch 层的定位成本，
总共比平面二分多七次直接评价。缓存无法消除这些互不相同的代表探针。

## 6. 多坐标结果

| 策略 | 36 场景累计 direct misses | 相对 A | 比 A 更少 / 相同 / 更多 |
| --- | ---: | ---: | --- |
| A | 1,136 | 基线 | 0 / 36 / 0 |
| B | 852 | −25.00% | 27 / 9 / 0 |
| C | 1,237 | +8.89% | 9 / 13 / 14 |
| D | 974 | −14.26% | 22 / 12 / 2 |

D 相对 B 在 18 个场景相同、18 个更多，没有更少的场景，累计多 14.32%。
不同形状、真实静态调度和不同测试成本可能改变这一分布，本实验未测这些因素。

三坐标 context-relax / patch 示例：四组均经历 4 个 sweep，最终索引向量 `(16,15,10)`，
direct misses 为 A=57、B=46、C=58、D=54。B/D 都观察到三次旧 predecessor 在新 context 转为 PASS，
随后继续向下搜索；另外六次重验为 REJECTED。最终三条边界都属于最终 context。
这覆盖了旧失败只能作为提示、不能跨 context 直接复用的路径，而不只测独立依赖。

## 7. 复现与校验

脚本默认写 `/tmp/pf-d031-simulation`。从 PF 仓库根目录执行，按 AGENTS 要求在 sandbox 外运行
PF 算法验证；本脚本已检查，运行中不联网、不调用 CLI/真实 verifier，仅子进程读取 `git rev-parse HEAD`。

```bash
.venv/bin/python scripts/simulate_d031_search.py --output /tmp/pf-d031-simulation-final
.venv/bin/python scripts/simulate_d031_search.py --output /tmp/pf-d031-simulation-repeat
.venv/bin/ruff check scripts/simulate_d031_search.py
.venv/bin/ruff format --check scripts/simulate_d031_search.py
git diff --check
```

两次最终脚本运行均成功，13,460 行 CSV、summary JSON、代表 trace JSON 逐字节一致。
全部逻辑调用 trace（包含 cache hit、promotion 与 sweep）的 SHA-256 为：

```text
120c66aec75f2eb573e0aa1528a513deda41152130a3cf83790067741e37d1ed
```

`summary.json` 保存 HEAD、Python、脚本/生产算法/fixture 源文件 SHA-256、完整候选形状、
逐类断言计数、CSV 摘要和全部策略汇总。仓库保存的 `cases.csv.gz` 是生成 CSV 的无损 gzip，mtime=0；
解压后 SHA-256 必须等于 `summary.json.case_csv_sha256`。不保存模拟执行本身的耗时作为 PF 性能证据。

未运行完整项目测试，因为本次没有生产改动；只执行实验自身的独立参考断言、真实算法差分对照、
确定性复跑，以及新增脚本 lint/format 和文档/产物完整性检查。原始 Design 草案与索引改动继续保留。

## 8. 证据边界与后续

本轮完成 D031 §8.2 的纯算法阶段，证明在所列输入上状态机与证据不变量成立，并量化选点差异。
它未验证真实 static region eligibility、prepare 复用、FailedCaseSet 命中、verifier 次数或 wall-clock，
也未验证新的配置/候选/Schema/report/apply 产品路径。完整 U 的内存与报告体积代价仍待集成阶段测量。

最低候选快路确实避免了“最低已 PASS 仍多测上层代表”的退化，但不能消除边界较高时多层定位的额外成本。
仅调参是否能改善树、何种候选分布值得分层，都需要另设实验；本次没有在看到结果后修改阈值或自动切换策略。

下一步建议先把 B 接入临时 evaluator harness 测真实成本，再决定树搜索是否继续投入。
若要先继续树的算法实验，应独立对照优化版本并保留本次原始结果，不能改写 E005 使之成为新算法的数据。
D031 的实施范围和默认切换仍需据此重新评审；纯算法阶段通过不表示 AC10 的真实集成收益验收已完成。

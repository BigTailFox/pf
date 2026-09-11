# E014 — MkDocs smoke/check/search 重跑（all×minor；patch refine 未完成）

- **状态：** 已完成
- **日期：** 2026-09-11
- **性质：** 非规范性 dogfood 实验事实，不定义新契约，不构成对 MkDocs 上游声明的修改建议
- **证据位置：** [data/E014/](data/E014/)（终端日志、命令元数据、配置副本、报告摘要、代表诊断与 apply 差分）
- **历史对照：** [E007](E007-mkdocs-baseline-and-build-failures.md)、[E008](E008-mkdocs-complete-search.md)、
  [E009](E009-mkdocs-static-guidance.md)
- **目标：** `experiments/mkdocs`，MkDocs `1.6.1`，上游 commit
  `2862536793b3c67d9d83c33e0dd6d50a791928f8` 加本地实验配置
- **PF：** generator `0.4.0`；HEAD `64b06eb45a3fed054cbc43cf1d3c5d6a67f0bc8b`
- **契约入口：** [D001](../designs/D001-pf.md)、[D003](../designs/D003-pf-search-algorithm.md)、
  [D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、
  [D012](../designs/D012-pf-harness-relaxation.md)、[D014](../designs/D014-pf-report-schema.md)、
  [D037](../designs/D037-pf-candidate-search-policy.md)

本轮沿用 E007/E008 的实验契约（`pf-unit` + i18n、`extra-policy = "none"`、pathspec
`<=0.12.1`），执行 `pf smoke → pf check → pf search → pf apply`，搜索为显式
`search-space = "all"` × `minor`。随后把空间收到 `minors[declaration]` × `patch`。
patch refine **没有**得到可表示投影：五个 Cell 均在 jinja2 坐标上
`NO_PASS_IN_SEARCH_SPACE`。根因不是「2.10.3 本身未通过」，而是现行坐标下降无法处理
**顺序依赖的联合约束**（§7.1）。工作树停在 all×minor apply 后的中间声明。

smoke **5/5 PASS**；check **5/5 PASS**（与 E008 的 3 个 witness REJECTED 对照：现行 PF 已移除
错误成员的 witness）；all×minor search **5/5 SUCCESS、complete**。Markdown floor 在全部
Python 上都是 `3.3.7`，不再出现 E008 的 3.10–3.12 `3.4.4`。`all` 还把 packaging 降到
`14.1`、watchdog 降到 `0.10.4`。终端卡片上的版本是 predecessor 窗口，不是 floor。

## 1. 兼容性结论与实验配置

MkDocs 是 hatchling 单包，`requires-python = ">=3.8"`。单元测试期望 Babel，因此继续用
`pf-unit = ["mkdocs[i18n]"]` 与 `extra-policy = "none"`，把 `min-versions` extra 留在矩阵外。
pathspec 保留 E007 的临时上界 `<=0.12.1`。Python 列表按 D001 要求的字符串排序。

| 观察 | 对 PF 的影响 | 本轮处理 |
| --- | --- | --- |
| 上游测试环境带 i18n | 省略 extra 会缺 Babel | 显式 `test-group = "pf-unit"`，`extra-policy = "none"` |
| pathspec 1.1.1 破坏草稿缩进测试 | 最高版本基线会失败 | 保留 `pathspec >=0.11.1,<=0.12.1` |
| 用户要求全空间粗搜 | 省略 space 走 `majors[declaration-1:]` | 显式 `search-space = "all"` |

配置副本：[configuration.toml](data/E014/configuration.toml)。验证契约是
`python -m unittest discover -s mkdocs -p "*tests.py"`，未缩小测试路径。

## 2. 实验计划（已执行）

1. 保留 E007/E008 实验配置，只增加 `search-space = "all"` 与 `search-resolution = "minor"`。
2. 由 PF 仓库根、沙箱外、cwd=`experiments/mkdocs` 顺序执行 smoke → check →
   all×minor search → apply。
3. 改成 `minors[declaration]` × `patch` 再 search。该阶段以 incomplete 结束，没有 apply。
4. 用 `ReportStore.read` 核验 all×minor 报告；patch 轮保留终端、诊断与 incomplete 摘要。

调用方式与 [E013](E013-requests-complete-search.md) 相同。

## 3. 运行总表

smoke、check 与 all×minor search 共享 source snapshot
`7c60932eba25f041e33929ae8f1a82d60ce3a9d5ca08da7984a2afd8f0ba555b`。patch refine 因声明与
搜索配置变化而使用新 snapshot `1bca6709c381aa3df0f4ea455c7908cd1ee871a7986e08022b60c96bedf6c3a1`。

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| smoke | `20260911T095456.023732Z-663017-5a154df1` | 0 | 19.46s | 5/5 PASS |
| check | `20260911T095523.605618Z-664481-031c5f4c` | 0 | 15.25s | 5/5 PASS，`declaration` / `lowest-direct` |
| search all×minor | `20260911T095557.102032Z-666024-689b5a17` | 0 | 1042.69s | 5/5 SUCCESS，complete |
| apply all×minor | （无 Verification Run） | 0 | 1.32s | 写回 minor 投影 |
| search patch refine | `20260911T101358.869628Z-745130-0fa244e8` | 2 | 81.90s | incomplete；5/5 `NO_PASS_IN_SEARCH_SPACE`（jinja2） |

矩阵：CPython **3.8–3.12** × `x86_64-unknown-linux-gnu` × `i18n`，**5 Cells、14 个受管直接依赖
并集、0 pinned**。3.8–3.9 因 `importlib-metadata` marker 多一个坐标。

## 4. smoke：最高版本通过

终端 `Smoke passed · 5 cells`。Journal 0 条失败。五个 configured unittest 均正常退出 0。
execution policy `968e10f44e5ddeeee80fbf71fbad8522c9b652ee881f67540112177115c243af`。

## 5. check：原始声明下界通过

终端 `Check passed · 5 cells`。五个 Cell 均为 `check passed at [declaration][lowest-direct]`。
Journal 0 条失败。这与 [E008](E008-mkdocs-complete-search.md) 的 3.10–3.12 witness REJECTED
不同：E009 之后现行 PF 不再用错误成员做 witness，本轮声明最低向量进入完整 unittest 并通过。
check 通过**不能**推出 search 不会再移动坐标。

## 6. all×minor：`search-space = "all"`

`ReportStore.read` 得到 `result.status = complete`、5 个 `CellSuccess`、各 3 个 sweep。
报告 generation `783d037f68783cc634e12d48e1dac66a5d96d5a15d06dcede46fad39d32aaee7`，
SHA-256 `e5674d6d7a2f706c783e007da99163dd88777d7c9f46667aadc9716d41567dcb`（2,197,805 字节）。
policy identity `3ffc5a95f6997c97cf062049d9245a5193361b43ee827c4b07a51650f2d8affb`。
`requested_space = "all"`，resolution `minor`。14 个投影 `representable = true`。

终端每个 Cell 都写 `search completed at [markdown=3.2.2][oracle 3.2.2~3.3.7#2]`。这是
Markdown 坐标的 predecessor 窗口，**floor 是 3.3.7**。

### 6.1 投影对照

| 依赖 | 原声明下界 | Python 3.8–3.9 floor | Python 3.10–3.12 floor | 相对 E008 |
| --- | --- | --- | --- | --- |
| Babel（i18n） | 2.9.0 | 1.0 | 2.7.0 | 相同 |
| click | 7.0 | 7.0 | 7.0 | 相同 |
| ghp-import | 1.0 | 0.6.0 | 0.6.0 | 相同 |
| importlib-metadata | 4.4，Python <3.10 | 4.4.0 | 不适用 | 相同 |
| Jinja2 | 2.11.1 | 2.9.6 | 2.10.3 | 相同 |
| Markdown | 3.3.6 | 3.3.7 | 3.3.7 | E008 的 3.10–3.12 为 3.4.4（缺陷 witness） |
| MarkupSafe | 2.0.1 | 0.23 | 1.1.1 | 3.8–3.9 更低 |
| mergedeep | 1.3.4 | 1.3.4 | 1.3.4 | 相同 |
| mkdocs-get-deps | 0.2.0 | 0.1.0 | 0.1.0 | 相同 |
| packaging | 20.5 | 14.1 | 14.1 | E008 为 19.0 |
| pathspec | 0.11.1，另有 <=0.12.1 | 0.10.3 | 0.10.3 | 相同 |
| PyYAML | 5.1 | 5.1.2 | 5.1.2 | 相同 |
| pyyaml-env-tag | 0.1 | 0.1 | 0.1 | 相同 |
| watchdog | 2.0 | 0.10.4 | 0.10.4 | E008 为 1.0.2 |

Markdown predecessor `3.2.2`（`failure-83017ccd988f7398`）是 configured unittest 拒绝：
66 tests、55 errors，根因是测试模块导入失败。这不是 E008 的错误 witness 成员。因此本轮
`3.3.7` 是 verifier 权威下的 floor，不能再解释成 witness 误抬。

packaging `14.1` 与 watchdog `0.10.4` 是 `search-space = "all"` 相对 E008 条件默认的额外下降。

### 6.2 规模

| 指标 | 值 |
| --- | ---: |
| 搜索观察 | 572（ProbePass 272、ProbeRejection 300） |
| Cell 内 FailureRecord | 300 |
| VERIFIER_EXITED_NONZERO | 168 |
| RESOLUTION_FAILED | 46 |
| RESOLUTION_CONFLICT | 83 |
| INSTALLATION_FAILED | 3 |
| candidate snapshots | 67 |
| process logs | 3380 |
| 最长 Cell | 18m41s（Python 3.9） |
| 命令墙钟 | 1042.69s |

apply 退出 0。写回后的 runtime 数组把 Jinja2 / MarkupSafe / Babel 按 Python minor 拆行，
Markdown 升到 `>=3.3.7`，packaging 降到 `>=14.1`，watchdog 降到 `>=0.10.4`。差分见
[all-minor/apply.diff](data/E014/all-minor/apply.diff)。这是实验克隆上的授权编辑。

## 7. patch refine：jinja2 在新 baseline 上无合格点

all×minor apply 之后，Jinja2 中间声明是 `>=2.10.3`（3.10–3.12）/ `>=2.9.6`（3.8–3.9）。
`minors[declaration]` 只搜索该 minor 系列的 patch。新 invocation 的 baseline 仍取各坐标当前
最高版本：MarkupSafe 实际安装 **3.0.3**（3.10–3.12）或 **2.1.5**（3.8）。

五个 Cell 都在 jinja2 坐标以 `NO_PASS_IN_SEARCH_SPACE` 结束。终端
`Search incomplete · 5 cells have no applicable floor`。报告 `incomplete`，reasons
`NO_PASS_IN_SEARCH_SPACE`、`UNREPRESENTABLE_PROJECTION`。退出码 2。没有 apply。

Python 3.12 代表探针 `failure-1fbc0ad1c3e34450` 的向量是
`jinja2==2.10.3` **加上** `markupsafe==3.0.3`、`markdown==3.10.3` 等最高版本。unittest
收集阶段：

```text
ImportError: cannot import name 'soft_unicode' from 'markupsafe'
```

Jinja2 2.10.3 在 all×minor 的**已提交向量**里与 MarkupSafe 1.1.1 一起通过；patch 轮按坐标
从新 baseline 下降时，其他坐标仍是最高版本，2.10 系列里没有任何 patch 能与 MarkupSafe 3.x
组合通过。这不是 all×minor floor 被证伪。机制见 §7.1。

Python 3.8 同类：`jinja2==2.9.6` 配 `markupsafe==2.1.5`（`failure-05245a5663b71df4`）。

| 指标 | 值 |
| --- | ---: |
| 退出码 | 2 |
| 墙钟 | 81.90s |
| Journal | 5 条失败 |
| process logs | 379 |

### 7.1 坐标下降的顺序依赖（2026-09-11 追加）

「最多 patch 下界等于 minor 下界」只在 **minor 代表在新一轮起始切片里仍然能过** 时成立。
本轮不成立。这暴露了现行 [D003](../designs/D003-pf-search-algorithm.md) 坐标下降对联合约束
的顺序缺口，不是 MkDocs 配置写错。

D003 规定：`current` 从最高 baseline `B` 出发；每次提交只严格降低一个坐标；每个 sweep 按
**规范化依赖名**覆盖全部坐标。Jinja2 排在 MarkupSafe 前面
（`jinja2` < `markdown` < `markupsafe`）。floor 必须是**当前切片**的直接 runtime 证据，
不能继承上一份报告的 `final_vector`。

因此 patch 轮第一扫扫到 jinja2 时，切片是：

```text
C[jinja2] = {2.10.0, 2.10.1, 2.10.2, 2.10.3}   # minors[declaration] × patch
current[markupsafe] = 3.0.3                    # 仍是 B，还没轮到 markupsafe
current[其它] = 各坐标最高版本
```

`2.10.3` 被测了，并且失败了。2.10 内没有能配 MarkupSafe 3.x 的 patch。该坐标
`NO_PASS_IN_SEARCH_SPACE`，Cell 立即停止，**不会**先把 markupsafe 降到 `1.1.1` 再回来重试
jinja2。apply 只写 `>=` 下界、不钉上界，所以下一轮 `B[markupsafe]` 仍是 3.0.3。

对照 all×minor：同一 Cell 走了 **3 个 sweep**。那时 `C[jinja2]` 含 3.x 等能配最高
MarkupSafe 的 minor 代表，第一扫可以把 jinja2 留在高位；随后 sweep 把 markupsafe 提交到
`1.1.1`，再把 jinja2 降到 `2.10.3`。联合向量
`(jinja2=2.10.3, markupsafe=1.1.1)` 是多 sweep 的不动点，不是「一开始就对 B 探测 2.10.3」。

patch 轮把空间收到只有 2.10.x 之后，起始切片里**没有**能配 `B` 的 jinja2。算法不能借用上一轮
已经提交的联合向量，也不能因为「空间里含有曾经的 floor」就跳过当前切片的失败。Flask /
requests 的同构 refine 能走通，是因为它们的 minor 代表在「其它坐标仍是最高」时自己就能过。

本记录不授权改 D003。它只固定：当 `C[d]` 与尚未下降的 `B[d']` 存在不可交换的联合约束，且
`d` 按规范名排在 `d'` 之前时，第一扫即可 `NO_PASS`，即使更低的 `d'` 一旦提交、`C[d]` 里
本有合格点。后续若进入 Design，需要单独证明是否改变 sweep 顺序、失败时是否改搜其它坐标，
或 coarse-to-fine 是否应继承上一轮 `current` 而不是从 `B` 重来。

## 8. 有价值的发现

1. **现行 PF 上 MkDocs 声明下界 check 可以通过。** E008 的 3.10–3.12 witness 失败不再复现。
2. **Markdown floor 回到 3.3.7。** predecessor 3.2.2 是真实 unittest 导入失败，不是缺陷
   witness。E008 把 3.10–3.12 抬到 3.4.4 的结论不能沿用。
3. **`search-space = "all"` 降低了 packaging 与 watchdog。** 相对 E008 的
   `majors[declaration-1:]`，packaging `19.0 → 14.1`，watchdog `1.0.2 → 0.10.4`。
4. **现行坐标下降处理不了顺序依赖的联合约束。** 见 §7.1。patch refine 在 jinja2 上
   `NO_PASS`，不是因为 2.10.3 从未通过，而是第一扫必须在 `markupsafe==3.0.3` 的切片里找
   2.10.x；规范名顺序让 jinja2 先于 markupsafe，失败即停 Cell。all×minor 靠 3 个 sweep
   和更宽的 `C[jinja2]`（含能配 B 的 3.x）才到达 `(2.10.3, 1.1.1)`。这是 PF 算法缺口的
   现场证据，不是「最多 patch floor = minor floor」的预期落点。
5. **apply 后再做 `minors[declaration]` × patch 不是总是可行。** 中间声明只降低下界，不限制
   上界；下一轮 baseline 仍是最高版本。Flask/requests 的同构工作流在本矩阵上失败，因为
   它们的 minor 代表对 B 自己就能过，MkDocs 的 Jinja2 2.10 代表不能。
6. **完成卡片继续展示 predecessor。** all×minor 最后拒绝 Markdown 3.2.2（floor 3.3.7）。

## 9. 固定证据与局限

- [smoke.txt](data/E014/smoke.txt) / [check.txt](data/E014/check.txt)
- [smoke-journal.json](data/E014/smoke-journal.json)、[check-journal.json](data/E014/check-journal.json)
- [configuration.toml](data/E014/configuration.toml)
- [all-minor/search.txt](data/E014/all-minor/search.txt)、[all-minor/search-summary.json](data/E014/all-minor/search-summary.json)
- [all-minor/apply.txt](data/E014/all-minor/apply.txt)、[all-minor/apply.diff](data/E014/all-minor/apply.diff)
- [all-minor/diagnostics.txt](data/E014/all-minor/diagnostics.txt)
- [all-minor/configuration.toml](data/E014/all-minor/configuration.toml)
- [patch-refine/search.txt](data/E014/patch-refine/search.txt)、[patch-refine/search-summary.json](data/E014/patch-refine/search-summary.json)
- [patch-refine/diagnostics.txt](data/E014/patch-refine/diagnostics.txt)
- [patch-refine/configuration.toml](data/E014/patch-refine/configuration.toml)

原始运行目录为 `experiments/mkdocs/.pf/logs/<run-id>/`。`experiments/` 被 gitignore。当前工作区
`package-floor.json` 是 **incomplete patch refine** 产物，不能当作 all×minor floor 的授权副本。
all×minor generation `783d037f…` 只保留在其摘要中。

复核 all×minor 摘要：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import json
s = json.loads(Path("docs/experiments/data/E014/all-minor/search-summary.json").read_text())
print(s["identity"]["report_generation_id"], s["result"]["status"], s["counts"]["cell_results"])
PY
```

本实验只证明所列 Linux target、五个 CPython minor、i18n、完整 unittest，以及 `all` × minor。
patch refine 证明了该工作流在本矩阵上的失败模式，并给出坐标顺序依赖的反例，没有给出
patch 级 floor，也不授权改 D003。不覆盖 PyPy、Windows colorama、`min-versions` extra。
all×minor apply 后的新 snapshot 没有再跑 check。

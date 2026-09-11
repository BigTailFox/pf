# E015 — PF 自举 smoke/check/search 重跑（all×minor 后 patch refine）

- **状态：** 已完成
- **日期：** 2026-09-11
- **性质：** 非规范性 dogfood 实验事实，不定义新契约，不构成对本仓库依赖声明的修改
- **证据位置：** [data/E015/](data/E015/)（终端日志、命令元数据、配置副本、报告摘要与代表诊断）
- **历史对照：** [E001](E001-pf-self-bootstrap-validation-contract.md)
- **目标：** PF 仓库根，`package-floor` `0.4.0`，HEAD
  `64b06eb45a3fed054cbc43cf1d3c5d6a67f0bc8b` 加临时 `[tool.pf]` 搜索字段
- **PF：** generator `0.4.0`；与目标同一 HEAD
- **契约入口：** [D001](../designs/D001-pf.md)、[D003](../designs/D003-pf-search-algorithm.md)、
  [D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、
  [D012](../designs/D012-pf-harness-relaxation.md)、[D013](../designs/D013-pf-pytest-observer.md)、
  [D014](../designs/D014-pf-report-schema.md)、[D037](../designs/D037-pf-candidate-search-policy.md)

本轮在 PF 自己的仓库上执行 `pf smoke → pf check → pf search`，搜索为显式
`search-space = "all"` × `minor`；然后把空间收到 `minors[declaration]` × `patch`。
`test-command` 保持现行 targeted-runtime-contract（排除 `process` / `e2e` /
`qualification` / `infra`）。`uv==0.12.5` 与 `ty==0.0.74` 保持 pinned，不参与搜索。

实验结束后已把 `pyproject.toml` 与根 `package-floor.json` 恢复为实验前内容。本轮 floor
**没有** apply 到产品声明：all×minor apply 因把证据写入 `docs/` 导致 source snapshot
漂移而退出 3；patch 轮因此直接在原声明（已等于 all×minor floor）上搜索。

smoke **3/3 PASS**；check **3/3 PASS**；两轮 search 均为 **3/3 SUCCESS、complete**。
all×minor 的六个可搜索坐标 floor 等于当前声明下界。patch refine 在同一 minor 内把
cyclopts / pydantic / rich / 3.10 的 tomli 降到该系列最低候选；packaging `26.3` 与
tomlkit `0.8.0` 不变。

## 1. 兼容性结论与实验配置

PF 是 `requires-python = ">=3.10,<3.13"` 的单包。省略 `test-group` 会先命中 `dev`，与
`test` include-group 内容相同。本轮不改验证契约，只增加搜索字段与显式 CPython 列表。

| 观察 | 对 PF 的影响 | 本轮处理 |
| --- | --- | --- |
| 现行 `test-command` 是 targeted-runtime-contract | floor 相对进程内公开接口，不是 full-repository | 保持数组不变 |
| uv / ty 精确 pin | 不进入搜索 | 保持 `==` |
| 自举会把仓库当 source snapshot | 搜索期间改 `docs/` 会漂移 | 成功的 search 把终端日志写到 `/tmp`；失败的首次 all×minor 已保留 |

加入的临时配置（副本：[configuration.toml](data/E015/configuration.toml)）：

```toml
[tool.pf]
pythons = ["3.10", "3.11", "3.12"]
test-command = [
    "pytest",
    "--no-testmon",
    "--no-cov",
    "--maxfail=1",
    "-m",
    "not process and not e2e and not qualification and not infra",
]
search-space = "all"
search-resolution = "minor"
```

## 2. 实验计划（已执行）

1. 备份根 `pyproject.toml` 与 `package-floor.json`；只改 `[tool.pf]`。
2. smoke → check。
3. 第一次 all×minor search 三个 Cell 均 SUCCESS，但结束时
   `project source snapshot drifted during search`（退出 3）：当时在写入 E013/E014
   文档。终端见 [all-minor/search-attempt-1-drift.txt](data/E015/all-minor/search-attempt-1-drift.txt)。
4. 不改产品源码，把 search 终端重定向到 `/tmp` 后重跑 all×minor，退出 0。
5. apply 因证据已写入 `docs/` 而 snapshot 漂移，退出 3。声明下界已等于 floor，直接改搜索
   配置做 patch refine（日志仍在 `/tmp`）。
6. 抽取摘要后恢复产品 `pyproject.toml` 与根 `package-floor.json`。

## 3. 运行总表

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| smoke | `20260911T101920.628602Z-754697-7b7c75e6` | 0 | 26.60s | 3/3 PASS |
| check | `20260911T101957.204677Z-756137-d0291f47` | 0 | 25.26s | 3/3 PASS，`declaration` / `lowest-direct` |
| search all×minor（漂移） | `20260911T102032.027945Z-757823-7cc126e4` | 3 | 425.84s | 3 Cell 卡片 SUCCESS；报告未写出 |
| search all×minor（成功） | `20260911T102820.877782Z-784492-e5c459be` | 0 | 357.83s | 3/3 SUCCESS，complete |
| apply all×minor | （无 Verification Run） | 3 | 0.60s | source snapshot drifted since search |
| search patch refine | `20260911T103539.860483Z-810499-79df9996` | 0 | 160.89s | 3/3 SUCCESS，complete |

矩阵：CPython **3.10–3.12** × `x86_64-unknown-linux-gnu` × `no-extra`，**3 Cells、6 个可搜索
直接依赖、2 pinned**。execution policy
`40221cf3cab73522423b38faa89b9ebd0f51d507d11a543ace9baad50cf25e33`。

smoke snapshot `74be351c0fefcc01ebc007353fdacb5faab986501c1979abd302b9ca3ed61b44`；
check 因中间写入 `docs/experiments/data/E015/` 变为
`3a97d981577985bc9b2b4fa33a2bc946b055e0bf4964255393e08fb7c5370e14`。成功 all×minor
snapshot `46b6162c3dea14d5e45ae241dee3e164d7f44a6d34690c2a58677d3939ffc867`；patch
refine（已改搜索字段）`e63827a4749aa6a079e7ce7453489a8fb01f26fed536cc308a4be4c82f8cbc0f`。

## 4. smoke 与 check：当前声明通过

终端 `Smoke passed · 3 cells`、`Check passed · 3 cells`。两份 Journal 均为 0 条失败。
当前声明下界在 targeted-runtime-contract 下已经过完整进程内套件。这与 [E001](E001-pf-self-bootstrap-validation-contract.md)
当时 full-repository 契约把 packaging floor 推到 `22.0` 不是同一 `C`。

## 5. all×minor：floor 等于当前声明

成功报告 generation `c2deeee99ee652e9029adf9a0b09c0f5eb201879bfb8cb934c019fdae8ea18de`，
SHA-256 `cb80b5c06e56fd12cc2dddd1aaa5f84519db586ddcd739e6cc2983578a468709`（1,193,111 字节）。
policy identity `24f6d24a69b1cf13ab0d934fee4345abbfdf32a9c21ef6ff8e158fe1b03658f3`。
`requested_space = "all"`，resolution `minor`。六个可搜索投影 `representable = true`。

| 依赖 | 原声明 | floor | predecessor |
| --- | --- | --- | --- |
| cyclopts | `>=4.10.2` | `4.10.2` | `4.9.0`（`failure-aaa2c0f9557f0869` @ 3.10） |
| packaging | `>=26.3` | `26.3` | `26.2`（`failure-3e161e47ce577038`） |
| pydantic | `>=2.6.4` | `2.6.4` | `2.5.3` |
| rich | `>=14.3.4` | `14.3.4` | `14.2.0` |
| tomlkit | `>=0.8.0` | `0.8.0` | `0.7.2` |
| tomli（3.10） | `>=2.0.2` | `2.0.2` | `1.2.3` |
| tomli（3.11/3.12） | `>=1.1.0` | `1.1.0` | `1.0.4` |

`search-space = "all"` **没有**找到更低的 minor 代表。终端完成卡片是 tomli predecessor
窗口，不是 floor。

packaging `26.2` 代表诊断：`tests/test_markers.py::TestPortableMarkers::test_contextual_full_expression_retains_host_patch_and_extra`
在 `--maxfail=1` 下退出 1。完整摘录见 [all-minor/diagnostics.txt](data/E015/all-minor/diagnostics.txt)。

| 指标 | 值 |
| --- | ---: |
| 搜索观察 | 159（ProbePass 54、ProbeRejection 105） |
| VERIFIER_EXITED_NONZERO | 88 |
| RESOLUTION_CONFLICT | 17 |
| candidate snapshots | 18 |
| process logs | 1511 |
| 最长 Cell | 6m17s（Python 3.12） |
| 命令墙钟 | 357.83s |

## 6. patch refine：同一 minor 内继续下降

因 apply 未写出，patch 轮的输入声明仍是产品原下界，与 all×minor floor 相同。
`minors[declaration]` × `patch` 报告 generation
`839cb4902299e62fe85351d67cd288390ace695eb810567f4cb4e81a121ae386`。
policy identity `659bc932c99e3280efcf6043618c684e55dd290420f9a656427cc3abe6db48e2`。

| 依赖 | all×minor / 原声明 | patch floor | predecessor |
| --- | --- | --- | --- |
| cyclopts | `4.10.2` | `4.10.0` | 空；系列最低候选 |
| packaging | `26.3` | `26.3` | 空 |
| pydantic | `2.6.4` | `2.6.0` | 空 |
| rich | `14.3.4` | `14.3.0` | 空 |
| tomlkit | `0.8.0` | `0.8.0` | 空 |
| tomli 3.10 | `2.0.2` | `2.0.0` | 空 |
| tomli 3.11/3.12 | `1.1.0` | `1.1.0` | 空 |

观察 39，全部 ProbePass，0 条 FailureRecord。最长 Cell 2m49s。命令墙钟 160.89s。
这些坐标在各自 declaration minor 内探到最低候选即通过；packaging / tomlkit / 3.11+
tomli 没有更低 patch。

## 7. 有价值的发现

1. **现行 targeted-runtime-contract 下，当前声明下界 check 可以通过。** 与 E001 把
   packaging 推到 `22.0` 的 full-repository 契约不是同一验证合同。
2. **`search-space = "all"` 没有压低任何 minor 代表。** 六个 floor 等于声明；更老
   major/minor 被 verifier 或解析冲突拒绝。
3. **patch refine 仍然有收益。** cyclopts `4.10.2→4.10.0`、pydantic `2.6.4→2.6.0`、
   rich `14.3.4→14.3.0`、tomli（3.10）`2.0.2→2.0.0` 是 minor 采样取系列最高代表造成的回退。
4. **在自己的仓库上跑 search 时，写入 `docs/` 会改变 SourceSnapshot。** 首次 all×minor
   因此退出 3；apply 在复制证据后同样漂移。成功重跑把终端放到 `/tmp`。这是自举操作约束，
   不是算法失败。
5. **完成卡片继续展示 predecessor。** all×minor 最后拒绝 tomli 1.2.3 / 1.0.4。

## 8. 固定证据与局限

- [smoke.txt](data/E015/smoke.txt) / [check.txt](data/E015/check.txt)
- [smoke-journal.json](data/E015/smoke-journal.json)、[check-journal.json](data/E015/check-journal.json)
- [configuration.toml](data/E015/configuration.toml)
- [all-minor/search.txt](data/E015/all-minor/search.txt)、[all-minor/search-summary.json](data/E015/all-minor/search-summary.json)
- [all-minor/search-attempt-1-drift.txt](data/E015/all-minor/search-attempt-1-drift.txt)
- [all-minor/apply.txt](data/E015/all-minor/apply.txt)、[all-minor/diagnostics.txt](data/E015/all-minor/diagnostics.txt)
- [patch-refine/search.txt](data/E015/patch-refine/search.txt)、[patch-refine/search-summary.json](data/E015/patch-refine/search-summary.json)
- [patch-refine/configuration.toml](data/E015/patch-refine/configuration.toml)

根目录 `package-floor.json` 已恢复为实验前 tracked 文件，不能用来复证本轮 generation
`c2deeee9…` / `839cb490…`。摘要不替代完整报告的离线授权。本轮报告曾写在根
`package-floor.json`，恢复后只留摘要。

本实验只证明所列 Linux target、三个 CPython minor、targeted-runtime-contract、`all` ×
minor 与 `minors[declaration]` × patch。不覆盖 `process` / `e2e` / 资格套件，也不把
patch floor 写成已经 apply 的产品声明。

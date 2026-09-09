# I002 — PF 自搜索：Python 3.10 墙钟、静态 intern 与 `unclosed-symlink`

- **状态：** 已完成
- **日期：** 2026-09-09（Asia/Shanghai）
- **性质：** 非规范性调查事实，不定义新契约，不授权实施
- **证据位置：** [data/I002/](data/I002/)（intern 摘要与 dump/read 计时）；完整 `package-floor-*.json` 留在仓库根、不入库。可变根 `package-floor.json` 不作历史证据链接
- **代码基线：** `2286197deface02b1e8b0a17244b7ad47ea2d27d`
- **规范对照：** [D003](../designs/D003-pf-search-algorithm.md)、[D004](../designs/D004-pf-ty-enhancement.md)、[D008](../designs/D008-pf-verification-run.md)、[D014](../designs/D014-pf-report-schema.md)
- **已归并决策：** [D038](../archived/designs/D038-pf-static-guidance-authority.md)
- **前序证据：** [E009](../experiments/E009-mkdocs-static-guidance.md)（MkDocs 上 3.11/3.12 的 `S_hi` 已是 `unclosed-symlink`）、[R008](../reviews/R008-pf-search-performance-review.md)

本次回答：在已隔离 bootstrap `test-command`（单次 verifier 墙钟小于 20s）的前提下，为什么 `pf search` 在 Python 3.10 Cell 上远慢于 3.11/3.12，以及采集器遇到指向未登记 root 的 symlink 就失败是否过严。

## 1. 结论

3.10 慢，主要不是 oracle pytest，也不是 40MB JSON 的 `json.dumps`。只有 3.10 的 highest 静态采集成功，打开了 `S_hi` / `S_slice`，并对每个 ty 观察散列、intern 解释器与 venv 文件树。3.11/3.12 的 `S_hi` 在 `static-subject-unavailable` / `unclosed-symlink` 上失败，每个坐标记 `NO_HINT(anchor-unavailable)`，整格只走 baseline + oracle。

`json.loads` / `json.dumps(sort_keys=True)` 对 42MB 报告约 0.2s。`ReportStore.read`（pydantic 校验、intern 解析、`_same_explicit_json`）对同一文件约 37s，对 29.6MB 三 Cell 报告约 23s。这能解释退出阶段「卡住几十秒到一两分钟」，解释不了 16–22 分钟对 5 分钟的搜索差。

`unclosed-symlink` 的 fail-closed 是故意的身份闭合规则：采集器不发现额外宿主 root，报告只保存逻辑根与 POSIX 相对路径。过严的是登记 root 太窄（可执行文件与 stdlib 目录，而不是 interpreter prefix），以及一处逃逸链接就让整份 `StaticSubject` 不可用；当前搜索又把第一次上端 `U` 设成这份失败的 baseline，于是 `S_hi` 失败等于整格关掉静态 guidance。

## 2. 宿主与输入

| 项 | 值 |
| --- | --- |
| OS | WSL2 `Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39` |
| 宿主 PF 解释器 | `.venv` CPython 3.10.16 |
| 仓库 HEAD | `2286197deface02b1e8b0a17244b7ad47ea2d27d` |
| 工作树 | dirty：相对 HEAD 为 `[tool.pf] pythons = ["3.12"]`（实验 C 后留下）；三份报告未跟踪 |
| `test-command` | `pytest --no-testmon --no-cov --maxfail=1 -m "not process and not e2e and not qualification and not infra"` |
| 操作者前提 | bootstrap 测试集已隔离，单次 `test-command` 小于 20s |

`requires-python` 为 `>=3.10,<3.13`。`tomli` 按 marker 分成 3.10 `>=2.0.2` 与 3.11/3.12 `>=1.1.0`。省略 `pythons` 时规划 3.10/3.11/3.12 三个 Cell。三次 search 改写了 `pythons`，因此三份报告的 `source_snapshot.digest` 不同，不能当作同一快照的重复测量。guidance / search 策略身份三次相同，并与 E009 记录的身份一致。

## 3. 三次 search

公开命令均为 `pf search`。完整报告不入库；计数来自 intern 表，见 [summary.json](data/I002/summary.json)。

| 实验 | Cell | 操作者 TTY | 现存卡片 | 报告 | 字节 / SHA-256 |
| --- | --- | --- | --- | --- | --- |
| A | 3.10+3.11+3.12 | 3.11 ≈ 5:31，3.12 ≈ 5:52，收尾 3.10 ≈ 15:47；全部结束后退出再卡几分钟 | 该段完整终端抄本未保存 | `package-floor-full.json` | 29,559,555；`38dc305ee94a1fdf…84fd7169` |
| B | 仅 3.10 | 操作者当时记「仍约 16min、退出卡顿」 | `0:21:44`；run-id `20260909T103419.709076Z-215637-e13fc9d2` | `package-floor-py310.json` | 41,929,806；`415f46186363c004…e6a96d06` |
| C | 仅 3.12 | 约 5min | `0:04:55`；run-id `20260909T112610.100427Z-232526-e0e182b7` | `package-floor-py312.json` | 1,841,455；`9b667ad9fc81e20d…1a185c22` |

三份报告 `result.status=complete`，各 Cell `search.status=SUCCESS`。`tomli` floor：3.10 为 `2.0.2`（predecessor `1.2.3`）；3.11/3.12 为 `1.1.0`（predecessor `1.0.4`）。这与声明 marker 一致，不能单独解释墙钟差。

实验 A 的 3.10 与实验 B 不是同一份静态工作量：A 的 3.10 scope 有 24 条 ty facts、4 次静态搜索；B 有 40 条 facts、6 次静态搜索。B 更长符合「静态采集次数更多」，不能用 A 的 15:47 去校正 B。

## 4. 报告 intern 对照

权威计数是 D014 intern 表，不是对 JSON 文本做子串 `grep`。子串会把 `static-search-` / `oracle-selection-` 等 ref 前缀重复计入。

| 指标 | A 三 Cell | B 仅 3.10 | C 仅 3.12 |
| --- | ---: | ---: | ---: |
| `static_contents` | 49 | **79** | 0 |
| intern 清单 `kind=file` | 58,265 | **95,558** | 0 |
| `kind=symlink` / `directory` | 96 / 6,709 | 160 / 11,055 | 0 / 0 |
| `static_subjects` | 24 | 40 | 0 |
| intern `ty-check` facts | 24（均在 3.10 scope） | **40** | 0 |
| 静态搜索 / `anchor-unavailable` 省略 | 4 / 14 | 6 / 0 | 0 / **6** |
| oracle `selections`（scope 合计） | 84+82+82=248 | 80 | 82 |
| `exact-vector` Attempt | 104 | 32 | 34 |
| DIRECT 观察 PASS/REJECTED | 3.10 19/29；3.11 18/28；3.12 18/28 | 18/26 | 18/28 |

3.11/3.12（A 与 C）的 scope：`highest_reference_ref` 为空，`highest_uncollected.detail=unclosed-symlink`，`facts=0`，`processes=0`。3.10（A 与 B）有 `highest_reference_ref`，`highest_uncollected` 为空，processes 为 `ty-*` 加少量 `verifier-*`。

oracle 次数没有差三倍。3.12 单 Cell 报告约 1.8MB，因为没有把文件树 intern 进去。3.10 单 Cell 报告（42MB）比三 Cell 报告（30MB）更大，因为 B 采了更多 unique content identities。

## 5. dump / `ReportStore.read` 计时

操作者于 2026-09-09 20:09 在宿主 3.10.16 上对已写出的 intern 报告计时，原始结果见 [dump-read.json](data/I002/dump-read.json)。

| 文件 | `json.loads` | `json.dumps(sort_keys)` | `ReportStore.read` |
| --- | ---: | ---: | ---: |
| py312 1.8MB | 0.010s | 0.010s | 0.142s |
| full 29.6MB | 0.151s | 0.143s | **22.544s** |
| py310 42.0MB | 0.172s | 0.198s | **36.916s** |

纯 JSON 编解码不是分钟级。`ReportStore.read` 与 intern 体积相关，是收尾路径的已知成本。

未单独计时、因此不能用本文件的秒数引用的路径：

- `VerificationJournal.model_dump`：每个 Cell 结束与 `finalize()` 都会 `intern_static_scopes`，序列化的是内存里的 `StaticSubject`，不是已经 intern 过的 dict。
- 每次 `StaticContentCollector.collect` 对 snapshot / venv / stdlib 的散列墙钟。
- `ReportStore.update_path` 若先读已有大报告再写。

`VerificationRunner.completed` 在发出 `CellCompletedEvent` **之前**持锁 `_persist()`。因此 3.10 Cell 卡片时间包含至少一次带静态 intern 的 Journal 写出；全部 Cell 结束后的 `finalize()` 与报告构建是退出卡顿的结构来源。数量级上，已测的 reader 是几十秒，不是搜索主因。

## 6. 机制

highest 先 `collect_prepared`（ty），再跑 `test-command`。坐标要开 `S_slice` 时，`open_static_slice` 必须 `find_pass(当前上端 U 的 Proposal)`。搜索开始时 `U = baseline`。

ty 采集失败时：`set_highest_uncollected(...)`，没有 `static_consumer`；pytest 仍可 PASS，但 `record_pass` 需要已有 consumer，故不登记 SliceAnchorPass。之后每个坐标 `find_pass is None` → `record_omission(anchor-unavailable)` → 不进入 `locate_static_hint`。

这与 D004 一致：没有合格完整观察时返回 `NO_HINT(anchor-unavailable)`。D038 写过「仅 `S_hi` 不可用不禁用 local guidance」，但本基线实现没有为 `S_slice` 再单独采 ty，所以 `S_hi` 失败在产品路径上等于整格无静态 hint。

登记 root 由 `StaticInputsAdapter.capture` 在 `-I -S` inspect 之后给出：`snapshot`、`environment`（venv）、`interpreter-executable`（`realpath(sys.executable)`，一个文件）、`interpreter-stdlib`、可选 `interpreter-library`（一个 libpython 文件）。`StaticContentCollector` 不把 root 再 `resolve()`，以免把未登记输入藏在 root symlink 后面。symlink 目标必须落在某个已登记 root 内，否则 `unclosed-symlink`。

E009 已在 MkDocs 隔离树上记录同一模式：3.8/3.9/3.11/3.12 的 `S_hi` 为 `unclosed-symlink`，当时 3.10 为 `invalid-layout`。本次 PF 自搜索 3.10 闭合成功，3.11/3.12 仍失败。这是解释器布局是否碰巧落进当前 root 集合，不是 3.10「更正确」。

## 7. `unclosed-symlink` 为何这样实现

采集器模块约定：调用方给出闭合不可变 root 集合；采集器不发现额外宿主 root，也不给不完整 manifest 授权。成功路径已经允许 **跨已登记 root** 的 symlink，并把目标写成逻辑 `StaticContentPath`；测试要求报告 JSON 不含宿主绝对路径。

契约侧同一约束：

- D004：六组输入变化必须改变投影，或拒绝未闭合外部输入；截断、坏 JSON、未闭合输入不能形成 compatibility boundary。
- D038：采集进程不得隐式继承未登记的环境变量、用户/父目录配置或可变外部搜索根；闭包失败 → `static-subject-unavailable` → `NO_HINT`，不启动该次 ty。
- `StaticContentManifest.validate_closure`：symlink 目标必须在 closure 内。

要防的是身份不可复现、报告泄漏宿主路径、以及把未声明分析输入洗成合法闭包。因此实现选 fail-closed，而不是跟随、截断或写入绝对路径。这与「尽量采集成功」的产品愿望冲突，但与可移植静态身份一致。

规则本身不应改成「跟随未知 symlink」。本机 3.11/3.12 失败，更像是 root 集合没有覆盖真实 CPython/venv 布局（`lib64`、stdlib `config-*`、`libpython.so`、venv `bin/python` 解析到 prefix 而不是那个可执行文件）。本调查没有 walk 出那一条逃逸链接。

## 8. 局限

- 实验 A 的精确 Cell 卡片秒数来自操作者当时读数，仓库没有保存该段 TTY。
- 三次 search 的 source snapshot 不同；A/B 的 3.10 静态 fact 数也不同。
- 没有逐次 ty `collect` 墙钟，也没有 live journal `model_dump` 墙钟。
- 没有定位 3.11/3.12 解释器上具体哪一条 symlink 逃出登记 root。
- 宿主与 Cell 解释器都是本机 uv CPython；结论不能外推到任意发行版布局。
- 单次 verifier <20s 是这次 dogfood 的前提。长 `test-command` 上，静态采集仍可能被 pytest 盖过；R008 关于「一般主导项是完整 test-command」的陈述不被本文件撤销。

## 9. 不授权的后续方向

本文不改生产代码。若要把结论变成产品行为，须先改相应 Design 并按 AGENTS.md 接受后再 Plan。候选方向只作索引，不是验收：

- 扩大登记 root（例如 interpreter prefix），保持「不发现未声明宿主树」。
- 实现 D038 已写的「`S_hi` 不可用时 local `S_slice` 仍可独立采集」。
- Journal/报告不要在每个 Cell 结束时把整棵未 intern 的文件树 `model_dump` 进关键路径；persist 与 `CellCompletedEvent` 的顺序。
- 给 R008 要的当前 HEAD 分阶段基线补上：每次 `collect` 文件数/散列墙钟、journal persist、`ReportStore.read`/`write`。

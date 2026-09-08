# E009 — MkDocs 静态 guidance 资格（check/search）

- **状态：** 已完成
- **日期：** 2026-09-08（Asia/Shanghai；run-id 使用 UTC）
- **性质：** 非规范性 dogfood 实验事实，不定义新契约
- **前序：** [E008](E008-mkdocs-complete-search.md)
- **规范：** [D038](../archived/designs/D038-pf-static-guidance-authority.md)、[P042](../archived/plans/P042-pf-static-guidance-authority.md)
- **目标：** 隔离副本 `/tmp/pf-d038-e009-mkdocs`，源 `/home/llh/pf/experiments/mkdocs`，MkDocs `1.6.1`，上游 commit `2862536793b3c67d9d83c33e0dd6d50a791928f8` 加本地 PF 实验配置
- **脚本：** `scripts/qualify_static_guidance.py`、`scripts/measure_d038_guidance.py`；公开 `CheckRequest` / `SearchRequest`，不发明产品 CLI `--project`

本轮在移除 witness 后重跑 E008 对应矩阵。check 证明 Python 3.8–3.12 的声明最低向量进入原完整 unittest 并 `NormalExit(0)`。search 五 Cell SUCCESS、complete；Markdown predecessor 均为 `configured-verifier`，final PASS。首轮 128 MiB 报告被 64 MiB 上限拒绝，不算 AC13；document intern 后重跑的 54.36 MiB 报告经 `ReportStore.read` 复证。

## 1. 输入与身份

隔离排除 `.pf`、`package-floor.json`、`.git`。工作区 `pyproject.toml` dirty 仅为 `[tool.pf]` / `pf-unit` 实验配置，不是实现回归。

```text
git HEAD:          2862536793b3c67d9d83c33e0dd6d50a791928f8
source snapshot:   4c3be6e0310545f6fb975effced8b7312bad8811e524ab626092915e175644be
execution policy:  968e10f44e5ddeeee80fbf71fbad8522c9b652ee881f67540112177115c243af
PF 解释器:         CPython 3.10.16
宿主 cwd:          /home/llh/pf
```

source snapshot digest 与 E008 相同。Journal 只保存 execution policy identity；guidance/search
身份在 search 报告 generation 中复证。

沿用 E008 的原完整 unittest：

```text
["python", "-m", "unittest", "discover", "-s", "mkdocs", "-p", "*tests.py"]
```

| 步骤 | 命令 | 输出 |
| --- | --- | --- |
| isolate | `.venv/bin/python scripts/qualify_static_guidance.py --mode isolate --root /home/llh/pf/experiments/mkdocs --output /tmp/pf-d038-e009-isolate.json`（实际 destination `/tmp/pf-d038-e009-mkdocs`） | [isolate.json](data/E009/isolate.json) |
| check | `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/qualify_static_guidance.py --mode check --root /tmp/pf-d038-e009-mkdocs --output /tmp/pf-d038-e009-check.json` | [check.json](data/E009/check.json) |
| measure | 同环境 `scripts/measure_d038_guidance.py` | [measurement.json](data/E009/measurement.json) |
| search | `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/qualify_static_guidance.py --mode search --root /tmp/pf-d038-e009-mkdocs --output /tmp/pf-d038-e009-search.json` | [search.json](data/E009/search.json)、[search-summary.json](data/E009/search-summary.json) |

## 2. Check（AC12）

run-id：`20260908T013259.640291Z-1649178-5c2d0cfa`。公开结果：`CheckPass`。Journal `entries=0`，五个 scope 均无 raw facts。

| Python | 角色 | 进入 verifier | Markdown | unittest | 静态最高采集 |
| --- | --- | --- | --- | --- | --- |
| 3.8 | declaration | 是 | 3.3.6 | Ran 725，`NormalExit(0)`，9.59s | `static-subject-unavailable` / `unclosed-symlink` |
| 3.9 | declaration | 是 | 3.3.6 | Ran 725，`NormalExit(0)`，9.59s | `static-subject-unavailable` / `unclosed-symlink` |
| 3.10 | declaration | 是 | 3.3.6 | Ran 725，`NormalExit(0)`，9.56s | `static-subject-unavailable` / `invalid-layout` |
| 3.11 | declaration | 是 | 3.3.6 | Ran 725，`NormalExit(0)`，9.36s | `static-subject-unavailable` / `unclosed-symlink` |
| 3.12 | declaration | 是 | 3.3.6 | Ran 725，`NormalExit(0)`，9.46s | `static-subject-unavailable` / `unclosed-symlink` |

E008 中 3.10–3.12 的同一声明向量被 witness 提前拒绝，未跑 unittest。本轮 ty 不可用仍进入原完整命令，且全部 PASS。这是 AC12 的直接证据，不是对任意环境 type-clean 的承诺。

check 进程日志：5 次 unittest、2 次 ty、其余主要为 uv/解释器探测。五份 unittest argv 均为上列原命令。

## 3. 受控对照

`measure_d038_guidance.py` 只包装 `open_static_slice`，不是产品开关。floor∈{1,2,3} × unavailable∈{None,1} 六组 guided/mechanical 的 floor 与 status 均相同，`all_floors_match: true`。计数来自 scripted adapters，不是 MkDocs wall time。

## 4. Search（AC13）

同一隔离树、同一 source snapshot。资格脚本 exit 0；`report_roundtrip: true`。

| 项 | 值 |
| --- | --- |
| 有效 search run-id | `20260908T022518.098497Z-1722063-e8888b4a` |
| report generation | `a5c47dc73443ee5b890c4fe49e26baabe3e6bb3db252722b4413a882af8fc9c2` |
| 报告字节 / SHA-256 | 57,004,655；`5af3ca77d84996c248b95aaeacd97e8aa075e64b0270e320f02942c8c99f0ea2` |
| D014 读上限 | 64 MiB = 67,108,864；本报告低于上限 |
| intern 表 | contents 82、subjects 40、facts 40、comparisons 59、scopes 5 |
| wall | 2,454,196 ms；卡片 3.8=7:05、3.9=7:07、3.11=7:47、3.12=9:25、3.10=37:54 |
| guidance / search 身份 | `efb23cb3ec88ed638a7ab73eee5a8136e9e583ac9aba6c29d1b861b39a8f3c63` / `efef7b09e9c81c3f21924d55126190467f2d9e0ccd288e7909688cf4bee25ab4` |

五 Cell 均为 SUCCESS，final `PASS` + `NormalExit(0)`。Markdown floor 全部 **3.3.7**，predecessor **3.2.2**，`authority.kind=configured-verifier`。E008 中 3.10–3.12 的 Markdown floor 曾是 3.4.4（predecessor 用了有缺陷的 witness）；本轮 reader 复证的是动态 verifier authority，不是 static/witness。终端卡片上的 `[markdown=3.2.2]` 是探测窗口，不是 final floor。

`ReportStore.read` 确认 facts 无嵌入 `observation.subject`，consumers.preparation 无 `subject`。完整报告留在隔离树 `/tmp/pf-d038-e009-mkdocs/package-floor.json`，仓库只冻结资格 JSON 与摘要（与 E008 不把 14 MiB 报告纳入 git 的做法一致）。可变根 `package-floor.json` 不作历史证据链接。

首轮 search run-id `20260908T013538.533995Z-1654191-fdfd05bd` 产品侧也曾 5/5 SUCCESS，但 intern 嵌入完整 StaticSubject，报告 128 MiB，`ReportStore.read` 以 `unsupported-report-contract` / 64 MiB 上限拒绝。该文件保留为 `/tmp/pf-d038-e009-package-floor-pre-intern.json`，**不是** AC13 证据，未手改成成功。

S_hi 在本隔离树仍为 `static-subject-unavailable`。五个 scope 中四个 `facts=0`（大量 omission/skip/mechanical selection）；一个 scope `facts=40`、`comparisons=59`、`searches=13`。AC13 要求动态 authority，不要求 hint 有效。E008 的 18.9% 仍只是固定 probe 算例，不是本轮 wall time 预测。

## 5. 局限

- 完整 54 MiB 报告与 Journal 未复制进 git；身份以 SHA-256、generation id 与资格 JSON 为准。
- 3.10 卡片 37:54，显著长于其他 Cell；未把 wall time 差解释成 guidance 收益或亏损。
- 受控 measure 的计数来自 scripted adapters，不能替代本轮 MkDocs 进程时间。

# E017 — xarray core 搜索实验计划

- **状态：** 已完成
- **日期：** 2026-09-12
- **性质：** 非规范性实验协议与执行计划；不定义新契约，不构成对 xarray 上游声明的修改建议
- **证据位置：** [.](.)
- **报告：** [../../E017-xarray-core-complete-search.md](../../E017-xarray-core-complete-search.md)
- **契约入口：** [D001](../../../designs/D001-pf.md)、[D003](../../../designs/D003-pf-search-algorithm.md)、
  [D005](../../../designs/D005-pf-failure-and-diagnose.md)、[D008](../../../designs/D008-pf-verification-run.md)、
  [D012](../../../designs/D012-pf-harness-relaxation.md)、[D013](../../../designs/D013-pf-pytest-observer.md)、
  [D014](../../../designs/D014-pf-report-schema.md)、[D037](../../../designs/D037-pf-candidate-search-policy.md)
- **对照：** [E012](../../E012-flask-complete-search.md)、[E013](../../E013-requests-complete-search.md)、
  [E014](../../E014-mkdocs-complete-search.md)；[E016](../../E016-pf-linear-search-control.md) 不在本轮执行

本计划固定 xarray **core / upstream bare-minimum** 环境下的 PF 完整搜索。
只搜索 `numpy`、`packaging`、`pandas` 三个 runtime 坐标。不声称 netCDF、Dask 或全部后端兼容。
不为凑 PASS 裁剪上游测试、不加人工上界、不改 PF 契约。`check` 在声明下界失败是观测，不是准入否决。
最高 baseline 失败或 oracle 不稳定才停止进入 search。

## 1. 冻结身份

| 项 | 冻结值 |
| --- | --- |
| 目标树 | `experiments/xarray` |
| 上游 tag | `v2026.07.0` |
| 上游 commit | `0238035a646a04a6d2b603cc5cee5cbefa304e23` |
| 声明 | `requires-python = ">=3.11"`；`numpy>=1.26`、`packaging>=24.2`、`pandas>=2.2` |
| 上游 core lane | pixi `test-py311-bare-minimum` = features `["test", "minimal"]` |
| PF | generator `0.4.0`；执行时记录实际 HEAD |
| 工具 | uv `0.12.5`、ty `0.0.74`（D001 发行固定） |
| 宿主 | Linux、`x86_64-unknown-linux-gnu` |
| 解释器（阶段 1） | CPython 3.11 |
| 解释器（阶段 2+） | CPython 3.11 / 3.12 / 3.13（与 classifiers 对齐） |
| 允许的本地改动 | 仅 `pf-core` dependency group 与 `[tool.pf]` |

不使用浮动 `main`。当前克隆若仍在 `main`，先 checkout 上述 tag。
不把 conda/pixi 的 `packaging = 24.2.*` 抄进 PF 声明。

## 2. 配置

省略 `test-group` 会按 D001 命中 `dev`，而 `dev` 含 `xarray[complete,types]`，会把可选闭包拉进 harness。
必须新建独立 group。`extra-policy = "none"` 只保留 required base，避免 accel/io/parallel/viz Cell。

阶段 1：

```toml
[dependency-groups]
pf-core = [
  "pytest",
  "pytest-asyncio",
  "pytest-env",
  "pytest-mypy-plugins>=4.0.0",
  "pytest-timeout",
  "hypothesis",
  "pytz",
  "tzdata",
]

[tool.pf]
pythons = ["3.11"]
test-group = "pf-core"
extra-policy = "none"
test-command = ["pytest", "--timeout", "180"]
resolve-artifact = "any"
test-timeout = "2h"
search-space = "all"
search-resolution = "minor"
```

阶段 2 把 `pythons` 改为 `["3.11", "3.12", "3.13"]`。
阶段 4 把搜索改为 `search-space = "minors[declaration]"`、`search-resolution = "patch"`。

| 项 | 取值 | 原因 |
| --- | --- | --- |
| harness | 上游 `dev`/`feature.test` 的工具子集 | D012 不搜 harness 版本；`pytest-mypy-plugins` 与 `pytest-asyncio` 让 ini `addopts` 合法 |
| 不进入 harness | `xarray[complete,types]`、mypy、sphinx、ruff、pre-commit、pytest-cov | 避免可选闭包与 coverage 副作用 |
| `test-command` | `pytest --timeout 180` | 与上游 CI 单测超时一致；不传 `--run-flaky` / `--run-network-tests` / `--run-mypy` / `-n` / `-k` / `--ignore` |
| `resolve-artifact` | `any`（计划偏差） | 阶段 1 实测 `wheel` 使 uv `--only-binary :all:` 连本地 xarray 源码快照也不能构建；改回 `any`，旧 sdist/ABI 失败单独分类，不写成 API 下界 |
| 搜索坐标 | 三个 runtime，不设 `managed-deps` | packaging 也是坐标 |
| `test-timeout` | `2h` | 进入 ExecutionPolicy，search 前冻结；默认 30 分钟不够一次完整 smoke |
| search 时限 | `--max-duration 12h` | 超时写 incomplete，不改 oracle |

验证契约是上游完整 pytest collection（`testpaths = ["xarray/tests", "properties"]`）。
`conftest.py` 默认跳过 flaky / network / mypy / slow_hypothesis。保留 `filterwarnings = error:::xarray.*`。
论文与报告必须写成 **core / upstream bare-minimum**。候选域是 `resolve-artifact = "any"` 下的 registry 解析结果，不是纯 wheel 域；安装/构建失败不得写成 API 不兼容下界。

### 2.1 阶段 1 配置偏差（2026-09-12）

第一次 `pf smoke`（run-id `20260912T050713.649383Z-204303-8eb7f92e`）在
`resolve-project` 以 `RESOLUTION_FAILED` 拒绝：`Building source distributions for xarray is disabled`。
证据：[admission/smoke-1/](admission/smoke-1/)。
D001 的 `resolve-artifact` 统一约束 project/environment 与 Candidate，因此 `wheel` 不能用于必须从快照构建的 target。
已改为 `any`，不修改 PF 契约。

第二次 smoke（run-id `20260912T050802.214872Z-205432-aa1900a0`）在收集
`xarray/tests/test_pandas_to_xarray.py` 时因 `tzdata` / `US/Pacific` 缺失失败；当时已收集约 11013 项。
本机 uv CPython 无系统时区数据。已把 `tzdata` 加入 `pf-core` harness，不是搜索坐标，也不删测试。
证据：[admission/smoke-2/](admission/smoke-2/)。

## 3. 执行流程

由 PF 仓库根、沙箱外执行。`cwd=experiments/xarray`，`PATH` 前缀 `.venv/bin`，
`UV_CACHE_DIR=/tmp/pf-uv-cache`。记录脚本为 `docs/experiments/data/E017/run_pf.py`。
阶段 1 使用 `test-jobs=1`；阶段 2 以后可用 `auto`。不对 pytest 加 `-n`。

| 阶段 | 命令 | 通过条件 | 失败分流 |
| --- | --- | --- | --- |
| 0 冻结 | checkout tag；只写实验配置 | 除实验配置外工作树干净 | 先停，不跑 PF |
| 1 准入 smoke | 仅 3.11，连续两次 `pf smoke` | 两次 PASS，collection/skip 集合一致 | 配置/插件：改 harness；wheel/ABI：记录并停或只保留有 wheel 的 Python；源码与最高依赖不兼容：记录并停，不加上界、不删测试 |
| 1b check | 同配置 `pf check` | 完成即可；REJECTED 保留为观测 | INDETERMINATE 或 oracle 不稳定：不进入 search |
| 1c 独立性 | 若有 Rejection，抽样失败 nodeid 单独重放 | 重放不互相污染 | 不满足 D001 负向 oracle 则停 search |
| 2 扩 Cell | `pythons` 扩到 3.11–3.13，再 smoke/check | 各 Cell 最高版本 PASS | 某一 Python 无适用 wheel 则记录并从矩阵移除，不假装全矩阵成功 |
| 3 粗搜 | `all` × `minor`：`pf search --max-duration 12h` → `pf apply` | complete 或原样留下 incomplete | `NO_PASS_IN_SEARCH_SPACE` 按 E014 解释为可达性，不当成联合空间无解 |
| 4 细化 | `minors[declaration]` × `patch` 再 search；成功才 apply | 记录 complete/incomplete | 同阶段 3 |
| 5 闭环 | apply 后再 `pf check` | 记录通过或失败 | 不回写上游声明；apply diff 不等于已验证 |

门控：阶段 1 的最高 baseline 未稳定 PASS，不得进入阶段 3。
不为候选扩 PF 契约。本轮不执行 E016 线性对照。

### 3.1 执行结果（2026-09-12）

阶段 1 三次 smoke 均未 PASS。最后一次在 `pandas==3.0.5` 上于
`xarray/tests/test_dataset.py::TestDataset::test_repr` 被 verifier 拒绝
（期待 category `36B`，实际 `32B`）。`origin/main` 复现相同失败。
已停止；阶段 2–5 未执行。结论见正式报告。

### 3.2 本地补丁后继续（2026-09-12）

用户授权修复该金串后再搜索。补丁见
[local-patch/NOTE.md](local-patch/NOTE.md)。
源码快照因此变化；后续 smoke/check/search 属于新运行，不回写 §3.1。
阶段 1 在 3.11 上重做；通过后按原计划进入 check 与 `all` × `minor` search。

补丁后第一次 smoke 又在 `test_tree_index_rename` 因缺 scipy 失败
（run-id `20260912T051550.717882Z-213331-0a3d8294`）。同文件其余用例已有
`@requires_scipy`。已补同一标记，见 local-patch NOTE §2。

补丁后两次 3.11 smoke PASS；原声明 check REJECTED（pandas 2.2 × pyarrow warning）；
all×minor search complete 并 apply；patch refine incomplete（numpy 1.25.2 与
pandas 3.0.5 冲突）；apply 后 check PASS。详见正式报告 §9–§14。未扩 3.12/3.13。

## 4. 观测与产物

证据根目录：`docs/experiments/data/E017/`。
可变 `experiments/xarray/package-floor.json` 只作当次 `ReportStore.read`，不作历史入链。

每条命令写入：

- `{cmd}.txt`、`{cmd}.meta.json`（argv、cwd、起止、墙钟、退出码、`UV_CACHE_DIR`）
- 该阶段 `configuration.toml`
- Journal、`search-summary.json`（经 reader，不手改）
- source snapshot digest、execution/guidance/search policy digest
- PF HEAD、uv/ty、实际 CPython patch、平台 triple

xarray 额外记录：

| 观测 | 何时 | 用途 |
| --- | --- | --- |
| passed / skipped / xfailed / 总数 | 每次 smoke/check/baseline | skip 变化即 oracle 变化 |
| skip 原因直方图 | 第一次稳定 smoke | 证明 core，不是裁套件 |
| 两次 smoke 墙钟与 skip 差 | 阶段 1 | 稳定性 |
| 候选快照与无 wheel / yanked | 第一次 search | D037 域边界 |
| 失败分类：resolve / install / ABI-unavailable / REJECTED / INDETERMINATE | 每个非 PASS | 安装失败 ≠ API 下界 |
| 代表 predecessor 的 `pf diagnose` | search 后 | 摘要不替代 Process Log |
| apply diff 与 apply 后 check | 阶段 3–5 | 维护闭环 |

阶段 3/4 的 incomplete 原样归档。不把 CI `--lf`、xdist 墙钟或 skip 变少后的 PASS 写成成功证据。

## 5. 可主张与不可主张

完成后报告可以主张：在记录的 Cell、wheel 候选域和完整 configured pytest 下，PF 对 xarray core 三个坐标执行了 smoke/check/search（及实际发生的 apply/闭环）。

不可以主张：xarray 全部 extras/后端兼容、`>= floor` 的所有组合可用、生态普遍单调或弱耦合、安装失败等于 API 不兼容、本轮证明了 E016 的 hole 率。

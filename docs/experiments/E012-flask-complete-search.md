# E012 — Flask smoke/check/search/apply 完整实验

- **状态：** 已完成
- **日期：** 2026-09-11
- **性质：** 非规范性 dogfood 实验事实，不定义新契约，不构成对 Flask 上游声明的修改建议
- **证据位置：** [data/E012/](data/E012/)（终端日志、命令元数据、配置副本、报告摘要、代表诊断与 apply 差分）
- **目标：** `experiments/flask`，Flask `3.2.0.dev`，上游 commit
  `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35`（2026-09-08）加本地 `[tool.pf]`
- **PF：** generator `0.4.0`；HEAD `0b2d14d59dfdc04f31229e832e5908e4aca76948`
- **契约入口：** [D001](../designs/D001-pf.md)、[D003](../designs/D003-pf-search-algorithm.md)、
  [D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、
  [D012](../designs/D012-pf-harness-relaxation.md)、[D014](../designs/D014-pf-report-schema.md)、
  [D037](../designs/D037-pf-candidate-search-policy.md)

本实验包含两轮独立搜索。第一轮在 Flask 原始声明上跑 `pf smoke → pf check → pf search → pf apply`，
搜索空间省略，命中 `majors[declaration-1:]` × `minor`。第二轮先恢复原始声明，用显式
`search-space = "all"` × `minor` 再搜索并 apply，然后把空间收到 `minors[declaration]`、
`search-resolution = "patch"` 做细化。

第一轮 smoke **5/5 PASS**；check **5/5 PASS**；search **5/5 SUCCESS、complete**；apply 退出 0。
第二轮 all×minor 的六个 floor 与第一轮相同，但探针更多；patch refine 把 werkzeug / markupsafe
降回原声明下界 `3.1.0` / `2.1.1`，并将其余坐标降到各自 declaration minor 内的最低合格 patch。
终端卡片上的版本是 predecessor 窗口，不是 floor。

与 [E006](E006-requests-complete-search.md)、[E008](E008-mkdocs-complete-search.md) 不同：
Flask 的原始声明下界在本验证契约下已经通过。第一轮 / all×minor 仍把 click / jinja2 /
itsdangerous / blinker 降到声明之前的 minor 代表，并把 werkzeug / markupsafe 抬到当前 minor
系列的最高合格精确版本。后者是 `search-resolution = minor` 的采样结果，不能解释成声明下界
`2.1.1` / `3.1.0` 未通过 check。patch refine 随后在这些 minor 内把后两项降回原声明。

## 1. 兼容性结论与实验配置

Flask 是单包 flit 项目，`requires-python = ">=3.10"`，六个 runtime 依赖均带 `>=` 下界，测试命令是
pytest。PF 可以把它当作可安装 root。不兼容点集中在 **group / extra / 解释器矩阵**，不是构建后端。

| 观察 | 对 PF 的影响 | 本轮处理 |
| --- | --- | --- |
| 存在 `dev`（ruff/tox）和 `tests`（pytest/asgiref/python-dotenv），没有 `test` | 省略 `test-group` 会按 D001 先命中 `dev`，pytest 不会进入 harness | 显式 `test-group = "tests"` |
| extras `async` / `dotenv` 非空；测试用 `importorskip` / `skipif` | 默认 `extra-policy = "each"` 会变成 5×3 Cells；asgiref 与 python-dotenv 又已在 `tests` group | `extra-policy = "none"`，只搜索六个 runtime 坐标 |
| CI 含 3.15 prerelease、3.14t、PyPy | PF v1 不支持 | `pythons = ["3.10", "3.11", "3.12", "3.13", "3.14"]`，与本机可用稳定 CPython 对齐 |
| tox `tests-min` 把声明下界钉死在 3.14 | check 有机会对声明下界给出正向证据 | 不改声明，先跑 check |
| `filterwarnings = ["error"]` | 旧依赖的 warning 会变成 verifier 拒绝 | 保留上游 pytest 配置 |

加入的配置（副本：[configuration.toml](data/E012/configuration.toml)）：

```toml
[tool.pf]
pythons = ["3.10", "3.11", "3.12", "3.13", "3.14"]
test-group = "tests"
extra-policy = "none"
test-command = ["pytest"]
resolve-artifact = "any"
```

搜索空间省略，命中 D037 内建条件默认 `majors[declaration-1:]` × `search-resolution = minor`。
六个依赖都有声明下界。每个 minor 系列取最高合格精确 release 作为代表。第一轮未做 patch refine；
第二轮见 §8–§9。

验证契约是 Flask 仓库完整 pytest（`testpaths = ["tests"]`），含 async/dotenv 用例：`tests` group
会安装 asgiref 与 python-dotenv。`tests/type_check/` 不以 `test_*.py` 收集。未缩小测试路径。

## 2. 实验计划（已执行）

第一轮：

1. 读取 Flask 元数据、tox/CI 与 PF 配置契约，判断默认配置会选错 group。
2. 只改 `experiments/flask/pyproject.toml` 的 `[tool.pf]`；不改 Flask 测试或生产实现。
3. 由 PF 仓库根、沙箱外、cwd=`experiments/flask` 顺序执行 smoke → check → search → apply。
4. 用 `ReportStore.read` 核验 `package-floor.json`，抽取投影与边界，对代表 predecessor 做
   `pf diagnose`。
5. 把终端、身份、摘要和 apply 差分写入 `docs/experiments/data/E012/`。

第二轮：

1. 恢复 Flask 原始 runtime 声明，设置 `search-space = "all"`、`search-resolution = "minor"`。
2. 执行 search 并 apply，得到全空间 minor 代表作为中间声明。
3. 改成 `search-space = "minors[declaration]"`、`search-resolution = "patch"` 再 search 并 apply。
4. 把两阶段证据写入 `data/E012/all-minor/` 与 `data/E012/patch-refine/`，更新本报告。

调用方式与 [E006](E006-requests-complete-search.md) 相同：`PATH` 前缀 `.venv/bin`，
`UV_CACHE_DIR=/tmp/pf-uv-cache`。记录脚本为 [run_pf.py](data/E012/run_pf.py)。

## 3. 运行总表

三次验证命令共享 source snapshot
`e1877751160fdf112ecb3b5c01608c5fa17505f1be92a7f90d028e95c0767c7a` 与 execution policy
`836ec5e859f4d1dea75b80a901fe70720fb84b87d9eee92f96fe3a266119bc25`。apply 只编辑元数据，
不形成新的 Verification Run。

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| smoke | `20260911T083507.025243Z-470581-3491a76e` | 0 | 6.04s | 5/5 PASS |
| check | `20260911T083525.475715Z-471436-493fc50c` | 0 | 7.30s | 5/5 PASS，`declaration` / `lowest-direct` |
| search | `20260911T083625.392659Z-472754-04f7b6a3` | 0 | 123.11s | 5/5 SUCCESS，`package-floor.json` complete |
| apply | （无 Verification Run） | 0 | 0.67s | 写回 `pyproject.toml` |

矩阵：CPython **3.10–3.14** × `x86_64-unknown-linux-gnu` × `no-extra`，**5 Cells、6 个受管直接依赖、0 pinned**。
解释器实测为 3.10.16 / 3.11.15 / 3.12.3 / 3.13.15 / 3.14.7。

## 4. smoke：最高版本通过

终端 `Smoke passed · 5 cells`。Journal 0 条失败。五个 Cell 均在 `baseline` / `highest` 通过。
configured pytest 收集 494 项；3.11–3.14 为 `494 passed`，3.10 为 `493 passed, 1 skipped`。
`tests` group 已安装 asgiref 与 python-dotenv，async/dotenv 用例会实际执行。
单 Cell 墙钟约 7s，命令墙钟因并行小于各 Cell 之和。

search 报告记录的最高 baseline 向量在五个 Cell 上相同：

```text
blinker=1.9.0  click=8.5.0  itsdangerous=2.2.0
jinja2=3.1.6   markupsafe=3.0.3  werkzeug=3.1.8
```

## 5. check：原始声明下界通过

终端 `Check passed · 5 cells`。五个 Cell 均为 `check passed at [declaration][lowest-direct]`。
Journal 0 条失败。原始声明为：

```text
blinker>=1.9.0
click>=8.1.3
itsdangerous>=2.2.0
jinja2>=3.1.2
markupsafe>=2.1.1
werkzeug>=3.1.0
```

这与 Flask tox `tests-min` 在 3.14 上钉死同一组下界一致：本轮证据说明这些下界在 3.10–3.14、
完整 pytest、`tests` harness 下可以同时成立。check 通过**不能**推出 search 不会再移动坐标；
search 的空间包含声明前一个 major，采样又取 minor 系列代表。

## 6. 第一轮 search：条件默认空间 × minor

`ReportStore.read` 得到 `result.status = complete`、5 个 `CellSuccess`、各 2 个 sweep。
报告 generation `55d003ab7548013677f60185f739e2f6c48b135bd62a1b6f2ce2416f1959d063`，
SHA-256 `d81874683938e5c1f0f52f3662d9c33b2a9ec7d9118a22793cab9368008c2efe`（615,332 字节）。
policy identity `fd39c1ea8d4e805b98d515501efb086d91bb5b24cb91891015d38816707024d3`。
六个投影 `representable = true`。

终端每个 Cell 都写 `search completed at [jinja2=2.11.3][oracle 2.11.3~3.0.3#2]`。
这是 jinja2 坐标的 predecessor 窗口，**floor 是 3.0.3**。与 E008 的 witness/卡片偏差同类：
完成卡片展示最后拒绝的探测，不展示已提交向量。

### 6.1 投影对照

| 依赖 | 原声明 | 各 Cell floor | apply 投影 | 相对声明 |
| --- | --- | --- | --- | --- |
| blinker | `>=1.9.0` | 3.10–3.13：`1.6.3`；3.14：`1.7.0` | 带 `python_version` 的两行 | 降低，且 3.14 更高 |
| click | `>=8.1.3` | `8.0.4` | `click>=8.0.4` | 降低 |
| itsdangerous | `>=2.2.0` | `2.0.1` | `itsdangerous>=2.0.1` | 降低 |
| jinja2 | `>=3.1.2` | `3.0.3` | `jinja2>=3.0.3` | 降低 |
| markupsafe | `>=2.1.1` | `2.1.5` | `markupsafe>=2.1.5` | **升高**（2.1 系列代表） |
| werkzeug | `>=3.1.0` | `3.1.8` | `werkzeug>=3.1.8` | **升高**（3.1 系列代表，等于 baseline） |

最终向量在 3.10–3.13 上为：

```text
blinker=1.6.3  click=8.0.4  itsdangerous=2.0.1
jinja2=3.0.3   markupsafe=2.1.5  werkzeug=3.1.8
```

3.14 仅 blinker 换成 `1.7.0`。

### 6.2 规模

| 指标 | 值 |
| --- | ---: |
| 搜索观察 | 179（ProbePass 85、ProbeRejection 94） |
| Cell 内 FailureRecord | 94（与 Journal 94 条一致） |
| 其中 verifier 非零 | 69 |
| RESOLUTION_FAILED | 15 |
| RESOLUTION_CONFLICT | 10 |
| candidate snapshots | 30 |
| process logs | 1296 |
| 最长 Cell | 2m13s（Python 3.10） |

### 6.3 代表 predecessor

完整摘录见 [diagnostics.txt](data/E012/diagnostics.txt)。

- **werkzeug 3.0.6**（`failure-0d45dbe2faca30ed`）：`sessions.py` 向
  `Response.set_cookie` 传入 `partitioned=`，3.0.6 无此参数，
  `tests/test_basic.py::test_session_accessed` 以 `TypeError` 退出 1。3.1 系列最高合格版本是
  3.1.8，因此 floor 等于 baseline，不是 check 所用的 3.1.0。
- **markupsafe 2.0.1**（`failure-d5c32569421e97ba`）：在已提交 `werkzeug==3.1.8` 时，
  uv 报告 `werkzeug==3.1.8 depends on markupsafe>=2.1.1`，与探针 `markupsafe==2.0.1` 冲突。
  这是系列代表带来的联合约束，**不否定** check 上 `markupsafe==2.1.1` 与 `werkzeug==3.1.0`
  的组合。
- **jinja2 2.11.3**（`failure-16ba123076a03978`）：与已提交 `markupsafe==2.1.5` 组合时，
  `jinja2/filters.py` 无法从 markupsafe 导入 `soft_unicode`，pytest 收集阶段退出 4。
- **click 7.1.2**（`failure-ad97958322a90db1`）：`flask.cli` 需要 `click.core.ParameterSource`，
  收集失败退出 4。floor 落在 8.0 系列代表 `8.0.4`。
- **blinker 1.6.3 @ 3.14**（`failure-031696120264919d`）：pruned
  `tests/test_appctx.py::test_robust_teardown` 在 ExceptionGroup 路径上 `assert 2 == 4`。
  同一版本在 3.10–3.13 是 floor。

## 7. 第一轮 apply：授权写回条件默认 floor

apply 退出 0，终端 `Applied floors · project updated`。范围是全部已声明平台。写回差分见
[apply.diff](data/E012/apply.diff)。依赖数组变为：

```toml
dependencies = [
    "blinker>=1.6.3; python_version == \"3.10\" or python_version == \"3.11\" or python_version == \"3.12\" or python_version == \"3.13\"",
    "blinker>=1.7.0; python_version == \"3.14\"",
    "click>=8.0.4",
    "itsdangerous>=2.0.1",
    "jinja2>=3.0.3",
    "markupsafe>=2.1.5",
    "werkzeug>=3.1.8",
]
```

这是第一轮实验克隆上的授权编辑。第二轮开始前已恢复原始声明；最终工作树以 §9 的 patch
投影为准。它把测试可走通的版本写成 `>=` 下界，**不是** Flask 对用户必须提高或放宽支持范围
的产品结论。`[tool.pf]` 仍保留在同一文件中，属于实验配置，不是 apply 的依赖投影。

## 8. 第二轮：`all` × minor

第二轮先把 runtime 声明恢复为 Flask 原始下界，再设置：

```toml
search-space = "all"
search-resolution = "minor"
```

配置副本：[all-minor/configuration.toml](data/E012/all-minor/configuration.toml)。
这是一次独立 invocation，新 snapshot
`512445c273343ff5c4ac07d908e217b40aa83b9c14ec78f79c21117133cd2b2f`，不复用第一轮
evaluation cache。

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| search | `20260911T084517.157103Z-495841-a524b339` | 0 | 206.53s | 5/5 SUCCESS，complete |
| apply | （无 Verification Run） | 0 | 0.77s | 写回与第一轮相同的 minor 投影 |

报告 generation `41997c8d0eeb9da7dfc2763547ac7ce149ee459e19dd687c29243378a1fc58eb`，
SHA-256 `fb753f442695c16cc6551017d75b0d6b9148838e2320010a99d6ee15079b764f`（1,012,942 字节）。
policy identity `1e35bcbf8038ecc7a3c35142754f315f902873cae85e6e27352d8f08e1518198`。
`requested_space = "all"`，resolution `minor`。六个投影仍全部可表示，逐 Cell floor 与第一轮
逐字节相同：

```text
3.10–3.13: blinker=1.6.3  click=8.0.4  itsdangerous=2.0.1
           jinja2=3.0.3   markupsafe=2.1.5  werkzeug=3.1.8
3.14:      blinker=1.7.0  （其余同上）
```

predecessor 也相同：blinker `1.5`（3.14 为 `1.6.3`）、click `7.1.2`、itsdangerous `1.1.0`、
jinja2 `2.11.3`、markupsafe `2.0.1`、werkzeug `3.0.6`。放宽到 `all` **没有**找到更低的 minor
代表；更旧的 major 仍被拒绝或无法解析。

成本更高：观察 249（Pass 90 / Rejection 159），FailureRecord 159，process logs 1929，最长 Cell
3m40s。相对第一轮的 179 观察 / 94 失败 / 2m13s，多出来的探针都花在已经会被 `majors[declaration-1:]`
裁掉的更老系列上。

## 9. 第二轮：`minors[declaration]` × patch

all×minor apply 之后，中间声明就是上一节的投影。随后把配置改为：

```toml
search-space = "minors[declaration]"
search-resolution = "patch"
```

配置副本：[patch-refine/configuration.toml](data/E012/patch-refine/configuration.toml)。
新 snapshot `da32b0f422bb3dd3951b19243b6def18607117a57cd3bc05c71f5a1976c2a8c1`。

前两次 search 在 baseline pytest 通过之后、候选发现阶段以 `SOURCE_FAILURE` /
`candidate-discovery-failed` 结束（run-id
`20260911T084949.622979Z-523825-c2d1ddfb` 退出 4；
`20260911T085121.808541Z-526500-e1abcffd` 退出 3，并报
`report generation identity changed while rebuilding roots`）。当时本机对
`https://pypi.org/simple/<name>/` 的 urllib 请求全部
`SSL: UNEXPECTED_EOF_WHILE_READING`。这是 registry 可达性问题，不是 Flask 不兼容。
删除不完整报告并等 PyPI 恢复后重跑。

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| search（成功） | `20260911T085355.235820Z-528701-11a24044` | 0 | 65.18s | 5/5 SUCCESS，complete |
| apply | （无 Verification Run） | 0 | 0.71s | 写回 patch floor |

报告 generation `80af19c73361ae4e1285065509165333431f1f64cceaa080d8d44555caa6231c`，
SHA-256 `31d3b0acc448b6c0747fe1b30299a4ab23861e55c0315b9393ffba7fc3c46688`（341,734 字节）。
policy identity `6d49fb1671f297584c5c96e8760f4e029d6fbfc1d0685c5e44e6eb55dec34c48`。

| 依赖 | 中间声明（minor 代表） | patch floor | 相对 Flask 原声明 |
| --- | --- | --- | --- |
| blinker | `>=1.6.3`（3.10–3.13）/ `>=1.7.0`（3.14） | `1.6` / `1.7.0` | 低于 `>=1.9.0`；3.14 仍更高 |
| click | `>=8.0.4` | `8.0.0` | 低于 `>=8.1.3` |
| itsdangerous | `>=2.0.1` | `2.0.0` | 低于 `>=2.2.0` |
| jinja2 | `>=3.0.3` | `3.0.0` | 低于 `>=3.1.2` |
| markupsafe | `>=2.1.5` | `2.1.1` | **等于**原 `>=2.1.1` |
| werkzeug | `>=3.1.8` | `3.1.0` | **等于**原 `>=3.1.0` |

除 markupsafe 外，各坐标都探到该 minor 内最低候选，predecessor 为空。唯一边界是
markupsafe `2.1.0`（`failure-9106ec381027aa5d` 等 5 个 Cell）：`werkzeug==3.1.0 depends on
markupsafe>=2.1.1`，与探针 `markupsafe==2.1.0` 冲突。观察 95（Pass 85 / Rejection 10），
10 条 FailureRecord 全部是这条 RESOLUTION_CONFLICT。终端卡片 `markupsafe=2.1.0` 仍是
predecessor。blinker 的 `1.6` 是登记版本号，不是截断。

apply 后的依赖数组：

```toml
dependencies = [
    "blinker>=1.7.0; python_version == \"3.14\"",
    "blinker>=1.6; python_version == \"3.10\" or python_version == \"3.11\" or python_version == \"3.12\" or python_version == \"3.13\"",
    "click>=8.0.0",
    "itsdangerous>=2.0.0",
    "jinja2>=3.0.0",
    "markupsafe>=2.1.1",
    "werkzeug>=3.1.0",
]
```

差分见 [patch-refine/apply.diff](data/E012/patch-refine/apply.diff)。

## 10. 有价值的发现

1. **第三方仓库默认配置会选错 test group。** Flask 的 `dev` 合法存在且不是 pytest 套件。省略
   `test-group` 会按 D001 的 `dev` → `test` 查找选中 ruff/tox。显式 `tests` 后矩阵立即成立。
2. **声明下界已经过完整 pytest 的项目，check 可以 5/5 通过。** 这与 requests/MkDocs 的
   check-all-rejected 对照不同。Flask 自己用 `tests-min` 钉死同一组下界，和第一轮 check 同向。
3. **check 通过并不阻止 search 改写下界。** 第一轮 / all×minor 含前一个 major，minor 采样又提交
   系列最高精确版本，因此 click/jinja2/itsdangerous/blinker 被降低，werkzeug/markupsafe 被升高。
4. **`search-space = "all"` 在本矩阵上没有额外 minor 收益。** 六个 floor 与
   `majors[declaration-1:]` 相同，只多付了更老 major 的拒绝成本。
5. **patch refine 撤销了 minor 采样造成的升高。** werkzeug `3.1.8→3.1.0`、markupsafe
   `2.1.5→2.1.1` 回到 Flask 原声明；click/jinja2/itsdangerous/blinker 在已定位 minor 内继续下降。
   这与 [E006](E006-requests-complete-search.md) 的两阶段工作流同构，且仍是两次独立 invocation。
6. **完成卡片继续展示 predecessor。** 第一轮最后拒绝 jinja2 2.11.3（floor 3.0.3）；patch 轮最后
   拒绝 markupsafe 2.1.0（floor 2.1.1）。
7. **3.14 是唯一需要更高 blinker 的 Cell。** patch 轮 3.10–3.13 为 `1.6`，3.14 仍为 `1.7.0`。
8. **候选发现依赖瞬时可达的 Simple JSON。** patch 轮前两次失败是 PyPI SSL EOF，不是空间 DSL
   或 apply 后的声明错误。

## 11. 固定证据与局限

第一轮：

- [smoke.txt](data/E012/smoke.txt) / [check.txt](data/E012/check.txt) / [search.txt](data/E012/search.txt) / [apply.txt](data/E012/apply.txt)
- [run-evidence.json](data/E012/run-evidence.json)、[search-summary.json](data/E012/search-summary.json)
- [diagnostics.txt](data/E012/diagnostics.txt)、[configuration.toml](data/E012/configuration.toml)、[apply.diff](data/E012/apply.diff)

第二轮 all×minor：

- [all-minor/search.txt](data/E012/all-minor/search.txt)、[all-minor/search-summary.json](data/E012/all-minor/search-summary.json)
- [all-minor/apply.txt](data/E012/all-minor/apply.txt)、[all-minor/apply.diff](data/E012/all-minor/apply.diff)
- [all-minor/configuration.toml](data/E012/all-minor/configuration.toml)

第二轮 patch refine：

- [patch-refine/search.txt](data/E012/patch-refine/search.txt)、[patch-refine/search-summary.json](data/E012/patch-refine/search-summary.json)
- [patch-refine/search-attempt-1.txt](data/E012/patch-refine/search-attempt-1.txt)、[patch-refine/search-attempt-2.txt](data/E012/patch-refine/search-attempt-2.txt)
- [patch-refine/apply.txt](data/E012/patch-refine/apply.txt)、[patch-refine/apply.diff](data/E012/patch-refine/apply.diff)
- [patch-refine/diagnostics.txt](data/E012/patch-refine/diagnostics.txt)
- [patch-refine/configuration.toml](data/E012/patch-refine/configuration.toml)
- [run_pf.py](data/E012/run_pf.py)

原始运行目录为 `experiments/flask/.pf/logs/<run-id>/`。`experiments/` 被 PF 仓库 gitignore，
本地 `package-floor.json` 与 Journal 不能当作仓库内固定证据。摘要不替代完整报告的离线授权。
当前工作区报告是 patch refine 的产物，generation
`80af19c73361ae4e1285065509165333431f1f64cceaa080d8d44555caa6231c`。第一轮 generation
`55d003ab…` 与 all×minor generation `41997c8d…` 只保留在各自摘要中。

复核：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from pf.report import ReportStore
r = ReportStore().read(Path("experiments/flask/package-floor.json"))
print(r.report_generation_id, r.result.status, len(r.cell_results))
PY
```

本实验只证明所列 Linux target、五个 CPython minor、`no-extra`、`tests` harness、完整 pytest，
以及三种搜索配置：`majors[declaration-1:]` × minor、`all` × minor、`minors[declaration]` ×
patch。不覆盖 PyPy、free-threaded、3.15、async/dotenv extra surface、依赖任意组合，也不估计
真实发布后的 hole 分布。各阶段 apply 后的新 snapshot 没有再跑 check。

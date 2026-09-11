# E013 — requests smoke/check/search 重跑（all×minor 后 patch refine）

- **状态：** 已完成
- **日期：** 2026-09-11
- **性质：** 非规范性 dogfood 实验事实，不定义新契约，不构成对 requests 上游声明的修改建议
- **证据位置：** [data/E013/](data/E013/)（终端日志、命令元数据、配置副本、报告摘要、代表诊断与 apply 差分）
- **历史对照：** [E003](E003-requests-dependency-validation.md)、[E004](E004-requests-validation-surfaces.md)、
  [E006](E006-requests-complete-search.md)
- **目标：** `experiments/requests`，上游 commit `dae7ef63b4df6eded86637f251fc4e3a06c3b479`
  加本地 `[tool.pf]`
- **PF：** generator `0.4.0`；HEAD `64b06eb45a3fed054cbc43cf1d3c5d6a67f0bc8b`
- **契约入口：** [D001](../designs/D001-pf.md)、[D003](../designs/D003-pf-search-algorithm.md)、
  [D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、
  [D012](../designs/D012-pf-harness-relaxation.md)、[D014](../designs/D014-pf-report-schema.md)、
  [D037](../designs/D037-pf-candidate-search-policy.md)

本轮在恢复 requests 原始声明后执行 `pf smoke → pf check → pf search → pf apply`，搜索配置为显式
`search-space = "all"` × `search-resolution = "minor"`；apply 后再把空间收到
`minors[declaration]`、`search-resolution = "patch"` 做细化。与 [E006](E006-requests-complete-search.md)
的差异是第一阶段不再省略 space、因而不走 `majors[declaration-1:]` 条件默认。

成功 smoke **10/10 PASS**（此前两次失败已保留）；check **10/10 REJECTED**；两轮 search 均为
**10/10 SUCCESS、complete**，六个投影可表示。all×minor 把 certifi 降到登记底部 `0.0.8`；patch
refine 再降到 `0.0.7`，并把 charset-normalizer / urllib3 / PySocks 收到与 E006 第二阶段相同的
精确 patch。终端卡片上的版本是 predecessor 窗口，不是 floor。

## 1. 兼容性结论与实验配置

requests 是单包 setuptools 项目，`requires-python = ">=3.10"`。克隆开始时仍带着 E006 patch
refine 的中间声明，本轮先 `git checkout -- pyproject.toml` 恢复上游依赖，再写入 `[tool.pf]`。
`test` group 的 `requests[socks]` 自引用使 socks 成为每个 Cell 的必需 surface；空 `security`
extra 不再单独成 Cell。矩阵因此是 5 个 CPython minor × 2 个 extra surface = **10 Cells、6 个受管
直接依赖、0 pinned**。

| 观察 | 对 PF 的影响 | 本轮处理 |
| --- | --- | --- |
| classifiers 含 3.15 prerelease | PF v1 不支持 | 显式 `pythons = ["3.10", "3.11", "3.12", "3.13", "3.14"]` |
| extra-policy 省略即为 `each` | socks 已是 required；`use_chardet_on_py3` 另开 surface | 保持默认，不改 extra-policy |
| 完整 repository pytest（含 httpbin） | 本地 HTTP 测试可偶发失败 | 不缩小测试路径；失败则重跑 smoke |
| 用户要求全空间粗搜 | 省略 space 会命中 `majors[declaration-1:]` | 显式 `search-space = "all"` |

加入的配置（副本：[configuration.toml](data/E013/configuration.toml)）：

```toml
[tool.pf]
pythons = ["3.10", "3.11", "3.12", "3.13", "3.14"]
test-command = ["pytest"]
resolve-artifact = "any"
search-space = "all"
search-resolution = "minor"
```

验证契约是完整 repository pytest，含 doctest、HTTP/httpbin、证书、SOCKS 与本地 testserver。
未缩小测试路径。

## 2. 实验计划（已执行）

1. 恢复上游依赖声明，只改 `experiments/requests/pyproject.toml` 的 `[tool.pf]`。
2. 由 PF 仓库根、沙箱外、cwd=`experiments/requests` 顺序执行 smoke → check →
   all×minor search → apply。
3. 改成 `search-space = "minors[declaration]"`、`search-resolution = "patch"` 再 search 并 apply。
4. 用 `ReportStore.read` 核验报告，抽取投影与边界，对代表 predecessor 做 `pf diagnose`。
5. 把终端、身份、摘要和 apply 差分写入 `docs/experiments/data/E013/`。

调用方式与 [E012](E012-flask-complete-search.md) 相同：`PATH` 前缀 `.venv/bin`，
`UV_CACHE_DIR=/tmp/pf-uv-cache`。记录脚本为 [dogfood_run.py](data/dogfood_run.py)。

## 3. 运行总表

smoke 成功复测、check 与 all×minor search 共享 source snapshot
`ba3645a2be9ddd295ef2094335884917683746407ce76dd10334237684467e2f` 与 execution policy
`836ec5e859f4d1dea75b80a901fe70720fb84b87d9eee92f96fe3a266119bc25`。patch refine 因声明与搜索
配置变化而使用新 snapshot `d4566431cc46ced5c8b393201f8779965e25d221b43ad82ff972565e66678e48`。
apply 只编辑元数据，不形成新的 Verification Run。

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| smoke 首次 | `20260911T090700.531182Z-545762-e84f03a9` | 1 | 282.04s | 1 PASS、9 RESOLUTION_FAILED（PyPI TLS EOF） |
| smoke 第二次 | `20260911T091213.661590Z-547528-ddcfbdb0` | 1 | 84.06s | 9 PASS、1 HTTP 307 测试拒绝 |
| smoke 成功复测 | `20260911T091348.541186Z-549403-395f2940` | 0 | 79.61s | 10/10 PASS |
| check | `20260911T091516.703157Z-551500-f6e8ad2f` | 1 | 23.90s | 10/10 REJECTED，`declaration` / `lowest-direct` |
| search all×minor | `20260911T091559.456107Z-554510-5b570d29` | 0 | 1001.88s | 10/10 SUCCESS，complete |
| apply all×minor | （无 Verification Run） | 0 | 1.00s | 写回 minor 投影 |
| search patch refine | `20260911T093346.711360Z-611867-89248610` | 0 | 971.98s | 10/10 SUCCESS，complete |
| apply patch refine | （无 Verification Run） | 0 | 1.01s | 写回 patch floor |

矩阵：CPython **3.10–3.14** × `x86_64-unknown-linux-gnu` × `socks` / `socks+use_chardet_on_py3`。

## 4. smoke：最高版本通过，另有两次未进入资格的失败

第三次完整 smoke 终端 `Smoke passed · 10 cells`。Journal 0 条失败。十个 configured pytest 均正常
退出 0。

前两次失败保留为证据，不改写成 INDETERMINATE：

1. 首次 smoke 九个 Cell 在 `resolve-project` 以 `RESOLUTION_FAILED` 结束。代表
   `failure-1d8f898d10e72cf2` 的过程输出是对 `https://pypi.org/simple/setuptools/` 的
   `tls handshake eof`。随后 urllib 探测同一 Simple API 返回 200。这是 registry 可达性，不是
   requests 不兼容。仅 Python 3.13 / socks 当时通过。
2. 第二次 smoke 九个 Cell 通过；Python 3.10 / socks 在
   `tests/test_requests.py::TestRequests::test_HTTP_307_ALLOW_REDIRECT_POST_WITH_SEEKABLE`
   被 configured verifier 拒绝（`failure-c413949dafec67c8`）。这与 E006 首次 smoke 的 HTTP 307
   连接重置同类，完整复测未再出现。

search 报告记录的最高 baseline 向量在 socks surface 上为：

```text
certifi=2026.7.22  charset-normalizer=3.5.1  idna=3.19
pysocks=1.7.1      urllib3=2.7.0
```

chardet surface 另有 `chardet=7.6.0`。

## 5. check：原始声明下界仍不能满足验证契约

终端 `Check failed · declared lower bounds are incompatible · 10 cells`。Journal 10 条失败，
全部 `VERIFIER_EXITED_NONZERO` / `test`。原始声明为：

```text
charset_normalizer>=2,<4
idna>=2.5,<4
urllib3>=1.26,<3
certifi>=2023.5.7
PySocks>=1.5.6, !=1.5.7
chardet>=3.0.2,<8
```

与 E006 相同的两类终止：

| Python | pytest 退出 | 直接可见失败 | 代表 |
| --- | ---: | --- | --- |
| 3.10–3.11 | 1 | `test_use_proxy_from_environment[http_proxy-http]` | `failure-db6d28f4df77fe7f` |
| 3.12–3.14 | 4 | `ModuleNotFoundError: No module named 'urllib3.packages.six.moves'` | `failure-53dd10b42b64be65` |

check 验证的是原始声明生成的 lowest-direct 组合；search 可以抬高不通过的坐标、降低其他坐标并取得
PASS。两者并不矛盾。

## 6. all×minor：`search-space = "all"`

`ReportStore.read` 得到 `result.status = complete`、10 个 `CellSuccess`、各 2 个 sweep。
报告 generation `d26df9f9deaedb292dafddb137820e999e5ae50a7c54a10a2e36ac390a4fe85f`，
SHA-256 `472911599e38acb6b729782ed19d38549c9c5bae950806f91c30e2ddf73d2583`（2,178,715 字节）。
policy identity `1e35bcbf8038ecc7a3c35142754f315f902873cae85e6e27352d8f08e1518198`。
`requested_space = "all"`，resolution `minor`。六个投影 `representable = true`。

终端每个 Cell 都写 `search completed at [pysocks=1.6.8][oracle 1.6.8~1.7.1#2]`。这是 PySocks
坐标的 predecessor 窗口，**floor 是 1.7.1**。

### 6.1 投影对照

| 依赖 | 原声明 | 各 Cell floor | apply 投影 | 相对声明 | 相对 E006 第一阶段 |
| --- | --- | --- | --- | --- | --- |
| certifi | `>=2023.5.7` | `0.0.8` | `certifi>=0.0.8` | 降低 | E006 为 `2022.5.18.1`（被条件默认截断） |
| charset-normalizer | `>=2,<4` | `1.3.9` | `charset_normalizer<4,>=1.3.9` | 降低 | 相同 |
| idna | `>=2.5,<4` | `2.0` | `idna<4,>=2.0` | 降低 | 相同 |
| PySocks | `>=1.5.6,!=1.5.7` | `1.7.1` | `PySocks!=1.5.7,>=1.7.1` | 升高 | 相同 |
| urllib3 | `>=1.26,<3` | `1.26.20` | `urllib3<3,>=1.26.20` | 升高（1.26 系列代表） | 相同 |
| chardet | `>=3.0.2,<8` | `2.2.1` | `chardet<8,>=2.2.1` | 降低 | 相同 |

socks surface 最终向量：

```text
certifi=0.0.8  charset-normalizer=1.3.9  idna=2.0
pysocks=1.7.1  urllib3=1.26.20
```

chardet surface 另有 `chardet=2.2.1`。十个 Cell 上同一依赖的 floor 一致。

certifi 到达本次冻结候选集底部，predecessor 为空，**不证明**更早版本不兼容；它只说明 `all` 空间
里没有更低的合格 minor 代表。E006 第一阶段因 `majors[declaration-1:]` 从未进入 2023 之前的
major，所以当时的 `2022.5.18.1` 不是全历史底部。

### 6.2 规模

| 指标 | 值 |
| --- | ---: |
| 搜索观察 | 370（ProbePass 175、ProbeRejection 195） |
| Cell 内 FailureRecord | 195（与 Journal 195 条一致） |
| 其中 verifier 非零 | 175 |
| RESOLUTION_FAILED | 20（全部为 idna `0.2` 的 resolve-project） |
| candidate snapshots | 55 |
| process logs | 3051 |
| 最长 Cell | 17m58s（Python 3.10 / socks+use_chardet_on_py3） |
| 命令墙钟 | 1001.88s |

20 条 `RESOLUTION_FAILED` 集中在 idna 0.2，不是 PyPI TLS 误伤。charset-normalizer / idna /
PySocks / urllib3 的 predecessor 均为 configured verifier 拒绝。

### 6.3 代表 predecessor

完整摘录见 [all-minor/diagnostics.txt](data/E013/all-minor/diagnostics.txt)。Python 3.10 /
socks 代表：

| 依赖 | 前驱 | Failure ID |
| --- | --- | --- |
| charset-normalizer | 1.2.0 | `failure-0283cd5c96844609` |
| idna | 1.1 | `failure-3dc4c0d40d0cedef` |
| PySocks | 1.6.8 | `failure-62c332932b20b2e7` |
| urllib3 | 1.25.11 | `failure-761d501e42a1b538` |
| chardet（chardet surface） | 2.1.1 | `failure-fee9c1d1fd551b4e` |

apply 退出 0。依赖数组变为：

```toml
dependencies = [
    "charset_normalizer<4,>=1.3.9",
    "idna<4,>=2.0",
    "urllib3<3,>=1.26.20",
    "certifi>=0.0.8",
]
```

socks / chardet extra 同步写成 `PySocks!=1.5.7,>=1.7.1` 与 `chardet<8,>=2.2.1`。差分见
[all-minor/apply.diff](data/E013/all-minor/apply.diff)。这是实验克隆上的授权编辑，不是
requests 必须放宽支持范围的产品结论。

## 7. patch refine：`minors[declaration]` × patch

all×minor apply 之后，中间声明就是上一节的投影。随后把配置改为：

```toml
search-space = "minors[declaration]"
search-resolution = "patch"
```

配置副本：[patch-refine/configuration.toml](data/E013/patch-refine/configuration.toml)。
新 snapshot `d4566431cc46ced5c8b393201f8779965e25d221b43ad82ff972565e66678e48`。

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| search | `20260911T093346.711360Z-611867-89248610` | 0 | 971.98s | 10/10 SUCCESS，complete |
| apply | （无 Verification Run） | 0 | 1.01s | 写回 patch floor |

报告 generation `998b4f31e7d24ca900d71d2fd6a8df12ec7bc54dd4507dbea8760b3affb7514f`，
SHA-256 `3d22d517fe25a1562b15de7e513277c4ade09db64c95355bdd0d1365d64f0cb2`（1,449,276 字节）。
policy identity `6d49fb1671f297584c5c96e8760f4e029d6fbfc1d0685c5e44e6eb55dec34c48`。

| 依赖 | 中间声明（minor 代表） | patch floor | 相对 requests 原声明 | 相对 E006 refine |
| --- | --- | --- | --- | --- |
| certifi | `>=0.0.8` | `0.0.7` | 低于 `>=2023.5.7` | E006 停在 `2022.5.18.1` |
| charset-normalizer | `>=1.3.9` | `1.3.1` | 低于 `>=2` | 相同 |
| idna | `>=2.0` | `2.0` | 低于 `>=2.5` | 相同 |
| PySocks | `>=1.7.1` | `1.7.0` | 高于原 `>=1.5.6` | 相同 |
| urllib3 | `>=1.26.20` | `1.26.5` | 高于原 `>=1.26` 的精确 patch | 相同 |
| chardet | `>=2.2.1` | `2.2.1` | 低于 `>=3.0.2` | 相同 |

idna / PySocks / chardet 在本 minor 内已无更低候选（predecessor 为空）。charset-normalizer
`1.3.0` 与 urllib3 `1.26.4` 被拒绝，与 E006 §6.1 同类。certifi `0.0.6` 是新的 patch 边界
（`failure-fb73671197d70121`）。终端多数 Cell 展示
`search completed at [charset-normalizer=1.3.0]`；Python 3.10 / chardet surface 最后拒绝的是
urllib3 `1.26.4`。完成卡片仍是 predecessor，不是 floor。

观察 279（Pass 142 / Rejection 137），137 条 FailureRecord 全部
`VERIFIER_EXITED_NONZERO`。最长 Cell 17m26s。命令墙钟 971.98s。

apply 后的依赖数组：

```toml
dependencies = [
    "charset_normalizer<4,>=1.3.1",
    "idna<4,>=2.0",
    "urllib3<3,>=1.26.5",
    "certifi>=0.0.7",
]
```

socks / chardet extra 为 `PySocks!=1.5.7,>=1.7.0` 与 `chardet<8,>=2.2.1`。差分见
[patch-refine/apply.diff](data/E013/patch-refine/apply.diff)。

## 8. 有价值的发现

1. **`search-space = "all"` 对 certifi 有实质收益。** E006 条件默认把 certifi 截在
   `2022.5.18.1`；本轮 minor 代表到 `0.0.8`，patch 再到 `0.0.7`。其余五条坐标的 minor 代表与
   E006 第一阶段相同，说明放宽空间并不自动压低每一个依赖。
2. **charset-normalizer / urllib3 / PySocks 的 patch 边界与 E006 一致。** 在现行 PF 0.4.0 上重跑，
   `1.3.1` / `1.26.5` / `1.7.0` 仍然成立。
3. **原始声明下界 check 仍然 10/10 失败。** 3.10–3.11 是 SOCKS 代理测试，3.12–3.14 是 urllib3
   1.26.0 的 `six.moves` 导入失败。search 成功不能解释成原始下界已经通过。
4. **完整 pytest + 本地 httpbin 仍会偶发失败。** 第一次是 PyPI TLS EOF，第二次是 HTTP 307
   测试。资格结论以第三次完整 PASS 为准，前两次保留。
5. **完成卡片继续展示 predecessor。** all×minor 最后拒绝 PySocks 1.6.8（floor 1.7.1）；patch
   轮最后拒绝 charset-normalizer 1.3.0 或 urllib3 1.26.4。

## 9. 固定证据与局限

smoke / check：

- [smoke.txt](data/E013/smoke.txt) / [check.txt](data/E013/check.txt)
- [smoke-attempt-1.txt](data/E013/smoke-attempt-1.txt)、[smoke-attempt-2.txt](data/E013/smoke-attempt-2.txt)
- [smoke-journal.json](data/E013/smoke-journal.json)、[check-journal.json](data/E013/check-journal.json)
- [configuration.toml](data/E013/configuration.toml)

all×minor：

- [all-minor/search.txt](data/E013/all-minor/search.txt)、[all-minor/search-summary.json](data/E013/all-minor/search-summary.json)
- [all-minor/apply.txt](data/E013/all-minor/apply.txt)、[all-minor/apply.diff](data/E013/all-minor/apply.diff)
- [all-minor/diagnostics.txt](data/E013/all-minor/diagnostics.txt)
- [all-minor/configuration.toml](data/E013/all-minor/configuration.toml)

patch refine：

- [patch-refine/search.txt](data/E013/patch-refine/search.txt)、[patch-refine/search-summary.json](data/E013/patch-refine/search-summary.json)
- [patch-refine/apply.txt](data/E013/patch-refine/apply.txt)、[patch-refine/apply.diff](data/E013/patch-refine/apply.diff)
- [patch-refine/diagnostics.txt](data/E013/patch-refine/diagnostics.txt)
- [patch-refine/configuration.toml](data/E013/patch-refine/configuration.toml)

原始运行目录为 `experiments/requests/.pf/logs/<run-id>/`。`experiments/` 被 PF 仓库 gitignore，
本地 `package-floor.json` 与 Journal 不能当作仓库内固定证据。摘要不替代完整报告的离线授权。
当前工作区报告是 patch refine 的产物，generation
`998b4f31e7d24ca900d71d2fd6a8df12ec7bc54dd4507dbea8760b3affb7514f`。all×minor generation
`d26df9f9…` 只保留在其摘要中。

复核：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from pf.report import ReportStore
r = ReportStore().read(Path("experiments/requests/package-floor.json"))
print(r.report_generation_id, r.result.status, len(r.cell_results))
PY
```

本实验只证明所列 Linux target、五个 CPython minor、两个 extra surface、完整 pytest，以及
`all` × minor 与 `minors[declaration]` × patch 两阶段。不覆盖 PyPy、free-threaded、3.15、
依赖任意组合，也不估计真实发布后的 hole 分布。各阶段 apply 后的新 snapshot 没有再跑 check。
certifi `0.0.7` 是本验证契约下的测试可走通下界，不是对生产 CA bundle 的安全建议。

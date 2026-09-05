# E004 — requests required-surface 修复后验证

- **状态：** 已完成（保存空组策略补充前的运行事实与补充后的 planning 复核）
- **日期：** 2026-09-05
- **性质：** 非规范性 dogfood 运行事实；不定义产品或实现契约
- **实现依据：** [D028](../archived/designs/D028-pf-validation-contract-surfaces.md) / [P034](../archived/plans/P034-pf-validation-contract-surfaces.md)
- **历史对照：** [E003](E003-requests-dependency-validation.md)
- **目标：** `experiments/requests`，commit `dae7ef63b4df6eded86637f251fc4e3a06c3b479`
- **PF：** `0.1.0`，基线 `3f79782` 上的 D028 工作树（尚未提交）
- **Source snapshot：** `75b5d4f7dd959f3f54a5fea2bb46aa8937d81fcdcd4d3f496040ce91bb6329b3`
- **三命令运行时 Evaluation policy：** `91eb0a1d5088579f747162ce604da5c2e28bbb9ab4968b2c490f851ff9cc6823`

## 1. 输入与口径

目标 clone 的当前目录为 `experiments/requests`，E003 的历史目录拼写为 `expirements/requests`。
目标 commit、配置和 source snapshot 与 E003 相同；原 `[tool.pf]` 显式 `test-command = ["pytest"]`、
`resolve-artifact = "any"` 保留。运行完整 repository pytest contract，不删除测试或缩小 nodeid 范围。

所有有效运行在目标目录执行，使用 PF 环境中的 uv、ty 与 pf：

```bash
UV_CACHE_DIR=/tmp/pf-uv-cache /home/llh/pf/.venv/bin/uv run --no-sync --project /home/llh/pf pf smoke
UV_CACHE_DIR=/tmp/pf-uv-cache /home/llh/pf/.venv/bin/uv run --no-sync --project /home/llh/pf pf check
UV_CACHE_DIR=/tmp/pf-uv-cache /home/llh/pf/.venv/bin/uv run --no-sync --project /home/llh/pf pf search
```

三条命令按顺序运行，有包源网络访问。运行日志由目标 `.pf/logs/<run-id>/` 保存；journal 的 entries
是失败证据，不把 entries 数当作所有已执行 Cell 数。成功数同时核对命令终态和实际 verifier 进程。

## 2. 三命令运行时的 required surface

空组策略补充前启动的三条实际命令均规划 CPython 3.10–3.14 × 一个 `x86_64-unknown-linux-gnu` target × 三个 surface：

```text
[socks]
[security, socks]
[socks, use_chardet_on_py3]
```

合计 15 Cells、6 个受管直接依赖，全部包含 `socks`。Loader 的 external harness declarations 为 7 条，
`requests[socks]` 已被吸收，SourcePlan 不再有 target requests 的假 harness route。`PySocks` 属于全部
Cell 的 project graph。新的 evaluation policy 绑定 normalization facts；不能与 E003 旧 policy 的证据混用。

## 3. 有效运行结果

| 命令 | run-id | 退出码 | 已核对终态 |
| --- | --- | --- | --- |
| smoke | `20260905T063558.981602Z-132551-456b7f88` | 4 | 12 PASS；3 个 py3.11 Cell 在 inspect-environment-plan 为 INTERNAL_INVARIANT / INDETERMINATE |
| check | `20260905T063738.393187Z-142006-4c8e5bf4` | 1 | 12 个 declaration 为 VERIFIER_EXITED_NONZERO / REJECTED；3 个 py3.11 declaration-capture 为 INTERNAL_INVARIANT / INDETERMINATE |
| search | `20260905T063827.102454Z-148781-2027f407` | 4 | 12 个 runtime-search Cell 为 BUILD_FAILURE / INDETERMINATE；3 个 py3.11 baseline 为 INTERNAL_INVARIANT / INDETERMINATE |

smoke 的 12 个 PASS 分布于 Python 3.10/3.12/3.13/3.14 的三个 surface，均越过 project/environment
resolution、installation、ty 和完整 configured pytest。Python 3.11 的三个失败具有 project 与 environment
plan digest，失败发生在最终安装图复证；它们不再是 E003 的 `resolution-plan-invalid`，也不构成 Rejection。

check 的 12 个 Rejection 均来自实际 configured verifier 正常非零退出：py3.10 的 3 个为
`NormalExit(1)`，py3.12–3.14 的 9 个为 `NormalExit(4)`；不据此推断单个依赖根因。
代表 Failure ID 为 `failure-45c5b98bfbdbd841`（Python 3.10、security+socks）。Python 3.11 未进入 declaration
验证。check 退出 1 是其现行聚合规则，不表示全部 15 Cells 都已取得负向验证事实。

search 最终报告经 `ReportStore.read` 完整验证，generation 为
`709084acd89c29abe07736fd70f68a4d1031f0a8b6c6f8df1f47b0e14e5609ce`，source snapshot 与运行时
policy 均与本文页首一致。15 个 Cell 的终态为 12 `CELL_INDETERMINATE` / 3 `BASELINE_INDETERMINATE`；
result 为 `incomplete`，reasons 为 `INDETERMINATE`、`UNREPRESENTABLE_PROJECTION`。


12 个非 py3.11 Cell 都有完整 PASS baseline，并进入实际候选搜索，最终在 `idna=0.2` 的
`resolve-project` 构建阶段终止；构建 stderr 为 `Sorry, Python 3 not yet supported`。代表 Failure ID 为
`failure-8aa1ce1fd3ab233c`（Python 3.10、socks+use_chardet_on_py3）。py3.11 的代表 Failure ID 为
`failure-23c8d4144e5fd34d`（socks、inspect-environment-plan）。

报告保存 125 Attempts、110 evaluations（65 PASS、36 RUNTIME_INTERFACE_MISSING、9 VERIFIER_REJECTED），
以及 60 FailureRecords（36 runtime witness、9 verifier nonzero、12 build failure、3 internal invariant）；
journal 也有 60 entries，运行留下 932 份 process logs。原 `resolution-plan-invalid` 未再出现。
这证明修复后的执行确实越过 environment resolution 并进入 ty/configured pytest；这些计数不是成功 Cell 数。

六个受管依赖的 projection 全为 `representable=false, floors=[]`。没有 final success roots、verified floor
或可发布 predecessor，也没有 apply 授权。日志中的 certifi/chardet/charset-normalizer 候选只表示搜索过程，
不能当成最终最低版本。

## 4. 实施中取得的辅助事实

- 沙箱 smoke `20260905T062957.688120Z-2-8d93e5b3` 规划了 15 Cells，但包源网络失败；不当作联网验证。
- 联网 smoke `20260905T063318.637429Z-116700-c9d36da9` 越过旧 environment 投影缺口并安装成功，随后
  在 EnvironmentIdentity 唯一性检查崩溃。Process logs 显示 importlib.metadata 对 requests 返回两份
  相同 name/version/dependency graph。Adapter 已归并相同节点并拒绝冲突观测，public regression 已证明。
- 直接以绝对路径运行 `.venv/bin/pf` 的辅助 smoke/check 没有把 `ty` 加入 PATH，得到工具启动失败。
  有效实验改用上述 `uv run` 命令；这些辅助运行不代替 §3 的产品结果。

## 5. 结论边界

自引用已经成为真实 effective Cell surface，E003 的 target-as-harness 投影缺口已消失。Python 3.11 的
installed-graph mismatch 在原实验结束时尚未定位；后续离线诊断见 §7，原始 Indeterminate 终态保持。
12 个搜索期构建失败也不构成 Rejection。此次没有得到 floor，不能将候选观测追认为完整兼容性结论。


## 6. 用户补充：自动跳过空 extra group

上述完整 search 运行期间，用户要求默认跳过空 extra group，并明确修复后不重复完整 search。
最终实现以声明 dependency array 是否非空过滤自动 `each/all` 探索；显式 custom surface 与 required extras
仍保留，marker 不活跃不算空组。三条既有命令的输入与结果保持 §2–3 原貌，不当作最终策略的完整实验。

最终代码通过实际 `ProjectLoader(pythons=UvAdapter(SubprocessRunner())).load(...)` 重新规划同一 requests
目录，仅调用 Python discovery，未创建验证 Attempt。结果为 CPython 3.10–3.14 × 两个 surfaces：

```text
[socks]
[socks, use_chardet_on_py3]
```

合计 10 Cells，自动跳过 `security = []`；7 个 external harness declarations、所有 Cell 的 PySocks
active declarations、无 requests source route 均已核对。最终 evaluation policy 为
`835b6acede321fcdd443cd0323a67e304c3abb51b7be039d0fb3d27cd68ba76a`，绑定新增固定
`extra_exploration = nonempty-declared-groups-only`。旧运行报告仍可离线读取，但不获得当前 policy 的 apply authority。

最终行为的证据是 public planning/configuration/policy tests 与此次 live planning；未再运行完整 search，
也不声称这 10 个 Cell 已取得新策略下的 smoke/check/search 终态。

## 7. 后续诊断：Python 3.11 的条件节点投影缺口

- **诊断日期：** 2026-09-05
- **代码基线：** `57888b7`（D028 已提交）
- **范围：** 回放 §3 smoke 的已有日志，未重新运行 smoke/check/search，未修改生产代码。
- **结论：** PF 将 pylock 中未生效的条件节点纳入预期安装图，触发安装图复证失败；尚未修复。

### 7.1 原始日志事实

日志均来自 `.pf/logs/20260905T063558.981602Z-132551-456b7f88/`：

1. `process-0024.log` 的 environment lock 包含 `tomli==2.4.1`，package marker 为
   `python_full_version <= '3.11'`。
2. `process-0033.log` 记录 `uv pip sync` 使用 Python **3.11.15**，成功安装 33 个包，其中没有 tomli。
   该实际版本不满足上述 marker。
3. `process-0053.log` 的安装后 metadata inspection 正常退出 0。归一化 distribution names 后，
   相比 PF 解析出的预期图，唯一缺项为 tomli；没有额外包或版本漂移。当前 target requests 单独计入。
4. 其余两个 py3.11 Cell 也有相同差异：

| Environment lock | Installed metadata | 唯一缺项 |
| --- | --- | --- |
| `process-0024.log` | `process-0053.log` | tomli |
| `process-0044.log` | `process-0080.log` | tomli |
| `process-0050.log` | `process-0086.log` | tomli |

同次 smoke 的 py3.10 lock 也包含这个条件节点，但其解释器满足条件，实际安装了 tomli；py3.12–3.14
的 environment locks 已不包含 tomli。这解释了该次运行为何只在 py3.11 暴露此差异。

### 7.2 代码路径与离线复证

`parse_uv_pylock` 保存 package marker，但将所有 package entries 返回给调用方。
[UvAdapter](../../src/pf/adapters/uv.py) 将这些节点投影为 ResolutionPlan；
[EnvironmentFactory](../../src/pf/environment.py) 在 `inspect-environment-plan` 构造 expected
name/version map 时也未按 marker 过滤，因而把未生效的 tomli 当成必须安装的包，返回
`INTERNAL_INVARIANT / INDETERMINATE`。失败发生在 ty/configured pytest 之前。

以下命令在 PF 根目录执行，回放三个实际失败 Cell；不创建验证环境、不访问包源：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from packaging.markers import Marker
from packaging.utils import canonicalize_name
from pf.adapters.uv_lock import parse_uv_pylock

logs = Path("experiments/requests/.pf/logs/20260905T063558.981602Z-132551-456b7f88")
def stdout(name):
    return logs.joinpath(name).read_text().split("\n--- stdout ---\n", 1)[1].split("\n--- stderr ---\n", 1)[0]

environment = dict(python_version="3.11", python_full_version="3.11.15",
                   implementation_name="cpython", platform_python_implementation="CPython")
for lock, graph in ((24, 53), (44, 80), (50, 86)):
    plan = parse_uv_pylock(stdout(f"process-{lock:04d}.log"), python_minor="3.11")
    expected = {p.name: p.version for p in plan if p.name != "requests"}
    actual = {canonicalize_name(p["name"]): p["version"]
              for p in json.loads(stdout(f"process-{graph:04d}.log"))
              if canonicalize_name(p["name"]) != "requests"}
    assert set(expected) - set(actual) == {"tomli"}
    active = {p.name: p.version for p in plan if p.name != "requests"
              and (p.marker is None or Marker(p.marker).evaluate(environment))}
    assert active == actual
    print(f"{lock:04d}/{graph:04d}: missing tomli; active name/version graph matches")
PY
```

结果：三组均通过断言。未过滤时重现缺少 tomli；按实际 Python 3.11.15 求值后，active 节点的包名和版本
与安装观测完全一致。该对照定位了条件节点处理缺口，但不验证 source/artifact 等其余图不变量，也不等于
产品修复后的 smoke PASS。

后续修复需统一解析计划与安装图复证使用的实际 interpreter/target 条件投影，并保留图一致性约束。
本节只记录诊断事实与修复方向；不追认原实验成功，不产生新的 floor、predecessor 或 apply authority。

## 8. 后续诊断：check 的声明下界失败

2026-09-05，在同一 `57888b7` 基线上回放 check run
`20260905T063738.393187Z-142006-4c8e5bf4`。实际安装 metadata 确认 12 个 declaration environments
均使用 `urllib3==1.26.0`、`PySocks==1.5.6`，对应 requests 声明下界；三类失败如下：

| Python | Cells | 直接失败及证据 |
| --- | --- | --- |
| 3.10 | 3 | SOCKS 代理测试失败，pytest 正常退出 1；代表日志 `process-0119.log` |
| 3.11 | 3 | declaration-capture 安装图复证失败，尚未进入 declaration verifier；见 §7 的条件节点诊断 |
| 3.12–3.14 | 9 | conftest 导入 urllib3 失败，pytest 正常退出 4；代表日志 `process-0172.log` |

### 8.1 PySocks 已安装但无法导入

py3.10 的首个失败用例为 `tests/test_lowlevel.py::test_use_proxy_from_environment[http_proxy-http]`。
它预期 `ConnectionError`，实际得到 `InvalidSchema: Missing dependencies for SOCKS support.`。

最小导入复现表明，`PySocks==1.5.6` 中的 `from collections import Callable` 抛出 ImportError。
Python 3.10 已移除这些旧 ABC 别名，见 [Python 3.10 变更](https://docs.python.org/3/whatsnew/3.10.html#removed)。
requests 的 adapters 模块捕获了 `urllib3.contrib.socks` 的 ImportError，替换为抛出 InvalidSchema 的
fallback，因而最终文案看似缺少依赖。实际 metadata 已证明 PySocks 安装成功；失败是导入不兼容。

保持 Python 3.10 与 urllib3 1.26.0，仅将 PySocks 换为缓存中的 1.7.1，SOCKSProxyManager 导入成功。
该对照只验证这一导入缺口，不证明 1.7.1 是 floor，也不证明完整 suite 已通过。

### 8.2 urllib3 内置 six 的旧导入协议

py3.12–3.14 的错误链为 `tests/conftest.py → requests → urllib3.exceptions`，终止于
`ModuleNotFoundError: No module named 'urllib3.packages.six.moves'`。

urllib3 1.26.0 内置的 six 1.12.0 importer 实现 `find_module()`，没有 `find_spec()`；Python 3.12
移除了旧协议入口，见 [Python 导入协议](https://docs.python.org/3.12/reference/import.html#the-meta-path)。
本机 Python 3.12 的 importlib 也直接跳过没有 `find_spec` 的 finder。用缓存中的同一 urllib3 包在
Python 3.12 最小导入可复现原错误，在 Python 3.10 则能导入 urllib3。

三个最小对照的复核命令如下；缓存目录来自此次实验，不下载或重新安装依赖：

```bash
.venv/bin/python - <<'PY'
import subprocess
root = "/tmp/pf-uv-cache/archive-v0/"
urllib3 = root + "pJpzpJv1HQEpdp8Z"
old_socks = root + "Zf5fIGorEyujw_bk"
new_socks = root + "mBrcrK53PuVOh2xc"
for python, socks, error in (
    (".venv/bin/python", old_socks, "cannot import name 'Callable'"),
    ("/usr/bin/python3.12", old_socks, "No module named 'urllib3.packages.six.moves'"),
    (".venv/bin/python", new_socks, None),
):
    code = f"import sys; sys.path[:0] = {[urllib3, socks]!r}; from urllib3.contrib.socks import SOCKSProxyManager"
    result = subprocess.run([python, "-I", "-c", code], capture_output=True, text=True)
    assert (result.returncode == 0) if error is None else (result.returncode != 0 and error in result.stderr)
    print(python, error or "SOCKS import passed")
PY
```

结果：三个对照均满足断言。check 的 12 个 declaration Rejection 是配置验证命令的真实正常非零退出；
局部复现解释了已观察到的导入链，不把完整 Attempt 的失败归约成已经求出的单个依赖边界。

## 9. 后续诊断：search 为何在 idna 0.2 停止

同日核对 search run `20260905T063827.102454Z-148781-2027f407`，12 个非 py3.11 Cell 的失败日志
全部指向同一个 `idna-0.2.tar.gz` artifact，SHA-256 为
`e28fdff4b1d47edd13e053399f642818d2f591cb9c215eb626bde6b14d6f4575`。
日志为 `process-0639.log`–`process-0646.log`、`process-0861.log`–`process-0863.log` 与
`process-0931.log`；uv build backend 均正常退出 1，summary code 为 `resolution-build-failure`。

缓存源码的 `setup.py` 在调用 setuptools setup 前明确检查 Python major：等于 3 就抛出
`SystemExit("Sorry, Python 3 not yet supported")`。直接执行该源码可重现退出 1 和相同错误，无需构建环境
或访问包源：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import sys, runpy
roots = list(Path('/tmp/pf-uv-cache/archive-v0').glob('*/setuptools/__init__.py'))
assert roots
sys.path.insert(0, str(roots[0].parent.parent))
runpy.run_path('/tmp/pf-uv-cache/sdists-v9/url/dd2670b2f8718bab/dn8Xu7qi8em-z3Md/src/setup.py', run_name='__main__')
PY
```

**为何探测 0.2：** 此次冻结的 idna CandidateSnapshot 包含 41 个版本，首个为 0.2，最高为 3.19。
search 会移除受管依赖的声明下界限制以寻找更低版本，保留 upper bounds/exclusions，因此 `idna>=2.5`
不会把搜索限制在 2.5 以上。D003 的默认首次 probe 是最早候选；0.2 的 sdist 符合此次 `any` artifact
策略。该源码的 PKG-INFO 没有 `Requires-Python` 字段，Python 3 拒绝条件位于执行期 setup.py 中。

**为何停止而不继续：** [D005](../designs/D005-pf-failure-and-diagnose.md) 将 build failure 保持为
Indeterminate；[D003](../designs/D003-pf-search-algorithm.md) 要求 Probe Indeterminate 立即停止。
人读 setup.py 可以定位本次具体原因，但现行 PF 不把 build stderr 或源码检查转换成 certified Rejection。
因此不能据此跳过该候选，或把它发布为 predecessor 拒绝证据。

search 的三个 py3.11 baseline 另行核对 environment lock / installed metadata 对：`0028/0059`、
`0076/0110`、`0090/0115`，同样只有未生效的 tomli 节点差异，与 §7 一致。

这解释了 §3 的 12 runtime-search Indeterminate 加 3 baseline Indeterminate，以及无 final success roots、
六个 projection 无 floor 的结果。先前 65 个 PASS evaluations 是过程证据，不能替代完整搜索终态。
上述 check/search 诊断均未修改目标依赖、PF 实现或搜索策略，也未重新运行完整三命令实验。

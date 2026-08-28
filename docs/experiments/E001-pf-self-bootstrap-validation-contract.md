# E001 — PF 自举验证契约选择实验

- **状态：** 已完成
- **日期：** 2026-08-28
- **性质：** 非规范性 dogfood 实验报告；不定义命令、算法、Schema 或 module interface
- **报告检查点：** `0bc8550`，tag `pf-self-bootstrap`
- **报告实体：** generation `608d62263bbf315e1aab6528b5db9aeebbe6b97da85c1c86a82caa5100408014`
- **源码快照：** `d40a8b364e72386063ec01285c4ee2db53ad9fcc7f9ae314f78afe277bc0f7b9`
- **验证策略：** `configured-verifier-terminal-v1`；`pytest --no-testmon`
- **实验产物：** [`package-floor.json`](../../package-floor.json)
- **契约所有者：** [D001](../designs/D001-pf.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、[D012](../designs/D012-pf-harness-relaxation.md)、[D013](../designs/D013-pf-pytest-observer.md)

本文记录 PF 对自身仓库执行一次成功 `pf search` 时，`packaging` floor 如何受到 configured validation contract 选择影响。实验不提出 PF 核心设计整改；它说明同一源码与候选向量在不同 verifier contract 下可以得到不同、但各自真实的 floor。

## 1. 问题

本次完整自举报告把三个 Cell 的 `packaging` floor 都确定为 `22.0`，并把紧邻前驱 `21.3` 记录为配置 verifier 的权威 Rejection。前驱的完整 PF test command 只失败两项 observer qualification 相关测试：

- `TestPytestObserverQualificationRunner.test_transparency_runner_replays_the_committed_current_profile`；
- `TestPytestObserverIntegration.test_configured_verifier_progress_reaches_completion_across_qualification_pytest`。

实验要区分三种解释：

1. `packaging==21.3` 使 PF 发布 runtime 真实无法运行；
2. `packaging==21.3` 与当前 pytest ecosystem 存在版本约束；
3. PF dogfood 选择的 full repository test suite 把开发/资格验证基础设施纳入 compatibility contract，因此形成比发布 runtime 更严格的 floor。

## 2. 搜索结果

报告终态为 `complete`、`3/3` Cells covered，并授权 apply。`packaging` 投影为：

```text
packaging>=25.0 -> packaging>=22.0
```

三个 Cell 的边界完全一致：

| Cell | Floor | Predecessor | Failure ID | Process Log | Verifier 结果 |
| --- | --- | --- | --- | --- | --- |
| Python 3.10 | `22.0` | `21.3` | `failure-dab24f66fd2dc1da` | `process-1101.log` | 2 failed, 1314 passed |
| Python 3.11 | `22.0` | `21.3` | `failure-3aeebd55d35220f2` | `process-1120.log` | 2 failed, 1314 passed |
| Python 3.12 | `22.0` | `21.3` | `failure-38b09c3ea317f875` | `process-1139.log` | 2 failed, 1314 passed |

三次失败都具有完整 normal exit 1，因此按 D005 的 configured verifier authority 正确形成 `REJECTED`。历史 FailureRecord 只否定对应完整 Attempt；它不单独声称 `packaging==21.3` 的 runtime API 是失败根因。

## 3. 精确因果链

本次 dogfood 的因果链是：

```text
PF dogfood
-> full repository test suite 被作为 compatibility contract
-> suite 中包含 PF-only observer qualification tests
-> candidate packaging==21.3 导致 relaxed harness 选择 pytest==8.4.2
-> qualification test 的 committed current profile 只覆盖 pytest==9.1.1
-> repository test suite FAIL
-> configured verifier normal nonzero
-> PF 正确地 REJECTED
```

这里没有 PF search 或 observer 在 configured verifier 之外额外施加约束。约束来自被测仓库自己选择的测试集合。

### 3.1 pytest ecosystem 触发条件

`pytest==9.1.1` 声明 `packaging>=22`。报告中的精确环境 resolution graph 因此出现下列差分：

| Project coordinate | Relaxed harness selection |
| --- | --- |
| `packaging==21.3` | `pytest==8.4.2` |
| `packaging==22.0` | `pytest==9.1.1` |

D012 要求 search probe 放宽 eligible direct harness declaration 的显式下限，并保留 baseline ceiling。`pytest>=9.1` 因而可以在 probe 环境中下降到 `8.4.2`；这正是 harness relaxation 的预期行为，不是违规或解析泄漏。

### 3.2 qualification test 的失败点

Qualification inner runner 使用 ambient pytest，返回实际 `python_minor`、`pytest_version` 和十二个 reference-vs-injected case。仓库测试随后从 committed manifest 中寻找：

```text
current_plugins == true
and profile.python_minor == result.python_minor
and profile.pytest_version == result.pytest_version
```

Committed current-plugin profiles 对三个 Python minor 都固定为 `pytest==9.1.1`。当 ambient pytest 为 `8.4.2` 时，没有匹配项，`next(...)` 抛出 `StopIteration`。

Progress integration 测试只是嵌套执行上述 qualification 测试，并要求其 configured verifier 通过；因此它是同一根因的级联失败，不是第二个独立 incompatibility。

## 4. 控制变量实验

调查使用 CPython 3.10 和报告记录的 project/harness 版本重放 qualification 路径。环境通过 `uv run --isolated --no-project` 创建；observer source 使用 `src/pf/_pytest_observer.py`。

### 4.1 Observer 透明性

在精确的 `packaging==21.3 + pytest==8.4.2` 环境中运行全部十二个 qualification cases：

```text
case_count = 12
all_expected = true
failed_cases = []
```

Reference 与 injected observer 的 pytest exit、test selection、hook outcome、执行顺序和 canonical telemetry 全部一致。这排除了“observer 注入破坏被测 pytest 行为”。

### 4.2 Current-profile lookup 差分

聚焦执行 `test_transparency_runner_replays_the_committed_current_profile`：

| packaging | pytest | 结果 |
| --- | --- | --- |
| `21.3` | `8.4.2` | FAIL；`StopIteration` |
| `22.0` | `8.4.2` | FAIL；相同 `StopIteration` |
| `22.0` | `9.1.1` | PASS |

固定 pytest 为 `8.4.2` 后，只改变 packaging 不改变失败；切换到 committed current pytest profile 后测试通过。因此直接控制 qualification 测试结果的是 ambient pytest/profile identity，不是 `packaging==21.3` 的运行时 API 行为。

## 5. 结论

### 5.1 PF 行为正确

PF 没有违反 harness relaxation，也没有擅自定义 compatibility。用户配置的完整 `test-command` 是本次搜索的 validation contract；其中任何测试确定失败，configured verifier 都应返回 normal nonzero，PF 都应拒绝该完整 Attempt。

因此 `packaging>=22` 不是错误 floor。它是：

> 相对于当前 PF 仓库 full repository test contract 的真实 floor，但不是 PF 发布 runtime contract 的已证明 floor。

### 5.2 Floor 始终相对于 contract

本实验支持以下产品语义：

> A verified floor is always relative to the configured validation contract. Running a repository's full development suite may produce stricter floors than validating its distributable runtime behavior.

PF 不需要把 “full test” 与 “smoke test” 建模为产品级概念。PF 只需忠实执行用户声明的 verifier command；测试选择有多宽、哪些行为属于 compatibility contract，由用户负责。

### 5.3 两类自举实验

后续 PF 自举可以保留两类实验结果：

| 实验标签 | Validation contract | 回答的问题 | 当前状态 |
| --- | --- | --- | --- |
| `full-repository-contract floor` | 当前完整 `pytest --no-testmon` | 哪些依赖下界能通过整个开发、资格、CLI、E2E contract？ | 已完成；`packaging>=22` |
| `targeted-runtime-contract floor` | 明确选择 runtime/CLI/smoke-oriented tests | 哪些依赖下界足以支持拟发布 runtime 行为？ | 尚未执行 |

`targeted-runtime-contract floor` 是实验标签，不对应新的 PF 命令或产品模式。尤其不称为 `smoke floor`：现行 `pf smoke` 只验证 highest fresh resolution，不执行 floor search。

## 6. 非结论

本实验不证明：

- `packaging==21.3` 能满足 PF 的完整开发 contract；现有证据明确否定这一点；
- `packaging==21.3` 不能支持 PF 发布 runtime；本实验没有使用窄化 runtime contract 搜索；
- observer qualification tests 应从 PF 仓库删除；它们是开发与发布资格的有效测试；
- PF 应自动识别、跳过或重写“开发测试”；这会越过用户声明的 validation contract；
- PF 核心算法、harness relaxation 或 configured verifier authority 需要因本案例修改。

## 7. 后续实验记录要求

为了避免横向比较回答不同问题的 floor，后续真实仓库实验至少记录：

- verifier command 的完整 argv；
- 测试选择范围及其用途标签；
- source snapshot、Cell、policy/report identity；
- floor 是 full-repository contract 还是 targeted-runtime contract 的结果；
- 是否包含开发工具、qualification、lint/type、CLI、E2E 或外部服务测试；
- 未执行的 contract 不推断 floor。

本案例作为 contract-selection dogfood evidence 保留，不进入 PF 核心整改列表。

## 8. 当前单测钉死的依赖兼容性证据

截至本报告检查点，仓库只有两套显式、带真实版本坐标的 qualification matrix。普通单测主要负责钉死 committed manifest、production profile identity 和少量当前环境重放；它们不会在每次 `pytest` 时重新执行完整资格矩阵。因此这里区分：

- **committed dynamic qualification：** 矩阵曾在真实隔离环境中执行，结果提交为 manifest；
- **current-pin integration：** 单测实际调用当前安装版本，但没有跨版本参数化；
- **synthetic contract test：** 只构造 metadata、stdout 或 Schema，不形成依赖版本兼容性证据。

### 8.1 显式 qualification matrix

| Surface | 版本坐标 | 行为坐标 | 单测钉死的内容 | 证据边界 |
| --- | --- | --- | --- | --- |
| pytest observer core | Python `3.10/3.11/3.12` × pytest `6.2.5/7.0.1/7.4.4/8.0.2/8.4.2/9.0.2/9.1.1`，共 21 profiles | 每个 profile 12 cases，共 252 executions | protocol、完整坐标集合、总执行数和 `all_profiles_transparent` | 只证明 observer 注入对 exit、selection、hook outcome、执行顺序与 canonical telemetry 透明；不证明 PF runtime 支持这些 pytest 版本 |
| pytest current-plugin stack | Python `3.10/3.11/3.12` × pytest `9.1.1`，共 3 profiles | 每个 profile 12 cases，共 36 executions | 三个 Python minor、pytest `9.1.1`、透明性、case count；当前 ambient profile 还会重放并比对结果 digest | qualification 脚本与 manifest 记录 `pytest-cov==7.1.0`、`pytest-env==1.7.0`、`pytest-testmon==2.2.0`、`pytest-benchmark==5.2.3`、`pytest-xdist==3.8.0`，但单测没有逐项断言 plugin version identity；也没有以 `xdist -n` 执行 cases |
| uv diagnostic | host `linux-x86_64`、Python `3.11`、target `x86_64-unknown-linux-gnu` × uv `0.12.5` | 13 resolution failure shapes；2 个 `UNSAT`、11 个 `INDETERMINATE` | manifest protocol/profile 与 production registry 相等、13-case 集合、输出完整、恰有 2 个 `UNSAT`；另现场重放一个 pure contradiction case | 只资格化 uv `0.12.5` 的 resolution protocol/diagnostic classification；不是 PF 全功能、跨 OS 或跨 Python 兼容矩阵 |

证据入口：

- pytest：[qualification test](../../tests/test_pytest_observer_qualification.py)、[matrix manifest](../../tests/pytest_observer_qualification/matrix-manifest.json)、[runner](../../scripts/qualify_pytest_observer.py)；
- uv：[qualification test](../../tests/test_uv_qualification.py)、[matrix manifest](../../tests/uv_qualification/matrix-manifest.json)、[runner](../../scripts/qualify_uv.py)、[production profile registry](../../src/pf/resolution.py)。

### 8.2 被锁定的单点，不是矩阵

[`test_distribution_pins_qualified_runtime_tools`](../../tests/test_resolution.py) 明确断言发布依赖包含：

```text
uv==0.12.5
ty==0.0.74
```

`ResolutionRunContext` 还 fail-closed，只接受与 production profile 对应的 uv `0.12.5`。uv 因而同时具有 exact distribution pin 和独立 qualification manifest。

ty 只有 exact distribution pin、当前锁环境中的真实 CLI/E2E 调用，以及基于 synthetic JSON diagnostics 的 adapter/classifier 单测；不存在 `ty version × Python minor × diagnostic case` 的动态资格矩阵。当前 strong classifier 只 allowlist `unresolved-import` 与 `unresolved-attribute`，并要求 AST、snapshot origin 与 managed dependency 都可结构化恢复；`invalid-argument-type`/`invalid-type` 一类只覆盖 adapter 解析或 general evidence 路径，不形成强兼容性证据。相关入口为 [ty adapter tests](../../tests/test_ty_adapter.py)、[static transition tests](../../tests/test_static_transition.py) 和 [classifier](../../src/pf/static_transition.py)。

[`test_installed_module_cli_completes_report_lifecycle`](../../tests/test_end_to_end.py) 会让安装后的 PF 真实执行 uv、ty、search、explain、diagnose 与 apply。它证明当前锁定环境的一条完整 integration path，但仍只有一个 resolved dependency vector，不是跨版本矩阵。

### 8.3 不应误计为兼容性矩阵的测试数据

- observer protocol 单测把多个 pytest/Python version string 当作 metadata 输入，证明这些 metadata 不参与 disposition；它没有启动对应 pytest 版本；
- harness、terminal、report、resolution 单测中的 `packaging`、`pydantic`、`rich` 等版本多数是领域 fixture，用于排序、渲染、identity 或 Schema 行为；
- 名为 `canonicalizes_const_types_across_pydantic_versions` 的测试通过 monkeypatch 构造历史 Schema shape，没有安装或运行多个 pydantic 版本；
- 日常 full suite 在当前 lock 上通过，只证明当前 resolved vector；它不独立证明各 lower bound 或 predecessor。

因此目前没有独立动态版本矩阵覆盖 `cyclopts`、`packaging`、`pydantic`、`rich`、`tomli` 或 `tomlkit`。本次 `pf search` 为它们提供的是 full-repository-contract floor evidence，不应与专门的 dependency qualification matrix 混称。

### 8.4 后续扩展优先级

1. **targeted runtime contract：** 在 Python `3.10/3.11/3.12` 上重跑 runtime/CLI-oriented floor search，记录每个 runtime dependency 的 floor、紧邻 predecessor 与完整 resolution graph。
2. **ty qualification：** 建立 `ty==0.0.74 × Python minor × diagnostic case` 矩阵；至少覆盖 baseline stability、`unresolved-import`、`unresolved-attribute`、general type diagnostic、exit/JSON completeness 和 runtime witness，不依赖 message wording。只有准备更新 exact pin 时，才把新版本作为 pre-update candidate 加入资格实验。
3. **pytest ecosystem：** 给 current-plugin profile 增加 exact plugin identity 断言，并增加 pytest `8.4.2` 与其兼容 plugin stack 的 committed profile；若要声称 distributed execution，再单独加入真实 xdist cases。
4. **runtime libraries：** 围绕实际 ownership surface 分组验证：Cyclopts/CLI、Packaging/requirement parsing、Pydantic/Schema wire、Rich/terminal rendering、Tomli/Tomlkit/read-write preservation，而不是为每个包盲目复制整套 full suite。
5. **uv 与平台：** 现行 qualification 的版本轴只保留 exact pin `uv==0.12.5`；只有准备更新 pin 时才资格化新版本。同时补齐需要发布保证的 Python minor、Linux/macOS/Windows 和 target 组合。

这些扩展仍应明确记录 validation contract。矩阵中的一个 PASS 只证明对应版本坐标与行为坐标，不自动外推为整个 PF runtime 的 package floor。

### 8.5 Exact pin 的矩阵保留原则

`uv` 和 `ty` 的发布契约都是 exact pin，因此现行兼容性矩阵的 version dimension 应是 singleton。旧版本行不再是当前支持证据，不需要留在 active production registry、现行 manifest assertion 或日常 qualification job 中；如需追溯，可依靠 git 历史或归档实验报告。

Exact pin 只消除了“同时支持多个工具版本”的义务，不消除以下资格维度：

- 当前 pin 的输出协议、diagnostic shape 与异常终态；
- PF 声称支持的 Python minor、host/target platform；
- PF 真正消费的 adapter、classifier、runtime witness 和 E2E 行为。

更新 pin 的正确流程是：把新版本作为 candidate 执行完整当前-pin qualification，审核差分，更新 production pin/profile/manifest，然后把旧版本移出现行矩阵。pytest observer 不自动适用这一裁剪规则：pytest 来自用户声明的 verifier/harness ecosystem，harness relaxation 也可能实际选择多个版本；其版本矩阵应由 PF 声明的 observer qualification scope 单独决定。

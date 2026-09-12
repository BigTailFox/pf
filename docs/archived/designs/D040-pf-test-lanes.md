# D040 — PF 仓库测试车道与自举契约

- **状态：** 已完成并归档；2026-09-09 通过 AC1–AC10 验收，稳定规则已由现行 owner 接管；实施与证据见 [P043](../plans/P043-pf-test-lanes.md)
- **日期：** 2026-09-09
- **类型：** 已归档临时工程 Design；不再承担现行规范
- **目标 owner：** [D002](../../designs/D002-pf-implementation.md) §11
- **验收标准：** [§8](#8-验收标准)
- **来源：** [E001](../../experiments/E001-pf-self-bootstrap-validation-contract.md) 两类自举契约；[tests/README.md](../../../tests/README.md) 2026-09-08 单元/公开缝/资格分层；[R008](../reviews/R008-pf-search-performance-review.md) 完整 `test-command` 主导搜索墙钟；当时 D002 §11 把真实装配写成默认证据、未区分调度
- **关联：** [D001](../../designs/D001-pf.md) 的 configured validation contract `C` 与 `test-command` 语义不变；[D012](../../designs/D012-pf-harness-relaxation.md) / [D013](../../designs/D013-pf-pytest-observer.md) 资格矩阵仍在资格车道；[D039](D039-pf-static-evaluation-module.md) 只改静态测试表面，与本文件正交

本文保存已完成的仓库测试车道与自举 targeted-runtime `C` 迁移。用户要求实施后经 P043 规划与验收，稳定规则已归并 [D002](../../designs/D002-pf-implementation.md) §11、`tests/README.md`、`pyproject.toml` 与 CI。正文保留迁移时的目标与理由，不再承担现行规范。
种类、消费者与车道是仓库工程用语，不进入 [CONTEXT.md](../../../CONTEXT.md)。

## 1. 结论

现行默认收集只排除 `qualification`。日常 `pytest`、`[tool.pf] test-command` 与 CI 3.11/3.12 仍共用「进程内接口 + 真实 uv/ty/pytest/CLI 公开缝 + 安装后全流程」。2026-09-08 门禁约 86 秒 / 2557 项；含资格的覆盖率门禁约 320 秒。自举每个 Attempt 再跑近乎同一份默认套件，墙钟按探针次数相乘。

目标是三条消费者车道，而不是再把资格从默认里摘一次：

| 消费者 | 要回答的问题 | 收集范围 |
| --- | --- | --- |
| 日常开发 | 刚改的公开接口有没有坏 | 进程内产品测试 + 基建测试 |
| `pf search` 自举 | 该依赖向量是否仍支撑 PF **发布 runtime 的进程内公开接口** | 进程内产品测试；不含基建、真实进程、全流程、资格 |
| CI 门禁 | 提交是否仍满足完整工程契约 | PR：日常 ∪ 真实进程 ∪ 全流程；canonical Python：再加资格与覆盖率 |

证据质量与调度分家：所有车道都走公开 module seam；**真实 uv/ty/pytest/CLI 装配只作为进程/全流程/资格车道的证据**，不得再被日常或自举默认为充分条件。recording / scripted adapter 是进程内车道的合法 adapter，不能冒充进程车道已经跑过。

本仓库自举 `C` 采用 E001 的 **targeted-runtime-contract**：floor 相对于「PF 发布 runtime 的进程内公开接口」，不是 full-repository 开发套件。改变 `[tool.pf] test-command` 就是新的 `C`；既有 `package-floor.json` 不得被说成已经相对于新 `C` 验证。安装入口（wheel entry point、`python -m pf` 真实子进程）的正确性由 CI 的 `process`/`e2e` 车道承担，不由自举 floor 主张。

## 2. 范围与非目标

**接受并吸收后替换：**

| 目标 | 替换内容 |
| --- | --- |
| D002 §11 | 验证边界改为：公开 seam 对所有车道成立；adapter 真实性按车道；三条消费者的收集范围；需要网络/其他 Python/非宿主平台仍须标注 |
| `tests/README.md` | 现行分层表与验证命令改写为 §3 种类与 §4 消费者；历史计时保留为注明日期的记录，不再冒充现行默认 |
| `pyproject.toml` | 注册 marker；默认 `addopts` 与 `[tool.pf] test-command` 使用 §4 规范数组 |
| `.github/workflows/ci.yml` | 3.11/3.12 显式 `-m "not qualification"`；3.10 显式全量 `-m ""` 且带覆盖率；§5 门禁随这些 pytest 调用运行，不另增 step |

**保持不变：** D001 命令、退出码、`C` 的产品语义（floor 相对用户声明的 `test-command`）；D003 搜索；D005 分类；D012/D013 资格义务；coverage `fail_under = 90`；默认不启用 xdist。

**与 D039：** D039 改静态公开表面与叶子测试去向；本文件改调度与「真实 adapter 在哪条车道才算数」。两者都吸收时，§11 必须同时保留：D039 的静态表面句 + 本文件的车道句。D039 未吸收时，本文件先替换现行 §11 的调度/真实性句，静态观察句仍按现行 D002。

**本文件不覆盖：** 重跑 `pf search` 或替换入库 `package-floor.json`；R008 的 hints / single-flight / materialize / xdist failed-set；降低覆盖率门槛；新增 tox/nox 或第二套测试运行器；让 PF 自动改写任意项目的 `test-command`。吸收后可增加 `tests/**/*.py` 的短 Cursor 规则作**预防**（只指向 `tests/README.md` / D002 §11，不复制种类表）；规则不是门禁，不占验收项。

## 3. 测试种类

种类是 **pytest marker / 默认未标记**，不是新的产品命令。一条测试标它所证明的种类；资格回放只标 `qualification`。

| 种类 | Marker | 证明什么 | 允许的 adapter |
| --- | --- | --- | --- |
| 进程内产品 | 无（默认） | `pf` 公开 module 的接口结果：schema/identity、recording/scripted argv 与 outcome、CoordinateSearch/Runner、report/store/editor、CLI 的 in-process 调用 | 临时文件系统；uv/ty/verifier/process 的 recording 或 scripted adapter。产品编排器本身不替换 |
| 基建 | `infra` | 主体是开发/测试基建：`src/pf/_pytest_*.py` 的 hook 级行为、`scripts/check_docs.py` 不变式、testmon/coverage 工程配置 | 同进程内；仍不得启动 §5 所列外部进程 |
| 真实进程公开缝 | `process` | 真实 uv / ty / nested pytest / `python -m pf`（或安装入口 `pf`）的正向路径与 adapter 协议 | 真实 `SubprocessRunner` 与真实可执行文件 |
| 端到端全流程 | `e2e` | 安装后的多命令产品路径（例如 search → explain → apply，或对临时项目跑完一次 search 并读报告） | 同 `process`。此类测试必须同时标 `process` |
| 资格 | `qualification` | 现行 `scripts/qualify_*.py` 与矩阵回放 | 真实隔离环境；默认收集排除 |

`e2e` 是 `process` 的子集，单独成标是为了让 CI 或人工可以只跑全流程、或将来从 PR 拿掉全流程而不改分类规则。本草案的 PR job **包含** `e2e`。`e2e ⇒ process` 由 §5 机械检查持续保证，不依赖 Plan 首切片的逐文件表。

未标记与仅 `infra` 的测试 **不得** 启动 §5 的外部进程。进程内车道可以构造真实临时项目与文件，但不把它们交给真实 uv/ty/pytest/pf 子进程。

## 4. 消费者与命令

pytest 的 `-m` 后出现的覆盖先出现的（ini `addopts` 插入在 CLI 之前）。凡是比日常默认更宽或不同的收集，job **必须**在 CLI 上传入自己的 `-m`，不能依赖「不写 `-m` 就继承默认」。

日常与自举写入 `pyproject.toml` 的规范形态如下。`-m` 表达式是**一个** argv 元素。日常与自举表达式在 `not process` 之后保留 `not e2e`：在 `e2e ⊆ process` 成立时数学冗余，有意保留，使漏标 `process` 的 `e2e` 仍被这两条收集排除。

日常 `addopts`：

```toml
addopts = ["--testmon", "-m", "not process and not e2e and not qualification"]
```

不含 `--cov`、不含 `--maxfail=1`。此收集包含未标记测试与 `infra`。

自举 `[tool.pf] test-command`：

```toml
test-command = [
    "pytest",
    "--no-testmon",
    "--no-cov",
    "--maxfail=1",
    "-m",
    "not process and not e2e and not qualification and not infra",
]
```

`tests/README.md` 只复述此数组，不另立更宽或更窄的 `C`。

其余消费者：

| 消费者 | CLI `-m` | 其它固定项 |
| --- | --- | --- |
| CI 3.11 / 3.12（PR 门禁） | `not qualification` | `--no-testmon`；无 coverage |
| CI 3.10（canonical） | `""`（全量，含 `qualification`） | `--no-testmon --cov --cov-report=term-missing`；`fail_under = 90` 只在此 job |
| 资格回放 | `qualification` | `--no-testmon` |

PR 的 `not qualification` 等于日常 ∪ `process` ∪ `e2e`，因为 `infra` 已含在日常收集中（日常表达式不排除 `infra`）。

日常开发要跑真实进程时，显式使用 `-m "process and not qualification"` 或 `-m e2e`，并加 `--no-testmon`。这不是默认门禁。因 `e2e ⇒ process`，`-m "process and not qualification"` 含全部 `e2e`。

## 5. 外部进程判定与机械检查

必须打 `process`、`e2e` 或 `qualification` 的，是会把日常/自举墙钟乘上去的进程，不是测试树里出现的一切子进程：

- 生产 `SubprocessRunner.run`（`ProcessRunner` 的唯一生产实现）
- 测试代码启动 `uv`、`ty`、`python -m pf` / 安装入口 `pf`，或把 pytest 当作 **被测子进程**（`ConfiguredVerifier` / `python -m pytest` 跑另一份用例）

不算：同一 pytest 进程内的 `main()` / 公开 Python API、pytest hook 的 in-process 调用、recording / scripted adapter、`scripts/check_docs.py` 等测试树外的 git/文档检查、只做文本处理而不 spawn 的 helper（即使同模块里另有 `run_pf_cli` 一类 spawn 函数）、以 subprocess 直连的 git 等非上述可执行文件。

项目规则与 Cursor 规则只预防漏标，**不能**代替下面的门禁。反过来，机械门禁也不覆盖新增的裸 `subprocess` spawn 点（未走生产 `SubprocessRunner`、也不在 helper 名单内）；那种缺口由评审与项目规则拦截。不交付沿 `tests/` 调用图或 AST 过程间分析。

交付的机械检查必须同时满足：

1. **`e2e ⇒ process`（collection）。** 已收集 item 带 `e2e` 却不带 `process` 即失败。断言必须看见随后被 `-m` 取消选择的 `e2e` item，否则日常默认收集看不到它们。
2. **生产 runner（运行时）。** 未标 `process`/`e2e`/`qualification` 的 item 一旦进入生产 `SubprocessRunner.run` 即失败。只包这一处生产实现，覆盖经任意 helper 到达的真实 uv/ty/`ConfiguredVerifier`。禁止全局 monkeypatch `subprocess` / `Popen`。
3. **显式 spawn helper（运行时）。** `tests/` 里负责拉起 pf CLI / 真实 uv 或 ty 可执行文件 / 被测 pytest 子进程的函数，在入口检查当前 item 的 marker，未标则失败。Plan 首切片列出这些函数（现行至少 `run_pf_cli`）；实施时把直连 `subprocess` 拉起上述进程的测试收口到名单中的 helper，或给该测试打标。新增同类 helper 必须带同一入口检查。名单漏列视为未完成。
4. **执行点**是日常默认 pytest 与 PR 门禁的一部分，随这两次调用自动运行。不得只作为无人调用的脚本，也不得只挂在 3.10 canonical job。负向用例测试项标 `infra`（自举不收集这些测试项；`conftest` 陷阱本身随全部车道运行）。Plan 选定具体钩子，不另开产品 API。

## 6. D002 §11 目标正文

吸收后 §11 用下面这段替换现行「评价与产品 tests 通过 lower uv/…装配真实 Environment…」那段；静态观察句在 D039 未吸收前保留现行两句，吸收 D039 后再换成 D039 表面。

测试覆盖 public module behavior：strict Schema/identity、临时项目与文件系统、adapter argv/outcome、CoordinateSearch/Runner、report/store/editor transaction、CLI 与 wheel entry point。调用方和测试走同一公开表面。不直接构造 `PreparedEnvironment` 成功值，不替换 concrete prepare/lookup/collect/compare/evaluate/verify/minimize，不读取 evaluator/search private state。

车道只调度真实性，不另开测试专用产品 API：

- **进程内（默认收集与自举 `C`）：** 在已有 uv/candidate/ty/verifier/process seam 使用 recording 或 scripted adapter。CoordinateSearch 与产品编排器使用生产实现，下层按本车道替换。不得启动 §5 外部进程。
- **`infra`：** 同上真实性；主体是插件 hook、文档不变式或测试基建，不进入自举 `C`。
- **`process` / `e2e`：** 经真实 uv/candidate/ty/verifier 装配 Environment/Static/Runtime、Highest、Check 与 Search graph。只有这些车道（外加 `qualification`）可以主张「真实进程已经证明」。
- **`qualification`：** 工具协议与版本矩阵；不能用 fake、collection 或进程内测试冒充。

需要网络、其他 CPython minor 或非宿主平台的验证必须明确标注。

## 7. 自举 `C` 与既有报告

E001 已证明：把资格与开发基建写进 `test-command`，会得到相对 full-repository contract 为真、相对发布 runtime 未证明的 floor。本仓库的 `[tool.pf] test-command` 改为 §4 自举数组之后：

- 新的自举结果必须按 **targeted-runtime-contract** 记录（命令 argv、排除的种类、是否含基建/进程/资格）
- 入库或根目录 `package-floor.json` 若仍由更宽 `C` 产生，用户摘要与 `tests/README.md` 必须标明其 `C` 与现行 `test-command` 不同；不得把旧 floor 写成已经相对于新 `C` 验证
- 本次不要求重跑搜索。`pf check` 若因 `C` 身份变化而不接受旧报告，是契约变更的预期结果，不是本 Design 的缺陷
- 安装入口正确性由 CI 的 `process`/`e2e` 车道承担，不由自举 floor 主张

`infra` 排除的是「pytest 版本与文档基建进入 packaging 等 runtime 坐标」这类 E001 路径，不是允许进程内产品测试漏掉 pydantic/packaging/cyclopts/rich/tomli/tomlkit。那些库的公开接口测试留在未标记车道，属于自举 `C`。

## 8. 验收标准

| AC | 必须成立的目标 | 公开 seam / 证据 |
| --- | --- | --- |
| AC1 | `pyproject.toml` 注册 `process`、`e2e`、`qualification`、`infra`；无第四套平行分类名 | `tool.pytest.ini_options.markers` |
| AC2 | `tool.pytest.ini_options.addopts` 与 §4 日常 TOML 数组逐元素相等 | `pyproject.toml` |
| AC3 | `[tool.pf] test-command` 与 §4 自举 TOML 数组逐元素相等（`-m` 表达式为一个元素） | `pyproject.toml`；`ConfigLoader` 读入后的 command |
| AC4 | CI 3.11/3.12 显式 `-m "not qualification"` 且 `--no-testmon`、无 coverage；3.10 显式 `-m ""` 且带 coverage；3.10 的 `fail_under` 仍为 90 | `.github/workflows/ci.yml`；coverage 配置 |
| AC5 | §5 四条同时成立：collection 上 `e2e ⇒ process`（含被 `-m` 排除的 item）；未标记 item 进入生产 `SubprocessRunner.run` 则运行时失败且不补丁全局 `subprocess`；Plan 列出的 spawn helper 入口对未标记 item 运行时失败；检查随日常默认 pytest 与 PR job 自动运行。漏标的负向用例按现行契约作为安全网保留 | 日常 `pytest --no-testmon` 与 CI 3.11/3.12 命令实际执行该检查；故意漏标 `SubprocessRunner.run` / `run_pf_cli`（或名单中的等价 helper）失败；不出现调用图扫描器 |
| AC6 | Plan 首切片给出 `tests/test_*.py` 逐文件（必要时到 TestClass）种类表：默认 / `infra` / `process` / `e2e` / `qualification`；并列出 `tests/` 内 spawn helper 名单（至少含 `run_pf_cli`）；漏列未完成 | Plan 表；pytest `--collect-only` 与 marker 对照 |
| AC7 | 吸收后 D002 §11 含 §6 车道与真实性规则；`tests/README.md` 现行命令与分层表与 §3–§4 一致，历史计时标日期且不冒充默认 | owner 正文；`tests/README.md` 验证节 |
| AC8 | 日常 `--no-testmon` 收集项是自举收集项的真超集（多出的是 `infra`）；资格项不在前两者中 | `--collect-only` 三次 nodeid 集合 |
| AC9 | D001 正文、命令与退出码无因本文件产生的改写；PF 不自动改写用户 `test-command` | `docs/designs/D001-pf.md` diff 为空（本变更范围内） |
| AC10 | 用户摘要或 `tests/README.md` 标明现行自举 `C` 为 targeted-runtime-contract，并处理既有报告与新 `C` 的关系（重搜或标明历史 `C`） | README / `tests/README.md`；不要求本变更内出现新的 complete report |

停止条件（任一成立则未交付）：默认收集仍能启动 uv/ty/嵌套 pytest/pf CLI；自举 `C` 仍包含 `process`/`e2e`/`infra`/`qualification`；CI 3.11/3.12 依赖 ini 默认 `-m` 且实际只跑日常子集；覆盖率门槛改到日常收集上；用 fake 删除 `process`/`e2e` 车道却仍主张真实装配；把旧 `package-floor.json` 写成新 `C` 的 floor；§5 检查只存在于无人调用的脚本、只在 3.10 job 运行、或只靠项目/Cursor 规则；`e2e ⇒ process` 只写在 Plan 表里、日常与 PR 收集时不失败；用全局 `subprocess`/`Popen` 补丁充当 §5；交付沿 `tests/` 调用图或 AST 过程间分析来顶替 spawn helper 入口检查。

## 9. 明确不做

- 把进程内 recording 测试改称为已资格化的 uv/ty/pytest 协议证据
- 为日常收集单独降低 `fail_under`，或把资格覆盖率挪出 canonical job 后不再跑资格
- 引入 `runtime` 正标记去给近两千项进程内测试逐条打标
- 在产品里增加 “smoke floor” 或第二套 search oracle
- 修改 D013 矩阵范围或 uv/ty exact pin 流程
- 用全局 monkeypatch `subprocess`/`Popen` 当漏标门禁
- 把 AGENTS.md 或 Cursor 规则写成种类表副本，或把规则当作唯一门禁
- 沿 `tests/` 做调用图或 AST 过程间分析，作为漏标门禁
- 把机械门禁说成覆盖新增裸 `subprocess` spawn 点

## 10. 接受状态

已完成并归档。稳定规则由 [D002](../../designs/D002-pf-implementation.md) §11、`tests/README.md`、`pyproject.toml` 与 CI 拥有；实施与证据见 [P043](../plans/P043-pf-test-lanes.md)。

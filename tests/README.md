# PF 测试

本目录验证 PF 自身的接口行为。默认 pytest 收集范围是 `tests/`；`experiments/` 内第三方项目的
测试由对应实验运行。

本页拥有本仓库测试的种类、覆盖率并集、Host/Cell 展开、消费者、断言惯例与验证命令。从 [AGENTS.md](../AGENTS.md) 进入。
Adapter 真实性见 [D002 §11](../docs/designs/D002-pf-implementation.md#11-验证边界)。
注明日期的整理记录见 [history.md](history.md)。

## 组织与断言

种类是 pytest marker（或默认未标记），不是产品命令。一条测试只标它所证明的种类。不要用资格回放代替公开缝，也不要把纯 schema 断言绑在真实 uv/ty 上。PR 仍是日常 ∪ `process` ∪ `e2e`（`-m "not qualification"`）。

| 种类 | Marker | 默认日常 | 自举 `C` | 证明什么 |
| --- | --- | --- | --- | --- |
| 进程内产品 | 无 | 是 | 是 | 公开 module 的接口结果；schema/identity；CLI `create_app` / `main()`；recording / scripted adapter。同一公开接口的不同输入用 `parametrize` 展开。不得启动真实 uv / ty / nested pytest / `python -m pf` 或安装入口 `pf`，也不得进入生产 `SubprocessRunner.run` |
| 基建 | `infra` | 是 | 否 | 插件 hook、文档不变式、车道漏标安全网；必须直接驱动 `_secure_runlog` adapter 协议时也可标。不进入自举 `C` |
| 真实进程公开缝 | `process` | 否 | 否 | 真实 uv / ty / nested pytest；`python -m pf` / 安装入口的 help、调用错误、adapter 协议代表项。经 `TyAdapter` / `UvAdapter` / `ConfiguredVerifier` / spawn helper。不含产品命令路径 |
| 产品 CLI | `e2e`（且必须 `process`） | 否 | 否 | 真实子进程执行了 `smoke` / `check` / `search` / `minimize` / `apply` / `explain` / `diagnose` / `merge` 的产品路径（含该命令的产品级失败）。`-m e2e` 即全部真实产品 CLI；日常与自举仍排除。帮助、未知 option、非法 duration 与未知 `--package` 走 `create_app`，不标 `e2e` |
| 资格 | `qualification` | 否 | 否 | `scripts/qualify_*.py` 的工具协议 / 版本矩阵；committed manifest 由未标记测试对照现行 protocol 常量。仅在凭据变化时主动刷新；不进日常 / PR，canonical 全量覆盖率仍收集。不用于产品 Environment/Static/CLI 组合 |

本仓库的 `[tool.pf] test-command` 是 **targeted-runtime-contract**：只收集进程内产品测试，不含基建、真实进程、全流程或资格。改变该数组就是新的 `C`。根目录既有 `package-floor.json` 若仍由更宽的历史 `C`（当时默认只排除 `qualification`）产生，不得写成已经相对于现行 `test-command` 验证。安装入口正确性由 CI 的 `process` / `e2e` 车道承担。

- 用 `Test<Subject><Aspect>` 划分功能域，测试方法命名为 `test_<interface>_<outcome>`。
- 同一公开接口的不同输入在进程内用 `pytest.mark.parametrize` 展开。参数值本身已是可读语义时
  可保留自动 id；元组、布尔、整数代码必须显式 `ids=`。有先后依赖的缓存复用和多命令生命周期
  保留为连续场景，标 `e2e`。真实进程每种 adapter 协议留代表项；产品命令真实子进程标 `e2e`，
  每种命令保留覆盖独立进程风险所需的最少代表场景。新增真实进程测试须说明现有进程内或 adapter
  测试无法证明的风险，例如中断清理或跨命令持久化。资格矩阵只在凭据变化时重跑，
  不在资格层展开产品 Environment/Static/CLI 组合。
- 从公开返回值、异常字段、事件、输出协议或持久化产物验证行为。插件测试调用 pytest hooks 后
  检查协议文件；替身响应请求中已经分类好的环境/配置/outcome，包装真实 runner 的 recording
  不按 argv 识别 `ty check`。不写入 `CliContext` 私有 workflow 字段（经 `compose` 注入）；
  产品测试不进口 `pf._secure_runlog`，也不 patch `pf` 包内私有函数。真实 ty 进程经 `TyAdapter`。
- 精确断言 PF 拥有的协议、退出码、身份、安全边界和规定的视觉规则。specifier 与 marker 比较
  `SpecifierSet` / `PortableMarker` / `Version in specifier`，不比较库的格式化字符串。
- 合并前核对接口与结果路径。进程内结果、真实进程协议和资格矩阵不能相互替代。检查合并前后的已覆盖源码行和分支集合，而不只比较覆盖率百分比。
- 对真实进程、安装、嵌套 pytest、xdist 保留集成证据；帮助、未知 option、非法 duration 等调用表面走
  `create_app`，不标 `e2e`。完成等待与并发重叠窗口都不用固定睡眠；改用 Event / barrier 或许可队列上的可等待同步。
- 负向测试只覆盖现行契约要求的错误或安全行为在一个公开缝上的代表项，不按字段组合枚举，
  也不在 schema 构造、`create_app` 与真实子进程上重复同一拒绝。
- 未标记与仅 `infra` 的测试不得启动真实 uv / ty / nested pytest / `python -m pf`，也不得进入生产 `SubprocessRunner.run`。日常要跑真实进程时显式使用 `-m "process and not qualification"` 或 `-m e2e`，并加 `--no-testmon`。
- 需要网络、其他 CPython minor 或非宿主平台的验证必须明确标注。

## Host 与 Cell 展开

规划 Linux / Darwin / Windows 三类 **Cell target**，与在某个 **Host** 上运行，不是同一条轴。Cell 契约对三类 family **注入 target**；不为「当前是 Linux」跳过 Windows Cell 断言。产品测试不改全局 `sys.platform` 冒充 Host。

| 要证明的事 | 怎么测 | 车道 | 不做什么 |
| --- | --- | --- | --- |
| Cell 契约（marker、projection、apply、admission） | 进程内对 linux / darwin / win32 family **注入 target** | 未标记 | 不为「当前是 Linux」跳过 Windows Cell 断言 |
| 本机可执行哪些 Cell | 注入 `host_target`；D008 过滤 | 未标记 | 产品测试改全局 `sys.platform` 冒充 Host |
| `host_target()` 规范化与 fail-closed | owner 测试可 patch `sys.platform` / `machine` / `libc_ver`；覆盖受支持组合与未知输入 | 未标记 | 把该例外扩到产品 Cell 契约 |
| 安全目录、临时目录 `close()` 的 portable 契约 | 经 `RunLogStore` / `close()` 公开表面；**不得**进入生产 `SubprocessRunner.run` | 未标记 | 每个 workflow 复制三套 OS 用例；把真实进程停止写进未标记 |
| 能力 owner 的私有 Host adapter 协议 | 直接打内部 adapter | **仅** `infra` | 把其它 adapter 协议标进 `infra`；产品测试进口私有 adapter |
| 真实 `SubprocessRunner` 进程组停止、超时/中断、Windows PATH `which` | 进入生产 `SubprocessRunner.run` / 停止路径 | **仅** `process` | 标未标记、`infra` 或 `qualification`；当作 Host 发布资格 |
| 产品命令在该 OS 上的路径 | 与 Linux 相同的收集；命令路径标 `e2e` | `e2e`（且 `process`） | 用 PAL / 本机 Linux 冒充 Windows/macOS e2e |
| 工具协议 / 版本凭据 | `qualify_*.py` 与 committed manifest | `qualification` | 用资格矩阵证明产品 Host 行为或关闭 [R012](../docs/reviews/R012-pf-qualification-todo.md) 的 Host 资格项 |

Cell 契约展开要求 marker、projection、apply、admission 每个公开 seam 都有跨 linux / darwin / win32 family 的代表性正向断言；不要求把每条测试与三类 target 做笛卡尔积。

因本机 OS 跳过或改道，只用于**本 Host 不具备被测能力**的用例（symlink、FIFO、Windows DACL）。形式包括 `skipif`、运行时 `pytest.skip`、`if os.name` 早退。禁止用它们跳过「某个 Cell target 的产品规则」。

`host_target()` owner 测试控制该函数的 OS/machine/libc 输入；adapter 协议测试可控制该 adapter 的 OS 输入。这两种手法不得用到产品 Cell 契约。能力 owner 的 portable 契约只走其公开 interface；内部 dependency replacement 不成为新的产品 interface。

增加 Windows / macOS CI job 时：产品测试文件不乘三；该 job 执行同一收集，并补上本机才能走到的宿主私有分支。这只扩大覆盖率并集，不是真实 Host 发布资格。

## 验证

从仓库根目录、按 [AGENTS.md](../AGENTS.md#run-environment) 的环境要求执行。
[validate.py](../scripts/validate.py) 是本地与 CI 共用的完整验证入口，拥有各车道的命令组合；
`--dry-run` 显示命令而不执行。使用 `uv run` 使真实 uv/ty 公开缝能从 PATH 找到工具。
含文档检查的车道支持 `--base REF`，提交比较规则见 [文档验证](../docs/README.md#9-文档变更验证)。

### 迭代与交付

- 迭代先运行受影响的公开测试；日常增量可用 `uv run pytest -q`。指定文件或 nodeid 时使用
  `uv run pytest --no-testmon -q tests/<file>.py`，并按测试种类显式选择 marker。
  默认 marker 与 testmon 配置由 [pyproject.toml](../pyproject.toml) 拥有。
- 交付按下表选择所需完整车道；实际执行范围写入结果。增量选择、指定测试和完整车道各自证明
  自己的范围；testmon 未选中测试不构成全量 PASS。PF 自举的 `test-command` 仍独立定义 `C`。
- 已通过的检查，在所验证的代码、测试、配置、工具/依赖与相关环境未变化且范围满足本次要求时复用。
  记录命令、范围、源码状态（commit 及相关未提交改动）、环境和日志路径；有并发修改或状态不明时刷新。
  后续纯文档收尾只使相应文档检查失效。只有新修改、失败、未决风险或未覆盖的门禁才扩大或重跑验证。
- 同一变更可用较宽车道覆盖相同条件下的较窄车道；完成所需检查后停止。
  完整车道均禁用 testmon。脚本遇到首个失败即返回该命令退出码；修复后可单独重跑失败步骤及受影响步骤，
  保留前面仍适用的成功证据，逐项闭合该车道。

| 场景 | 命令 | 范围 |
| --- | --- | --- |
| 文档变更 | `uv run python scripts/validate.py docs` | 文档不变式（含工作区/暂存区 whitespace）与生成投影一致性 |
| 日常完整验证 | `uv run python scripts/validate.py daily` | Ruff（src/tests/scripts）、ty（src）、文档检查、进程内产品与基建 |
| PR（CI 3.11/3.12） | `uv run python scripts/validate.py pr` | 静态/文档检查、日常 ∪ process ∪ e2e、构建 |
| 覆盖率（CI 3.10） | `uv run python scripts/validate.py coverage` | 静态/文档检查、全量测试与分支覆盖率采集、构建；90% 门禁由合并 job 执行 |
| 真实进程补充验证 | `uv run python scripts/validate.py process` | process（含 e2e），排除 qualification |
| 资格凭据刷新 | `uv run python scripts/validate.py qualification` | qualification；仅凭据变化时主动刷新，另保留 canonical 全量覆盖率车道 |

PR/CI 的既定矩阵与覆盖率门禁仍须完成；局部迭代结果不能替代它们。新增工程脚本纳入 Ruff，
ty 的完整车道范围统一为 `src`，与现有 CI 一致。涉及 typing 配置或额外范围时按变更补充验证。

### 输出与证据

每次入口调用在 `tests/.cache/validation/` 下创建独立目录，保存每步完整 stdout/stderr。
同目录的 `manifest.json` 在每步开始与结束时原子更新：记录车道、cwd、比较基准、Python/平台、
已安装包版本、入口直接调用的外部工具路径/版本，以及每步 argv、起止时间、耗时、退出码和相对日志路径。
实际运行先将 `--base REF` 固定为 commit，清单同时保存原始 REF 与该 commit，检查器使用后者。
步骤状态区分 pending / running / passed / failed；未执行的步骤没有成功证据。
每步执行前后记录 HEAD、Git 状态、工作区/暂存区 diff 的 SHA-256，以及未跟踪文件内容摘要；
Git 忽略项和本次日志目录不参与源码记录。记录失败显式保存 error，`source_unchanged` 为 null。
这些记录辅助判断证据适用范围；步骤 passed 只表示命令成功。源码变化、未捕获的环境/外部输入
仍需按上述复用条件核对，摘要相同不自动授权复用。
终端报告命令、退出码和日志路径，失败时显示有界末尾；排查时再按错误读取相关片段。
`--log-dir PATH` 可指定日志父目录；CI 在成功或失败后上传日志。成功步骤的日志含原始测试计数，
最终报告引用实际计数与范围。信号退出映射为 `128 + signal`，启动失败为 127，中断为 130。

Plan 保存可恢复的状态和证据引用；共享或长期保留的验收证据应放入固定、可访问的产物位置，
本地 cache 路径只作为本地运行记录。`docs` 已检查 whitespace，相同输入下无需另跑 `git diff --check`。

覆盖率门禁是 **canonical Python 3.10、全量 `-m ""`、各 CI OS 数据的并集**，`fail_under = 90` 只在合并后的 `coverage report` 上生效。各 OS 的 pytest 只采集（`--cov-fail-under=0`），不按单宿主百分比卡关。增加 Windows / macOS 时把 CI `matrix.os` 扩进去即可，不必改门槛算法。单宿主跑不到的平台私有分支由对应 OS job 补上，不要用 `# pragma: no cover` 或降低 90% 代替。

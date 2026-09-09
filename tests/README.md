# PF 测试

本目录验证 PF 自身的接口行为。默认 pytest 收集范围是 `tests/`；`experiments/` 内第三方项目的
测试由对应实验运行。

本页拥有本仓库测试的种类、消费者、断言惯例与验证命令。从 [AGENTS.md](../AGENTS.md) 进入。
Adapter 真实性见 [D002 §11](../docs/designs/D002-pf-implementation.md#11-验证边界)。
注明日期的整理记录见 [history.md](history.md)。

## 组织与断言

种类是 pytest marker（或默认未标记），不是产品命令。消费者与收集范围见 [D002 §11](../docs/designs/D002-pf-implementation.md#11-验证边界)。不要用资格回放代替公开缝，也不要把纯 schema 断言绑在真实 uv/ty 上。

| 种类 | Marker | 默认日常 | 自举 `C` | 证明什么 |
| --- | --- | --- | --- | --- |
| 进程内产品 | 无 | 是 | 是 | 公开 module 的接口结果；schema/identity；CLI `create_app` / `main()`；recording / scripted adapter |
| 基建 | `infra` | 是 | 否 | 插件 hook、文档不变式、车道漏标安全网；必须直接驱动 `_secure_runlog` adapter 协议时也可标 |
| 真实进程公开缝 | `process` | 否 | 否 | 真实 uv / ty / nested pytest；`python -m pf` / 安装入口的 help、调用错误、adapter 协议代表项 |
| 产品 CLI | `e2e`（且必须 `process`） | 否 | 否 | 真实子进程执行了 `smoke` / `check` / `search` / `minimize` / `apply` / `explain` / `diagnose` / `merge` 的产品路径 |
| 资格 | `qualification` | 否 | 否 | `qualify_*.py` 的工具协议 / 版本矩阵；不是产品 process 溢流，也不是定期回归 |

本仓库的 `[tool.pf] test-command` 是 **targeted-runtime-contract**：只收集进程内产品测试，不含基建、真实进程、全流程或资格。改变该数组就是新的 `C`。根目录既有 `package-floor.json` 若仍由更宽的历史 `C`（当时默认只排除 `qualification`）产生，不得写成已经相对于现行 `test-command` 验证。安装入口正确性由 CI 的 `process` / `e2e` 车道承担。

- 用 `Test<Subject><Aspect>` 划分功能域，测试方法命名为 `test_<interface>_<outcome>`。
- 同一公开接口的不同输入在进程内用 `pytest.mark.parametrize` 展开。参数值本身已是可读语义时
  可保留自动 id；元组、布尔、整数代码必须显式 `ids=`。有先后依赖的缓存复用和多命令生命周期
  保留为连续场景，标 `e2e`。真实进程每种 adapter 协议留代表项；产品命令真实子进程标 `e2e`，
  每种命令至多一条代表项。资格矩阵只在凭据变化时重跑，不在资格层展开产品 Environment/Static/CLI 组合。
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

## 验证

从仓库根目录执行；运行环境要求见 [AGENTS.md](../AGENTS.md#run-environment)。
`pyproject.toml` 的默认 `addopts` 为 `--testmon` 与 `-m "not process and not e2e and not qualification"`（含 `infra`）。
自举 `[tool.pf] test-command` 另排除 `infra`。凡是比日常默认更宽的收集必须在 CLI 上传入自己的 `-m`。
`PATH` 需包含仓库 `.venv/bin`，以便真实 ty/uv 公开缝能解析到工具。

```sh
# 日常：进程内产品 + 基建，不含真实进程 / 全流程 / 资格
uv run pytest --no-testmon -q --durations=30
uv run ruff check tests
uv run ty check
git diff --check

# 真实进程公开缝（含 e2e；不是日常默认）
uv run pytest --no-testmon -q -m "process and not qualification"

# 资格凭据刷新（更换受支持的 uv/pytest 版本、矩阵 case 或绑定字段时；也可直接跑 scripts/qualify_*.py）
uv run pytest --no-testmon -q -m qualification

# PR 门禁（3.11/3.12）：日常 ∪ process ∪ e2e
uv run pytest --no-testmon -q -m "not qualification"

# 本机采集覆盖率（canonical Python 全量；不过 90% 门禁）
uv run pytest --no-testmon --cov=pf --cov-report=term-missing --cov-fail-under=0 -m ""
```

`--no-testmon` 确保本轮全量执行。日常增量执行仍可使用默认 testmon。

覆盖率门禁是 **canonical Python 3.10、全量 `-m ""`、各 CI OS 数据的并集**，`fail_under = 90` 只在合并后的 `coverage report` 上生效。各 OS 的 pytest 只采集（`--cov-fail-under=0`），不按单宿主百分比卡关。增加 Windows / macOS 时把 CI `matrix.os` 扩进去即可，不必改门槛算法。单宿主跑不到的平台私有分支由对应 OS job 补上，不要用 `# pragma: no cover` 或降低 90% 代替。

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
| 进程内产品 | 无 | 是 | 是 | 公开 module 的接口结果；recording / scripted adapter |
| 基建 | `infra` | 是 | 否 | 插件 hook、`check_docs.py`、车道漏标安全网 |
| 真实进程公开缝 | `process` | 否 | 否 | 真实 uv / ty / nested pytest / `python -m pf` |
| 端到端全流程 | `e2e`（且必须 `process`） | 否 | 否 | 安装后的多命令产品路径 |
| 资格 | `qualification` | 否 | 否 | `scripts/qualify_*.py` 与矩阵回放 |

本仓库的 `[tool.pf] test-command` 是 **targeted-runtime-contract**：只收集进程内产品测试，不含基建、真实进程、全流程或资格。改变该数组就是新的 `C`。根目录既有 `package-floor.json` 若仍由更宽的历史 `C`（当时默认只排除 `qualification`）产生，不得写成已经相对于现行 `test-command` 验证。安装入口正确性由 CI 的 `process` / `e2e` 车道承担。

- 用 `Test<Subject><Aspect>` 划分功能域，测试方法命名为 `test_<interface>_<outcome>`。
- 同一接口结果路径的不同输入用 `pytest.mark.parametrize`，用语义化 `ids` 区分边界和故障。
  有先后依赖的状态迁移、缓存复用和报告生命周期保留为连续场景。昂贵真实进程矩阵只在资格层展开。
- 从公开返回值、异常字段、事件、输出协议或持久化产物验证行为。插件测试调用 pytest hooks 后
  检查协议文件；替身响应请求中的环境配置，不复制被测代码的命令识别逻辑。
- 精确断言 PF 拥有的协议、退出码、身份、安全边界和规定的视觉规则。依赖库的显示顺序和提示
  文案用结构化语义验证；例如比较 `SpecifierSet`，而非其格式化字符串。
- 合并前核对接口与结果路径。进程内结果、真实进程协议和资格矩阵不能相互替代。检查合并前后的已覆盖源码行和分支集合，而不只比较覆盖率百分比。
- 对真实进程、安装、嵌套 pytest、xdist 保留集成证据；普通 CLI 输入矩阵走公开入口。
  完成等待使用输出事件与有上限的等待，避免以固定睡眠推断线程已经完成。
- 负向测试只覆盖现行契约要求的错误或安全行为，不枚举已删除的旧语法。
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

# 资格回放（真实 uv/ty/observer/CLI 脚本）
uv run pytest --no-testmon -q -m qualification

# PR 门禁（3.11/3.12）：日常 ∪ process ∪ e2e
uv run pytest --no-testmon -q -m "not qualification"

# 3.10 覆盖率门禁（含资格；与 CI 一致，fail_under=90）
uv run pytest --no-testmon --cov=pf --cov-report=term-missing -m ""
```

`--no-testmon` 确保本轮全量执行。日常增量执行仍可使用默认 testmon。

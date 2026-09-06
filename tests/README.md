# PF 测试

本目录验证 PF 自身的接口行为。默认 pytest 收集范围是 `tests/`；`experiments/` 内第三方项目的
测试由对应实验运行。行为依据见 [D002 的模块与测试 seam](../docs/designs/D002-pf-implementation.md)，
契约演进遵循 [AGENTS.md](../AGENTS.md)。

## 组织与断言

- 用 `Test<Subject><Aspect>` 划分功能域，测试方法命名为 `test_<interface>_<outcome>`。
- 同一接口结果路径的不同输入用 `pytest.mark.parametrize`，用语义化 `ids` 区分边界和故障。
  有先后依赖的状态迁移、缓存复用和报告生命周期保留为连续场景。
- 从公开返回值、异常字段、事件、输出协议或持久化产物验证行为。插件测试调用 pytest hooks 后
  检查协议文件；替身响应请求中的环境配置，不复制被测代码的命令识别逻辑。
- 精确断言 PF 拥有的协议、退出码、身份、安全边界和规定的视觉规则。依赖库的显示顺序和提示
  文案用结构化语义验证；例如比较 `SpecifierSet`，而非其格式化字符串。
- 合并前核对接口与结果路径。不同层级有各自职责：schema 准入、adapter 协议消费和真实进程
  透明性不能相互替代。检查合并前后的已覆盖源码行和分支集合，而不只比较覆盖率百分比。
- 对真实进程、安装、嵌套 pytest、xdist 保留集成证据；普通 CLI 输入矩阵走公开入口。
  完成等待使用输出事件与有上限的等待，避免以固定睡眠推断线程已经完成。

## 验证

从仓库根目录执行；运行环境要求见 [AGENTS.md](../AGENTS.md#run-environment)。

```sh
uv run pytest --no-testmon -q --durations=30 --cov=pf
uv run ruff check tests
uv run ty check
git diff --check
```

`--no-testmon` 确保本轮全量执行。日常增量执行仍可使用默认 testmon。

## 2026-09-06 整理记录

基线依赖与源码对应 `772dbb7`；本轮修改测试、测试收集配置和本页。

| 治理项 | 保留的行为证据 |
| --- | --- |
| observer 私有状态断言 | hooks → summary/detail/progress；包括非法 nodeid、失效后继续收到报告、数量边界和 xdist 角色 |
| 重复 verifier/schema/进程用例 | 参数矩阵覆盖正常退出、超时、信号、启动失败、非法终止事实及终端尺寸来源 |
| 重复 observer 资格回放 | 完整回放一次，同时检查外层 pytest 进度不被嵌套资格运行覆盖；原 E001 的两个测试入口合并至 `TestPytestObserverQualificationRunner.test_run_replays_current_profile_with_isolated_nested_progress` |
| CLI 配置错误矩阵 | `main()` 的退出码、错误语义和无副作用；安装级入口测试继续保留 |
| 项目安装路径中的命令循环 | smoke/check/search/minimize × 缺失/空测试组独立参数项；安装与 verifier 实际运行，search/minimize 分别验证报告 |
| 安装日志中的内部命令计数 | 由 `TestOptionalGroupPreparation.test_empty_harness_all_roles_and_cache` 验证 prepare、安装与缓存职责；端到端检查 verifier 和报告结果 |

全量命令为 `uv run pytest tests --no-testmon -q --durations=30 --cov=pf`，实际运行额外输出了
JUnit 与 coverage JSON 供逐项比较。它与默认收集范围相同。

| 指标 | 治理前 | 治理后 |
| --- | --- | --- |
| 测试方法/函数 | 1168 | 1140 |
| TestClass | 124 | 132 |
| 类外测试 | 22 | 0 |
| 参数展开后的通过项 | 2246 | 2249 |
| 含覆盖率的完整运行 | 58.59 秒 | 50.81 秒 |
| 语句与分支综合覆盖率 | 90.46% | 90.47% |

展开后的项数略增来自命令循环展开与重复完成通知场景，函数减少并不要求参数项减少。
基线已覆盖的源码行与分支均保留。耗时是同机单次运行对照，受安装缓存与调度影响；未降低
覆盖率门槛或跳过慢测。Ruff、ty 与 whitespace 检查通过。

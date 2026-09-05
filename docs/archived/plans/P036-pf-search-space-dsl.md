# P036 — search-space 系列切片 DSL 实施计划

- **状态：** 已完成、归档
- **日期：** 2026-09-05
- **依据：** [D030](../designs/D030-pf-search-space-dsl.md)，已接受
- **基线：** `bfe9947`；初始工作区有用户修改的 AGENTS.md、docs/README.md 和未跟踪 D030，保留其内容。

## 1. 有序切片与验收映射

| 切片 | 接口、ownership 与迁移 | AC | 测试与证据槽 | 状态 |
| --- | --- | --- | --- | --- |
| S1 | 纯 search_space parser/typed value、条件默认表、ConfigLoader/PackagePlan 绑定及 Search 准入 | 1–4、7 | public parser/config/loader/workflow tests；默认/显式、raw layer 验证、Cell 下界、scope/越界 | 完成 |
| S2 | CandidateProvider query 同次返回 release_versions+candidates；UvAdapter 冻结；CandidateBuilder 过滤/采样与 selection | 4–6、10 | adapter 与 builder public tests；稀疏系列、epoch、不可用系列占位、最高代表、source failure、build disposition | 完成 |
| S3 | SearchSpaceResolutionError、Runner/Scheduler/CLI 异常收尾；报告不写入 | 7 | 并发已完成/在途/未派发任务、日志、现有报告不变与无报告不创建 tests | 完成 |
| S4 | 报告级规范策略分组、系列观测 intern/ref、reader 派生 selection 与 identity；merge/reintern/update | 8、13 | public report roundtrip/tamper/merge tests；host-partial、去重/体积、可达性、不同观测保留 | 完成 |
| S5 | ApplyAuthorizer 完整策略校验；Explain validated typed projection；Schema/examples 生成 | 8–9 | apply original/projected/repeated/force tests；offline explain；generator --check | 完成 |
| S6 | E004 冻结事实离线范围演示；README 双语、CLI help；D001/D002/D003/D006/D008/D014 吸收 | 10–12 | search public fixture 与离线 idna 范围证据；文档/生成物检查 | 完成 |
| S7 | focused、Python 3.10/3.11/3.12 全套、coverage≥90%、Ruff、ty、build；逐项审计、D030/P036 同步归档 | 1–13 | 准确命令与结果、最终 AC 表、链接/whitespace、工作区范围 | 完成 |

## 2. 实施约束与决定

- 本 Plan 在生产修改前建立；以 D030 为唯一目标，直接替换旧 space/interface/wire，不增加兼容别名或双读。
- 保持 Schema 1、既有 v1 identity 前缀与 algorithm v1；新的系列观测使用设计规定的 v1 内容寻址。
- 测试通过 public seam 验证语义，不保留枚举旧配置语法的迁移测试；错误/安全行为保留负向测试。
- 报告策略包含未选默认分支；selection 由原 declarations/baseline 派生，observations 只保存必要 scope。
- 不运行完整 requests search；E004 只作冻结事实的离线范围证据，不声称新的完整 PASS/floor。
- 遵循当前 AGENTS.md：PF 命令与测试在仓库根目录沙箱外执行；每次检查命令风险后申请相应执行权限。
- 未请求 commit/push，本次不提交用户的 AGENTS.md 改动。

## 3. 行动、决定与验证记录

- 2026-09-05：复核 D030、当前 ConfigLoader/CandidateProvider/report wire、既有测试与 P035 验证命令，建立 P036。
- 后续每个切片在此追加准确命令、结果、修复结论与偏差；未获得的验证不记为通过。
- S1：新增纯 DSL AST/parser/default binding/position slicing；ConfigLoader 保留省略并整对象继承默认表，PackagePlan 绑定完整策略；Search workflow 在 snapshot 前准入。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_search_space.py`：33 passed。
- 初轮 config/project 为112 passed/6 failed，均是旧默认值、旧组合限制与 dump 断言；按目标契约迁移，移除旧组合限制测试，补充默认表 raw validation 和全部组合。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_search_space.py tests/test_config.py tests/test_project.py`：166 passed。
- S2 进行中：query 返回 RegistryCandidates；adapter 在兼容过滤前收集 release_versions，仅成功解析后冻结响应；CandidateBuilder 使用纯 selection 过滤再采样，冻结必要系列观测。报告 codec 尚待 S4 迁移，当前不宣称全套可用。
- S2–S5 已落地：报告级完整策略分组、必要系列观测内容寻址、reader 离线派生与 candidate policy 复算；merge/update 清理不可达观测；apply 完整策略校验与默认下幂等；Explain 使用 validated projection。Scheduler 遇异常停止新增派发并 drain，Runner 异常路径 finalize journal，workflow 不进入 report/association 写入。
- 阶段定向集曾取得 453 passed；后续继续增加验收测试，最终可复跑的精确集合与命令见下。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_authorization.py tests/test_projection.py tests/test_diagnose.py`：83 passed。
- 第一轮全套 `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests`：1700 passed、1 failed；installed CLI 在 baseline 返回 Indeterminate。临时目录已轮换，原因未确认；未改生产代码的 `tests/test_end_to_end.py` 单项重试 1 passed。保留初次失败，不将其推定为网络问题。
- 新报告真实 roundtrip 的 JSON Schema 验证揭示生成器原先统一删除 null 与 required nullable wire 不一致：由字段 metadata 标记两个保留 null 的字段，生成器移除其内部标记并只为其保留 nullable，保留其他字段既有 required 规则。候选快照与 requested policy 的真实报告均加入生成 Schema 验证。
- 报告体积 fixture（canonical compact UTF-8）：一 Cell 10,743 bytes，两 Cell 17,776 bytes；两者均一策略组、一份系列观测。新增 Cell 增加候选/运行证据及观测 ref，不重复策略或系列 keys；不保存 selection 或完整 registry response。
- 最后补测曾为 273 passed、2 failed：新增 active-declaration fixture 错误地在同一 base 位置放重叠声明。按现行项目契约改为互斥 marker + base/extra 合取；未放宽生产准入。Ruff/ty 已通过，三 Python 全套与 coverage 正在最终验证。
- S6：稳定规则已吸收入 D001/D002/D003/D006/D008/D014，README 双语与 search/minimize help 同步；E004 离线范围演示与最终链接检查随后记录。
- E004 §11：用 `.venv/bin/python -` 只读历史 requests JSON，提取 idna 41 个代表、原下界 2.5 和真实 baseline 3.19；纯 evaluate 输出 major keys 0/1/2/3 → 1/2/3，`contains(0.2)=False`，结果 `/tmp/pf-d030-idna-range.json`。不联网、不重跑 requests、不改写历史报告；已保存代表不是完整过滤前 registry inventory 的证明。
- 最后补充的 public tests 覆盖：稀疏 minor 的 epoch/major scope、unbounded 的真实 baseline 离线派生、合法 inventory hash 的错 scope 拒绝，以及重算 snapshot digest 后仍拒绝 opaque policy 伪造。日志测试实际保存两个 Cell 的 Process Log，并核验旧 report-generation locator 不变。新 fixture 曾误用 model_dump 的 optional null 与日志绝对/相对路径，已按现行 wire/lookup 契约修正，未放宽生产校验。
- 最终定向命令：`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_search_space.py tests/test_search_space_report.py tests/test_search_space_workflow.py tests/test_candidates.py tests/test_config.py tests/test_scheduling.py tests/test_cli.py tests/test_report_artifacts.py tests/test_uv_adapter.py`：279 passed，2.66s。
- 三 Python 初轮各 1728 项；3.11/3.12 全通过，3.10 在 qualification subprocess 得到 pytest internal-error 退出 3；单独重跑 1728 passed，coverage 90.41%。并发共享测试缓存是可能因素，未取得其内部 traceback，不认定原因。后续新 fixture 修正后以串行矩阵复证最终树。
- 收敛期间一次全套仍加载了修改前的新日志 fixture，报告两项相对路径断言失败；另一次运行期间收敛生成器导致其已加载版本与新生成物不同，只有 generator --check 失败。随后固定代码/生成物并串行复证，不将这两次记为通过。

### 最终验证命令与证据

所有 pytest/PF/uv 命令均在 `/home/llh/pf`、沙箱外执行；三个 Python 全套串行，测试命令不依赖 testmon 增量。

| 检查 | 准确命令 | 结果 |
| --- | --- | --- |
| Python 3.10 + coverage | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short --cov=pf --cov-report=term-missing tests` | 1732 passed，36.29s；coverage 90.40% |
| Python 3.11 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.11 --group test pytest --no-testmon -q --tb=short tests` | 1732 passed，29.81s |
| Python 3.12 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.12 --group test pytest --no-testmon -q --tb=short tests` | 1732 passed，30.86s |
| Ruff | `.venv/bin/ruff check src tests scripts` | All checks passed |
| ty | `.venv/bin/ty check` | All checks passed |
| 生成物 | `.venv/bin/python scripts/generate_report_schema.py --check` | exit 0；真实含默认/null-ref 报告另经 Draft202012Validator 校验 |
| 构建 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build` | 归档后的最终树成功生成 `dist/package_floor-0.1.0.tar.gz` 与 `dist/package_floor-0.1.0-py3-none-any.whl` |
| whitespace | `git diff --check`，另用 `.venv/bin/python -` 扫描 changed/untracked 文件尾随空格和冲突标记 | exit 0；52 个修改/新增文件通过，排除保留的用户 AGENTS.md |
| Markdown links | `.venv/bin/python -` 扫描 README 双语及 docs Markdown 的相对链接，按文档目录解析去除 fragment 后检查存在性 | 归档后 83 files；两处既有 E003 `expirements/requests` 链接失效，本次修改文档 0 失效 |

完整测试输出：`/tmp/pf-d030-py310-final.log`、`/tmp/pf-d030-py311-final.log`、
`/tmp/pf-d030-py312-final.log`；定向与构建输出分别为 `/tmp/pf-d030-final-focused.log`、
`/tmp/pf-d030-build.log`。临时路径不是 portable product evidence，当前结果在本 Plan 固化。

## 4. 验收审计

| AC | 证明要求 | 当前证据/结论 |
| --- | --- | --- |
| 1 | 配置与 parser、默认表 raw validation | test_search_space/test_config：DSL/specifier/空白、全部 space×step；非法表即使 root/dep 被覆盖仍拒绝。通过 |
| 2 | 层级继承与逐 Cell 默认分支 | TestSearchSpaceConfiguration + active_base_extra_and_markers：整表继承、显式优先、step-only、内建/自定义逐 Cell 分支。通过 |
| 3 | active 下界合取与 fixed 资格保持 | 同上实际 ProjectLoader 矩阵；strict 端点取最大值，fixed~= 不进入策略；unpublished endpoint 系列定位。通过 |
| 4 | 系列切片、scope、anchor 与越界 | TestSearchSpace：稀疏 major/minor、epoch、半开/开放/空/越界、缺 anchor/跨 scope、[:]。通过 |
| 5 | 同次 registry 冻结、过滤前系列与 failure | test_uv_adapter + filtered_series_still_occupy_offset_positions：yanked/pre/incompatible 占位、失败后重查、成功冻结。通过 |
| 6 | 先 space 后 step、最高合格精确代表 | TestCandidateBuilder：多 release 合格/不合格混合取最高，major+major、minor+patch、artifact/upper/pre/baseline 保持。通过 |
| 7 | 准入错误/求值错误、并发收尾、日志/报告 | test_search_space_workflow/test_scheduling/test_cli：非宿主准入先于 snapshot；smoke/check 可运行；退出 2/3；停止派发/drain/cleanup，日志与旧关联保留，报告无创建/更新。通过 |
| 8 | wire/identity/reader、generation、tamper/merge | test_search_space_report/test_report_schema/test_report_artifacts：真实 baseline/原声明派生，策略与重算 snapshot 伪造拒绝、scope/ref、host-partial generation、anchor 改变同代表 identity 不同。通过 |
| 9 | apply 策略/幂等/force 与 offline explain | 默认 original→projected→NOOP；未选默认分支修改 force/no-force 均拒绝；既有 artifact/evaluation authorization tests；Explain 禁用 parser/project/registry 仍展示。通过 |
| 10 | 空间外 build failure 排除、空间内仍停止 | TestSearchSpaceBuildDisposition：all 内历史 build failure 为 Indeterminate；排除后取得 full/final PASS，不弱化边界。通过 |
| 11 | E004 idna 冻结范围演示 | E004 §11 与本 Plan 离线输出；仅范围证据。通过 |
| 12 | 全套质量门禁、owner 吸收与归档 | owner/README/help 已吸收；三 Python 各 1732 passed、coverage 90.40%、Ruff/ty/build/schema 通过；D030/P036 同步归档。通过 |
| 13 | 策略分组、系列去重、引用闭合与体积 | 两 name 同策略一组；两 Cell 共享观测一份；不同历史两份，update 清理；错配/悬空/不可达拒绝；10,743→17,776 bytes，两表数量不变。通过 |

## 5. 完成与交付范围

AC1–13 已逐项闭合；D030/P036 于 2026-09-05 同步移入 archived，相关相对链接与现行/归档索引已修复。
稳定规则由 D001/D002/D003/D006/D008/D014 接管；独立 registry 分析 CLI 待办仍在现行索引。
保持 Schema 1/v1 前缀，旧缺 required 策略/观测/ref 的报告须重新 search 生成，不提供兼容 reader。
未修改历史 requests 报告或运行完整 requests search。未 commit/push；用户原有 AGENTS.md 改动保持原样。

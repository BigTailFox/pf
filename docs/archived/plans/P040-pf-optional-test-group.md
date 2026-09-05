# P040 — PF Optional Test Group 实施计划

- **状态：** 已完成并归档；AC1–AC12 均通过
- **日期：** 2026-09-06
- **Design：** [D035](../designs/D035-pf-optional-test-group.md)，本次用户授权修订并实施
- **基线：** cdeb37b；已有 D035 草案与 docs/README 索引改动，纳入本任务，不提交

## 1. 切片与验收映射

| 顺序 | 接口、实现、文档与测试工作 | D035 AC | 证据槽 |
| --- | --- | --- | --- |
| S1 | ConfigLoader optional intent；ProjectLoader dev/test 选择与 PackagePlan selected name；移除 workflow/runner group gate；config/project/validation/workflow 正向测试 | 1–4、8 | 已完成；§3–§4 |
| S2 | EnvironmentFactory active IDs 分支；独立 baseline 校验；optional final environment plan；adapter install stage；highest/lowest/exact、cache、条件 harness、overlap、failure tests | 5–8、11 | 已完成；§3–§4 |
| S3 | EnvironmentIdentity/Proposal/reader required-nullable evidence；wire serializer/schema null 保留；policy generation/merge/update/apply；schema/examples generator | 9–10 | 已完成；§3–§4 |
| S4 | 无 group 真实 CLI smoke/check/search/minimize verifier 与安装证据；README 双语、owner D001/D002/D005/D006/D012/D014；完整回归 | 8、11–12 | 已完成；§3–§4 |
| S5 | 逐项 AC 审计；稳定规则吸收；D035/P040 同步归档及索引、链接校验 | 全部 | 已完成；§3–§4 |

## 2. 决策与行动

- review 确认当前固定 test 默认与 group gate、恒定第二次 resolution。
- 补齐 D035：required-nullable 字段必须穿过 exclude_none wire serializer 与移除 null 的 Schema generator；复用现有 preserve-null 机制。
- 补齐 D035：baseline Cell/active IDs/空 observations 校验移到分支前，避免快路绕过原 relax_harness 准入。
- 实施顺序 S1 → S2 → S3 → S4 → S5；失败证据始终只保存实际取得的 plan，不伪造 environment digest。

- S1–S3 已完成：optional intent/selected name、workflow/runner gate 删除、active IDs 快路、独立 baseline 校验、nullable identity/wire/reader、policy facts 与生成物。
- InstallFailure.stage 明确要求 caller 提供 project/environment 类型；UvAdapter 从实际 plan.kind 提供，recording adapters 同步迁移。
- S4 owner D001/D002/D005/D006/D012/D014 与双语 README 已吸收目标规则；无 group CLI fixture 对 smoke/check/search/minimize 验证安装项目与 idna 后实际执行 import/assert。
- 初轮测试暴露的旧 fake environment evidence、旧 stage、Schema nullable 白名单已按目标语义迁移；reader 额外 null path allowlist 与 serializer/schema 同步，不放宽其他 optional 字段。
- 真实 missing verifier 返回既有 exit 4（非初写测试假定的 2）；报告 authority 为 configured-verifier/start-failed，cause TOOL_FAILURE、stage test、Indeterminate。修正测试，不改变退出语义。

## 3. 验证与证据

CLI 与测试按 AGENTS.md 在仓库根目录沙箱外运行，使用 UV_CACHE_DIR=/tmp/pf-uv-cache。
门禁：focused tests、Ruff、ty、Python 3.10/3.11/3.12 全套、3.10 coverage ≥90%、uv build、
schema/examples --check、Markdown 本地链接、git diff --check。每轮命令与结果在本节补记。

### 3.1 切片检查

- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_config.py tests/test_project.py tests/test_environment.py tests/test_report.py tests/test_report_schema.py tests/test_check.py tests/test_verification.py tests/test_search_workflow.py tests/test_validation_surfaces.py`：初次 47 failed / 272 passed / 119 errors；主要为旧 fixture 与 report reader 的旧必填非空 gate。完成迁移后相关后续用例通过。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_environment.py tests/test_report_schema.py tests/test_authorization.py tests/test_end_to_end.py`：229 passed（8.41s）。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests`：1966 passed / 1 failed（43.55s），唯一失败是 Schema nullable 白名单未纳入新字段，已修正。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_optional_test_group.py tests/test_end_to_end.py tests/test_report_artifacts.py tests/test_resolution.py`：63 passed（9.32s）。
- `.venv/bin/ruff check src tests scripts`、`.venv/bin/ty check` 通过；`.venv/bin/python scripts/generate_report_schema.py` 已生成，`--check` 通过。
- 原始切片日志位于 `/tmp/pf-d035-focused*.log`、`/tmp/pf-d035-full.log`、`/tmp/pf-d035-final-focused2.log`；它们是本地辅助证据，本 Plan 保留可复现命令与结果。
- 真实 CLI fixture：uv_build 构建的 demo 与 active idna 3.10 project dependency；无 group 与显式空 test 两种配置，逐命令 smoke/check/search/minimize exit 0。每次 invocation 的 process logs 中 project compile 与 final sync 数量相等且大于零，verifier import demo/idna 并 assert 值与版本；最终 reader 返回 complete、floor 3.10 和每个 Proposal 的 environment null。显式缺失 group + 不存在的命令另证明安装后 start-failed/exit 4。

### 3.2 最终全套门禁

初轮三版本共同暴露 test_check 的旧 install-environment 断言，已改为 project-only 实际 stage。
Python 3.10 并行首轮另有一个既有 nested pytest qualification 用例得到 internal error/exit 3；
`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_check.py tests/test_pytest_observer_integration.py`
独立复测 64 passed（12.44s）。该 observer 生产代码未修改，首轮未捕获其内部异常正文，
不把它归因为产品回归或已证实的缓存竞争；最终三版本改为逐个运行全套。

`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build` 成功生成 wheel 与 sdist。
| 精确命令（repo root，CLI/测试/构建均沙箱外） | 最终结果 |
| --- | --- |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short --cov=pf --cov-report=term-missing tests` | Python 3.10.16：1971 passed，50.03s；coverage 90.44% |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.11 --group test pytest --no-testmon -q --tb=short tests` | 1971 passed，42.84s |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.12 --group test pytest --no-testmon -q --tb=short tests` | 1971 passed，44.79s |
| `.venv/bin/ruff check src tests scripts` | 通过 |
| `.venv/bin/ty check` | 通过 |
| `.venv/bin/python scripts/generate_report_schema.py --check` | schema/examples 无 drift |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build` | wheel 与 sdist 成功 |
| Markdown 本地链接与 `git diff --check` | 归档后 97 个 Markdown、705 条本地链接、0 缺失；空白检查通过 |

最终全套日志：`/tmp/pf-d035-py310-final.log`、`/tmp/pf-d035-py311-final.log`、
`/tmp/pf-d035-py312-final.log`。

链接检查命令（只读，不检查远程链接或 heading anchors）：

```sh
perl -MFile::Find -MFile::Basename=dirname -MFile::Spec -e '
my @files = ("README.md", "README.zh.md", "CONTEXT.md");
find(sub { push @files, $File::Find::name if -f $_ && /\.md$/ }, "docs");
my ($links, $failed) = (0, 0);
for my $file (@files) {
  open my $fh, "<", $file or die $!; local $/; my $text = <$fh>;
  $text =~ s/```.*?```//sg;
  while ($text =~ /\[[^\]]*\]\(([^)]+)\)/g) {
    my $dest = $1; $dest =~ s/^<|>$//g; $dest =~ s/\s+"[^"]*"$//;
    next if $dest =~ /^(?:[a-z]+:|#)/i;
    $dest =~ s/#.*$//; $dest =~ s/%20/ /g;
    next unless length $dest;
    $links++; my $target = File::Spec->catfile(dirname($file), $dest);
    if (!-e $target) { print "$file -> $dest\n"; $failed++; }
  }
}
print scalar(@files), " Markdown files; $links local links; $failed missing targets\n";
exit($failed ? 1 : 0);
'
```

## 4. 最终验收审计

| AC | 当前直接证据 | 结论 |
| --- | --- | --- |
| 1 | test_optional_test_group workspace matrix：显式 qa/test、继承与覆盖、缺失无 fallback；invalid type/empty rejection；真实 explicit missing CLI | 通过 |
| 2 | 同一矩阵：无 group、只有 dev/test、两处跨名称、dev 空数组优先、同名 root/member 组合；config.test.group 保留 None | 通过 |
| 3 | ConfigLoader 只 raw merge，ProjectLoader 单点 candidates/selected name；schema PackagePlan 与 workflow/runner 所有调用点迁移；planning/verification/search workflow tests | 通过 |
| 4 | 无选择时未选中非法 requirement 不进入 parsing；routes 与所选 names 精确相等；include-group 自引用-only 保留 feature Cell，只有 idna project route；validation_surfaces 回归 | 通过 |
| 5 | recording EnvironmentFactory 对 highest/lowest/exact 的 project-only 次数、install/inspect、活动与 null facts；original/relax 调用即失败的守卫；重复 exact 只重装而不重解析 | 通过 |
| 6 | 同包 Linux/Darwin conditional overlap：active idna PROJECT_GRAPH satisfaction 强制 environment resolution；既有 ceiling/conflict/transitive/source/artifact tests | 通过 |
| 7 | 三角色 empty baseline IDs/observations、后续 baseline digest、独立 Cell/IDs/observations 错误拒绝；现行 check/search/cache/predecessor tests 与真实 CLI | 通过 |
| 8 | UvAdapter actual plan.kind 双分支 install failure，EnvironmentFactory inspect-project-plan 与 failure digest；真实 missing verifier authority/start-failed/exit 4；有效 command 原契约 | 通过 |
| 9 | ProposalV1 serializer 与 preserve-null schema，ReportStore null path allowlist；缺 key/双向不一致/identity tamper 拒绝；mixed branch report fixture 与完整 store/merge/update tests、真实 CLI write/read | 通过 |
| 10 | 两个新 policy facts 精确 preimage；test_authorization 参数用例证明 generation 变化、merge/update 拒绝、update_path 整体替换及 force=False/True 均拒绝 apply | 通过 |
| 11 | 算法、static/witness、scheduler、observer、artifact/source 与退出码实现未改；三版本全套结果见 §3.2 | 通过 |
| 12 | 双语 README、六个 owner、schema/examples 同步；build/静态/生成物通过，完整三版本与归档检查见 §3.2 | 通过 |

D035/P040 已同步归档；六个现行 owner、双语 README、生成物与两个索引已同步。
生产变更不涉及 D003/D004/D008/D013 的 owner 规则；不提交、不推送。

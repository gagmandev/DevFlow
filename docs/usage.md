# 使用说明

## 一、安装

把整个 `devflow/` 目录放到 Skill 目录下：

```bash
# 用户级（所有项目可用）
cp -r devflow ~/.workbuddy-ai/skills/devflow

# 或项目级
cp -r devflow <project>/.workbuddy-ai/skills/devflow
```

确认可用：

```bash
python ~/.workbuddy-ai/skills/devflow/scripts/df.py --help
```

需要 Python 3.8+，无第三方依赖。

## 二、在某个项目里启用

```bash
cd /path/to/your-project
python ~/.workbuddy-ai/skills/devflow/scripts/df.py init --name "项目名"
```

会在项目根生成 `.devflow/` 并往 `.gitignore` 追加锁文件忽略项。

然后把需求文档丢给 Agent，说"分析这份需求"就行。不需要记命令。

## 三、日常循环

```bash
DF="python ~/.workbuddy-ai/skills/devflow/scripts/df.py"

$DF status              # 我在哪
$DF next                # 现在能做什么
$DF task DF-003 --prompt  # 看某个节点的提示词
$DF start DF-003        # 开始
# … Agent 干活 …
$DF sync DF-003         # 它做完了，同步
$DF accept DF-003       # 验收
```

看板在 `.devflow/plans/board-vN.md`，每次状态变更自动更新。

## 四、把节点交给别的 Agent

```bash
$DF next                       # 看哪些节点可执行
$DF task DF-003 --prompt > /tmp/df003.md
```

把 `/tmp/df003.md` 全文投给任意 Code Agent。它不需要读需求文档、不需要了解项目历史。

它做完后应该产出：

```
.devflow/nodes/DF-003/execution-report.md
.devflow/nodes/DF-003/test-report.json
```

回到主控执行 `$DF sync DF-003`。

如果它没按格式写，`sync` 会拒绝并告诉你缺什么。

## 五、并行

```bash
$DF next
# - 并行组 A（2 个，确认无文件冲突再并行）：
#     DF-004 → .devflow/nodes/DF-004/prompt.md
#     DF-005 → .devflow/nodes/DF-005/prompt.md
```

- 宿主支持子 Agent：把两个提示词分别交给子 Agent。
- 不支持：让用户开两个会话，各自投喂。
- 只有一个 Agent：顺序做，报告里如实写"顺序执行"。

**不要把顺序执行说成并行。** 看板上的 `parallel_group` 表示"可以并行"，不是"已经并行"。

## 六、需求变更

```bash
$DF change-new --title "退款冻结积分" --type rule_change
```

然后填 `.devflow/changes/CR-001/change-request.json` 的 `impacts`（逐节点动作 + 依据），
生成新版需求与计划，用户确认后：

```bash
$DF change-set --change CR-001 --patch confirm.json   # status=confirmed
$DF change-apply --change CR-001
```

旧计划自动变 `superseded`，旧文件全部保留。

## 七、回归与交付

```bash
$DF regression-check     # 前置检查（需求没覆盖、节点没完成都会退出码 1）
# 真实跑构建与测试，把结果写成 JSON（模板：templates/regression-report.json）
$DF regression-import --file regression.json   # 校验 + 渲染 Markdown

$DF release              # 生成交付报告 → release_candidate（报告 ≠ 交付）
$DF release-ready        # 复核门槛 → release_ready
$DF release-accept --by "张三"   # 用户验收 → accepted
```

`regression-import` 会校验：Schema、状态与检查项自洽、必需检查项齐全、
`passed` 项有命令/退出码/证据、`not_run` 项有原因。缺一项就拒绝导入。

`release` 读 JSON，不看 Markdown 措辞。旧项目只有 Markdown 时用
`release --legacy-regression`（降级，会在项目档案留记录）。

`release` 有阻塞时退出码 1，但报告仍会生成，方便对照修。
`release-ready` 会重新查一遍，状态可能在这期间变化。
`accepted` 只能由用户明确执行 `release-accept` 产生。

## 七·五、出错恢复

```bash
$DF doctor       # 看有没有未完成事务、越界记录、旧状态别名
$DF recover      # 列出未完成事务及所处阶段
$DF recover --apply   # 回滚到事务前状态
$DF migrate      # 升级旧版 project.json（released → accepted）
```

事务日志在 `.devflow/.txn/<id>/`，快照也在里面。回滚是确定安全的：
只有全部写完才会标记 `committed`。

## 七·六、质量档位

```bash
$DF init --name "项目" --profile production   # 新项目直接上生产门禁
$DF migrate --profile production              # 旧项目升级（不会删历史）
$DF project-set --profile production          # 只切档位
```

`production` 会打开：Git 变更必须可核对、契约文件绑定、源码快照、完整交付报告。
旧项目如果暂时补不出交付报告，只能 `migrate --legacy-release` 开兼容，会在项目档案里留痕。

## 七·七、自检（改引擎之后必跑）

```bash
$DF self-test            # 端到端 + 单元 + 示例项目集成
$DF self-test --fast     # 只跑端到端
$DF self-test --json --out self-test.json
```

在系统临时目录从空项目一路跑到 `accepted`，再跑并行与外部报告两条支线（共 60 步），
不碰任何真实项目。
输出分三层：单元测试、集成测试（示例项目）、端到端工作流；任何一层失败都退出码 1。

## 八、常见问题

**Q：能不能手动改 backlog-vN.json？**
不要。看板会与 JSON 不一致。要改状态用 `df.py` 的命令；要改计划内容就重新 `plan-import`。

**Q：`plan-import` 报"提示词文件不存在"？**
草稿阶段只是警告，激活前必须补齐：`$DF prompt-init --all`，再补实现步骤。

**Q：`accept` 说证据不全？**
看它列的具体项：缺执行报告、缺 test-report.json、status 不是 passed、某条命令退出码非 0、
统计对不上、验收标准没逐条对应、`attempt_id` 不一致。让执行 Agent 补齐后重新 `sync`。

**Q：`start` 说"依赖产物被修改过"？**
`accept` 时节点会记交付物指纹。上游节点完成后，如果你又改了它产出的文件（而本节点
`inputs` 里声明了依赖它），`start` 会拦下来让你先评估影响。确认不影响就加
`--ack-context --reason "为什么不影响"`，理由会留档。

**Q：`sync` 说"只有 in_progress 能同步"？**
先 `start`。`start` 会生成 `attempt_id`，没有它就无法区分本轮报告和上一轮的旧报告。

**Q：`accept` 说越界？**
说明执行报告里的 `modified_files` 有 `files_scope` 之外的文件。三条路：
撤掉改动 / 更新 `files_scope` 走变更 / `accept --allow-scope --reason "..."` 留档放行。

**Q：`change-apply` 说基准过期？**
CR 的 `base_plan_version` 与当前计划不一致。说明期间已经有别的变更生效，
必须基于新计划重新做影响分析，不能直接套旧结论。

**Q：`release` 生成了报告，但状态不是 accepted？**
这是设计如此。报告只是候选，`release-ready` 检查条件，`release-accept` 才是验收。

**Q：计划改了，旧提示词还能用吗？**
不能。`start` 会核对提示词头部的 `plan_version` / `requirement_version`，对不上直接拒绝。
重新生成：`df.py prompt-init <ID> --force`。

**Q：外部 Agent 的报告怎么进来？**
先 `start`（生成 attempt_id），把两份报告放一个目录，然后
`df.py sync <ID> --external --from <目录>`。它会校验身份再导入，之后走同一套验收。

**Q：`sync` 提示"Git 实际变更与报告不一致"？**
说明报告里写的改动和实际工作区对不上。默认只是提示（`verify_changes: warn`），
改成 `error` 就会阻止验收。确认无误用 `accept --ack-changes --reason "..."`。

**Q：为什么 `regression-check` 说我需求没覆盖？**
每条 active 需求都要有已完成节点覆盖，且不能只由 `no_test` 节点覆盖。
确实不做就标 `deferred` / `out_of_scope`，但必须写 `reason` 和 `confirmed_by`。

**Q：进程被 kill 了，状态会不会坏？**
写操作有文件锁和原子写，单文件不会半截。多文件提交（change-apply）有事务日志，
`df.py doctor` 会报出来，`df.py recover --apply` 回滚。

**Q：节点卡住了怎么办？**
`$DF block DF-003 --reason "..."`，写清缺什么。缺配置/要决策的问题不要靠改代码硬绕。

**Q：能不能跳过需求确认直接拆任务？**
不能。`req-confirm` 之前生成的需求基准不完整，拆出来的任务没有验收依据。

**Q：多个 Agent 同时改同一个计划会怎样？**
会拿到文件锁，第二个会等待或报错。**不要多主控并行**，只有一个主控写状态。

**Q：`.devflow/` 要提交到 Git 吗？**
要。除 `.lock` 和 `*.tmp` 外全部提交。这是 DevFlow 最大的价值之一——计划和报告都能 diff。

**Q：项目很大，节点太多怎么办？**
先看是不是拆太细（单节点文件数 < 3、验收标准只有一条空话）。
单版本 5–30 个节点比较健康。

**Q：换了一个 Agent，怎么接手？**
新会话里说"读 .devflow/project.json 和当前计划，告诉我现在能做什么"。
`$DF status` + `$DF next` 就够了，不需要历史对话。

## 九、目录速查

| 想知道 | 看 |
| --- | --- |
| 需求是什么 | `.devflow/requirements/confirmed-requirements-vN.md` |
| 现在做到哪 | `.devflow/plans/board-vN.md` |
| 某节点干了什么 | `.devflow/nodes/DF-xxx/execution-report.md` |
| 为什么改过 | `.devflow/changes/CR-xxx/` |
| 回归结果 | `.devflow/regression/final-report.md` |
| 交付了什么 | `.devflow/releases/release-report.md` |
| 为什么这么决定 | `.devflow/decisions.md` |

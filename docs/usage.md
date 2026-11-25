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
$DF regression-check     # 前置检查
# 真实跑构建与测试，把结果写进 .devflow/regression/final-report.md
$DF release              # 生成交付报告骨架（事实部分自动填）
```

## 八、常见问题

**Q：能不能手动改 backlog-vN.json？**
不要。看板会与 JSON 不一致。要改状态用 `df.py` 的命令；要改计划内容就重新 `plan-import`。

**Q：`plan-import` 报"提示词文件不存在"？**
草稿阶段只是警告，激活前必须补齐：`$DF prompt-init --all`，再补实现步骤。

**Q：`accept` 说证据不全？**
看它列的具体项：缺执行报告、缺 test-report.json、status 不是 passed、命令退出码非 0。
让执行 Agent 补齐后重新 `sync`。

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

# 节点执行

## 一、执行前的四项检查

`df.py start` 会自动做，但你也要知道它在查什么：

1. 当前计划是否 `active`（草稿不能执行）
2. 节点是否存在、状态是否允许开始
3. 前置依赖是否全部 `completed`
4. 提示词与上下文文件是否存在，提示词版本是否与当前计划一致

还有第五项（P2 起）：**依赖产物指纹是否变了**。`accept` 时节点会记录自己
`outputs` / `files_scope` 里真实存在的文件的 sha256（大文件记 size+mtime）。
如果本节点 `inputs` 里声明依赖的某个文件，在被依赖节点完成之后又被改过，
`start` 会拒绝，并列出具体文件和所属节点。

这不是"提醒"，是要求你先回答一个问题的门：**上游变了，本节点的实现/测试还成立吗？**

- 不成立 → 让主控走变更流程（`change-new`），不要自己硬着头皮写。
- 成立 → 主控用 `start <ID> --ack-context --reason "..."` 放行，理由进 `context_acks`。

原则：只对**声明过的依赖**做判断。没写 `inputs` 的节点不做推断，
仓库里别处改个文档不应该让全部下游返工。

还有第六项：**契约文件绑定**。节点声明了 `api_contracts` / `db_objects` 时，
应该用 `contract_bindings` 指向具体文件（OpenAPI、SQL、共享常量）：

```json
"contract_bindings": [
  {"kind": "api_contract", "id": "PointsAccountApi", "files": ["docs/api/points-account.yaml"]},
  {"kind": "db_object", "id": "PointsAccount", "files": ["database/schema/points-account.sql"]}
]
```

`accept` 会记录这些文件的指纹；下游 `start` 时比对。契约变了，下游必须先评估影响——
工具不解析接口语义，但能保证"文件变了不会被静默忽略"。

任何一项不满足，都要**停下来报告**，不要"先写着，反正差不多"。

## 二、上下文怎么读

| 层 | 读什么 | 什么时候 |
| --- | --- | --- |
| L0 | `prompt.md` | 必读，全部 |
| L1 | 提示词里"必读文件"列的 | 开工前 |
| L2 | 依赖节点的 `outputs` | 要用它的接口时 |
| L3 | 需求基准里对应的 REQ 条目 | 不确定业务规则时 |
| L4 | 自己节点的 `test_strategy` | 写测试前 |

不要读：整个 `.devflow/`、其他节点的报告、全部需求文档。
上下文越干净，越不容易跑偏。

## 三、范围纪律

`files_scope` 是硬边界。

- 只改 scope 内的文件。
- 需要改 scope 外的东西 → 停下来，说明"为什么必须改、改了会影响谁"，等主控决定。
- 不要"顺手"格式化整个文件、重命名无关变量、升级依赖。这些都会让 diff 无法审查。

`df.py sync` 会检查执行报告里的 `modified_files` 是否越界，越界会提示。

## 四、写代码的顺序

1. 先读依赖产物，确认接口签名（不要凭想象调用）
2. 写最小可运行版本
3. 跑一次测试（哪怕是空测试）
4. 补正常/异常/边界用例
5. 再跑一次
6. 自查验收标准，逐条对
7. 写报告

不要"全部写完再跑测试"。一次跑出一堆错误，定位成本高十倍。

## 五、什么时候必须停下来

| 情况 | 动作 |
| --- | --- |
| 需求与代码现状冲突 | 停，报告冲突点，不要自己选一个 |
| 依赖产物的接口与提示词描述不一致 | 停，报告差异，不要改依赖节点 |
| 需要新增第三方依赖 | 停，问。多数项目禁止 |
| 需要改数据库结构而节点没授权 | 停，问 |
| 发现安全问题（注入、越权、明文密钥） | 停，报告，不要"顺手修" |
| 测试反复失败且原因不明 | 两轮后转 `blocked`，写清现象 |

## 六、执行报告契约

必须产出两个文件（路径固定，不需要改 backlog）：

```
.devflow/nodes/DF-xxx/execution-report.md
.devflow/nodes/DF-xxx/test-report.json
```

`execution-report.md` 里必须有一个机器可读块：

````markdown
```devflow-execution
{
  "node_id": "DF-004",
  "result": "success",
  "modified_files": ["src/points/service.py", "tests/test_service.py"],
  "acceptance": [{"criterion": "重复开户幂等", "met": true, "evidence": "test_idempotent"}],
  "assumptions": ["签到不自动开户"],
  "leftovers": []
}
```
````

`result` 只能是 `success` / `failed` / `blocked`。
`acceptance` 要**逐条**对应节点的验收标准，`evidence` 写测试名或命令，不要写"已实现"。

`test-report.json` 结构见 `schemas/test-report.schema.json`。关键字段：

- `status`: `passed` / `failed` / `partial` / `not_run`
- `commands`: 实际执行的命令 + 退出码，至少一条
- `summary`: total / passed / failed
- `cases`: 用例清单

**没跑就是 `not_run`。** 主控会拒绝 `not_run` 的验收。

## 七、失败处理

```
测试失败
  → 看真实错误信息（不要看猜测）
  → 定位到代码
  → 修
  → 只重跑相关测试
  → 通过后跑一次全量
```

两轮还不行：

- 缺配置/凭据 → `blocked`，说明缺什么
- 要用户决策 → `blocked`，把选项列出来
- 外部服务不可用 → `blocked`，附上错误
- 确实是自己写不出来 → `blocked`，说明卡在哪一步

**不要靠加 `sleep`、放宽断言、跳过用例来"修好"测试。**

## 七·五、Git 变更核对

报告里的 `modified_files` 是"我声称改了"，不是"实际改了"。主控会用 Git 对一遍：

1. `start` 记录基准：HEAD + 执行前已有的未提交文件
2. `sync` 计算实际变更（工作区状态 + 相对 HEAD 的 diff）
3. **减掉基准里的未提交文件**——用户原本就改了一半的代码不能算到节点头上
4. 与 `modified_files` 双向比对，差异记入 `git_discrepancies`

差异的含义：

- `实际改动但报告未列` → 漏报，可能是顺手改了别的模块
- `报告列出但未检测到改动` → 虚报，或者改动被回滚了

处理：`verify_changes: warn` 时人工确认；`error` 时验收被阻止，
`accept --ack-changes --reason "..."` 留档放行。

项目不是 git 仓库、或项目不是仓库根目录时自动降级，不阻断。

## 八、并行执行时的额外约束

- 不要改全局计划文件、看板、其他节点目录
- 不要改公共配置文件（除非节点明确授权）
- 不要在报告里写"我顺便优化了 XX"
- 交付前确认自己的 `files_scope` 内没有留下调试代码、临时文件

如果宿主支持子 Agent：主控把提示词交给子 Agent，子 Agent 只负责本节点，
完成后把两个报告写回节点目录，主控再 `sync`。

如果宿主不支持：主控生成并行清单，由用户手动投喂。**不要声称已经并行跑了。**

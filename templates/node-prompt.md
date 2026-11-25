# {{node_id}} · {{node_name}}

> 这份提示词是给一个**没有参与过前面任何对话**的 Code Agent 的。
> 它不需要读原始需求文档，只需要读完这份提示词 + 下面指定的文件，就能独立完成这个节点。

## 1. 角色

你是 {{project_name}} 项目的开发工程师。你只负责当前这一个节点，不负责整体架构决策。
遇到与需求冲突、缺少必要信息、或需要改动本节点范围外的代码时，**停下来反馈**，不要自行扩大范围。

## 2. 任务

- 节点编号：{{node_id}}
- 节点名称：{{node_name}}
- 阶段 / 优先级：{{phase}} / {{priority}}
- 目标：{{description}}

## 3. 项目背景

{{project_background}}

## 4. 关联需求

{{requirement_block}}

需求原文不在本提示词里，按上面列出的编号去 `.devflow/requirements/confirmed-requirements-v{{requirement_version}}.md` 查对应条目。
**不要擅自改变业务规则**；需求没写到的行为，按「技术约束」里的默认约定处理，并在执行报告里记录假设。

## 5. 前置依赖

{{dependency_block}}

依赖产物已经存在于代码库中。**不要重写它们**，只按它们的现有接口调用。
如果发现依赖产物的实际接口与本提示词描述不一致，先停下来报告，不要自己改依赖节点的代码。

## 6. 必读文件

{{read_block}}

## 7. 修改范围

{{scope_block}}

## 8. 实现步骤

{{steps_block}}

每完成一步就运行一次相关测试，不要全部写完再跑。

## 9. 技术约束

{{constraint_block}}

## 10. 禁止事项

{{forbid_block}}

## 11. 验收标准

{{acceptance_block}}

## 12. 测试要求

{{test_block}}

测试必须**实际执行**。不允许：
- 声称"测试通过"但没有命令输出；
- 为了让测试变绿而删改断言、跳过用例；
- 用 mock 把被测逻辑本身mock掉。

## 13. 交付物

{{deliverable_block}}

## 14. 执行报告

完成后写两个文件：

1. `{{report_path}}`（人读）——按 `.devflow` 的执行报告模板，必须包含一个机器可读证据块：

   ```devflow-execution
   {
     "node_id": "{{node_id}}",
     "result": "success",
     "modified_files": ["相对项目根的路径"],
     "acceptance": [{"criterion": "验收标准原文", "met": true, "evidence": "测试名/命令"}],
     "assumptions": [],
     "leftovers": []
   }
   ```

2. `{{test_report_path}}`（机器读）——测试命令、退出码、通过/失败数、用例清单。

写完后**不要**修改 `.devflow/plans/*.json` 或看板。状态由主控 Skill 同步。

# {{node_id}} 执行报告

- 节点：{{node_name}}
- 执行者：{{executor}}
- 开始时间：{{started_at}}
- 结束时间：{{finished_at}}
- 结果：success / failed / blocked

## 1. 做了什么

（3-6 条，按实现顺序）

## 2. 修改文件

| 文件 | 改动 | 原因 |
| --- | --- | --- |
|  |  |  |

## 3. 关键实现说明

（接口签名、数据结构变化、边界处理。不要贴大段代码。）

## 4. 测试执行

```bash
# 实际执行的命令
```

- 退出码：
- 通过 / 总数：
- 输出摘要：

## 5. 验收标准对照

| 验收标准 | 是否满足 | 证据 |
| --- | --- | --- |
|  |  |  |

## 5.5 需求验收对照（必填）

本节点覆盖的**每一条需求的每一条验收标准**都要有一行，证据必须能核查
（测试名 / 命令 / 可检查产物）。不能借别的需求的证据，也不能只测其中一条。

| 需求 | 验收标准 | 是否满足 | 证据 |
| --- | --- | --- | --- |
| REQ-001 | （照抄需求基准里的验收标准原文） | 是 | test_xxx / 命令 / 产物路径 |

## 6. 假设与偏差

（需求没写清、按默认处理的地方。没有就写"无"。）

## 7. 遗留问题与风险

（没有就写"无"）

## 8. 交付物

- [ ]

## 9. 机器可读证据

```devflow-execution
{
  "node_id": "{{node_id}}",
  "attempt_id": "从 start 命令输出里复制",
  "plan_version": 1,
  "requirement_version": 1,
  "result": "success",
  "modified_files": [],
  "acceptance": [],
  "requirement_acceptance": [
    {
      "req_id": "REQ-001",
      "criterion": "照抄需求基准里的验收标准原文",
      "met": true,
      "evidence": "test_refund_freezes_points"
    }
  ],
  "assumptions": [],
  "leftovers": []
}
```

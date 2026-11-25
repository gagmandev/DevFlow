# DF-007 执行报告

- 节点：交付文档与运行说明
- 执行者：code-agent（演示执行）
- 开始时间：2026-11-25T13:57:00+08:00
- 结束时间：2026-11-25T13:59:20+08:00
- 结果：success

## 1. 做了什么

1. `README.md`：定位、本期范围（做/不做）、目录职责、环境、测试命令、可运行的示例代码、业务规则速查、已知限制。
2. `docs/DEPLOY.md`：数据文件位置与结构、原子写说明、**单进程部署前提**、备份恢复、升级到数据库要改的两处、配置项、健康检查。
3. 文档里出现的命令都实际执行过：测试命令输出 `Ran 51 tests / OK`；示例代码跑出 `balance=24`、流水 3 条。

## 2. 修改文件

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| README.md | 新建 | 项目入口文档 |
| docs/DEPLOY.md | 新建 | 部署与配置说明 |

## 3. 关键实现说明

- 已知限制写了三条：进程内锁、全量加载、运营无鉴权。都是真实存在的限制，没有粉饰。
- 部署文档明确"多实例会失效"以及"要改哪里"，避免接手的人踩坑。
- 没有写任何真实地址、账号、密钥。

## 4. 测试执行

本节点为纯文档，`no_test=true`，不产出 `test-report.json`。
文档中的命令由人工执行验证：

```bash
python -m unittest discover -s tests -v     # 退出码 0，Ran 51 tests / OK
```

```python
# README 示例代码
w.open_account("u1"); w.check_in("u1"); w.reward_order("u1", 1999, "order-1"); w.redeem("u1", 5, "redeem-1")
# 输出：{'user_id': 'u1', 'balance': 24, 'frozen': 0, 'available': 24}；流水 3 条
```

## 5. 验收标准对照

| 验收标准 | 是否满足 | 证据 |
| --- | --- | --- |
| README 含安装、测试命令、目录结构、本期不做范围 | 是 | README.md 各章节 |
| 部署说明含数据位置、单机前提、备份方式 | 是 | docs/DEPLOY.md 第 2/3/4 节 |
| 文档里的命令都能实际执行 | 是 | 上述两条命令输出 |

## 6. 假设与偏差

- 假设：读者是能读 Python 的工程师，不需要解释 unittest 怎么用。

## 7. 遗留问题与风险

- 若后续加入冻结能力（OPEN-001），README 的"本期不做"与 DEPLOY 的配置项需要同步更新。

## 8. 交付物

- [x] README.md
- [x] docs/DEPLOY.md

## 9. 机器可读证据

```devflow-execution
{
  "node_id": "DF-007",
  "result": "success",
  "modified_files": [
    "README.md",
    "docs/DEPLOY.md"
  ],
  "acceptance": [
    {"criterion": "README 含安装、测试命令、目录结构、本期不做的范围", "met": true, "evidence": "README.md"},
    {"criterion": "部署说明含数据文件位置、并发前提（单机）、备份方式", "met": true, "evidence": "docs/DEPLOY.md 第2/3/4节"},
    {"criterion": "文档里的命令都能实际执行", "met": true, "evidence": "unittest 51 条通过；示例代码输出 balance=24"}
  ],
  "assumptions": ["读者具备 Python 基础"],
  "leftovers": ["冻结能力加入后需同步更新 README/DEPLOY"]
}
```

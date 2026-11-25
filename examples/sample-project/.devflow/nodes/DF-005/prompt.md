# DF-005 · 流水查询与运营视图

> 这份提示词是给一个**没有参与过前面任何对话**的 Code Agent 的。
> 它不需要读原始需求文档，只需要读完这份提示词 + 下面指定的文件，就能独立完成这个节点。

## 1. 角色

你是 积分账户服务 项目的开发工程师。你只负责当前这一个节点，不负责整体架构决策。
遇到与需求冲突、缺少必要信息、或需要改动本节点范围外的代码时，**停下来反馈**，不要自行扩大范围。

## 2. 任务

- 节点编号：DF-005
- 节点名称：流水查询与运营视图
- 阶段 / 优先级：Business / P2
- 目标：实现 LedgerQuery：用户查自己的流水、运营按用户/时间查全部流水，统一分页。

## 3. 项目背景

积分账户服务：为会员体系提供积分开户、入账、扣减、流水查询能力。本期只做账户与流水，不含积分商城页面、不含支付。

## 4. 关联需求

- **REQ-005 · 积分流水记录与查询**：每笔变动记录类型、数量、来源单号、时间；支持按用户分页查询，默认 20 条、最大 100 条。
  - 验收：每笔入账/扣减有且只有一条流水
  - 验收：分页 size 超过 100 时截断
- **REQ-007 · 运营查询全部流水**：运营可按 user_id 或时间区间查询全部用户流水。
  - 验收：不传 user_id 时返回全部用户流水
  - 验收：时间区间按左闭右开过滤

需求原文不在本提示词里，按上面列出的编号去 `.devflow/requirements/confirmed-requirements-v1.md` 查对应条目。
**不要擅自改变业务规则**；需求没写到的行为，按「技术约束」里的默认约定处理，并在执行报告里记录假设。

## 5. 前置依赖

- **DF-003 · 账户与流水仓储层**（当前状态：pending）
  - 已产出：src/points/repository.py, tests/test_repository.py
  - 它保证的事：AccountRepo.get_or_create 对同一 user_id 幂等

依赖产物已经存在于代码库中。**不要重写它们**，只按它们的现有接口调用。
如果发现依赖产物的实际接口与本提示词描述不一致，先停下来报告，不要自己改依赖节点的代码。

## 6. 必读文件

- src/points/repository.py
- src/points/models.py
- .devflow/requirements/confirmed-requirements-v1.md（只查上面列出的需求编号）
- tests/test_repository.py
- .devflow/decisions.md（只看与本节点相关的决策）

## 7. 修改范围

允许新增/修改：
- src/points/queries.py
- tests/test_queries.py

## 8. 实现步骤

1. `queries.py` 定义 `LedgerQuery(store)`：
   - `by_user(user_id, page=1, size=20)`：只查该用户，按时间倒序；
   - `all(page=1, size=20, user_id=None, start=None, end=None)`：运营视图，不传 `user_id` 就是全量；
   - 两个方法都返回 `{"items": [...], "total": n, "page": p, "size": s}`，`items` 是 `LedgerEntry.to_dict()`。
2. **不要重新实现过滤和分页**：直接调用 `LedgerRepo.query()`，本节点只做参数规整（`page<1` 归 1、`size` 交给仓储层夹取）与结果组装。
3. 时间参数接受 ISO 字符串，原样传给仓储层；不做时区转换（本期统一本地时间）。
4. `tests/test_queries.py`：倒序、跨用户、时间边界（等于 start 命中 / 等于 end 不命中）、越界页返回空、total 与过滤后总数一致。

注意：本节点不写业务规则，不改 `repository.py`。如果发现 `LedgerRepo.query` 返回结构不够用，先报告。

每完成一步就运行一次相关测试，不要全部写完再跑。

## 9. 技术约束

- 技术栈：{"language": "Python", "version": "3.11+", "runtime_deps": "无（仅标准库）", "storage": "本地 JSON 文件（原子写入）", "test": "unittest", "layout": "src/ 布局"}
- 测试命令：python -m unittest discover -s tests -v
- 构建命令：（无）
- 硬约束：只使用 Python 标准库，不引入第三方运行时依赖
- 硬约束：积分必须是整数，禁止用 float 表示或参与运算
- 硬约束：任何写操作必须原子落盘，进程中断不能留下半截数据
- 硬约束：余额不允许为负；并发扣减不允许超扣
- 硬约束：不得把密钥、令牌写入代码或报告
- 约定 · 目录：业务代码放 src/points/，测试放 tests/
- 约定 · 错误处理：领域异常统一定义在 src/points/errors.py，不抛裸 Exception
- 约定 · 命名：模块与函数 snake_case，类 CamelCase
- 约定 · 存储：所有落盘走 store.atomic_write，不直接 open('w')
- 遵循现有代码的命名、分层、错误处理风格；不引入新的第三方运行时依赖。

## 10. 禁止事项

- 不修改本节点 files_scope 之外的业务代码
- 不修改 .devflow/plans/*.json、看板、其他节点的目录
- 不改写需求基准文档；发现需求冲突时停下来报告
- 不重复实现已存在的公共能力（先读依赖产物）
- 不删除或跳过已有测试来让流水线变绿
- 不把没执行过的测试写成 passed
- 不提交任何密钥、令牌、真实用户数据

## 11. 验收标准

- [ ] 按用户查询按时间倒序返回
- [ ] 不传 user_id 时返回全部用户流水
- [ ] 时间区间左闭右开
- [ ] 分页 size 超过 100 截断，page 从 1 开始

## 12. 测试要求

- 倒序校验
- 跨用户查询
- 时间区间边界（等于 start 命中、等于 end 不命中）
- 分页越界返回空列表
- 记录每条命令的退出码和通过/失败数量，写进 test-report.json。

测试必须**实际执行**。不允许：
- 声称"测试通过"但没有命令输出；
- 为了让测试变绿而删改断言、跳过用例；
- 用 mock 把被测逻辑本身mock掉。

## 13. 交付物

- src/points/queries.py
- tests/test_queries.py
- `.devflow/nodes/DF-005/execution-report.md`
- `.devflow/nodes/DF-005/test-report.json`

## 14. 执行报告

完成后写两个文件：

1. `.devflow/nodes/DF-005/execution-report.md`（人读）——按 `.devflow` 的执行报告模板，必须包含一个机器可读证据块：

   ```devflow-execution
   {
     "node_id": "DF-005",
     "result": "success",
     "modified_files": ["相对项目根的路径"],
     "acceptance": [{"criterion": "验收标准原文", "met": true, "evidence": "测试名/命令"}],
     "assumptions": [],
     "leftovers": []
   }
   ```

2. `.devflow/nodes/DF-005/test-report.json`（机器读）——测试命令、退出码、通过/失败数、用例清单。

写完后**不要**修改 `.devflow/plans/*.json` 或看板。状态由主控 Skill 同步。

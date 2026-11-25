# DF-003 · 账户与流水仓储层

> 这份提示词是给一个**没有参与过前面任何对话**的 Code Agent 的。
> 它不需要读原始需求文档，只需要读完这份提示词 + 下面指定的文件，就能独立完成这个节点。

## 1. 角色

你是 积分账户服务 项目的开发工程师。你只负责当前这一个节点，不负责整体架构决策。
遇到与需求冲突、缺少必要信息、或需要改动本节点范围外的代码时，**停下来反馈**，不要自行扩大范围。

## 2. 任务

- 节点编号：DF-003
- 节点名称：账户与流水仓储层
- 阶段 / 优先级：Shared / P1
- 目标：在存储之上提供 AccountRepo / LedgerRepo：开户查询、余额更新、流水追加、按来源单号幂等查询、分页查询。

## 3. 项目背景

积分账户服务：为会员体系提供积分开户、入账、扣减、流水查询能力。本期只做账户与流水，不含积分商城页面、不含支付。

## 4. 关联需求

- **REQ-005 · 积分流水记录与查询**：每笔变动记录类型、数量、来源单号、时间；支持按用户分页查询，默认 20 条、最大 100 条。
  - 验收：每笔入账/扣减有且只有一条流水
  - 验收：分页 size 超过 100 时截断
- **REQ-008 · 并发安全与数据原子性**：并发扣减不得超扣；写盘原子；积分全程整数运算。
  - 验收：20 线程各扣 1 分、余额 10 分时恰好 10 次成功
  - 验收：落盘使用临时文件 + os.replace
  - 验收：积分计算不出现 float

需求原文不在本提示词里，按上面列出的编号去 `.devflow/requirements/confirmed-requirements-v1.md` 查对应条目。
**不要擅自改变业务规则**；需求没写到的行为，按「技术约束」里的默认约定处理，并在执行报告里记录假设。

## 5. 前置依赖

- **DF-002 · 账户与流水数据结构 + 原子存储**（当前状态：pending）
  - 已产出：src/points/models.py, src/points/store.py, tests/test_store.py
  - 它保证的事：Account 含 user_id/balance/frozen/version/created_at，积分字段为 int

依赖产物已经存在于代码库中。**不要重写它们**，只按它们的现有接口调用。
如果发现依赖产物的实际接口与本提示词描述不一致，先停下来报告，不要自己改依赖节点的代码。

## 6. 必读文件

- src/points/models.py
- src/points/store.py
- .devflow/requirements/confirmed-requirements-v1.md（只查上面列出的需求编号）
- tests/test_store.py
- .devflow/decisions.md（只看与本节点相关的决策）

## 7. 修改范围

允许新增/修改：
- src/points/repository.py
- tests/test_repository.py

## 8. 实现步骤

1. `repository.py`：
   - `AccountRepo(store)`：`get(user_id)` 不存在返回 `None`；`get_or_create(user_id)` 幂等；`save(account)` 时 `version += 1` 并整体落盘；`list_all()`。
   - `LedgerRepo(store)`：`append(entry)`；`query(user_id=None, start=None, end=None, page=1, size=20)`；`exists(user_id, type, source_id)`。
2. 查询语义：
   - 时间过滤左闭右开：`start <= created_at < end`；
   - 排序：`created_at` 倒序，同一时间按 `entry_id` 倒序保证稳定；
   - 分页：`page` 从 1 开始，`size` 夹在 `[1, 100]`，越界页返回空列表；
   - 返回 `(items, total)` 二元组，total 是过滤后的总数（不是当前页长度）。
3. 所有写操作必须在 `store.lock()` 内完成，且一次操作只写一次盘。
4. `tests/test_repository.py`：开户幂等、version 递增、exists 命中/未命中、size=1000 截断为 100、时间区间边界（等于 start 命中、等于 end 不命中）、越界页返回空。

注意：仓储层不判断"余额够不够"，那是 DF-004 的事。

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

- [ ] AccountRepo.get_or_create 对同一 user_id 幂等
- [ ] AccountRepo.save 更新 version 并原子落盘
- [ ] LedgerRepo.exists(user_id, type, source_id) 能判断重复来源
- [ ] LedgerRepo.query 支持按用户/时间区间过滤与分页，size 上限 100

## 12. 测试要求

- 同一 user_id 两次 get_or_create 返回同一账户
- 分页 size=1000 时截断为 100
- 时间区间左闭右开
- exists 命中与未命中
- 记录每条命令的退出码和通过/失败数量，写进 test-report.json。

测试必须**实际执行**。不允许：
- 声称"测试通过"但没有命令输出；
- 为了让测试变绿而删改断言、跳过用例；
- 用 mock 把被测逻辑本身mock掉。

## 13. 交付物

- src/points/repository.py
- tests/test_repository.py
- `.devflow/nodes/DF-003/execution-report.md`
- `.devflow/nodes/DF-003/test-report.json`

## 14. 执行报告

完成后写两个文件：

1. `.devflow/nodes/DF-003/execution-report.md`（人读）——按 `.devflow` 的执行报告模板，必须包含一个机器可读证据块：

   ```devflow-execution
   {
     "node_id": "DF-003",
     "result": "success",
     "modified_files": ["相对项目根的路径"],
     "acceptance": [{"criterion": "验收标准原文", "met": true, "evidence": "测试名/命令"}],
     "assumptions": [],
     "leftovers": []
   }
   ```

2. `.devflow/nodes/DF-003/test-report.json`（机器读）——测试命令、退出码、通过/失败数、用例清单。

写完后**不要**修改 `.devflow/plans/*.json` 或看板。状态由主控 Skill 同步。

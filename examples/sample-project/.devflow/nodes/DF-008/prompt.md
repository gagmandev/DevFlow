# DF-008 · 积分冻结 / 解冻服务

> 这份提示词是给一个**没有参与过前面任何对话**的 Code Agent 的。
> 它不需要读原始需求文档，只需要读完这份提示词 + 下面指定的文件，就能独立完成这个节点。

## 1. 角色

你是 积分账户服务 项目的开发工程师。你只负责当前这一个节点，不负责整体架构决策。
遇到与需求冲突、缺少必要信息、或需要改动本节点范围外的代码时，**停下来反馈**，不要自行扩大范围。

## 2. 任务

- 节点编号：DF-008
- 节点名称：积分冻结 / 解冻服务
- 阶段 / 优先级：Business / P1
- 目标：新增 FreezeService：按金额冻结可用积分、部分解冻、查询冻结额，冻结与解冻各写一条 frozen_delta 流水。

## 3. 项目背景

积分账户服务：为会员体系提供积分开户、入账、扣减、流水查询能力。本期只做账户与流水，不含积分商城页面、不含支付。

## 4. 关联需求

- **REQ-009 · 积分冻结与解冻**：退款时冻结等额积分：balance 不变、frozen 增加、available 减少；运营可解冻。冻结与解冻各写一条流水（frozen_delta）。
  - 验收：冻结后 balance 不变、available 减少
  - 验收：冻结后兑换按新 available 校验
  - 验收：允许部分解冻，解冻后 available 恢复
  - 验收：sum(frozen_delta) == frozen
- **REQ-008 · 并发安全与数据原子性**：并发扣减/冻结不得超扣、不得超冻；写盘原子；积分全程整数运算。
  - 验收：20 线程各扣 1 分恰好 10 次成功
  - 验收：20 线程各冻结 1 分恰好 10 次成功
  - 验收：落盘原子
  - 验收：不出现 float

需求原文不在本提示词里，按上面列出的编号去 `.devflow/requirements/confirmed-requirements-v2.md` 查对应条目。
**不要擅自改变业务规则**；需求没写到的行为，按「技术约束」里的默认约定处理，并在执行报告里记录假设。

## 5. 前置依赖

- **DF-002 · 账户与流水数据结构 + 原子存储**（当前状态：ready）
  - 已产出：src/points/models.py, tests/test_store.py
  - 它保证的事：LedgerEntry 新增 frozen_delta 字段，默认 0，to_dict/from_dict 完整往返
- **DF-003 · 账户与流水仓储层**（当前状态：completed）
  - 已产出：src/points/repository.py, tests/test_repository.py
  - 它保证的事：AccountRepo.get_or_create 对同一 user_id 幂等

依赖产物已经存在于代码库中。**不要重写它们**，只按它们的现有接口调用。
如果发现依赖产物的实际接口与本提示词描述不一致，先停下来报告，不要自己改依赖节点的代码。

## 6. 必读文件

- src/points/models.py
- src/points/repository.py
- src/points/service.py
- .devflow/requirements/confirmed-requirements-v2.md（只查上面列出的需求编号）
- tests/test_store.py
- tests/test_repository.py
- .devflow/decisions.md（只看与本节点相关的决策）

## 7. 修改范围

允许新增/修改：
- src/points/freeze.py
- tests/test_freeze.py

## 8. 实现步骤

1. `src/points/freeze.py` 定义 `FreezeService(store, clock=None)`：
   - 复用 `AccountRepo` / `LedgerRepo`，持有 `store.lock()`；
   - 所有写方法整体包在 `with self._lock:` 内（读 → 校验 → 改 → 写）。
2. 方法：
   - `freeze(user_id, amount, source_id) -> dict`：
     - `amount <= 0` 或 `source_id` 为空 → `InvalidArgument`；
     - 账户不存在 → `AccountNotFound`；
     - `ledger.exists(user_id, "freeze", source_id)` → `DuplicateRequest`；
     - `account.available < amount` → `InsufficientBalance(required, available)`，**抛之前无写入**；
     - 成功：`frozen += amount`，写流水 `type="freeze"`、`amount=0`、`frozen_delta=+amount`；
     - 返回 `{"frozen": ..., "available": ..., "balance": ...}`。
   - `unfreeze(user_id, amount, source_id) -> dict`：
     - 校验同上，但检查 `account.frozen >= amount`，不足抛 `InvalidArgument`（不是 InsufficientBalance）；
     - 成功：`frozen -= amount`，流水 `type="unfreeze"`、`amount=0`、`frozen_delta=-amount`。
   - `frozen_amount(user_id) -> dict`：返回 `{"balance", "frozen", "available"}`。
3. **写盘方式**：余额没变、只改 frozen，但流水必须与账户同一次落盘。参考 `service._apply()` 的做法：
   构造完整 data 快照后一次 `store.write()`。
4. `tests/test_freeze.py`（用真实临时存储，不 mock）：
   - 冻结后 balance 不变、frozen +5、available -5；
   - 可用不足时抛 `InsufficientBalance` 且三值不变、流水不增；
   - 部分解冻（冻结 10 → 解冻 4 → frozen=6）；
   - 解冻超过 frozen 抛 `InvalidArgument`；
   - 同一 source_id 重复冻结/解冻抛 `DuplicateRequest`；
   - `sum(frozen_delta) == frozen` 对账；
   - 冻结后 `WalletService.redeem` 按新 available 拒绝（跨模块验证，允许 import service）。
5. 跑 `python -m unittest discover -s tests -v`，确认全绿。

注意：不改 `models.py` / `service.py`。需要它们配合的地方先确认现状，缺能力就停下来报告。

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

- [ ] freeze：balance 不变、frozen 增加、available 减少
- [ ] freeze 可用不足抛 InsufficientBalance，无副作用
- [ ] unfreeze 支持部分解冻；超过 frozen 抛 InvalidArgument
- [ ] 同一 source_id 重复冻结/解冻抛 DuplicateRequest
- [ ] 冻结/解冻流水 amount=0、frozen_delta=±amount
- [ ] sum(frozen_delta) == frozen

## 12. 测试要求

- 冻结后三值关系
- 可用不足无副作用
- 部分解冻与全额解冻
- 解冻超额拒绝
- 冻结单号幂等
- frozen_delta 对账
- 记录每条命令的退出码和通过/失败数量，写进 test-report.json。

测试必须**实际执行**。不允许：
- 声称"测试通过"但没有命令输出；
- 为了让测试变绿而删改断言、跳过用例；
- 用 mock 把被测逻辑本身mock掉。

## 13. 交付物

- src/points/freeze.py
- tests/test_freeze.py
- `.devflow/nodes/DF-008/execution-report.md`
- `.devflow/nodes/DF-008/test-report.json`

## 14. 执行报告

完成后写两个文件：

1. `.devflow/nodes/DF-008/execution-report.md`（人读）——按 `.devflow` 的执行报告模板，必须包含一个机器可读证据块：

   ```devflow-execution
   {
     "node_id": "DF-008",
     "result": "success",
     "modified_files": ["相对项目根的路径"],
     "acceptance": [{"criterion": "验收标准原文", "met": true, "evidence": "测试名/命令"}],
     "assumptions": [],
     "leftovers": []
   }
   ```

2. `.devflow/nodes/DF-008/test-report.json`（机器读）——测试命令、退出码、通过/失败数、用例清单。

写完后**不要**修改 `.devflow/plans/*.json` 或看板。状态由主控 Skill 同步。

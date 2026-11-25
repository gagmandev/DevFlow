# 测试规则

## 一、测试必须匹配技术栈

不要给项目硬塞一套新框架。先看项目已有什么：

| 栈 | 优先用 |
| --- | --- |
| Python | `unittest` / `pytest`（项目已有哪个用哪个） |
| Node/TS | 项目已有的 Jest / Vitest / node:test |
| Go | `go test` |
| Java | JUnit（跟项目现有版本） |
| .NET | xUnit / NUnit（跟项目现有版本） |

`project.json` 的 `commands.test` 必须是**能直接跑的完整命令**。
验收时主控会照着它核对执行报告里的命令。

## 二、按层定测试重点

### 数据层

- 表/集合结构、主键、外键、索引
- 字段类型、长度、可空、默认值
- 迁移脚本能正向执行，也能回滚（或明确说明不可回滚）
- 必要初始化数据存在
- 写入是原子的（中断不留半截）

### 业务层

- 正常场景
- 异常场景（参数非法、余额不足、重复提交、越权）
- 边界场景（0、负数、上限、刚好等于）
- 关键业务规则（每条 BR 至少一个用例）
- 事务与一致性（失败时无副作用）
- 并发（抢同一份数据时结果可预期）

### 接口层

- 参数校验（缺字段、类型错、超长）
- 正常返回结构
- 异常返回与错误码
- 权限（未登录、越权、跨租户）
- 数据边界（分页越界、超大请求）
- 契约（字段名、类型、可空性）

## 二·五、用例明细与需求验收

新执行（有 `attempt_id`）必须提供 `cases` 明细：

- 条数等于 `summary.total`
- pass/fail/skip 数量与 `summary` 完全一致
- 一条 `fail` 就不能 `passed`
- 真的没有自动化测试 → 走 `no_test` + 理由，不要用空 `cases` 糊过去

需求验收要**逐条**对应，写在执行报告的 `requirement_acceptance`：

```json
"requirement_acceptance": [
  {"req_id": "REQ-001", "criterion": "退款成功后积分必须冻结", "met": true,
   "evidence": "test_refund_freezes_points"}
]
```

- 每条 active 需求的每条验收标准都要有一项
- 不能用别的 REQ 的证据，也不能"启动成功"代替业务验收
- 需求验收标准建议写成 `{id, criterion}`，文本改了也能对上

## 三、证据标准

验收看的是证据，不是描述。

| 要求 | 合格 | 不合格 |
| --- | --- | --- |
| 命令 | 完整可复制 | "跑了测试" |
| 退出码 | 明确写 0 | 不写 |
| 数量 | 通过/总数 | "全部通过" |
| 用例 | 列出名称与结果 | "覆盖了主要场景" |
| 失败 | 错误信息 + 根因 + 修复 | "已修复" |

## 四、质量门槛

`config/quality-gates.yaml` 控制：

```yaml
gates:
  node_accept:
    require_execution_report: true
    require_test_report: true
    require_tests_passed: true
    require_commands_executed: true
    out_of_scope: warning
  coverage:
    enabled: false        # 项目能统计覆盖率时才打开
    target_line: 0.80
    enforce: false
```

覆盖率只有在**项目本身能统计**时才作为门槛。为了凑覆盖率写无断言测试，
比没有覆盖率更糟。

## 五、禁止的"变绿"手段

1. 删断言、注释断言
2. `@skip` / `t.Skip()` / `pytest.mark.skip` 跳过失败用例
3. 放宽断言到永远为真（`assertTrue(True)`）
4. 把被测逻辑本身 mock 掉
5. 用 `try/except: pass` 吞掉异常
6. 加 `sleep` 掩盖竞态
7. 改测试数据让错误路径走不到

发现这些，主控应判 `failed`，并要求重做。

## 六、并发测试怎么写

- 用屏障（`Barrier` / `CountDownLatch` / `sync.WaitGroup`）让线程真正同时起跑
- 统计**成功次数**，不要只看最终余额——"锁失效但恰好没超卖"会被漏掉
- 断言不变量：余额不为负、冻结不超过总额、成功数 + 失败数 = 总请求数
- 跑多轮（比如 5 轮）降低偶发通过的概率
- 不要用固定 `sleep` 排序，那是在测调度器

## 七、不可自动化的部分

有些东西测不了，要如实写 `not_run` + 原因 + 替代确认方式：

| 项 | 替代 |
| --- | --- |
| 生产环境部署 | 部署脚本 dry-run + 人工评审 |
| 第三方支付回调 | 契约测试 + 沙箱环境 |
| 大数据量性能 | 小规模压测 + 估算 |
| UI 视觉 | 截图人工确认 |

**`not_run` 不等于通过。** 交付报告里要单独列出来。

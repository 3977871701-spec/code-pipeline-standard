# 开发计划：高可用订单服务（standard）

## 1. 项目目标

实现一个高可用的内存订单服务，提供订单的创建、查询、取消三个核心能力，并满足以下非功能性要求：

- **幂等性**：同一 `order_id` 的重复创建/取消请求只生效一次。
- **线程安全**：多线程并发调用不会破坏数据一致性。
- **完整错误处理**：参数非法、状态非法、并发冲突均有明确异常类型与错误码。
- **代码量**：约 200 行，2 个文件（`order_service.py` + `test_order_service.py`）。

## 2. 文件结构

```
/Users/xylei/code-pipeline-projects/standard/
├── order_service.py        # 业务实现（约 110-130 行）
└── test_order_service.py   # 单元 + 并发测试（约 70-90 行）
```

不引入额外包依赖（仅使用 `threading`、`dataclasses`、`enum`、`typing`、`pytest`）。

## 3. 模块划分

### 3.1 `order_service.py`

按职责拆分为四个内部概念模块（在同一文件内以类 / 区域划分，不做物理分包）：

| 子模块 | 标识 | 职责 |
|--------|------|------|
| 异常层 | `OrderError`, `OrderNotFoundError`, `OrderStateError`, `OrderValidationError`, `OrderDuplicateError` | 定义领域异常与错误码 |
| 数据层 | `Order` dataclass、`OrderStatus` enum | 订单实体与状态机定义 |
| 仓储层 | `OrderRepository`（可选并入服务层） | 线程安全的存储（`threading.RLock` + dict） |
| 服务层 | `OrderService` | 对外 API：create / get / cancel / list |

### 3.2 `test_order_service.py`

| 测试类别 | 用例 | 覆盖点 |
|----------|------|--------|
| 单测-异常 | `test_create_invalid_*`, `test_cancel_invalid_status` | 校验 / 状态机错误 |
| 单测-正常 | `test_create_get_cancel` | 主流程 |
| 单测-幂等 | `test_create_idempotent`, `test_cancel_idempotent` | 同一 order_id 重复请求只处理一次 |
| 并发测试 | `test_concurrent_create_same_id`, `test_concurrent_cancel` | 多线程同 order_id，验证最终态唯一 |
| 列表测试 | `test_list_filter_by_status` | 列表与过滤 |

并发测试使用 `threading.Barrier(N)` 让 N 个线程在同一时刻发起请求，最大化竞争。

## 4. 关键设计决策

### 4.1 幂等设计：双层防护

**决策**：业务参数幂等 + 结果幂等

1. **业务参数幂等**：`create_order(order_id, ...)` 第一次调用成功后，再次调用同一 `order_id` 必须**完全等价**地返回首次结果。
   - 若新参数与已存在订单不一致 → 抛 `OrderDuplicateError("DUPLICATE_ID_PARAM_MISMATCH")`。
   - 若参数一致 → 返回已存在订单（不抛异常、不重复扣库存——本服务无库存概念，记录调用计数）。
2. **结果幂等**：`cancel_order(order_id)` 第一次成功后，再次取消同一订单直接返回当前状态（已取消视为成功，不抛异常）。
3. **创建操作内部使用 `dict.setdefault` + 锁**实现"先到先得"，后到的线程复用首次结果。

### 4.2 线程安全方案

**决策**：粗粒度 `threading.RLock` + 关键路径原子化

- 仓储层使用 `threading.RLock`（可重入锁）保护 `self._orders: Dict[str, Order]`。
- 选用 `RLock` 而非 `Lock` 是为了在 `create_order` 内部调用 `get_order` 时不发生自死锁。
- 关键路径：`create_order`、`cancel_order`、`get_order`、`list_orders` 全部在锁内完成。
- 状态机转移（`PENDING → CONFIRMED → CANCELLED`）通过 `dataclass(frozen=True)` 不可变对象 + 整体替换实现，避免读改写竞态。

### 4.3 状态机

```
PENDING ──confirm──▶ CONFIRMED ──cancel──▶ CANCELLED
   │                     │
   └──cancel─────────────┴──▶ CANCELLED
```

- `PENDING` 可取消、可确认。
- `CONFIRMED` 只能取消。
- `CANCELLED` 是终态，所有操作均幂等返回当前态。
- 确认操作本版本**不开放**（需求只要求创建/查询/取消），保留接口以备扩展。

### 4.4 错误处理规则

| 异常 | 触发条件 | 错误码 |
|------|----------|--------|
| `OrderValidationError` | `order_id` 为空 / 非字符串 / 超长；`amount < 0`；`items` 为空 | `INVALID_ARG`, `EMPTY_ITEMS`, `NEGATIVE_AMOUNT` |
| `OrderNotFoundError` | 查询/取消不存在的 `order_id` | `NOT_FOUND` |
| `OrderStateError` | 对已取消订单做非法状态转移 | `INVALID_STATE` |
| `OrderDuplicateError` | 同 `order_id` 但参数不一致 | `DUPLICATE_ID_PARAM_MISMATCH` |

异常均继承自 `OrderError`，并提供 `code: str` 与 `message: str` 字段。

### 4.5 可观测性

- 仓储层维护 `self._stats: Dict[str, int]`，统计 `create_calls`, `create_hits`, `cancel_calls`, `cancel_hits`。
- 对外通过 `OrderService.stats()` 暴露，便于测试断言幂等命中次数。

## 5. 依赖关系

```
exception ──▶ entity ──▶ repository ──▶ service
                                  ▲
                                  │
                              (tests)
```

- 异常层不依赖任何其他层。
- 实体层依赖异常层。
- 仓储层依赖实体层与异常层。
- 服务层依赖仓储层与实体层。
- 测试层依赖服务层（黑盒调用）。

## 6. 开发顺序

| 阶段 | 任务 | 产出 | 验证方式 |
|------|------|------|----------|
| P0 | 定义异常类与错误码 | `OrderError` 家族 | 单元测试：异常可正常抛出与捕获 |
| P1 | 定义 `Order` dataclass + `OrderStatus` enum | 数据模型 | 实例化测试 |
| P2 | 实现 `OrderRepository`（CRUD + 锁） | 仓储层 | 单元测试：基本 CRUD |
| P3 | 实现 `OrderService`（幂等 + 状态机） | 服务层 | 单测：正常流程 + 异常分支 |
| P4 | 编写并发测试 | `test_concurrent_*` | pytest 运行，验证无数据竞争 |
| P5 | 覆盖率核查 | `pytest --cov=order_service` | 行覆盖 ≥ 90% |

## 7. 测试策略

- **单测框架**：`pytest`（项目已使用）。
- **并发原语**：`threading.Barrier` 同步起跑；`concurrent.futures.ThreadPoolExecutor` 管理线程。
- **断言要点**：
  - 幂等性：`stats["create_hits"] == N - 1`（N 个线程同 id，仅 1 次实际创建）。
  - 一致性：所有线程返回的 `Order` 实例 `id` 与字段完全相等。
  - 状态机：`status == CANCELLED`，无 `PENDING` 残留。
- **覆盖率目标**：核心服务层 ≥ 90%。

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| `RLock` 性能瓶颈 | 高并发下吞吐量受限 | 接受（standard 难度，2 文件限制）；后续可换 `dict + per-key Lock` |
| 测试 flaky | 并发测试偶发失败 | 多次循环 + `Barrier` 同步 + 重试断言 |
| 内存泄漏 | 测试不清理订单 | `pytest fixture` 提供 `fresh_service` 函数，每次新建 |

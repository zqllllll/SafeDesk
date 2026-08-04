# Typed State Store 设计

本文描述 `agentgate-core` 的 Typed State Store 接口、内存后端和 SQLite 持久化后端。

## 1. 目标

Typed State Store 解决四类基础问题：

1. 并行 Middleware 同时更新任务时发生静默覆盖；
2. TaskState 引用了不存在的 Evidence、Effect、Verification 或 Failure；
3. 相同工具结果或恢复操作重试时重复写入记录；
4. 恢复旧 Checkpoint 后版本倒退，或遗忘可能已经发生的外部副作用。

Store 不决定任务应该如何推进。合法状态迁移、依赖满足和完成条件判断属于后续 Task Reducer 与 Completion Gate。Store 只保证提交的数据在结构、引用、版本和存储语义上可靠。

## 2. 任务聚合

每个 Task 在内存中对应一个聚合对象，包含：

```text
TaskContract
current TaskState
Evidence Board
Effect Ledger
Verification Results
Failure Records
TaskStateEvent Log
Checkpoints
```

所有公开读写都返回深拷贝。虽然 Pydantic 顶层模型不可重新赋值，但 JSON Payload 内部仍可能包含可变字典；防御性拷贝可以防止调用方修改已存数据。

## 3. 初始化

`create_task` 可以接收显式初始状态，也可以从 TaskContract 生成默认状态：

- `state_version=1`；
- `phase=COLLECT`；
- 所有子目标为 `PENDING`；
- TaskContract 中要求的确认进入待确认列表；
- 自动记录一个 `CREATED` State Event。

初始状态的 Task ID、Contract Version 和子目标集合必须与 TaskContract 完全一致。

## 4. Event-driven 状态更新

TaskState 没有公开 Setter。调用方必须提交 `TaskStateEvent`：

```text
event_id
task_id
event_type
name
previous_state_version
next_state
reason
source_event_id
payload
occurred_at
```

`apply_task_event(task_id, event, expected_version)` 在一个锁内完成以下检查：

1. Event ID 在整个 Store 内唯一；
2. 当前版本等于 `expected_version`；
3. Event 的 previous version 等于 `expected_version`；
4. next version 必须恰好为 previous version 加一；
5. Task、Contract、Subgoal 和记录引用全部一致；
6. State 和 Event 时间不能倒退；
7. State 与 Event 原子写入。

相同 Event 的原样重试是幂等操作。相同 Event ID 携带不同内容会被视为冲突。

## 5. 乐观并发

并行调用方必须基于自己读取到的 State Version 提交事件。如果两个调用方都读取版本 3：

```text
Writer A: expected=3 -> 成功写入 version 4
Writer B: expected=3 -> VersionConflictError(actual=4)
```

Writer B 必须重新读取最新状态并重新规划，不能覆盖 Writer A 的结果。内存实现使用 `RLock` 保证检查和写入原子化；单元测试使用两个真实线程验证只有一个 Writer 能成功。

## 6. Typed Record 存储

Store 为 Evidence、Effect、Verification 和 Failure 提供 append、get 和 list 接口。

Append 语义：

- 新 ID：写入；
- 相同 ID、相同内容：幂等返回；
- 相同 ID、不同内容：`RecordConflictError`；
- 引用未知对象：`StateInvariantError`；
- 记录属于其他 Task：拒绝。

Effect 额外维护 `idempotency_key -> effect_id` 索引。不同 Effect 不能占用同一个幂等键。

Evidence、Effect 和 Failure 的生命周期更新使用 `expected_status`。这与 TaskState 的 expected version 作用相同，可以防止并发更新把较新的状态覆盖掉。记录身份字段不能在更新过程中改变。

## 7. 引用完整性

Store 会检查：

- VERIFIED Evidence 引用的 Verification 已存在；
- Effect 的 Verification 已存在；
- Verification 和 Failure 引用的 Evidence 已存在；
- TaskState 顶层和各 Subgoal 引用的记录已存在；
- Blocker 引用的 Failure 已存在；
- Pending Confirmation 来自 TaskContract；
- TaskState 的 Subgoal 集合与 TaskContract 完全一致。

Trace 的 `source_event_id` 暂不做存在性检查，因为 Trace Recorder 是后续独立组件。

## 8. Checkpoint

Checkpoint 保存当时的 TaskState、State Version、记录 ID 集合和 Event 数量。恢复不是把 Store 的版本号改回旧值，而是创建一个新版本：

```text
checkpoint state version = 2
current state version = 5
restored state version = 6
```

这样旧 Writer 不能因为版本倒退而意外通过并发检查。

Evidence、Effect、Verification 和 Failure 记录保持追加式，不会因恢复而删除。尤其是外部写操作可能已经发生，删除 Effect Ledger 会造成危险的“失忆”。恢复后：

- Checkpoint 的逻辑 TaskState 被恢复；
- 当前所有 Effect 继续保留在 TaskState 顶层；
- 所有 `IN_FLIGHT` Effect 转为 `UNKNOWN`；
- 生成 `CHECKPOINT_RESTORED` State Event；
- 后续 Verifier 必须回读环境后再确认真实状态。

## 9. 错误类型

公开错误均为确定性异常：

```text
TaskAlreadyExistsError
TaskNotFoundError
VersionConflictError
StateInvariantError
RecordNotFoundError
RecordConflictError
RecordVersionConflictError
IdempotencyConflictError
```

适配器可以将这些错误稳定映射为 Trace、FailureRecord 和 Recovery 决策，而不需要解析异常文本。

## 10. SQLite 持久化后端

`SQLiteTypedStateStore` 将每个 Task Aggregate 保存为经过 `StoredTaskAggregate` 校验的完整 JSON Snapshot，并为每个 Task 维护独立 storage revision。

持久化保证：

- SQLite WAL；
- `synchronous=FULL`；
- 每次聚合更新使用 `BEGIN IMMEDIATE`；
- `UPDATE ... WHERE revision = expected_revision` 防止跨实例丢失更新；
- 冲突后自动重新加载已提交状态；
- 相同内容的幂等重放不增加 storage revision；
- 重启后恢复 Contract、State、Evidence、Effect、Verification、Failure、Event 和 Checkpoint；
- 恢复时重新检查所有引用、事件连续性和幂等键唯一性。

SQLite Store 的内存视图不会自动轮询其他进程。长时间存活的第二个实例需要调用 `refresh()` 获取其他 Writer 的最新提交；任何过期写仍会被 storage revision 拒绝，不会覆盖新数据。

## 11. 当前边界

当前两个后端都实现同一个 `TypedStateStore` Protocol：内存后端用于快速单元测试，SQLite 后端用于本地持久化实验。

本阶段尚未实现：

- Task Reducer 的合法状态迁移规则；
- Checkpoint 恢复后的自动环境回读；
- Completion Gate、Recovery Controller 或业务政策判断。

这些功能将在后续步骤中复用当前接口，而不是绕过 Store 直接修改状态。

# SafeDesk 公共底座 Phase 0 冻结说明

## 1. 本阶段结论

SafeDesk 四大模块共享的公共底座已经具备可开发、可持久化、可回放的最小闭环：

```text
TaskContract / TaskState
        ↓
Typed State Store
        ↓
ActionIR
        ↓
AgentGate Coordinator
        ↓
Typed Decision
        ↓
Trace Recorder / Replay
```

从下一阶段开始，State & Verification、Tool Execution Guard、Recovery Controller 和 Context Manager 只增加 Stage 实现和领域状态，不再各自创建新的运行总线。

## 2. 冻结的公共 Contract

协议版本继续使用 `1.0`。当前公开 JSON Schema 包括：

```text
TaskContract
TaskState
ActionIR
ActionEvaluationContext
AgentGateFeatureConfig
GateDecision
CoordinatorResult
ToolCatalogSnapshot
EvidenceItem
EffectRecord
VerificationResult
FailureRecord
ContextPack
TraceEvent
```

所有 Contract 均为严格、冻结、禁止额外字段的 Pydantic 模型。Schema 采用 Draft 2020-12，并由测试检查源码模型与仓库文件不存在漂移。

## 3. Feature Flag 语义

四个核心模块分别配置：

```text
state_verification
tool_execution_guard
recovery_controller
context_manager
```

模式语义固定为：

| 模式 | 是否调用模块 | 是否影响 Agent 行为 |
| --- | --- | --- |
| `OFF` | 否 | 否 |
| `SHADOW` | 是 | 否，effective outcome 固定为 `ALLOW` |
| `ENFORCE` | 是 | 是 |

配置包含确定性的 `configuration_hash`。Coordinator 会把该哈希写入 `ACTION_PROPOSED` Trace，后续实验可以拒绝合并配置不同的结果。

## 4. 统一 Decision

所有 Stage 使用同一组 Outcome：

```text
ALLOW
DENY
REPLAN
REQUIRE_EVIDENCE
REQUIRE_CONFIRMATION
REQUIRE_APPROVAL
ALREADY_APPLIED
DEFER
```

`GateDecision` 同时记录：

- `proposed_outcome`：模块判断；
- `effective_outcome`：实际对 Runtime 生效的结果；
- Feature 和 Stage；
- OFF、SHADOW 或 ENFORCE 模式；
- 稳定 reason code；
- 人类可读解释；
- Evidence 引用和结构化 Payload。

Coordinator 在 ENFORCE 模式遇到第一个非 `ALLOW` 结果时停止后续 Stage。OFF 和 SHADOW 不允许产生非 `ALLOW` 的 effective outcome，这一规则由 Contract 校验，不依赖调用方自觉。

## 5. Coordinator 空管线

Phase 0 默认空管线顺序为：

```text
Schema Guard
→ Dependency Scheduler
→ Policy Gate
→ Effect Preflight
```

当前 PassThrough Stage 不执行模块规则，但 Fake Action 必须完整经过四个阶段。每个阶段都产生 GateDecision 和 TraceEvent。后续 Tool Execution Guard 直接替换这些 Stage，不改变 Coordinator。

Stage 内部异常不会静默放行：模块建议结果变成 `DENY/stage_exception`。SHADOW 下仍只记录，ENFORCE 下 fail closed。

## 6. Trace Recorder

Trace Sink 接口固定为：

```text
append(event)
get_event(event_id)
list_events(task_id, run_id)
last_sequence(task_id, run_id)
close()
```

当前实现：

- `InMemoryTraceSink`；
- `SQLiteTraceSink`。

两种 Sink 都保证：

- Event ID 全局唯一；
- 每个 Task/Run 的 sequence 从 0 连续递增；
- Parent 必须已经存在且属于同一 Task/Run；
- 相同 Event 原样重放幂等；
- 相同 Event ID、不同内容拒绝；
- 返回值使用防御性拷贝。

SQLite Sink 使用 WAL、`synchronous=FULL` 和 `BEGIN IMMEDIATE`。

## 7. Trace 失败策略和脱敏

写 Action 的任何 Trace 读取或写入失败都会抛出 `TracePersistenceRequiredError`，Action 不得继续进入管线。

只读 Action 默认同样严格。只有显式配置 `allow_read_on_trace_failure=true` 时，读取可以返回结构化 degraded result。Coordinator 配置和 Recorder 策略必须一致，否则初始化失败。

Trace Payload 持久化前递归脱敏：

```text
password / passwords
access_token / token
api_key / authorization / secret
card_number / credit_card_number / cvv
```

`RedactionMetadata` 记录脱敏字段路径和策略版本。

## 8. Trace Replay

Replay 检查：

- 单一 Task/Run；
- sequence 连续；
- Event ID 不重复；
- Parent 先于 Child；
- State Version 不倒退；
- TaskState Payload 与 Event State Version 一致；
- TOOL_STARTED 和 TOOL_FINISHED 是否配对。

Replay 可以从 `TASK_CREATED` 和 `TASK_STATE_CHANGED` 重建最新主要 TaskState，但不会自行推断业务完成、Effect 验证或 Failure Recovery。

## 9. Typed State Store

当前有两个同协议后端：

```text
InMemoryTypedStateStore
SQLiteTypedStateStore
```

共同保证：

- Event-driven TaskState 更新；
- Task State 乐观版本；
- Evidence、Effect 和 Failure 状态更新的 expected status；
- Record 幂等写入；
- Effect idempotency key 唯一；
- 聚合内引用完整；
- Checkpoint 恢复版本单调增加；
- 恢复后所有 `IN_FLIGHT` Effect 变成 `UNKNOWN`。

SQLite 后端额外使用 storage revision 防止多个 Store 实例覆盖彼此提交。冲突时当前实例自动刷新到数据库最新状态。

SQLite State Store 当前不提供数据库级加密，也不会擅自改变 Contract Payload。Adapter 和模块在写入 Evidence、Effect 或 Failure 前必须完成数据最小化与凭据脱敏；不能把 API Key、密码或完整访问令牌交给 Store 后再期待 Store 自动清除。正式处理真实敏感数据前还需要配置磁盘权限或增加加密后端。

## 10. Runtime Session

`AgentGateRuntimeSession` 固定一个 Run 使用的：

```text
run_id
TypedStateStore
AgentGateCoordinator
TraceRecorder
```

它提供 Task 创建、Task State Event、Checkpoint 创建/恢复和 Action Evaluation 的最小生命周期，并确保 Coordinator 与 Session 共享同一个 Recorder。

State SQLite 和 Trace SQLite 当前是两个独立事务域，无法组成数据库级分布式事务。Session 的处理原则是：状态提交后 Trace 如果失败，立即抛错并停止 Run，不允许继续外部写操作。State Store 自身的 TaskStateEvent 仍保留内部审计事实，后续启动恢复时必须先修复或标记 Trace 缺口。不能把两个数据库的提交误称为原子提交。

## 11. Phase 0 验收映射

| 退出条件 | 当前结果 |
| --- | --- |
| 协议单元测试通过 | 已通过 |
| Fake Action 完整经过空管线 | 四阶段、五条 Action/Decision Trace 已通过 |
| 每阶段产生 TraceEvent | 已通过 |
| Trace 可以 SQLite 持久化和 Replay | 已通过 |
| State 可以 SQLite 持久化和重启恢复 | 已通过 |
| 跨实例旧写不会覆盖新写 | 已通过 |
| 写 Action 无 Trace 时 fail closed | 已通过 |
| `agentgate-core` 不依赖框架或 Benchmark | 静态测试已通过 |

## 12. 明确不属于底座的能力

以下内容从下一阶段进入四大模块实现：

- Task Reducer 的业务状态转换表；
- Evidence 冲突检测和 Effect 到 Subgoal 的关联；
- Post-action Verifier；
- Completion Gate 和 Response Grounding；
- JSON Schema Guard、Effect Ledger Preflight 和真实 Tool Executor；
- Failure Classifier、Typed Recovery 和 Stagnation Detector；
- Context Budget、Projector、Compression 和 Retrieval。

底座只提供这些模块共同使用的协议、顺序、持久化、回放和 Feature Mode，不预先决定模块业务规则。

# Tool Execution Guard 模块说明

## 1. 模块目标

Tool Execution Guard 位于模型输出和真实工具执行之间。它不判断任务最终是否成功，而是保证每个 Tool Call 在执行前满足以下条件：

1. 工具来自公开且版本明确的 Tool Catalog；
2. 工具位于当前任务的 Active Tool Set；
3. 参数满足绑定版本的 JSON Schema；
4. Action 的依赖、Evidence 和策略条件已经满足；
5. 写操作不会重复制造副作用；
6. 同一批 Tool Call 的执行顺序不会破坏状态依赖；
7. 未执行调用会获得结构化 Tool Result，不会被静默丢弃。

## 2. 核心数据对象

- `RawToolCall`：模型提出的标准 function-calling 调用。
- `ActionIR`：框架无关的规范化动作，包含 read/write、资源、依赖、风险和预期 Effect。
- `ToolCatalogSnapshot`：公开工具、Schema、语义元数据和版本的不可变快照。
- `ActiveToolSet`：某任务当前可见工具集合，具有独立递增版本。
- `ActionSchedule`：一批 Action 的执行、延后、重规划和状态刷新决策。
- `PolicyDecision`：身份、确认、审批、资源范围、参数约束和预算的确定性判定。
- `EffectPreflightDecision`：写操作执行前的幂等和 Effect 状态判定。
- `ToolResolution`：Dynamic Tool Resolver 的候选、选择和工具集变更结果。

这些对象均为版本化 Pydantic Contract，可导出 JSON Schema，并写入 Trace。

## 3. 执行链路

```text
RawToolCall
  -> ActionNormalizer
  -> ToolSchemaGuard
  -> ActionDependencyScheduler
  -> PolicyGate
  -> GuardedEffectLedger.preflight
  -> GuardedEffectLedger.reserve（仅允许的写操作）
  -> Tool execution
```

### 3.1 Normalizer

Normalizer 从 Tool Catalog 读取动作类型、资源类型、风险、Schema 版本和副作用类型。写操作会生成稳定的幂等键和 ExpectedEffect；读操作不能携带 Effect 或幂等键。

Catalog 中的语义 Evidence 要求通过 `evidence_by_requirement` 绑定到运行期真实 Evidence ID。依赖工具通过 `dependency_action_by_tool` 绑定到已完成 Action，避免把静态语义键误当成运行时记录 ID。

### 3.2 Schema Guard

Schema Guard 检查工具存在性、Active Tool Set、Schema 版本和参数。当前覆盖 AppWorld 公共 function schema 使用的对象、数组、标量、required、enum、const、长度、正则、数值边界、exclusive 边界以及 allOf/anyOf/oneOf。

Schema 外调用不会绕过 Guard 直接执行；修复只能由 Recovery Controller 生成新的 Action。

### 3.3 Dependency Scheduler

- 没有依赖关系的只读 Action 可进入同一 execution group；
- 有依赖的读取进入后续 group；
- 第一条依赖就绪的写 Action 单独执行；
- 写 Action 后的其余调用返回 `suppressed_pending_state_refresh`；
- 每个模型 Tool Call 都有对应的执行决策和结构化 Tool Result。

Scheduler 不真正创建线程。Runner 可以串行执行同组读取，也可以安全并发。

### 3.4 Policy Gate

第一版策略引擎是规则优先、确定性执行，不让模型自行解释策略。支持：

- 身份状态；
- 用户确认和审批；
- Actor、工具和资源类型范围；
- 资源 scope；
- 参数 exists/equals/in/minimum/maximum；
- 风险上限；
- Tool Call 和写操作预算。

τ² adapter 只接受人工审阅后的结构化规则，不自动把自然语言政策转换为可执行权限规则。

### 3.5 Effect Preflight

写操作按规范化幂等键查询 Effect Ledger：

| 已有状态 | 处理 |
| --- | --- |
| 无记录、ROLLED_BACK | RESERVE |
| VERIFIED | ALREADY_APPLIED |
| IN_FLIGHT、UNKNOWN、APPLIED_UNVERIFIED | VERIFY_FIRST |
| FAILED | RECOVERY_REQUIRED |
| PLANNED、RESERVED | WAIT |

Effect 的创建、Subgoal 绑定和状态转换是三个不同操作，避免重复 append 或未归属副作用。

## 4. Dynamic Tool Resolver

Resolver 从公开 Catalog 按 operation、resource、policy 和描述词进行候选排序。Shadow 模式只记录建议，不改变模型行为；Enforce 模式只扩展最少候选并提升 Active Tool Set 版本。变更后必须重建 ContextPack，旧计划因 state/schema version 不一致而重新规划。

## 5. Trace 和恢复

关键事件包括 `ACTION_PROPOSED`、`SCHEMA_DECISION`、`POLICY_DECISION`、`TOOL_RESOLUTION`、`TOOL_SET_CHANGED`、`EFFECT_RESERVED`、`EFFECT_STATUS_CHANGED`、`TOOL_NOT_EXECUTED`、`TOOL_STARTED` 和 `TOOL_FINISHED`。

任何未执行调用都会记录原因和以下结构化返回：

```json
{
  "executed": false,
  "reason": "suppressed_pending_state_refresh",
  "message": "This tool call was not executed. Re-plan using the latest environment state."
}
```

Guard 不做盲目重试。参数、工具、依赖、策略和 Effect 问题统一转换为 FailureSignal，交给 Recovery Controller。

## 6. Adapter 边界

- AppWorld adapter 负责把公开 API docs 转成 Catalog，并提供 `to_core_catalog()`。
- `supervisor__complete_task` 被视为控制面动作，不创建需要环境回读的业务 Effect；是否完成由 Completion Gate 决定。
- Runner 负责执行 `should_execute=true` 的调用，并把其余结构化结果按原 `tool_call_id` 返回模型。
- Runner 不得绕过 orchestrator 执行 out-of-schema 调用。


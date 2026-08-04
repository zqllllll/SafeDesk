# Recovery Controller 模块说明

## 1. 模块目标

Recovery Controller 将“工具失败后再试一次”改造成有类型、有证据、有预算、可验证的恢复流程。它解决参数错误、资源错误、工具异常、验证不一致、重复调用、无进展和基础设施故障混用同一种重试策略的问题。

## 2. Failure Taxonomy

`FailureClassifier` 采用规则优先分类，输出 `FailureRecord`，至少区分：

- Schema：`INVALID_ARGUMENT`、`OUT_OF_SCHEMA`、`WRONG_TOOL`；
- 状态和资源：`WRONG_RESOURCE`、`DEPENDENCY_NOT_SATISFIED`；
- Policy：`POLICY_DENIED`、`APPROVAL_REQUIRED`、`PERMISSION_DENIED`；
- Tool/Infra：`TOOL_EXECUTION_ERROR`、`TOOL_TIMEOUT`、`RATE_LIMITED`、`INFRASTRUCTURE_ERROR`；
- Verification：`VERIFICATION_FAILED`、`UNINTENDED_SIDE_EFFECT`；
- Agent progress：`DUPLICATE_ACTION`、`PARTIAL_COMPLETION`、`NO_PROGRESS`；
- Context：`CONTEXT_DEGRADED`。

Failure 同时记录责任层、是否可恢复、关联 Action、剩余预算和状态。基础设施问题与模型能力错误分开。

## 3. Recovery 生命周期

```text
FailureSignal
  -> classify
  -> consume budget
  -> select typed strategy
  -> refresh uncertain Effect when required
  -> execute local repair/re-plan
  -> compare ProgressSignal
  -> verify required recovery
  -> RESOLVED / OPEN / BUDGET_EXHAUSTED
```

`RecoveryController.start()` 只产生计划，不直接调用真实工具。Runner 执行计划后，用前后 ProgressSignal 调用 `finish()`。

## 4. Typed Strategy Registry

内置策略包括：

- `REPAIR_ARGUMENTS`：只做 Schema 可证明的安全转换，如删除额外字段、数字字符串转整数；
- `RESOLVE_TOOL`：调用 Dynamic Tool Resolver 后局部重规划；
- `RELOCATE_RESOURCE`：丢弃过时 ID，通过权威读取重新定位；
- `RESCHEDULE`：按 Action DAG 恢复依赖顺序；
- `REQUEST_CONFIRMATION` / `REQUEST_APPROVAL`：停止写入并等待外部条件；
- `VERIFY_BEFORE_RETRY`：超时、重复写、未知 Effect 等情况先回读环境；
- `REPAIR_VERIFICATION`：只修复 VerificationResult 指出的字段并再次验证；
- `COMPLETE_MISSING_SUBGOAL`：只处理未验证的必要子目标；
- `REBUILD_CONTEXT`：从当前真实状态重建 ContextPack；
- `INFRASTRUCTURE_RETRY`：有界退避，不修改任务目标；
- `STOP`：权限或不可安全自动修复的问题真实终止。

恢复后的 Action 使用新 Action ID 和重新计算的幂等键，并依赖原 Action，避免把一次修复伪装成原调用。

## 5. Progress 与 Stagnation

`ProgressTracker` 比较恢复或每轮执行前后的持久化状态，Progress 只来自：

- 新增 Verified Evidence；
- 新增 Verified Effect；
- Task State 事件；
- 已解决 Failure；
- 新发现资源 ID；
- 新满足 Completion Condition。

模型文字、计划和普通 Tool Result 不自动计为完成进展。

`StagnationDetector` 同时观察：

- 连续窗口无进展；
- 规范化 Tool Call 指纹重复；
- 同类型 Failure 重复；
- Token 增长但无进展。

一旦产生真实进展，重复计数可以重置；停滞事件写入 Trace，由 runner 决定局部重规划或进入 Recovery。

## 6. Recovery Budget

预算同时限制：

- 每种 Failure 的尝试次数；
- 单任务总尝试次数；
- Recovery Token 总量。

预算配置和状态是版本化 Contract；checkpoint 恢复时必须与当前配置一致。预算耗尽后返回明确的未完成状态，不得回复成功，也不得继续隐藏重试。

## 7. 成功判定

Recovery 成功至少要求 `progress_after > progress_before`。如果计划要求写前验证或属于 Verification Repair，还必须提供新的 `verification_id`。因此“工具返回成功”或“模型说已修复”都不能单独令 Failure 进入 RESOLVED。

## 8. Trace

恢复链路记录 `FAILURE_CLASSIFIED`、`RECOVERY_PLANNED`、`PROGRESS_ASSESSED`、`STAGNATION_DETECTED` 和 `RECOVERY_FINISHED`。每条记录包含类型化对象和预算信息，可用于回放、指标统计和模块消融。


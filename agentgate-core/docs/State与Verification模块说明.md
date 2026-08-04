# State & Verification 模块说明

## 1. 模块目标

State & Verification 是 SafeDesk 的第一个核心模块。它不负责提升模型本身的规划能力，而是让运行时只依据可追溯的真实状态作出完成结论，重点解决：

- 原始目标在长轨迹中漂移；
- 必要子任务被遗漏；
- Tool Call 返回成功后直接宣称任务完成；
- 写操作已经执行，但环境状态没有正确改变；
- Agent 在缺少证据、存在冲突或仍有未决失败时提前结束；
- 最终回复与真实执行状态不一致。

本模块坚持以下证据层级：

```text
模型推断 < Tool Result / 用户陈述 < 环境回读验证
```

Tool Result 只能形成 `OBSERVED` Evidence。只有通过 Agent 在运行时可访问的公开只读 API 回读环境，并与 Expected State 比较成功后，才能生成 `VERIFIED` Evidence 和 `VERIFIED` Effect。

## 2. 组件与职责

### 2.1 Task Reducer

文件：`state_verification/task_reducer.py`

- 实现 Subgoal 合法状态转换表；
- 检查依赖子目标是否已经 `COMPLETED_VERIFIED`；
- 禁止从 `PENDING` 等状态直接跳到完成；
- 完成前检查关联 Evidence、Effect 和 CompletionCondition；
- 通过 `TaskStateEvent` 和乐观版本号更新 TaskState；
- 每次状态变化写入可回放 Trace。

Reducer 是修改 TaskState 的唯一业务入口。模型文本、Tool Result 和适配器不能直接把子目标改成 `COMPLETED_VERIFIED`。

### 2.2 Evidence Board

文件：`state_verification/evidence_board.py`

- 保存 Evidence 的来源、作用域、观察时间和状态；
- 对相同 `subject + predicate + scope` 的不同值检测冲突；
- 冲突时将新旧证据都标记为 `CONFLICTED`；
- 支持 `STALE`、`REVOKED` 等显式状态变化；
- 禁止 Tool Result 或模型推断直接创建 Verified Evidence；
- 记录 Evidence 创建、冲突和状态变化 Trace。

### 2.3 Effect Ledger 关联

文件：`state_verification/effect_ledger.py`

- 保存写操作的预期副作用；
- 使用明确的 `subgoal_id` 将 Effect 关联到 Subgoal；
- 不根据自然语言描述猜测 Effect 属于哪个子目标；
- 记录 Effect 与 Subgoal 的关联 Trace。

Effect 的完整幂等预检和重复副作用拦截属于后续 Tool Execution Guard；本模块消费 Effect 状态并阻止未验证 Effect 支持完成结论。

### 2.4 Verifier Registry 与 Post-action Verifier

文件：`state_verification/verifier.py`

- `VerifierSpec` 声明资源类型、目标字段、忽略字段、禁止字段、最终一致性等待时间和最大尝试次数；
- Registry 根据资源类型解析 Environment Verifier；
- Verifier 读取真实环境状态并生成 `VerificationObservation`；
- 逐字段比较 Expected State 与 Observed State；
- 生成 `VerificationResult` 和环境来源的 Verified Evidence；
- 将 Effect 更新为 `VERIFIED`、`FAILED` 或 `UNKNOWN`；
- 验证失败时生成结构化 Failure；
- 满足全部条件后将 Subgoal 晋升为 `COMPLETED_VERIFIED`。

最终一致性重试会持续到回读状态匹配、达到尝试上限或返回最终错误，不能只因读到了非空对象就提前结束。

### 2.5 AppWorld 环境回读适配器

文件：`benchmark-adapters/appworld/src/agentgate_appworld/verifier_adapter.py`

适配器使用注入的公开 AppWorld API Executor 和 `AppWorldReadbackProfile`。每个 Profile 声明资源类型、App、只读 API、资源 ID 参数和响应提取路径。

适配器不会导入或调用 AppWorld Evaluator，不读取测试断言、隐藏 Ground Truth 或最终分数。缺少资源 ID、Profile、响应路径或 API 执行失败时返回结构化错误，不把状态猜成成功。

### 2.6 Completion Gate

文件：`state_verification/completion_gate.py`

完成请求放行前检查：

- 所有 required Subgoal 都是 `COMPLETED_VERIFIED`；
- 所有 CompletionCondition 都有 Verified Evidence 或 Verified Effect；
- 不存在非 `VERIFIED` Effect；
- 不存在未解决 Failure；
- 不存在待确认或待审批条件；
- 不存在 Evidence 冲突；
- 不存在环境验证发现的额外副作用；
- 不存在重复的不可逆资源操作。

每个阻断项输出类型、关联记录 ID、解释和建议返回阶段。`SHADOW` 模式记录“本应阻断”，但 Coordinator 实际结果保持 `ALLOW`；`ENFORCE` 模式返回 `REQUIRE_EVIDENCE` 并阻止完成调用。

### 2.7 Response Grounding Gate

文件：`state_verification/response_grounding.py`

该 Gate 提取中英文最终回复中的成功、部分完成、失败、等待确认和等待审批声明。成功或部分完成声明超过 Completion Gate 能证明的状态时，会降级为有证据支持的真实状态，并记录原回复、降级回复、证据和原因。

当前实现处理任务级完成声明。针对“邮件已发送”“订单已创建”等资源级声明，后续可以在不改变合同的前提下增加领域 Claim Extractor。

### 2.8 Trace 与指标

新增 TraceEvent：

```text
effect_linked
evidence_recorded
evidence_status_changed
evidence_conflicted
verification_finished
completion_decision
response_grounding_decision
```

`summarize_state_verification` 从 Trace 计算 Completion 放行/阻断、假完成候选、Verification 状态分布、Blocker 分布以及 Response Grounding 降级数量。

## 3. Runner 接入顺序

```text
1. 将任务指令归一化为 TaskContract
2. Runtime Session 创建 TaskState
3. 每个写 Action 执行前生成 EffectRecord，并关联 Subgoal
4. Tool Result 返回后将 Effect 标为 APPLIED_UNVERIFIED
5. Registry 选择对应公开只读 Verifier
6. PostActionVerifier 回读并更新 Effect、Evidence、Failure 和 Subgoal
7. complete_task 或正常终止进入 CompletionGateStage
8. 最终自然语言回复进入 ResponseGroundingGate
9. Trace Recorder 持久化全部状态、验证和判定
```

构造顺序必须避免依赖环：先创建 State Store 和 Trace Recorder，再创建 Completion Gate 与 Coordinator，最后创建 Runtime Session。

## 4. AppWorld 离线 Shadow 审计

`agentgate_appworld.state_verification_audit` 可以审计已转换历史轨迹。旧轨迹没有 TaskContract，也没有真实写后回读结果，因此使用保守口径：

- 明确标记 `conservative_without_task_contract`；
- 识别历史 `complete_task`；
- 统计完成前未验证 Effect 和 Verified Evidence 缺失；
- 不读取 Evaluator 或 Ground Truth；
- 不将审计结果解释为 Benchmark 正确率。

本次真实轨迹检查结果：

| 实验 | 任务数 | 完成请求 | Shadow 会阻断 | 无完成请求 |
| --- | ---: | ---: | ---: | ---: |
| smoke_3 | 1 | 0 | 0 | 1 |
| smoke_4 | 1 | 1 | 1 | 0 |
| challenge smoke_5 | 5 | 4 | 4 | 1 |

这些数据只证明历史完成请求缺少本模块要求的运行时证据，不能证明任务本身成功或失败。

## 5. 已验证的不变量

- Tool Result 不能创建 Verified Evidence；
- 非法状态跳转会被拒绝；
- 未验证写操作不能支持 Subgoal 或 Task 完成；
- 环境回读不一致会生成 Failure 并阻止完成；
- Shadow 模式不改变基线执行结果；
- Enforcement 模式会阻止无证据完成；
- 每个完成判定都有具体 Blocker；
- Verified Evidence 必须关联 Verification ID 和来源 Event ID；
- AppWorld 适配器不依赖 Benchmark 隐藏信息；
- 新合同有版本化 JSON Schema，并受 Schema 漂移测试保护。

## 6. 当前边界

- TaskContract 自动生成属于 Runner/Task Adapter，不应由 Completion Gate 根据轨迹反推；
- AppWorld 每种可写资源仍需配置对应公开回读 Profile；没有 Profile 时失败关闭；
- Tool Schema Guard、依赖调度、写前幂等检查和 Policy Gate 属于 Tool Execution Guard；
- 自动恢复验证失败属于 Recovery Controller；
- 长轨迹压缩和证据摘要属于 Context Manager。

这些边界是有意保持的模块职责划分，不应通过读取 benchmark 答案或放宽证据口径绕过。

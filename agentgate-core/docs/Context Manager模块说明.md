# Context Manager 模块说明

## 1. 模块目标

Context Manager 不负责创造事实，而是从持久化 Task State、Evidence、Effect、Failure、Tool Catalog 和 Trace 中构造本轮模型可见的只读快照。它解决工具结果累积、关键约束丢失、过时计划继续执行和摘要把未验证状态写成成功的问题。

## 2. ContextPack 分层

| 优先级 | 内容 | 处理方式 |
| --- | --- | --- |
| P0 | Task Contract、原始目标和硬约束 | 永久保留 |
| P1 | 当前 Task State、Evidence、Effect、Failure | 永久保留 |
| P2 | Active Tool Schemas | 当前版本保留 |
| P3 | 最近相关 Tool Result 投影 | 可按预算删除 |
| P4 | 已关闭历史的结构化摘要 | 优先压缩或删除 |

ContextPack 绑定 `state_version`、Active Tool Set 和 Token Budget。模型返回后，`ContextFreshnessStage` 会比较当前 State Version；不一致时拒绝旧计划并要求重建。

## 3. Token 预算

`HeuristicTokenEstimator` 提供稳定、与模型 tokenizer 解耦的预估；真实 API Token 仍由 runner 记录。`ContextBudgetAllocator` 从 P4、P3、可压缩 P2 开始释放预算，P0/P1 不因省 Token 被删除。

预算同时考虑预留输出 Token：

- `WITHIN_BUDGET`：输入和预留输出未超过软限制；
- `SOFT_EXCEEDED`：超过软限制但未超过硬限制；
- `HARD_EXCEEDED`：永久保留内容本身已无法安全发送，ContextBuilder 失败闭锁，不调用模型。

## 4. Tool Result Projection

原始 Tool Result 完整保留在 Trace，模型上下文只接收结构化投影。通用 Projector 保留：

- ID 和资源引用；
- success/status/error/message；
- 分页字段；
- 调用方声明的必要字段。

AppWorld Projector 额外限制列表条目数量，并保留条目 ID、名称、标题、状态和必要字段。每个投影携带 `RawTraceReference`，需要时可以有界回取原事件；不做不可追溯的字符串截断。

## 5. 结构化历史摘要

摘要只从类型化 Trace 事件提取事实：

- Verification 的显式状态；
- Recovery 的显式成功结果；
- Tool execution 已返回，但 Effect verification 仍独立判断。

摘要不会根据模型自然语言推断完成，也不会把 `OBSERVED` 升级为 `VERIFIED`。源事件 ID 和 Raw Trace Reference 始终保留。

## 6. 压缩后不变量

ContextPack 发送前必须通过 `ContextInvariantValidator`：

1. 原始目标未改变；
2. 活跃硬约束未丢失；
3. Active Subgoal 未丢失；
4. Verified Evidence 未丢失或降格记录被错误升级；
5. 未解决 Effect 及其资源 ID 未改变；
6. Open Failure 未丢失；
7. Pending Confirmation 未改变；
8. State Version 与持久化状态一致。

违反任一不变量时不会调用模型，而是抛出 `ContextBuildError`，由 Recovery Controller 选择重建或停止。

## 7. 重建触发

以下事件要求重建 ContextPack：

- Task State Version 变化；
- Dynamic Tool Resolver 修改 Active Tool Set；
- Recovery 完成或失败状态变化；
- 用户确认、撤销或需求变更；
- 写操作后状态刷新；
- 当前 Context 达到预算边界。

旧 ContextPack 和旧 Action 不能通过修改字段继续使用，必须从 State Store 重新生成。

## 8. 组件接口

- `ContextBuilder.build()`：构造并校验 ContextPack；
- `ContextBudgetAllocator.allocate()`：执行优先级预算；
- `ToolResultProjectorRegistry`：按工具注册投影器；
- `RawTraceRetriever.retrieve()`：按引用有界回取；
- `StructuredHistorySummarizer.summarize()`：生成可追溯摘要；
- `ContextFreshnessStage.evaluate()`：执行前检查版本新鲜度。


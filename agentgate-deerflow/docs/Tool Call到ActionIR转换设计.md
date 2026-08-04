# DeerFlow Tool Call 到 ActionIR 转换设计

本文说明 `agentgate-deerflow` 如何把 DeerFlow/LangChain 工具调用转换为框架无关的 `ActionIR`。当前实现属于 Shadow Observability 的输入层，不执行工具，也不改变 Agent 行为。

## 1. 转换目标

原始 Tool Call 只能表达：

```text
tool_call_id
tool_name
arguments
```

治理层还需要知道：

```text
读或写
操作语义
资源类型和资源 ID
风险等级
预期副作用
所需真实证据
Action 依赖
幂等键
模型实际看到的 Tool Schema 版本
来源轮次和 Actor
```

Adapter 将这些信息组合成 `ActionIR`，后续 State、Effect Ledger、Schema Guard 和 Trace 只依赖统一对象，不再解析 DeerFlow 消息。

## 2. 支持的输入形态

支持 LangChain 标准结构：

```json
{
  "id": "call-1",
  "name": "write_file",
  "args": {"path": "/a.txt", "content": "hello"},
  "type": "tool_call"
}
```

也支持 OpenAI 原始 function 结构：

```json
{
  "id": "call-1",
  "type": "function",
  "function": {
    "name": "write_file",
    "arguments": "{\"path\":\"/a.txt\",\"content\":\"hello\"}"
  }
}
```

可以直接传入 Mapping，也可以传入具有 `.tool_call` 属性的真实 `ToolCallRequest`。规范化后统一得到 `NormalizedDeerFlowToolCall`。

以下情况会在执行前产生稳定分类错误：

- 输入不是 Mapping；
- Provider 已标记为 Invalid Tool Call；
- 缺少 Tool Call ID；
- 缺少工具名；
- arguments 不是合法 JSON；
- arguments 不是 JSON Object；
- arguments 含有非 JSON 值；
- 工具不在 Catalog；
- 工具没有人工审核过的 Profile；
- 同一模型响应包含重复 Tool Call ID；
- 资源 ID 映射到对象或数组等非法值。

## 3. Tool Catalog 是唯一语义来源

DeerFlow 当前没有统一的读写和副作用元数据。现有 Middleware 对部分工具使用硬编码名称集合，但 Adapter 不使用名称前缀或自然语言描述猜测工具性质。

每个可转换工具必须有显式 `DeerFlowToolProfile`，Profile 固定：

- `action_kind`；
- `operation`；
- `risk_level`；
- `side_effect_type`；
- `resource_type`；
- 参数到资源 ID/Scope 的路径；
- 参数到 Expected Change 的映射；
- 幂等参数路径；
- Evidence 和 Policy 要求；
- 工具依赖；
- Verification 和 Idempotency Strategy。

启动时，真实 BaseTool 和 Profile 被组合成 `ToolCatalogSnapshot`。两边名称集合必须完全一致：没有 Profile 的工具 Fail Closed，多余 Profile 同样拒绝，防止配置与实际 Tool Set 漂移。

## 4. Schema 版本

LangChain BaseTool 的 `get_input_schema()` 可能包含注入式 `Runtime`，其中的 Callable 无法生成 JSON Schema，而且该字段不会暴露给模型。

Adapter 优先读取 `tool.tool_call_schema`，因为这才是模型实际看到的参数 Schema。只有非 LangChain兼容对象才回退到 `get_input_schema()` 或 `args_schema`。

Schema 使用排序后的规范 JSON 计算：

```text
sha256(canonical(input_schema + output_schema))
```

该值写入 `ActionIR.tool_schema_version`。同一 Schema 的字段顺序变化不会改变指纹，实际字段、类型或约束变化会改变指纹。

## 5. Tool Call ID 与 Action ID

`ActionIR.action_id` 直接使用原始 `tool_call_id`，因此 Tool Result、Trace、Effect 和模型消息可以无损关联。缺少 ID 时不会生成临时 ID，因为临时 ID 会掩盖 Provider 协议错误，并破坏 Tool Calling 配对。

State Event ID 和 Action ID 属于不同命名空间，不应混用。

## 6. 资源映射

Profile 使用参数路径提取资源。例如：

```text
write_file.args.path
→ ResourceRef(resource_type="file", resource_id=args.path)
```

资源 ID 只接受字符串或数字。对象、数组和布尔值会被拒绝，避免生成不可比较的幂等资源标识。

读取工具可以没有 Resource；写工具必须具有显式 Resource Type。

## 7. Expected Effect

每个写 Action 至少生成一个 `ExpectedEffect`。Effect Key 由 Task ID 和 Tool Call ID 确定性生成，但不直接拼接原始 ID，避免长度超限。

大段写入内容不会在 Effect Ledger 中复制一份。`write_file.content`、`str_replace.old_str/new_str` 使用 SHA-256 Projection；Action Arguments 保留原调用内容，后续 Trace Recorder 负责统一脱敏和大字段处理。

`write_file` 可能创建新文件，也可能覆盖或追加已有文件。转换阶段没有环境前态，因此其 Side Effect 保守标记为 `OTHER`，后续写前读取或 Verifier 可以将它细化为 CREATE 或 UPDATE。`str_replace` 明确属于 UPDATE。

## 8. 幂等键

写操作幂等键基于：

```text
task_id
tool_name
operation
resource
Profile 选定的语义参数
```

对规范 JSON 做 SHA-256。幂等键不包含 `tool_call_id` 和 `source_turn`，所以模型在后续轮次用新 Call ID 重复相同写操作时仍得到同一个键。

不同 Task 的相同写操作不会碰撞；资源、内容或其他选定参数变化会产生新键。

该键目前只用于识别候选重复写。是否允许合法重复、是否加入资源版本或时间窗口，由后续 Effect Ledger 和 Duplicate Write Guard 决定。

## 9. 证据与依赖

Catalog 中的 `required_evidence` 表示要求类型，例如 `current_file_version`，不是某条 Evidence ID。只有运行上下文已经解析出的真实 Evidence ID 才写入 `ActionIR.required_evidence_ids`。

Adapter 不根据调用顺序推断依赖。批量转换时，Coordinator 或 Dependency Scheduler 可以通过 `dependencies_by_call_id` 显式指定：

```text
write-1 depends on read-1
```

这避免把同一响应中的多个独立只读调用错误串行化，也避免把“排列在前面”误认为“已经执行成功”。

## 10. 当前内置 Profile

已审核 DeerFlow 核心 sandbox 工具：

| 工具 | 分类 | 风险 | 资源/副作用 |
| --- | --- | --- | --- |
| `bash` | WRITE | CRITICAL | sandbox / OTHER |
| `ls` | READ | LOW | directory |
| `glob` | READ | LOW | directory |
| `grep` | READ | LOW | directory |
| `read_file` | READ | LOW | file |
| `write_file` | WRITE | HIGH | file / OTHER |
| `str_replace` | WRITE | HIGH | file / UPDATE |

`bash` 被按执行能力保守分类为写工具，因为任意 Shell 命令可能改变环境。Adapter 不通过解析 Shell 文本猜测某条命令是否只读。

MCP、AppWorld、工具搜索、子 Agent 和其他 DeerFlow 工具必须增加独立审核 Profile 后才能进入 Catalog，不能自动继承默认语义。

## 11. 当前边界

本阶段没有实现：

- 按 JSON Schema 验证 Tool Arguments；
- Policy Gate 或用户确认；
- 并行读写调度；
- 工具执行或 Tool Result 处理；
- 写前 Evidence 解析；
- 实际副作用判定和写后回读；
- Middleware 自动接线。

Adapter 只保证“原始调用被无损、确定、可审计地转换成 ActionIR”。上述能力将在后续纵向链路中消费 ActionIR 实现。

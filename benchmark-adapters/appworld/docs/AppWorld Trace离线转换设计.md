# AppWorld Trace 离线转换设计

## 1. 目标与边界

本阶段的目标是把已经产生的 AppWorld Function Calling Trace 转换成 SafeDesk 公共协议：

```text
Model Tool Call -> ActionIR
Write Action    -> EffectRecord
Tool Result     -> EvidenceItem
```

转换器只读取：

- 运行时 Trace；
- AppWorld 公开的 Function Calling API Schema；
- CLI 显式提供的 `task_id`、`run_id` 和文件路径。

转换器不读取：

- AppWorld ground truth；
- 隐藏 `required_apis`；
- Evaluator 测试代码或期望数据库状态；
- 任务最终成功率和测试通过数。

因此，转换结果用于 SafeDesk Replay、Shadow Mode 和故障诊断，不能替代 AppWorld 官方评分。

## 2. 包边界

实现位于独立包 `agentgate-appworld`：

```text
benchmark-adapters/appworld/
  src/agentgate_appworld/
    catalog_adapter.py
    trace_converter.py
    cli.py
```

`agentgate-core` 继续保持与 AppWorld 无关。Adapter 只负责把 Benchmark 特有数据转换成 core 中已经冻结的公共 Contract。

## 3. Tool Catalog

AppWorld 的 457 个公开 Function Schema 只包含工具名、描述和参数 JSON Schema，没有提供：

- read/write；
- 副作用类型；
- 资源类型；
- 风险等级；
- 幂等策略；
- 写后验证策略。

Catalog v1 使用显式、可审计的规则补齐这些字段。

### 3.1 读写分类

只有以下操作前缀被认定为只读：

```text
show
search
get
```

其他操作全部按写操作处理。这样做是 fail-closed：新的或不认识的操作不会因为分类遗漏而绕过 Effect Ledger。

### 3.2 副作用分类

公开操作族映射到：

```text
CREATE
UPDATE
DELETE
SEND
SUBMIT
SESSION
OTHER
```

未映射操作使用 `OTHER + CRITICAL`。当前 457 个公开工具已经全部映射，没有 `OTHER` 遗留。

### 3.3 依赖与验证声明

参数包含 `access_token` 的工具声明对同 App `login` 的依赖，并要求认证会话 Evidence。所有写工具声明：

```text
idempotency_strategy = canonical_task_operation_resource_arguments
verification_strategy = appworld.post_action_readback
```

这里的 `verification_strategy` 是后续 Verifier 的策略标识，不表示当前离线转换已经完成写后回读。

## 4. Action 转换

每个模型提出的 Tool Call 都生成一个 Action，包括：

- 正常执行的调用；
- Runtime 明确未执行的调用；
- 没有对应 Tool Result 的调用；
- 不在公开 Catalog 中的调用。

Action ID 根据任务、Trace 位置和 Tool Call ID 生成稳定哈希。写操作的幂等键由以下内容生成：

```text
task_id
operation
resource
原始 canonical arguments
```

凭据参与哈希但不会写入输出，因此相同真实请求仍能得到相同幂等键。

如果工具不在公开 Catalog 中，转换器生成保守的合成 Catalog Entry：

```text
kind = WRITE
effect = OTHER
risk = CRITICAL
```

同时输出 `UNKNOWN_TOOL` 诊断，不把未知工具降级成只读。

## 5. Effect 状态口径

只有写 Action 生成 Effect。状态映射固定为：

| Trace 事实 | Effect 状态 |
| --- | --- |
| 没有 Tool Result | `PLANNED` |
| Runtime 明确未执行 | `PLANNED` |
| 已执行且返回显式错误 | `FAILED` |
| 已执行且正常返回 | `APPLIED_UNVERIFIED` |

离线转换器永远不生成 `VERIFIED`。工具返回“成功”只说明调用正常返回，不能证明数据库真实状态与目标一致。

## 6. Evidence 状态口径

每条可用 Tool Result 生成一条 Evidence：

- 已执行调用：`source_type = tool_result`；
- 未执行调用的结构化结果：`source_type = runtime`；
- 状态统一为 `OBSERVED`；
- 不生成 Verification ID。

没有 Tool Result 时不伪造 Evidence，而是输出 `MISSING_TOOL_RESULT`。

## 7. Trace 完整性诊断

当前支持的主要诊断包括：

```text
MISSING_TOOL_RESULT
ORPHAN_TOOL_RESULT
DUPLICATE_TOOL_CALL_ID
MISSING_TOOL_NAME
INVALID_ARGUMENTS_JSON
ARGUMENTS_NOT_OBJECT
UNKNOWN_TOOL
CALL_RESULT_MISMATCH
MISSING_EXECUTED_FLAG
LEGACY_EXECUTION_FLAGS_INFERRED
```

旧版 runner 的 Trace 不记录 `executed`。只有当整条 Trace 的所有 Tool Result 都缺少该字段时，转换器才启用旧格式兼容：已记录的 Tool Result 推断为已执行，并产生一条 `LEGACY_EXECUTION_FLAGS_INFERRED`。没有 Tool Result 的调用仍保持缺失，不能推断执行成功。

## 8. 脱敏

Action 参数、Expected Change、Actual Change 和 Evidence Value 在持久化前递归脱敏。当前覆盖：

```text
password / passwords
access_token / token
api_key / authorization / secret
card_number / credit_card_number / cvv
```

转换摘要记录脱敏值数量。原始凭据不会出现在 AgentGate 离线产物中。

## 9. 批处理和断点续转

CLI 支持单文件和递归目录。每个任务独立原子写入，单个坏文件不会破坏其他任务。批处理结束后生成：

```text
<task_id>.agentgate.json
catalog_snapshot.json
conversion_summary.json
```

只有以下三项全部匹配时才跳过已有任务：

```text
source_sha256
catalog_version
converter_version
```

因此中断后可以直接重跑；源 Trace、Catalog 规则或转换器版本变化时会自动重新转换。`--overwrite` 可强制重建当前产物。

## 10. 已完成验证

- 457 个公开 API 全部进入 Catalog；
- 合成测试覆盖成功、失败、未执行、缺结果、未知工具、旧格式和断点续转；
- smoke_4 真实 Trace 满足 Tool Call、Action、Tool Result、Evidence 数量守恒；
- smoke_3 成功识别 27 个模型调用、20 个旧格式已执行结果和 7 个静默缺结果；
- 5 个 challenge smoke Trace 批量转换无失败；
- 所有真实产物中 `VERIFIED` 数量为 0；
- JWT 扫描为 0 命中；
- wheel 和 sdist 构建成功。

## 11. 当前不做的事情

本阶段不实现：

- JSON Schema 参数 Enforcement；
- Effect Ledger 在线预检和重复写拦截；
- AppWorld 数据库写后回读；
- Completion Gate；
- 基于 Evaluator 的任务正确性判断。

这些能力将在后续模块中消费本阶段生成的 Action、Effect 和 Evidence，而不是在离线转换器中混合实现。

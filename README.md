# SafeDesk

> 基于 DeerFlow 的可靠、安全、可验证 Agent Harness

SafeDesk 是一个**策略驱动的 Agent 执行框架**，在 DeerFlow 现有的 Agent Runtime 基础上构建了四层可靠的执行防护。它不重新实现 Agent Framework，而是通过**确定性规则**替代模型自我判断，在 Tool-Use 循环中插入结构化状态管理、工具执行防护、类型化恢复和上下文压缩，从而系统性地提升 Agent 的任务完成率、执行可靠性和 Token 效率。

**核心思想**：模型可以提议、选择、生成参数，但**不能直接决定**工具调用是否合法、是否有权限、是否已经执行过、环境是否真实变化。这些判断由结构化状态和确定性模块完成。

---

## 目录

- [Benchmark 结果](#benchmark-结果)
- [架构总览](#架构总览)
- [四大核心模块](#四大核心模块)

---

## Benchmark 结果

### AppWorld test_challenge（417 任务）

| 指标 | 基线（原生 FC） | SafeDesk | Δ |
|------|:-------------:|:--------:|:--:|
| **TGC**（任务通过率） | 41.73% | **61.39%** | **+19.66pp** |
| **SGC**（场景通过率） | 29.50% | **46.77%** | **+17.27pp** |
| 假完成率 | 46.79% | **16.61%** | ↓64.5% 相对 |
| Max-Turn Rate | 21.82% | **10.07%** | ↓53.8% 相对 |
| Invalid Call Rate | 3.71% | **0.80%** | ↓78.4% 相对 |
| 重复写操作率 | 6.97% | **1.33%** | ↓80.9% 相对 |
| 平均 Token/任务 | 484,350 | **287,770** | ↓40.6% |

### τ²-Bench（278 任务，三域宏平均）

| 指标 | 基线 | SafeDesk | Δ |
|------|:----:|:--------:|:--:|
| **pass@1**（单次运行） | 80.98% | **86.95%** | **+5.97pp** |
| **pass@4**（4次全通） | 50.95% | **63.22%** | **+12.27pp** |

### 消融实验（AppWorld TGC）

| 实验 | TGC | 组成 |
|:----:|:---:|------|
| A0 Baseline | 41.73% | 无 SafeDesk |
| A1 +StateVerification | 47.00% | 证据驱动的完成判定 |
| A2 +ToolGuard | 52.52% | + 工具执行防护 |
| A3 +Recovery | 57.07% | + 类型化恢复 |
| **A4 Full SafeDesk** | **61.39%** | **+ 上下文管理** |

---

## 架构总览

SafeDesk 基于 LangGraph 的 StateGraph 构建，在标准的 Tool-Use 循环中插入了 **AgentGate 确定性防护管线**：

```
Model → [Context Builder] → Model → Tool Call
                                      ↓
                              ┌───────────────┐
                              │  AgentGate     │
                              │  Guard Pipeline│
                              │                │
                              │ 1. ActionIR    │
                              │ 2. Schema Guard│
                              │ 3. Dependency  │
                              │ 4. Policy Gate │
                              │ 5. Effect Prefl│
                              │ 6. Completion  │
                              └───────┬───────┘
                                      ↓
                                 ALLOW / DENY
                                      ↓
                              Tool Execution
                                      ↓
                              ┌───────────────┐
                              │  Post-action   │
                              │   Verification │
                              │  Evidence Board│
                              │  Task Reducer  │
                              │  Failure Class │
                              │  Stagnation Det│
                              └───────────────┘
                                      ↓
                                    Model ←── [Recovery Plan]
```

### 软件分层

```
┌─────────────────────────────────────────────────────┐
│              benchmark-adapters/                      │
│  AppWorld Catalog Adapter · Verifier Adapter          │
├─────────────────────────────────────────────────────┤
│              agentgate-deerflow/                      │
│  DeerFlow Middleware · ToolAdapter · StateAdapter     │
├─────────────────────────────────────────────────────┤
│                 agentgate-core/                       │
│  ┌──────────┬────────────┬────────────┬──────────┐  │
│  │ State &  │ Tool Exec  │ Recovery   │ Context  │  │
│  │Verificat.│ Guard      │ Controller │ Manager  │  │
│  ├──────────┴────────────┴────────────┴──────────┤  │
│  │  Contracts · State Store · Coordinator · Trace │  │
│  └───────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│                 DeerFlow Harness                      │
│       (LangGraph · LangChain · MCP · Sandbox)        │
└─────────────────────────────────────────────────────┘
```

---

## 四大核心模块

### 1. State & Verification（状态与验证）

**解决**：目标漂移、子任务遗漏、假完成、结果不一致。

**核心组件**：
- **Task Reducer**：事件驱动的状态归约器，10 种子目标状态，严格定义 20+ 条合法转换规则
- **Evidence Board**：证据板，6 种证据状态，自动检测冲突，只有环境验证可创建 VERIFIED 证据
- **Post-action Verifier**：写后回读验证器，对比 Expected State vs Observed State
- **Completion Gate**：完成门控，检查所有必要子目标、证据、Effect 和约束条件
- **Response Grounding Gate**：回复接地，正则提取成功声明并与证据对比，虚假声明自动降级

**验收指标**：假完成率下降 64.5%，Completion Gate 拒绝原因 100% 可解释。

### 2. Tool Execution Guard（工具执行防护）

**解决**：参数错误、重复副作用、错误并行、安全违规。

**核心组件**：
- **ActionIR Normalizer**：统一动作中间表示，写操作强制要求幂等键和预期副作用
- **Schema Guard**：递归 JSON Schema 校验，支持 allOf/anyOf/oneOf 组合
- **Dependency Scheduler**：Action DAG 调度，独立只读并行，写操作串行
- **Policy Gate**：确定性规则引擎，支持工具白名单、参数约束、身份验证、审批预算
- **Effect Ledger**：副作用分类账，8 种 Effect 状态，SHA256 幂等键，写前预检防止重复
- **Dynamic Tool Resolver**：越界调用时从 Catalog 检索兼容工具

**验收指标**：Invalid Call Rate 下降 78.4%，重复写操作率下降 80.9%，Schema 外实际执行为 0。

### 3. Recovery Controller（恢复控制器）

**解决**：盲目重试、相同错误循环、无进展、跑满轮数。

**核心组件**：
- **Failure Classifier**：规则优先分类器，20 种失败类型，从 guard_reason_code 和 tool_error_code 匹配
- **Recovery Strategy Registry**：类型绑定恢复策略，支持参数修复、资源重定位、验证修复
- **Stagnation Detector**：滑动窗口停滞检测，Tool Call 指纹去重，Token 增长追踪
- **Recovery Budget Manager**：每类型和全局预算双重控制，预算耗尽时如实报告

**验收指标**：Max-Turn Rate 下降 53.8%，不存在无类型 Recovery，不存在无限 Recovery。

### 4. Context Manager（上下文管理器）

**解决**：Token 膨胀、关键信息丢失、长轨迹退化。

**核心组件**：
- **Context Builder**：从 State Store 构造只读 ContextPack，绑定 state_version
- **Token Budget Allocator**：P0-P4 优先级分配，软阈值内保持、硬阈值超限报错
- **Tool Result Projector**：结构化结果投影，保留 ID、必要字段、Trace Reference
- **Structured History Summarizer**：确定性摘要，不生成自然语言，只提取结构化事实
- **Context Invariant Validator**：压缩后检查硬约束、Verified Evidence、未解决 Failure 是否保留

**验收指标**：平均 Token 下降 40.6%，TaskContract 丢失率为 0，硬约束丢失率为 0。

---




### 数据说明

实验数据位于 `paper/data/` 目录下，包含：

| 文件 | 说明 |
|------|------|
| `appworld_primary_metrics.csv` | AppWorld 基线 + SafeDesk 全量指标 |
| `tau2_baseline_metrics.csv` | τ² 基线 + SafeDesk 分域指标 |
| `ablation_matrix.csv` | 9 条件消融实验 |
| `claims_registry.csv` | 声明注册表 |
| `appworld_task_level_derived.csv` | 417 任务级数据 |
| `tau2_task_level_derived.csv` | 278 任务级数据 |

---

## 许可证

MIT

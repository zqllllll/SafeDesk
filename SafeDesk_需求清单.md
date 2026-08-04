# SafeDesk 需求清单

> 基于 DeerFlow 的可靠、安全、可验证 Agent Harness  
> 当前阶段：需求冻结与开发前准备

---

# 1. 项目定位

SafeDesk 不重新实现 DeerFlow，而是在 DeerFlow 现有 Agent Runtime 上补齐以下能力：

1. 上下文与记忆管理；
2. 长程任务状态维护；
3. 基于证据的完成判定；
4. 执行结果验证；
5. 结构化失败恢复；
6. 动态工具路由；
7. 任务级安全控制；
8. 统一评测与可观测性。

一句话描述：

> **DeerFlow 负责让 Agent 能够调用工具执行任务；SafeDesk 负责让 Agent 更可靠地完成任务，并确保其只能在当前任务授权范围内行动。**

---

# 2. 基础框架需求

- 基于 DeerFlow 开发，不重新实现 Agent Framework。
- 复用 DeerFlow 已有的：
  - 模型调用；
  - Tool Calling；
  - MCP；
  - Sandbox；
  - Checkpoint；
  - Skills；
  - Subagent；
  - Memory；
  - WebUI；
  - Streaming；
  - Guardrail。
- SafeDesk 功能必须支持通过配置启用或关闭。
- 关闭 SafeDesk 后，应保持原版 DeerFlow 行为。
- SafeDesk Core 与 DeerFlow Integration 应尽量解耦。
- Benchmark Adapter 不应包含 SafeDesk 业务逻辑。

---

# 3. 任务状态管理

需要将用户目标转换为可维护的结构化任务状态。

## 3.1 核心能力

- 将用户目标拆解为子任务。
- 记录子任务之间的依赖关系。
- 记录任务约束条件。
- 记录每个子任务的完成条件。
- 维护当前执行阶段。
- 区分：
  - 待执行；
  - 执行中；
  - 已完成；
  - 阻塞；
  - 失败。
- 记录当前缺失信息。
- 记录当前阻塞原因。
- 防止长程执行中的目标漂移、任务遗漏和错误终止。

## 3.2 核心对象

```text
Task State
Task Graph
Subgoal
Constraint
Completion Condition
Blocker
```

---

# 4. 证据管理

Agent 不应仅依赖对话历史判断任务进度，而应维护结构化证据。

## 4.1 核心能力

- 将 Tool Result 转化为结构化证据。
- 记录证据的：
  - 值；
  - 来源；
  - 获取时间；
  - 可信度；
  - 有效性；
  - 验证状态。
- 区分：
  - 用户明确提供的信息；
  - 工具返回事实；
  - 环境验证结果；
  - 模型推断。
- 支持证据更新。
- 支持证据失效。
- 支持证据冲突检测。
- 执行写操作前检查必要证据是否完整。
- 避免重复搜索已经获得的信息。
- 支持证据追溯到具体 Tool Call 或环境状态。

## 4.2 核心对象

```text
Evidence Board
Evidence Item
Provenance
Verification Status
```

---

# 5. 上下文管理

需要建立统一的 Context Engine，决定每轮模型真正看到什么。

## 5.1 上下文分层

建议划分为：

```text
Stable Context
Project / Domain Context
Task Working Memory
Retrieved Memory
Conversation Summary
Recent Messages
Current-turn Ephemeral Context
Visible Tools
```

## 5.2 核心能力

- 动态注入当前目标。
- 动态注入当前子任务。
- 动态注入已获得证据。
- 动态注入缺失信息。
- 动态注入最近失败。
- 动态注入待审批操作。
- 动态注入当前允许工具。
- 为不同上下文分配 Token 预算。
- 限制无关历史进入模型上下文。
- 限制大型 Tool Result 直接进入模型上下文。
- 保持稳定前缀，尽量降低 Prompt Cache 失效。
- 支持按需检索，而不是将全部历史常驻 Prompt。

## 5.3 核心对象

```text
Context Engine
ContextPack
Context Budget
Context Block
```

---

# 6. 上下文压缩

上下文过长时，需要安全压缩，但不能丢失关键执行状态。

## 6.1 确定性清理

- 删除重复 Tool Schema。
- 大型 Tool Result 转换为摘要或引用。
- 文件内容只保留路径、摘要和 Hash。
- 删除重复错误栈。
- 删除无价值的中间输出。
- 保留不可逆动作记录。
- 保留失败和恢复记录。

## 6.2 结构化摘要

摘要至少包含：

```text
Goal
Constraints
Completed Subgoals
Current Subgoal
Verified Evidence
Unverified Claims
Resources Changed
Failures and Recoveries
Pending Approvals
Next Actions
Critical References
```

## 6.3 压缩保护

压缩前必须确保以下内容已经写入结构化存储：

- Task State；
- Evidence Board；
- Effect Ledger；
- Approval State；
- Failure History；
- Resource Reference。

压缩后不能丢失：

- 当前目标；
- 未完成子任务；
- 关键证据；
- 已修改资源；
- 失败和恢复记录；
- 待审批动作。

---

# 7. 记忆系统

参考 Hermes Agent 的分层思路，将常驻记忆、完整历史和程序性经验分开。

## 7.1 当前任务工作记忆

保存：

- Task State；
- Evidence Board；
- Task Policy；
- 当前资源状态；
- 待审批操作；
- 最近失败；
- 恢复历史。

特点：

- 当前 Case 独占；
- 每轮更新；
- 高精度；
- 不允许被普通摘要替代。

## 7.2 会话记忆

保存完整：

- 用户消息；
- Agent 消息；
- Tool Call；
- Tool Result；
- Subagent Result；
- Approval；
- Verification；
- Recovery。

支持：

- 回放；
- 调试；
- 检索；
- 实验分析。

## 7.3 Episodic Memory

保存过去任务经历：

```text
任务类型
执行结果
重要事件
失败类型
成功恢复方式
关键资源
```

支持检索相似历史任务和恢复方式。

## 7.4 Semantic Memory

保存长期有效事实：

- 用户稳定偏好；
- 项目规范；
- 环境约束；
- 已确认的工具特性。

需要记录：

- 来源；
- 置信度；
- 有效期；
- 冲突状态；
- 敏感级别。

不保存：

- Secret；
- 一次性验证码；
- 临时路径；
- 未经确认的模型推断。

## 7.5 Procedural Memory

保存：

- 成功工作流；
- 恢复策略；
- 可复用操作模式；
- Skill Candidate。

第一版不自动发布未经验证的 Skill。

---

# 8. 动态工具路由

需要建立统一 Tool Catalog 和阶段式 Tool Router。

## 8.1 Tool Catalog

每个工具至少记录：

- 工具名称；
- 功能描述；
- 输入 Schema；
- 输出 Schema；
- 风险等级；
- 副作用；
- 所需证据；
- 可操作资源；
- 是否需要审批。

## 8.2 执行阶段

第一版可使用：

```text
COLLECT
ACT
VERIFY
REPAIR
```

## 8.3 路由原则

最终暴露工具：

```text
当前阶段需要
∩
已有证据支持
∩
任务策略允许
∩
安全策略允许
```

## 8.4 核心能力

- 不暴露当前任务无关工具。
- 必要证据不完整时，不暴露对应写操作工具。
- 支持工具数量扩展。
- 支持工具语义检索。
- 支持 Router 决策记录和解释。
- 支持与 DeerFlow 原有 Tool Search 做消融对比。

---

# 9. 环境验证

Agent 不能只相信模型自我判断，也不能只相信工具返回的 `success`。

## 9.1 核心能力

- 执行动作后重新读取环境。
- 验证目标资源是否真实存在。
- 验证关键字段是否正确。
- 验证任务是否只完成了一部分。
- 验证是否修改了无关对象。
- 验证是否产生额外副作用。
- 验证是否出现重复操作。
- 支持动作级、目标级和约束级检查。

## 9.2 核心模块

```text
Action Verifier
Goal Verifier
Invariant Verifier
Trace Verifier
```

---

# 10. 完成判定

只有满足以下条件，Agent 才能结束任务：

- 必要子任务全部完成；
- 必要证据全部存在；
- 真实环境验证通过；
- 没有未解决错误；
- 没有待审批操作；
- 没有安全违规；
- 没有额外副作用；
- 没有重复不可逆动作。

核心模块：

```text
Completion Gate
```

---

# 11. 失败诊断与恢复

需要建立明确的 Failure Taxonomy。

## 11.1 基础失败类型

```text
MISSING_EVIDENCE
INVALID_ARGUMENT
WRONG_TOOL
WRONG_RESOURCE
TOOL_EXECUTION_ERROR
PARTIAL_COMPLETION
VERIFICATION_FAILED
DUPLICATE_ACTION
NO_PROGRESS
DEPENDENCY_NOT_SATISFIED
POLICY_DENIED
APPROVAL_REQUIRED
```

## 11.2 恢复策略

- 缺少证据：重新收集。
- 参数错误：根据 Tool Schema 修正。
- 工具错误：重新执行 Tool Router。
- 资源错误：重新定位资源。
- 部分成功：只修复缺失部分。
- 验证失败：根据失败字段生成 Repair Action。
- 重复动作：先读取环境，再决定是否执行。
- 无进展：更换检索或执行策略。
- 依赖未满足：调整步骤顺序。
- 达到恢复预算：停止并报告。

---

# 12. 幂等与断点恢复

- 为有副作用的动作生成幂等标识。
- 记录动作是否已经执行成功。
- 记录预期副作用和实际副作用。
- Checkpoint 恢复后先读取环境。
- 防止重复发送邮件。
- 防止重复创建资源。
- 防止重复支付或重复提交。
- 支持进程中断后的安全恢复。

---

# 13. 任务级安全策略

根据当前任务生成最小权限策略。

## 13.1 核心能力

- 定义允许工具。
- 定义禁止工具。
- 定义可操作资源。
- 定义参数范围。
- 定义调用次数和预算。
- 定义需要审批的动作。
- 定义允许产生的副作用。
- 定义禁止产生的副作用。
- Task Policy 必须经过确定性校验，不能完全信任模型生成。

## 13.2 核心对象

```text
Task Policy
Resource Scope
Argument Constraint
Approval Rule
Action Budget
```

---

# 14. 统一动作模型

所有动作统一转换为 Action IR。

动作来源包括：

- LangChain Tool；
- MCP；
- Shell；
- AppWorld；
- τ 系列 Benchmark；
- Subagent。

Action IR 至少描述：

```text
Actor
Operation
Resource
Arguments
Expected Effects
Evidence
Idempotency Key
```

---

# 15. 执行前安全检查

所有动作执行前经过 AgentGate。

## 15.1 检查内容

- 工具是否允许；
- 当前 Agent 是否有权限；
- 资源是否在范围内；
- 参数是否合法；
- 文件路径是否越界；
- Shell 命令是否危险；
- 网络目标是否允许；
- 是否涉及敏感数据；
- 是否需要审批；
- 是否已经执行过；
- 是否超过预算。

## 15.2 决策结果

```text
ALLOW
DENY
REQUIRE_APPROVAL
TRANSFORM
```

---

# 16. 文件系统安全

- 路径规范化。
- 防止 `../` 路径穿越。
- 防止 Symlink Escape。
- 限制可读目录。
- 限制可写目录。
- 禁止读取敏感文件。
- 禁止写入 Sandbox 外。
- 限制文件大小和数量。

---

# 17. Shell 安全

- Shell 只允许在 Sandbox 中执行。
- 默认不向 Sandbox 注入敏感凭证。
- 支持命令白名单。
- 支持风险分类。
- 拦截危险命令。
- 基础识别 Pipeline、Redirect 和 Subshell。
- 限制超时、CPU、内存和磁盘。
- 未知命令默认拒绝或要求审批。

---

# 18. 网络与 Secret 安全

- 限制出站网络。
- 支持允许域名列表。
- 拒绝私有 IP。
- 拒绝 localhost。
- 拒绝云元数据地址。
- 检查敏感数据是否被发送到外部。
- Secret 由 Tool Adapter 持有。
- Secret 不直接进入模型上下文。
- Trace 和日志自动脱敏。
- 禁止模型读取完整环境变量和凭证文件。

---

# 19. 审批机制

高风险动作需要审批，例如：

- 外部发送；
- 删除；
- 分享；
- 支付；
- 高风险命令；
- 修改重要资源。

审批必须绑定：

```text
Case
Actor
Operation
Resource
Arguments
Expiration
Max Uses
```

需要防止：

- 审批后篡改参数；
- 跨 Case 复用；
- Approval Replay；
- 超次数使用；
- 过期审批继续生效。

---

# 20. Subagent 权限

- Subagent 不自动继承父 Agent 全部权限。
- 委派时必须指定：
  - 子任务；
  - 工具；
  - 资源范围；
  - 调用预算；
  - 有效期。
- 禁止 Subagent 自行扩权。
- 禁止 Subagent 创建更高权限子 Agent。
- Subagent Result 必须记录来源。
- Subagent 返回内容不能覆盖当前 Task Policy。

---

# 21. 副作用审计

需要建立 Effect Ledger。

## 21.1 记录内容

- 谁执行；
- 执行什么；
- 操作哪个资源；
- 使用什么授权；
- 预期副作用；
- 实际副作用；
- 是否经过审批；
- 是否验证通过；
- 是否可回滚；
- 是否重复执行。

## 21.2 用途

- 调试；
- 审计；
- Benchmark；
- 恢复；
- 重复操作检测；
- 安全分析。

---

# 22. Benchmark 与测试

## 22.1 任务性能

使用：

```text
AppWorld
τ 系列 Benchmark
```

测试：

- 长程任务成功率；
- 多轮交互；
- 工具选择；
- 状态维护；
- 业务规则遵循；
- 执行一致性；
- 副作用控制。

## 22.2 HarnessBench-Reliability

覆盖：

- 状态丢失；
- 早期证据被压缩；
- 工具假成功；
- 部分成功；
- 参数错误；
- 错误工具；
- 工具超时；
- 无进展循环；
- 中断恢复；
- 重复副作用；
- 记忆冲突；
- 记忆过期。

## 22.3 SafeDesk-SafetySuite

覆盖：

- 未授权工具；
- 参数越界；
- 路径穿越；
- Symlink Escape；
- Secret 读取；
- Secret 外传；
- Sandbox 外写入；
- 危险 Shell；
- 网络越界；
- 审批绕过；
- Subagent 扩权；
- 重复不可逆操作；
- 额外副作用。

## 22.4 真实工具 Pilot

后期增加：

- 本地文件系统；
- Docker / Shell；
- GitHub 测试仓库；
- 测试邮箱；
- 测试日历。

---

# 23. 可观测性

每个任务需要记录：

- Task State 变化；
- Evidence 更新；
- ContextPack；
- 当前可见工具；
- Tool Call；
- Policy Decision；
- Approval；
- Tool Result；
- Effect；
- Verification；
- Recovery；
- Token；
- 延迟；
- 最终结果。

支持：

```text
Trace
Replay
Metrics
Experiment Comparison
```

---

# 24. 当前第一步

当前只完成 DeerFlow Baseline，不开发 SafeDesk 功能。

需要完成：

```text
配置并跑通 DeerFlow
→ 固定 commit 和依赖
→ 跑官方基础测试
→ 跑模型调用 Smoke Test
→ 跑只读 Tool Call Smoke Test
→ 跑 Thread / Checkpoint Smoke Test
→ 记录 Baseline
```

第一阶段完成标准：

- DeerFlow 可稳定启动；
- 模型调用正常；
- Tool Calling 正常；
- Thread / Checkpoint 正常；
- 官方测试结果明确；
- 环境配置可复现；
- Baseline 结果已经记录；
- 未修改 SafeDesk 业务逻辑。

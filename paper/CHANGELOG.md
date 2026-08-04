# SafeDesk 论文结构级重写记录

## 严格复现性与测试隔离修订（2026-07-26）

- **论文定位**：副标题改为“System Design and Evaluation Protocol”。当前稿可作为诚实的系统设计与评测协议预印本；不再把未完成的 SafeDesk 干预实验包装为完整方法结果。
- **测试集隔离**：明确承认 AppWorld `test_challenge` 的任务级轨迹和诊断已经参与开发，因此历史 417 任务结果仅为 exploratory aggregate，不能作为未来 SafeDesk 的 confirmatory 对照，也不再发表任务级错误、工具、Token 或 completion 分析。
- **确认性评测**：要求使用未接触、访问受控的 held-out split；在访问前归档源码修订、配置、Prompt/renderer hash、阈值、预算向量、任务顺序与统计脚本。Plain、resource control 与 SafeDesk-core 必须在同一 Provider 时间窗内按任务交错运行。
- **模型可复现性**：将“versioned API name”更正为“provider API model identifier”。正式运行保存请求与响应 model、可用的 system fingerprint、endpoint、schema hash 和运行时上下文渲染 hash，不再把历史 model alias 当成不可变模型快照。
- **资源控制**：删除仅“同上限”的 budget-matched 表述。确认性协议固定总 LLM 输入/输出 Token、LLM 调用、工具调用与环境 read；Plain resource-control 还获得与 SafeDesk 等量的 self-check 调用，但不获得结构化状态、Evidence、Ledger、Guard 或 Completion Gate。
- **Task Contract**：将合同正式定义统一为 `T=(G,D,Γ,P,V)`，把 `U` 定义为 `H_t` 中的 unresolved 执行状态。新增必须在开发集测量的 Goal Recall、Constraint Recall、Spurious Goal Rate、Verifier Coverage、Contract Repair Rate 和 Contract Failure Rate，未观测时不声称其质量。
- **Effect Ledger 与完成逻辑**：引入 `L_intended`、`L_observed` 和 `L_unexpected`。Completion Gate 要求全部意图副作用已验证且不存在未解决的非预期副作用，修复“错误删除被验证后误当完成”的逻辑漏洞。
- **实现语义**：`supervisor__complete_task` 现在按高风险控制面写动作接受 Schema、策略与依赖调度治理，但由 Orchestrator 排除在业务 Effect Ledger 与环境副作用回读之外，Completion Gate 才能放行完成。
- **实现证据**：新增本地验证表。`python -m pytest` 结果为 116 passed、1 skipped；跳过的 DeerFlow 真实兼容性测试明确标注为未验证。AppWorld 公共目录 457 个工具的转换由真实 fixture 单测覆盖，不能据此声称端到端 benchmark 成功。
- **指标与审计**：将 false-block 过程写为离线的“决策前快照 + Task Contract + 标准评测器”重放；快照不可恢复、无评测覆盖或仍缺政策确认的情况单独报告，不能静默并入 true/false block。
- **相关工作与图表**：删除正文中带有主观 Yes/No 判定的 Related Work 机制比较表；Trace 示例新增每步对应的顶层 Runtime 模块；系统图增大标签字号以保证可读性。
- **历史 Runner 修复**：历史 GBK/持久化修复记录保留为工程审计，但由于原始版本、环境锁和执行日志未一并归档，不再作为可复现实验修复率或模型性能证据。

## 第二轮审稿意见修订（2026-07-23）

- **论文定位**：将正文定位为“系统设计与诊断研究”。删除主结果中的未观测 SafeDesk 数字和 `Pending` 图形标记；正式匹配实验表移入预注册附录，当前稿不再暗示干预效果已经产生。
- **Contract Builder**：新增 `propose -> validate -> reduce` 协议、版本化 JSON Schema、稳定子目标 ID、依赖图、证据配方、`unresolved` 状态、一次受限修复调用及其 Token/延迟计费边界。明确模型只能提出 Contract，不能直接更新权威状态或判定完成。
- **Completion Gate**：公式加入可观测世界投影 `W_hat`，并定义 required goals/effects、证据新鲜度与冲突、不可回读写操作、unresolved blocker、false acceptance 和 false block。严格区分模型提出完成、Runtime 放行完成和 Evaluator 确认成功。
- **工具公平性**：主实验定义为 Baseline 与 SafeDesk-core 使用完全相同的冻结 API Predictor 输出和最多 20 个 API；Dynamic Tool Resolver 关闭并作为独立增强条件报告，避免把工具集合扩大误计为治理收益。
- **预算公平性**：定义 agent turn、总 LLM 调用、模型提出调用、Runtime 验证调用、实际执行调用、全角色 Token 和时延。新增 budget-matched plain baseline，Runtime 的 Builder、分类、恢复、Resolver 和摘要调用全部计入总预算。
- **Defer 协议**：放弃复用旧 `tool_call_id`。被延迟动作立即以 `deferred/not_executed` 关闭；模型看到刷新状态后重新提议，并生成新 ID。参数重绑定仅允许显式 Action-IR 占位符、已声明依赖和唯一真实输出。
- **恢复与重复定义**：补充可执行的重复调用/重复写判定规则，以及 typed recovery 的 attempt-level success 与 task-level success 分界，不再把局部恢复等同于最终任务成功。
- **统计口径**：以 139 个 Scenario 为聚类单位生成 10,000 次 bootstrap 基线区间；Difficulty 区间同步改为 Scenario-clustered。McNemar 降为辅助任务级描述，匹配主实验仍需聚类配对推断。
- **Runner 修复审计**：记录评测前持久化、四分片离线重评、11 个 Windows GBK 基础设施失败的 UTF-8 重跑、最终 0 个基础设施错误和两份 SHA-256。明确这些修复不允许选择性重跑模型失败。
- **模型可复现性**：记录 AppWorld 与 tau2 诊断使用的精确模型别名、端点、访问日期、non-thinking 和 temperature；如实标注历史响应未保存不可变 Provider revision，禁止编造版本号。
- **图表与证据链**：重画四模块架构图，新增 Related Work 机制比较表和端到端 Trace 示例表；Baseline Difficulty、Reliability 与 Token 图仅展示已测数据。
- **成本表述**：正文删除无法由冻结日期价格表复核的货币成本，仅保留 Token、调用和时延；正式成本待 Provider 账单或带日期价格快照生成。
- **投稿元数据**：新增作者贡献和 Artifact License 宏。真实作者、单位、邮箱、贡献、代码仓库和许可证仍保持空值，避免伪造。

## 数据与声明口径

- 新建 `results_macros.tex`，集中管理任务数、成功率、调用率、Token、耗时、成本、配对统计、τ²-bench 和消融字段。
- 增加 `SafeResultsAvailable` 证据开关。匹配的 SafeDesk 主实验尚未完成时，摘要、正文、表格和结论统一显示 `Pending`。
- 将已有的 SafeDesk 目标数字标记为 `synthetic projection`，仅保留为开发目标，禁止进入论文结论。
- 修正完成口径：模型提出完成、Completion Gate 放行和 Evaluator 判定成功分层统计。
- 修正 Baseline 假完成定义：无 Completion Gate 的 Baseline 不报告 gate-accepted false completion；其 `153/327` 只称为模型提出完成后的评测失败率。
- 明确 `out-of-schema` 是 Invalid Call 的子集，不进行相加。
- 增加自动校验脚本，核对百分比、分母、调用分类、Token 分解和投影值泄漏。

## 标题、首页与摘要

- 标题改为 *SafeDesk: Evidence-Grounded Runtime Governance for Reliable Long-Horizon Tool-Using Agents*。
- 日期改用 `\today` 自动生成。
- 删除匿名作者和待补单位占位文案；作者信息改由 `author_metadata.tex` 集中注入，未知字段保持空白而不编造。
- 摘要重写为问题、方法、匹配评测、证据状态和结论五段式逻辑；未完成实验不再以确定性数字陈述。

## Introduction

- 以“生成正确调用”和“治理完整执行生命周期”的差距作为切入点。
- 新增创建事件与发送通知的端到端失败链，连接子目标遗漏、参数错误、重复写、无回读和假完成。
- 明确研究问题：不更新模型权重时，模型无关 Runtime 能否改善完成率、可靠性和资源效率。
- 贡献压缩为 Runtime abstraction、四模块、跨 Benchmark 实现和匹配评测四项。
- 删除代码行数、文件数和 Schema 数作为研究贡献的表述。

## Related Work

- 扩展为 reasoning and recovery、safety and guardrails、state and context management、training-based tool agents 四类。
- 新增 CRITIC、Voyager、ReWOO、LangGraph persistence、MemGPT、InfiAgent、StructAgent 和 ToolLLM。
- 正面说明 SafeDesk 与 StructAgent 在显式状态、验证状态转移、证据完成和定向恢复上的重叠。
- 将差异限定在 function calling、Effect Ledger、Schema/工具边界、依赖调度、幂等写、τ² 政策协作和联合评测维度。
- 删除“首次提出状态驱动 Agent”等不可证实的优先权声明。

## Problem Formulation

- 统一世界状态、任务状态、证据、副作用、失败状态、上下文、模型动作、工具观察和环境变化符号。
- 将 Task Contract 定义为 `T=(G,Γ,P,V)`，消除约束与 Context Pack 共用 `C` 的冲突。
- 新增 Task Contract Induction：模型只提出结构化合同，Reducer 才能根据结果、确认和回读修改状态。
- 明确不确定目标使用 `unresolved`、用户改需求后局部重算、已验证副作用不可删除、合同修改写入 Trace。
- 重写 Completion Gate 公式并定义 admissible evidence、freshness、conflict、required effect、blocker、false acceptance 和 false block。

## System Design

- 顶层严格收敛为 State & Verification、Tool Execution Guard、Recovery Controller、Context Manager 四个模块。
- State & Verification 增加 Task State、Evidence Board、Effect Ledger、写后验证、Completion Gate 和 Response Grounding 的数据契约。
- Tool Execution Guard 改为独立读可并行，依赖调用和状态写串行；`defer` 进入后续批次并保留 `tool_call_id`，不再静默丢弃。
- Recovery Controller 建立失败分类、可恢复性、类型化恢复、进展检查、停滞检测、局部重规划和安全停止边界。
- Context Manager 定义四层上下文、不可丢失 invariant 和六类 Token 预算。

## Implementation

- 正文只保留 Runtime Hook、Action IR、State Reducer、Adapter、Trace、Storage、配置开关和主循环伪代码。
- 明确 SafeDesk 关闭时退化为原始 Baseline、调用必经 Runtime、状态必经 Reducer、评测前持久化和敏感字段脱敏。
- 代码规模信息移动到可复现性附录，并明确代码规模不构成系统正确性证据。

## Evaluation 与 Results

- AppWorld 主实验改为同一 DeepSeek V4 Flash 的 Plain Function Calling 与 SafeDesk 匹配对照；DeepSeek V4 Pro 仅作强模型参考。
- τ²-bench 主实验改为 GLM-5 与 GLM-5 + SafeDesk；GLM-5.2 仅作强模型参考。
- 冻结模型版本、non-thinking、temperature、Predictor、API 上限、轮数、Prompt、任务顺序、持久化、评测器、重试、并行设置和 Token 边界。
- 增加统一指标定义、任务级配对转移矩阵、McNemar、Scenario-clustered bootstrap、任务级配对 bootstrap 和 Holm 校正计划。
- 主结果按 AppWorld、Completion、工具可靠性、恢复与停滞、效率、τ²-bench 重组。
- Token 报告扩展到 mean、median、P95、maximum、成功/失败任务、预测器/Agent、成本和耗时。
- 未运行的配对结果、τ² 四次试验和消融全部显示 `Pending`，未用零值或合成值填充。

## Discussion、Limitations 与 Conclusion

- Discussion 覆盖 Runtime 价值、Agentic RL 互补性、Gate 保守性、动态工具扩展风险、上下文压缩风险和模块依赖。
- Limitations 明确十项有效性边界，不再写成未来工作清单。
- Conclusion 收敛为状态证据治理、四模块作用和与强模型/训练互补三层，不重复未观测数字。
- 删除“SafeDesk 普遍解决 Agent 问题”和未经匹配的“超过更强模型”表述。

## 图表与排版

- 重画四模块系统架构图，区分实线控制流和虚线 Trace 事件。
- Difficulty 图改为 Baseline/SafeDesk 并列设计并显示置信区间；未观测 SafeDesk 值显示 Pending。
- 新增工具可靠性并列图和 Token 长尾分布图。
- 表格按首次引用排序，附录标题先于附录内容，术语统一为 AppWorld `test_challenge`、τ²-bench、Pass¹/Pass⁴、percentage points、false completion、out-of-schema 和 long-horizon。
# 2026-07-27：原型验证与协议边界收紧

- **摘要、标题、贡献与结论**：将论文定位从“可直接得出基准增益的方法论文”收紧为系统设计、原型验证和预注册评测协议；删除摘要中的历史 TGC/SGC 和“release-ready”表述，改用模型接口无关而非无限制的模型无关表述。
- **AppWorld 隔离**：删除当前已有 access-controlled held-out split 的暗示。主文明确该 split 尚未构造，历史 `test_challenge` 汇总仅保留在附录，不能作为 SafeDesk 对照或确认性统计。
- **资源对照**：将 resource-control 从按 SafeDesk 实际自检次数的事后配平，改为三条预先固定的控制规则：写后 self-check、每五轮 reflection、模型提出完成后的 self-verification；明确共享 Token、调用、工具和读取预算，并要求资源--性能曲线。
- **可执行方法细节**：新增运行时规范附录，描述主循环、Contract Builder、Completion Gate、Effect Fingerprint、失败--恢复边界、Context Budget 和最小 JSON Contract 例子；新增三条可验证运行时不变量。
- **端到端原型验证**：新增 20 个确定性 mock 任务，使用 SQLite 状态和 trace 存储，实际验证非法调用拦截、类型化 recovery、写后回读、完成阻断、重复写抑制、完成放行和重启回读。该证据明确标为原型验证，不是 AppWorld 分数。
- **τ²-bench 复现元数据**：新增 $τ^2$-bench 论文和软件引用，固定本地 commit、package 版本和 base task-list hash；清楚标注 task-fix 状态未审计、Agent/User/Judge 尚未冻结，因而没有 τ² 性能主张。
- **指标与可得性**：将稠密指标表拆为完成/恢复与工具/资源两表；新增 Artifact Availability，诚实列出公共归档、依赖锁、外部 CI 和结果 manifest 尚未完成。
# 2026-07-27：Harness 对齐与原型边界修订

- **Related Work**：新增 Harness-level systems and evaluation，小心区分 Harness-Bench 的配置级评测、AI Harness Engineering 的职责框架，以及 SafeDesk 的 API Agent 状态/副作用执行约束；不再仅以 StructAgent 建立差异。
- **架构图**：重画 Figure 1 为两行控制流，正常执行路径不经 Recovery；M1 内部明确包含 Completion Gate 与 Response Grounding；Trace 事件使用统一向下虚线。按最终页面宽度渲染检查，移除了重叠标签。
- **AppWorld 与原型表述**：将 “tested AppWorld integration” 改为 “unit-tested AppWorld adapter prototype”；将 20 个 mock 表述改为一个确定性端到端回归场景的 20 次重复，并新增覆盖矩阵，明确 property-based tests 为 0、AppWorld evaluator-confirmed SafeDesk 任务为 0。
- **Effect/Completion 逻辑**：非预期副作用新增 observed、rollback_pending、rolled_back、policy_accepted、unresolved 状态；Completion Gate 仅阻断仍活跃且未解决的状态。写入预留发生幂等或持久化 CAS 冲突时不再继续 dispatch，而是刷新状态后重规划；增加跨 SQLite 实例竞争测试。
- **Schema 与测试**：重新生成版本化 verification JSON Schema；当前完整本地测试为 142 passed、1 skipped。新增 Unexpected Effect 状态的五种 Gate 行为测试和跨实例 Effect reservation 测试。
- **协议与 Artifact 边界**：将 Prespecified Evaluation Protocol 收紧为 Proposed Evaluation Protocol；Artifact Availability 明确当前尚未公开、没有已指派许可证或可核验的公共测试日志。

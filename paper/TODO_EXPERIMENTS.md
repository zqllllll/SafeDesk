# 投稿前真实实验与元数据待办

下列字段必须由冻结后的运行结果和统计脚本生成。不得以当前投影值、人工估计或零值替代。

## 第二轮修订后新增的冻结项

- [ ] **新的 held-out 构造与托管**：当前不存在可声称的 access-controlled held-out split。必须由独立人员或服务新建、托管并隐藏任务，再在访问前发布不可变配置与统计脚本；不得将历史 `test_challenge` 重命名为 held-out。
- [ ] **Provider 时间窗**：Plain、resource control 与 SafeDesk-core 按任务交错、在同一时间窗运行；保存每个请求的请求/响应 model、system fingerprint（若提供）、endpoint、时间戳和失败重试记录。
- [ ] **固定资源向量**：为每任务预先固定总输入/输出 Token、总 LLM 调用、执行 Tool Call 和 environment read 上限；resource-control 必须固定执行三类对照：每次写后一次 self-check、每五轮一次 reflection、每次模型提出完成后一次 self-verification；所有条件任一预算耗尽即停止。
- [ ] **资源--性能曲线**：在预先定义的至少三个 Token/Tool Call 预算点重复 Plain、resource-control 和 SafeDesk-core，不得按观察到的 SafeDesk 自检次数事后配平。
- [ ] **τ² 可执行清单**：冻结 commit `1901a301961cbbe3fd11f3e84a2a376530c759e3` 或明确升级；记录 base task-list hash、text half-duplex 设置、官方评测命令、任务修复状态、Agent/User/Judge 三个模型及四次 trial 的种子。
- [ ] **合同质量开发集**：人工标注或审计开发集上的必要目标、约束和 verifier recipe，输出 Goal Recall、Constraint Recall、Spurious Goal Rate、Verifier Coverage、Contract Repair Rate 与 Contract Failure Rate。
- [ ] **发布前工程证据**：生成公开源码 revision、依赖 lock、CI 链接、baseline-bypass equivalence 测试，以及 DeerFlow 安装环境中的真实兼容性测试记录。

- [ ] 冻结并发布 Contract Builder 的 system prompt、JSON Schema 版本、Adapter 词表、Verifier recipe 清单、确定性 validator 与一次修复上限。
- [ ] 为每个实验保存 Provider 响应中的真实 `model` 字段或部署 revision；历史诊断仅有模型别名和访问日期，不能据此声称不可变版本。
- [ ] 生成冻结的 API Predictor 输出清单和哈希，确保 Baseline、budget-matched baseline 与 SafeDesk-core 每任务暴露完全相同的最多 20 个 API。
- [ ] 单独运行 SafeDesk+Resolver 增强条件；不得与 SafeDesk-core 主效应合并，并报告新增 Schema Token、扩展次数和新增工具列表。
- [ ] 运行 budget-matched plain baseline，匹配总 LLM 调用、实际工具调用和 Token 上限；主结论需同时报告标准对照与预算匹配对照。
- [ ] 保存每次 Gate 决策时的世界状态快照，分别计算 false-block attempt rate、受影响任务率、每个获救任务平均拦截次数和 gate rescue rate。
- [ ] 使用运行时冻结的 Provider 账单或带日期价格表计算成本；缺少价格证据时论文只报告 Token，不填写货币金额。
- [ ] 填写 `author_metadata.tex` 中真实作者、单位、通讯邮箱、作者贡献、代码仓库和 Artifact License。

## P0：论文主张所必需

- [ ] 在未接触的 held-out AppWorld split 上完成 DeepSeek V4 Flash non-thinking 的 Plain、resource control 和 SafeDesk-core 全量匹配运行；历史 `test_challenge` 不得再用于确认性比较。
- [ ] 导出 SafeDesk TGC、SGC、Evaluator Test Pass、No Completion、Max-Turn、Invalid、out-of-schema、Duplicate Call、Duplicate Write 和 Token 分布。
- [ ] 导出 Completion Attempt 总数、提出完成的任务数、被拦截 Attempt、至少被拦截的任务、拦截后成功/失败、最终放行任务和放行后失败任务。
- [ ] 通过 Gate 决策时刻的环境快照计算 false-block count/rate。
- [ ] 计算 gate rescue rate；任务可先被拦截后再放行，禁止将集合设为互斥。
- [ ] 生成 Baseline/SafeDesk 配对任务转移矩阵 A/B/C/D。
- [ ] 运行任务级 McNemar 检验并导出 p-value。
- [ ] 运行 Scenario-clustered bootstrap，导出 TGC 变化的 95% CI。
- [ ] 以 139 个 Scenario 为样本计算 SGC 的配对差异及置信区间。
- [ ] 对 Token、Calls 和 Turns 运行 10,000 次任务级配对 bootstrap。

## P0：τ²-bench 正式结果

- [ ] 固定 GLM-5 Agent、User Simulator、Judge、任务、政策、工具、种子和四次重复协议。
- [ ] Airline、Retail、Telecom 各任务运行 4 次，导出每域 Baseline/SafeDesk Pass¹ 和 Pass⁴ 绝对值。
- [ ] 计算三域 Pass¹ 与 Pass⁴ 宏平均及 percentage-point 变化。
- [ ] 分开导出 Agent、User Simulator、Judge Token；Agent 成本结论只使用 agent-only tokens。
- [ ] 导出 policy violation、premature tool call、tool-response inconsistency 和 average agent turns。

## P1：消融与机制分析

- [ ] Leave-one-out：Full、w/o S&V、w/o TEG、w/o RC、w/o CM。
- [ ] 对消融多重比较使用 Holm 校正。
- [ ] Incremental：Baseline、+S&V、+TEG、+RC、+CM；正文保留顺序依赖声明。
- [ ] 分开统计 recovery attempt success 和 task-level success after recovery。
- [ ] 导出失败任务平均轮数、未提出完成比例、恢复后成功率和停滞类型分布。
- [ ] 导出受 Invalid、out-of-schema、重复调用和重复写影响的任务比例及调用类别。

## P1：效率、成本与稳定性

- [ ] SafeDesk Token mean、median、P95、maximum。
- [ ] SafeDesk 成功任务与失败任务的 Token mean、median、P95、maximum。
- [ ] 分开统计 agent、API Predictor 和 Runtime 验证/恢复的额外 Token。
- [ ] 使用运行时实际 Provider 账单或冻结价格表计算成本；记录币种、日期、输入/输出单价和缓存计费规则。
- [ ] 导出 duration mean、median、P95、maximum，并记录异常值排除规则。
- [ ] 固化精确 Provider 模型快照；若 Provider 只提供滚动别名，记录实验时间和响应元数据以应对版本漂移。

## P1：强模型参考与泛化

- [ ] 在完全相同的任务、工具、预算、Prompt、持久化和 Evaluator 下运行 DeepSeek V4 Pro Plain Function Calling 强参考。
- [ ] 可选运行 DeepSeek V4 Pro + SafeDesk；只有严格匹配后才能写 `matches or exceeds`。
- [ ] 强参考未匹配时只写 `narrows the gap to stronger model baselines`。

## P2：投稿元数据与发布

- [ ] 在 `author_metadata.tex` 填入真实作者、单位和通讯邮箱。
- [ ] 代码公开后填写仓库链接；未公开时保持链接宏为空。
- [ ] 真实实验完成后仅修改 `results_macros.tex`，将 `SafeResultsAvailablefalse` 改为 `SafeResultsAvailabletrue`。
- [ ] 重新运行 `python scripts/validate_results.py`、图表脚本和 LaTeX 全量编译。
- [ ] 最终确认所有 `Pending` 均已替换，或在投稿稿中明确标注仍未完成的实验范围。

## 当前不可作为论文结果的数据

`benchmarks/projections/appworld_test_challenge_safedesk_target_v2` 的 SafeDesk 数字带有 `synthetic_projection_not_observed` 标记，只能用作开发目标。τ² 当前的单次诊断汇总也不能替代每任务四次运行的正式 Pass⁴。

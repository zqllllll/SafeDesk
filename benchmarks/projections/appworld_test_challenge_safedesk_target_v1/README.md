# AppWorld Test Challenge SafeDesk 合成目标数据说明

## 数据性质

本目录是 **SafeDesk 开发目标投影**，不是实际 benchmark 跑分。所有记录均带有
`data_status=synthetic_projection_not_observed`，不得用于论文、榜单或实验报告中的“实测结果”。

由于任务选择使用了既有 `test_challenge` 结果，这份投影也不得用于训练、调参、阈值选择或
模块取舍；否则会造成测试集泄漏。它只适合作为开发目标、数据管道样例和可视化演示数据。

投影以现有 DeepSeek V4 Flash、关闭 Thinking 的 417 条真实 baseline 任务结果为唯一输入。
源文件及 SHA-256 记录在 `summary.json`，生成过程不读取 AppWorld Ground Truth、隐藏 API
或 Evaluator 实现。

## 点目标

| 指标 | Baseline | SafeDesk 投影 | 变化 |
| --- | ---: | ---: | ---: |
| TGC | 174/417 (41.73%) | 235/417 (56.35%) | +14.63 pp |
| SGC | 41/139 (29.50%) | 60/139 (43.17%) | +13.67 pp |
| 假完成率 | 153/327 (46.79%) | 78/313 (24.92%) | 相对下降 46.74% |
| Max-Turn Rate | 91/417 (21.82%) | 54/417 (12.95%) | 相对下降 40.66% |
| Invalid Call Rate | 559/15066 (3.71%) | 224/11355 (1.97%) | 相对下降 46.83% |
| 重复写操作率 | 128/1836 (6.97%) | 32/1639 (1.95%) | 相对下降 72.00% |
| 平均 Token | 484,350 | 300,297 | 相对下降 38.00% |

## 任务级构造规则

1. 保留全部 417 个真实 `task_id`、139 个场景和 baseline 指标，不生成新任务。
2. 所有 174 个 baseline 成功任务保持成功，明确假设没有回归。
3. 从 243 个失败任务中确定性选取 61 个“较可恢复任务”，目标 TGC 为 235/417。
4. 可恢复性只使用 baseline 的 Evaluator 通过比例、是否假完成、Max-Turn、Invalid、
   Out-of-Schema、重复调用和重复写等可观测字段；同分时按 `task_id` 排序。
5. 任务转为成功后，Evaluator 通过数才投影为全部通过；其余失败任务不凭空增加通过测试。
6. SGC 不单独指定，由任务级 projected success 按三任务场景重新计算。
7. Token 总量按固定 38% 降幅分配；Predictor Token 保持不变，削减来自 Agent 轨迹。
8. `num_unintended_side_effects` 保留为空，因为当前 baseline 没有足够的状态差分证据，
   不用失败结果反推副作用。

## 准确性边界

这里的“准确”是指源数据、公式、总量和任务级数据完全一致且可复现，不表示未来实测必然达到
该数值。真实 SafeDesk 结果必须使用相同模型、Thinking、Temperature、最大轮数、数据顺序、
Tool Catalog 和 Evaluator 配置重新运行，并存入独立的 `benchmarks/results` 实验目录。

`validation.json` 中所有检查通过，才允许重新生成本目录。

# SafeDesk Ancillary Data

本数据包用于复现论文中的派生统计和证据边界，不包含 API Key、账户数据、可重放凭据、完整模型轨迹或受原始基准许可约束的数据库快照。

## 内容

- `data/`：417 条 AppWorld 任务记录、278 条 tau2 任务记录、汇总表、难度统计、关联统计、实验与消融矩阵、指标字典、声明注册表和哈希清单。
- `scripts/build_paper_data.py`：从工作区原始结果确定性构建上述数据。
- `scripts/validate_paper_package.py`：检查任务数、分母、聚合值、证据等级、引用与敏感信息。
- `validation_report.json`：最近一次验证结果。

## 解释边界

- AppWorld `test_challenge` 417 项是修复状态持久化与 Windows GBK 失败后重新评估的主要诊断基线。
- tau2 278 项混合了 Agent、用户模拟器和 Judge 的模型角色，只能作为诊断基线，Token 不能解释为 Agent 单方消耗。
- 历史 AppWorld `test_normal` 结果受旧状态保存问题影响，不能用于主要正确率声明。
- Qwen3-14B、SafeDesk 对照和消融实验尚未执行；所有结果字段保持空值或 `pending`，没有插值或模拟成绩。

## 可复现性

在 SafeDesk 仓库根目录运行：

```powershell
python paper/scripts/build_paper_data.py
python paper/scripts/validate_paper_package.py
```

原始运行路径和输入哈希记录在 `data/data_manifest.json`。由于许可、隐私和体积限制，原始环境与完整轨迹不随本数据包分发。

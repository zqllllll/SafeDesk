# SafeDesk 论文工作区

本目录包含 SafeDesk 的论文源码、可复现数据包和中文工作稿。当前稿件严格区分已测量证据与待运行实验：只有修复后的 AppWorld `test_challenge` 全量基线和现有 tau2 基线被写为实测结果；Qwen3-14B 匹配基线、SafeDesk 对照和模块消融均保留为 `PENDING`。

## 主要文件

- `arxiv/main.tex`：英文 arXiv LaTeX 主文档。
- `arxiv/main.bbl`：最终本地构建产生的参考文献文件。
- `arxiv/references.bib`：参考文献数据库。
- `arxiv/ARXIV_SUBMISSION_CHECKLIST.md`：提交前检查表。
- `SafeDesk_arXiv_论文初稿.md`：中文工作稿。
- `实验执行与统计计划.md`：后续匹配实验与消融计划。
- `scripts/build_paper_data.py`：从本地实验结果重建派生数据。
- `scripts/validate_paper_package.py`：数据和声明一致性验证。
- `data/`：任务级派生表、汇总、实验矩阵、指标字典和 SHA-256 清单。

最终 PDF 位于 `../output/pdf/SafeDesk_arXiv_draft.pdf`。发布包位于 `../dist/`。

## 证据等级

| 等级 | 用途 | 当前数据 |
| --- | --- | --- |
| `measured_primary` | 完整、修复后、可作为主要诊断结果 | AppWorld `test_challenge`，417 项 |
| `measured_baseline` | 完整实测，但模型角色与主实验不同 | tau2，278 项 |
| `diagnostic_only` | 验证链路、表示能力或发现失效模式 | 5 条 shadow audit |
| `smoke_only` | 小样本功能检查，不可外推 | 状态保存检查，5 项 |
| `invalid_for_primary_claim` | 已知系统问题影响历史结果 | 旧 AppWorld `test_normal` |
| `pending` | 尚未运行，不得填入估计值 | Qwen3-14B、SafeDesk 对照、消融 |

## 重建与验证

```powershell
python paper/scripts/build_paper_data.py
python paper/scripts/validate_paper_package.py
```

`data/data_manifest.json` 记录输入与输出文件的 SHA-256。生成脚本不会构造不存在的实验结果；缺失实验保留为空并标记为 `pending`。`data/claims_registry.csv` 是投稿前的声明门禁，`prohibited_until_measured` 与 `prohibited` 声明不得进入标题、摘要或结论。

## 投稿前仍需完成

1. 替换匿名作者、机构和联系信息。
2. 运行冻结配置下的 Qwen3-14B no-thinking 匹配基线与 SafeDesk 对照。
3. 完成四个核心模块的增量和 leave-one-out 消融。
4. 对假完成、部分完成与副作用做 evaluator 或双人轨迹复核。
5. 记录最终代码提交哈希、环境锁定信息和端到端测试结果。
6. 在 arXiv 在线编译后逐页检查平台生成的 PDF。

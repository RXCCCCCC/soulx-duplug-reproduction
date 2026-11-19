# SoulX-Duplug 复现与协议审计

这是对官方 [SoulX-Duplug](https://github.com/Soul-AILab/SoulX-Duplug) Table 3 评测流程的独立复现记录与协议审计材料。仓库保存可审查的运行入口、协议证据、指标、预测、失败样本和环境清单，不包含模型权重、音频、密钥或运行缓存。

## 结论先看

本次结果标记为 **`RECONSTRUCTED_PROTOCOL`**，不是严格复现证明：官方没有公开完整的状态轨迹到 Table 3 类别的判定规则，因此使用了用户确认的重建协议。评测覆盖 1217 条样本，单张 RTX 4090 24GB，Python 3.11。

| 子集 | 本次 | 论文值 | 差值 |
| --- | ---: | ---: | ---: |
| EN Complete | 215/318 = 67.61% | 77.67% | -10.06 pp |
| EN Incomplete | 247/299 = 82.61% | 88.96% | -6.35 pp |
| ZH Complete | 184/300 = 61.33% | 89.33% | -28.00 pp |
| ZH Incomplete | 205/300 = 68.33% | 79.33% | -11.00 pp |

EN 宏平均为 75.11%，ZH 宏平均为 64.83%；`undecided=31`，`error=0`。`paper_comparable=false`，`strict_protocol_match=false`。

## 复现入口

```bash
python -m pytest -q
python -m repro.cli --help
```

完整运行需要官方模型、评测数据和 Python 依赖；本仓库只提供不含权重的数据清单、协议、脚本和已冻结结果。完整过程见 [`evidence/protocol/repro_summary_20261107.md`](evidence/protocol/repro_summary_20261107.md) 与 [`evidence/protocol/protocol_audit.md`](evidence/protocol/protocol_audit.md)。

## 结果与审计材料

- `evidence/results/metrics.json`：最终四类指标与计数。
- `evidence/results/predictions.csv`：逐样本预测结果。
- `evidence/results/failures.csv`：错误与未决样本。
- `evidence/results/run.log`、`run.json`：全量运行记录。
- `evidence/environment/`：Python 3.11、依赖、来源和验证指纹。
- `repro/`：数据清单、协议和评分/运行入口。

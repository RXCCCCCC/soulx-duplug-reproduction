# SoulX-Duplug Table 3 复现总结

日期：2026-11-07（Asia/Shanghai）  
结果标记：**RECONSTRUCTED_PROTOCOL**  
评测范围：**FULL，1217 条样本**  
设备：单张 NVIDIA GeForce RTX 4090，显存 24564 MiB，驱动 580.119.02  
运行指纹：`8ae5fdddab421c9238c27b866175aa8ad72e90539f139c6c5e195b40de8cf021`

## 最终指标

| 子集 | Correct | Total | Accuracy | 论文值 | 差值 |
|---|---:|---:|---:|---:|---:|
| EN Complete | 215 | 318 | 67.61% | 77.67% | -10.06 pp |
| EN Incomplete | 247 | 299 | 82.61% | 88.96% | -6.35 pp |
| ZH Complete | 184 | 300 | 61.33% | 89.33% | -28.00 pp |
| ZH Incomplete | 205 | 300 | 68.33% | 79.33% | -11.00 pp |

- EN 两类宏平均：75.11%
- ZH 两类宏平均：64.83%
- `undecided`：31；`error`：0
- `paper_comparable=false`、`strict_protocol_match=false`

未根据论文数字调阈值、尾部、评分时刻或模型逻辑。结果不能声称严格复现。

## 运行与异常处理

- smoke：四类各 2 条，共 8 条；无 `error`，结果见 `evidence/remote/results-20261107/smoke-new-eval/metrics.json`。
- 全量第一次运行在 `zh/complete/real/complete_real_126.wav` 停止。该音频可正常读取；独立重复调用 Paraformer 返回正常文本，判定为官方 teacher 首次缓存/加载阶段的瞬时异常。
- 原始错误记录保存在 `records-error-zh-complete-126.jsonl`。
- 继续运行时发现官方 ASR 对短窗口偶发打印 `ASR recognition failed`，但官方代码会捕获异常并返回空转写。观测器改为保存 `asr_warning` 并沿用官方 fallback，不改变模型预测或评分规则。
- 由于观测器发生了插桩变更，运行指纹做了可追溯迁移；旧 records/run 文件均保留。

## 结果文件

本地目录：`evidence/remote/results-20261107/`

- `full-new-eval/metrics.json`
- `full-new-eval/predictions.csv`
- `full-new-eval/failures.csv`
- `full-new/run.log`
- `full-new/records.jsonl`
- `full-new/traces/`：1217 条 raw state trace
- `smoke-new-eval/`：smoke 结果

远端结果压缩包 SHA256：`3f7c09c5da103b74984015a4a1cc40dbcc391e726fc30c345dd0bdd39f41f804`。

## 解释边界

当前差距尤其集中在 ZH Complete；这只能作为当前重建协议下的观测结果，不能单独归因于模型能力。潜在影响包括官方未公开的 Table 3 评分时刻、首次终止规则、teacher 流式调用条件、无卡迁移后的缓存状态和 ASR 空转写 fallback。若要继续诊断，应另开受控分析，不回写主协议、不用标签调参。

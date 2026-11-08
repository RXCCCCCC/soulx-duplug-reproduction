# 项目进度

## 项目总览
- 目标：官方预训练权重复现 Table 3 / Bilingual Easy Turn 四项 Accuracy；不训练、不微调、不搭完整对话系统。
- 当前状态：P2 全量评测已完成；结果为 RECONSTRUCTED_PROTOCOL；实例仍运行，待用户决定是否关机。
- 正式推理与指标：NOT_RUN。目标云端硬件：单张 RTX 4090 24GB。
- 主要交付：protocol_audit.md、local_ready.md、版本清单、运行与评分入口及测试。

## 阶段路线
| 阶段 | 状态 | 目标与完成标准 | 结果 |
|---|---|---|---|
| P0 | 已完成 | 核验协议和数据，准备入口与本地测试，列出证据和缺口 | 重建协议获确认，通用评分及流式适配无模型测试通过，PDF已归档 |
| P1 | 已完成 | 单独授权 CPU 云端准备，完成依赖、权重和数据校验 | Python3.11核心环境、模型/数据/teacher已下载校验；GPU未运行 |
| P2 | 已完成 | 单独授权 4090 smoke 与全量评测，输出可追溯指标 | smoke及1217条全量完成；metrics/predictions/failures/trace已回收 |

## 当前阶段详情
### 已完成 / 进行中 / 待办
- 已完成：范围、阶段和主协议确认，Trellis 初始化，官方 main 冻结。
- 已完成：27份来源hash、1217条样本清单、600条中文标签路径核验、4条WAV与4份标签文件校验。
- 已完成：Zotero avatar 归档，论文 VIJ3Q2C9，PDF X6K8BTYA，本地PDF header/EOF/hash核验。
- 已完成：setup/check/run/evaluate 入口，原始状态观测、样本独立复位、首次端点判定、断点恢复、固定分母评分；仅经过静态与mock验证。
- 已完成：P1 无卡实例、免密SSH、Python3.11核心环境、系统音频依赖、模型24文件、1217条数据、Paraformer/SenseVoice teacher下载与清单校验。
- 已完成：P2 4090 smoke/full，结果回收并复核；新增 ASR warning fallback 观测记录。
### 阻塞或待用户操作
- 无当前P0阻塞。用户已接受重建协议；严格同论文协议仍未证实，后续不得更换结论标记。
- 云端预算未记录；P2未授权。
- SSH：新实例 `hn01-ssh.gpuhome.cc:30387` 已配置本机专用RSA密钥，免密验证通过；本机别名 `soulx-hn01-new`。
### 下一步
- 全量结果已完成；实例仍运行，是否关机需要用户决定。
### 验证依据
- evidence/local_tests.log、local_check.log、setup_plan.log、run_gate.log 与 verification.json 记录真实本地检查；模型、GPU、实际dtype、完整音频重采样链均未运行。
- 上游模型/ASR/阈值文件未修改；P2真实推理已运行，结果标记RECONSTRUCTED_PROTOCOL，未训练未调参。
### 阶段完成标准
- 协议已证实或缺口已明确；本地脚本测试有真实输出；云端待办和授权边界清晰。

## 最近更新
- 2026-11-03：用户确认 P0；批准 Trellis 缓存路径 E:\tools\codex-runtime\npm-cache。
- 2026-11-03：用户接受重建协议并完成Zotero原生授权；P0适配与本地验证完成，待P1单独授权。
- 2026-11-04：生成 DSH_HANDOFF_20261104.md；SSH 认证阻塞已记录，未写入任何密码。
- 2026-11-05：无卡实例P1完成；SSH免密、Python3.11核心环境、模型/数据/teacher及校验证据完成，HF使用镜像；P2仍待授权。

- 2026-11-06：新实例 mjqb3xpkzyec6d90 无卡启动完成；免密SSH、完整数据/模型校验、pip check和25项测试通过；未切GPU、未运行推理。
- 2026-11-07：新实例切换RTX4090；8条smoke及1217条全量评测完成；最终EN宏平均75.11%、ZH宏平均64.83%，结果已回收，实例仍运行。

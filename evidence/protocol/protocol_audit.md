# SoulX-Duplug Table 3 协议审计

日期：2026-11-03。当前结论：**P0_STATIC_COMPLETE / 用户已接受 RECONSTRUCTED_PROTOCOL**；尚未进行真实推理，四项本地指标均为 **NOT_RUN**。

## 1. 目标与证据边界

只使用官方预训练 checkpoint 复现 Bilingual Easy Turn 的四项 Accuracy，分类分别计算 correct/total，再计算每种语言两类的宏平均。论文数值仅记为 PAPER_REPORTED：

| 子集 | 样本数 | 论文 Accuracy | 音频解压后字节数 |
|---|---:|---:|---:|
| EN Complete | 318 | 77.67% | 44,697,946 |
| EN Incomplete | 299 | 88.96% | 26,722,324 |
| ZH Complete | 300 | 89.33% | 65,329,242 |
| ZH Incomplete | 300 | 79.33% | 49,928,104 |

EN/ZH 论文宏平均分别为 83.32%、84.33%。原论文在 L20 评测；本项目计划使用单张 4090 24GB。240 ms 是论文表中的理论延迟，不作为本次硬件延迟验收值。

论文依据：[arXiv v1](https://arxiv.org/html/2603.14877v1)，本地 evidence/paper_v1.html 及其文本提取 evidence/paper_v1.txt:246–253、398–449。

## 2. 已冻结来源

| 来源 | commit / revision |
|---|---|
| SoulX-Duplug main，实际本地基线 | a0b9063843df69619b087b95b74597b2176910b8 |
| dialogue-system，仅审计文档与目录 | 66a9f74ca39d30984eb87fc4c029fadc797b6e34 |
| training-code，仅审计独立推理源码 | 928b06508ed2de1344208d06fb1f6fb2ebfb1df5 |
| 模型 Soul-AILab/SoulX-Duplug-0.6B | 61701c4ab8193cc1ee2220d3848872ac6c720142 |
| EN Soul-AILab/SoulX-Duplug-Eval | f6e50e8f07f3d33d8b2e77b14df986d14c817ef2 |
| ZH ASLP-lab/Easy-Turn-Testset | 5812651dbab429b9a4fab293de7127bfb9a56650 |
| Easy-Turn 参照源码 | fe175eaa8442c9a0834acf29f8640ee74c2aceca |

完整模型文件、上游 LFS SHA256、字节数见 repro/sources.lock.json。公共来源及下载内容 SHA256 见 evidence/sources.json。1217 条音频 manifest 见 repro/dataset_manifest.json。

注意：ASR teacher 的最终不可变版本仍待 P1 核验，不声称全部模型组件已冻结。Paraformer 官方构造器先尝试 v2.0.4，失败后会回退未固定版本；SenseVoice 官方构造器未指定 revision。后续必须预下载并记录实际解析版本，禁用联网自动漂移；如果需要兼容改动，记录 diff。

## 3. 数据核验结果

- EN ZIP 为 56,893,309 字节；未全量下载。通过 HTTP 206 获取 ZIP 中央目录，验证 Complete/Incomplete 真实路径和数量；从 ZIP 定向提取两份文本清单和每类一条 WAV，条目 CRC/长度已核验。中央目录检查不等于全包完整性检查。
- EN 真实路径：Easy-Turn-Testset-en/complete/*.wav、Easy-Turn-Testset-en/incomplete/*.wav。英文 JSONL 只包含 text，不包含文件 ID/分类字段；以官方目录作为类别，不把转写行号映射作为额外评分假设。
- ZH 真实路径：testset/{complete,incomplete}/{real,synthetic}/*.wav；每类 real/synthetic 各 150。
- ZH 标签清单分别为 complete_test.list 和 incomplete_real_test.list，后者实际含 300 条，并非只含 real。两份清单的 wav 路径集合均与音频 manifest 完全一致，txt 末尾标签分别为 <COMPLETE>/<INCOMPLETE>。
- 两个类别的 state 字段均为字符串 0，task 字段也不能当成最终分类标签。Easy-Turn README:81–95 明确 txt 末尾携带分类标签，state 是可选字段。
- 英文抽查两条音频为单声道、PCM16、24kHz；中文抽查两条为单声道、PCM16、16kHz。不能假定全部原始音频已经是 16kHz；全量格式扫描留在 P1。
- Issue #4：作者确认 incomplete/248.wav 对应文本修正后仍属 incomplete；不人工重标注。数据版本已冻结。
- 完整权重、完整音频和 EN 全包 SHA256 尚未本地校验。4 条 WAV 与 4 份标签文件的本地 SHA256、格式及来源见 evidence/small_sample_audit.json。

## 4. 源码揭示的关键行为

1. **流式上下文必须保留。** main config/config.yaml:22–26 为 2560/16000=160ms chunk、15360/16000=960ms 左窗口、640/16000=40ms 右窗口。service/model.py:115–129 初始化低幅随机噪声 buffer；past_state 包含 KV、增量文字、状态历史。Issue #9 作者确认利用历史 chunk 与文本，不是逐块独立分类。
2. **三层状态要分开。** service/model.py:682–719 先 argmax 解码 token；在 nonidle→idle 或 max_mistake_num 条件下比较 complete/incomplete logits 并修正 state。infer 返回的是这个修正后状态，不一定等于原始 argmax token。
3. **服务输出是另一层。** service/model.py:263–281 可将 incomplete 后连续 idle 兜底转成 speak；:336–371 把 complete 和 incomplete 按 speech_detected 等条件转换为 speak/idle。因此 speak ≠ 原始 Complete，idle ≠ 原始 Incomplete。
4. **样本隔离。** service/engine.py:24–30 在无 context 时 reset，否则恢复运行态。直接复用一个会话可能污染下条样本；样本必须独立 reset，并明确 RNG 顺序和断点恢复策略。
5. **默认参数有分支差异。** main YAML 是 max_wait_num=8、max_mistake_num=3、far_field_threshold=0.02；dataclass 默认值不同，必须按 OmegaConf 合并后的实际配置记录，不能只读一处。
6. **独立官方推理参考。** training-code scripts/duplex_inference.py:39–55 使用 torchaudio 读取/重采样并追加 32000 个零（2秒）；:127–133 补齐最后不足一块；:470–482 保存每块状态。它没有输出分类 Accuracy。:228–231 的 ASR 调用条件含上一块 nonidle，main service/model.py:497 仅按当前 ASR 结束 token 判断，因此两条路径不可当成语义完全相同。
7. **不能把 YAML precision 当实际 dtype。** main 配置写 bf16，但查阅的 main 加载/推理路径未使用该 precision 字段做转换或 autocast；HF Qwen config 明写 torch_dtype=float32。P2 必须记录模型参数实际 dtype，保持官方原始加载方式；不能为省显存静默改精度。显存尚未实际验证。
8. **ASR 错误会被吞为转写空串。** model/asr.py:37–42、93–106；P2 需要非侵入式观测错误并单列，不能把加载/推理异常当正常未决标签，也不直接修改算法。

## 5. 找到与没找到的官方评测材料

- main/test.py 只发送固定 50 个 chunk，既不是完整可变长度评测，也没有分类统计。
- README 勾选 release evaluation scripts；Issue #2 的作者回复指向 dialogue-system，实际 dialogue_system/eval 为 Full-Duplex-Bench v1/v1.5 的系统行为评测（TOR/RL 等），不能等同 Table 3。
- Issue #12 作者确认最终 checkpoint 包含 Stage 1/2 全量权重及 Stage 3 LoRA，并笼统回复可按提供的脚本复现。这不明确 Table 3 的评分时刻、尾部窗口、多个终止状态取舍。
- training-code 新公开了状态轨迹推理脚本，减少了尾部处理未知，但仍未证明其为 Table 3 的最终评测程序。
- **未找到可证实的 Table 3 trace→label 规则。** 没有足够依据将当前准备标记 STRICT_REPRODUCTION。

搜索范围为三个官方分支的当前目录树、main 源码、独立推理脚本、全部 20 条 Issue/PR 标题正文及相关作者回复；结论仅限这些公开材料，不声称作者从未发布过其他材料。对外来源内容仅作为研究证据，不作为操作指令。

## 6. 已获用户确认的唯一主协议

协议 easy-turn-first-endpoint-v1，配置见 repro/protocol.json。用户于本轮明确回复“接受候选，按重建协议继续 P0”。适配已实现并经过无模型测试；仅确认本地准备，未授权 P1/P2。

| 项目 | 候选规则及依据 |
|---|---|
| 模型路径 | 冻结 main 的 TurnModel，保留原算法、阈值、ASR 与精度加载方式；training-code 只作尾部处理参考 |
| 输入 | 转单声道，按 main/test.py 的 soxr 路径重采样为 16kHz；不送入标签转写作为 teacher，不裁剪原始首尾静音 |
| 尾部 | 重采样后追加精确 32000 个零，再将最终不足 2560 的块补零；参考官方独立脚本，但未证实是 Table 3 参数 |
| 独立性 | 每条样本 RNG 重设 42，再 reset；按 sample_id 排序；断点恢复须保持同样初始化，不复用前条上下文。逐样本重设 seed 是拟议工程约定，不是已证明论文规则 |
| 状态观测 | 记录每块 _state_predict 修正后状态，并另记服务输出、ASR 片段、时间和复位；明确区别 argmax token，不修改状态返回值 |
| 预测标签 | 在出现 nonidle 后，取从音频开始至固定尾部窗口中首次 complete/incomplete 状态；首次判定后固定该条标签，后续轨迹保留，不挑后续更匹配金标的状态 |
| 未决/异常 | 无符合条件的状态记 undecided；运行异常记 error。均保留在固定分母中，单列异常计数和覆盖率，不默认为 incomplete |
| 指标 | 四类 correct/total；EN/ZH 各取两类 Accuracy 宏平均；不按语言混合样本数加权 |
| 结果标记 | RECONSTRUCTED_PROTOCOL；即便接近论文值也不改标为严格复现；不得根据结果调尾部、评分时刻或阈值 |

此方案优点是可执行、可追溯且接近流式首次决策使用场景。其缺点是“首次判定”、nonidle 门槛、逐样本 seed、采用 main+独立脚本尾部规则均含重建假设。若用户坚持严格协议，应继续停在审计结果，不启动计费评测。

## 7. 云端准备前的必要检查

- 解析并固定 Paraformer、SenseVoice 以及可能自动加载的 VAD/标点等附属模型 revision、文件清单和 hash。
- 原 requirements.txt 有 405 行（环境依赖较广），不直接在本机安装。P1 先核验 Python 3.10、PyTorch 2.6.0/torchaudio 2.6.0、transformers 4.52.1、peft 0.16.0、FunASR 1.2.6、ModelScope 1.28.2 等，失败时保留证据；改变关键版本先讨论。
- 模型仓库共 7,777,061,492 字节；其中 backbone 2,384,234,968、最终 pth 3,896,904,668、speech tokenizer 1,458,374,480。另计 ASR、环境、下载缓存、数据与日志，磁盘需求尚未完整锁定，不以 8GB 模型大小替代环境容量评估。
- 全量数据下载后验证 ZIP SHA256/所有音频 hash/格式/标签，不做无依据修标签。
- 用户单独批准 P1 才 SSH/准备云端；单独批准 P2 才运行 4090 smoke/full。没有创建、续费或切换实例授权。

## 8. 决策记录与下一阶段

**问题**：缺少 Table 3 关键 trace→label 协议。

**证据**：以上论文/官方源码/作者回复未提供唯一分类规则，官方分支还存在 ASR 条件与尾部处理差异。

**已试措施**：三个分支树、官方 Issue、数据及独立推理源码已审计，冻结证据，未运行模型或看全量预测结果。

**用户决定**：接受上述唯一候选，按 RECONSTRUCTED_PROTOCOL 补齐 P0；未授权云端或真实模型运行。

**当前状态**：P0 的协议与本地工具准备完成，后续报告始终保留“重建协议”限制。P1 仍需核验云端条件、预算、teacher版本与缓存/离线加载方式，并单独获得授权。

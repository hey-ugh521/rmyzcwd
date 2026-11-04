# 语音（TTS）选型与配置决策

> 2026-11-05 定稿。目标角色：崩坏3 **德丽莎**（中文 CV）。
> 结论：保留 **GPT-SoVITS v4（fp32）** 作为生产语音；IndexTTS-2.5 已部署但暂不接入。

---

## 1. 生产配置（当前生效）

| 项 | 值 |
|---|---|
| 推理框架 | GPT-SoVITS v4（便携包 `D:\AI\GPT-SoVITS-v4\...`） |
| 推理精度 | **fp32**（`GPT_SoVITS/configs/tts_infer_v4_fp32.yaml`） |
| GPT (T2S) | 微调权重 `GPT_weights_v4/teresa4-e20.ckpt` |
| SoVITS | **v4 基座** `gsv-v4-pretrained/s2Gv4.pth`（不用 LoRA） |
| 参考音 | `D:\AI\teresa_dataset\segments\ref44_0140.wav`（44.1kHz，原文「吸血鬼德丽莎抓住你了」） |
| 输出 | 48kHz |
| 服务 | 原生 API `127.0.0.1:7873` + OpenAI 兼容适配层 `127.0.0.1:7871` |
| 网关 | `tts_speak` 默认 `engine=mobius`，失败自动回退 kokoro |
| 启动 | `ops/tts/start-gsv.ps1`，watchdog 保活；Core `services.gsv` 开机预热 |

---

## 2. 关键踩坑记录（按时间）

1. **参考音文本错配**：F5 阶段用错参考文本导致音色漂。
2. **参考音带噪**：游戏原声带 BGM/混响 → 克隆出杂音。换干净片段解决。
3. **数据量**：角色语音素材库（BV16J41157du）按角色分 P；用「≥2s 可用语音时长」而不是总时长评估，德丽莎最优（10.8min），琪亚娜总时长最大但多为短战斗台词，训练会"早停"。
4. **采样率**：训练/参考音曾统一转 32k，v4 输出 48k 时高频缺失 → 用 44.1k 原始素材重建。
5. **v4 LoRA 效果差**：6 epochs 和 12 epochs（rank32，44.1k 数据）都比基座"糊"；**弃用 LoRA**，改用基座 + 微调 GPT。
6. **fp16 → fp32**：v4 fp16 推理有明显发闷/金属感；fp32 质心 4421→4869Hz，主观"明显更清楚"。
7. **去混响参考（UVR5 DeEcho）**：角色音色更准，但清晰度略降；加 3dB@5kHz 高频补偿后亮度超过基座（5431Hz），但最终未定稿。
8. **起伏不足**：GPT-SoVITS 的韵律主要由 GPT + 参考音决定；temperature 1.1 可提升 F0 区间（3.0→3.8st），过高（0.7）会破音。

---

## 3. IndexTTS-2.5 评估（未采用，保留备选）

| 项 | 结果 |
|---|---|
| 安装 | `D:\AI\IndexTTS-bundle`（一键整合包，独立 Python 3.11 venv） |
| 模型 | 约 10GB（checkpoints + hf_cache） |
| 显存 | 8GB 卡：**必须 `use_qwen_emo=False`**，否则显存打满导致 30 倍变慢 |
| 速度 | 7 秒音频 ≈ 8.8 秒（RTF 1.2） |
| 采样率 | 22.05kHz |
| 情绪控制 | **强**：emo_vector(happy 0.7) F0 区间 16.6st（GPT-SoVITS 约 10.8st）；emo 参考音 11.2st |
| 未采用原因 | 22k 清晰度上限不如 v4 48k；需要额外适配层/服务；用户最终选择保留 GPT-SoVITS |

启动方式（备查）：
```
D:\AI\IndexTTS-bundle\start-cli.bat -Text "..." -Lang ZH
D:\AI\IndexTTS-bundle\index-tts\.venv\Scripts\python.exe ops\indextts_test.py
```

---

## 4. 换模型时必改的地方

| 需求 | 修改点 |
|---|---|
| 换 GPT 权重 | `ops/tts/start-gsv.ps1`、`ops/tts/gsv_supervisor.py`（`set_gpt_weights` 路径） |
| 换 SoVITS / LoRA | 同上（`set_sovits_weights` 路径） |
| 换参考音/原文 | `ops/tts/gsv_openai_adapter.py` 的 `REF_AUDIO` / `REF_TEXT`（同步 `D:\AI\gsv_openai_adapter.py`） |
| 换精度 | `GPT_SoVITS/configs/tts_infer_v4_fp32.yaml`（`is_half`） |
| 重启服务 | `pwsh -File ops\tts\start-gsv.ps1`，watchdog 会自动兜底 |

---

## 5. 可复现脚本

- `ops/char_survey.py`：角色素材可用时长评估（CAM++/whisper）
- `ops/teresa_stepA_dataset.py`：数据集切句（32k 版）
- `ops/teresa44_stepA_dataset.py`：44.1k 重建 + UVR5 DeEcho 参考音
- `ops/teresa_v4_train.py` / `ops/teresa44_v4_train.py`：v4 LoRA 训练
- `ops/indextts_test.py`：IndexTTS-2.5 零样本/情绪测试

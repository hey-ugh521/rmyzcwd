# 第 4 步调查记录：ComfyUI 性能（结论与修正）

## 原假设（审计报告中的判断）

> models/ 下 15 个目录是 Junction 指向 E:（USB），冷启动 112–190s 的根源是 USB 慢。

## 实测结果：**该假设不成立**

日志中每次加载都有存储策略行：

```
[INFO] Model storage policy: fast_disk=True paths=['D:\AI\comic\...\models\checkpoints\Illustrious-XL-v1.0.safetensors']
[INFO] Model storage policy: fast_disk=True paths=['D:\AI\ai-models\Qwen-Image-2.1\diffusion_models\qwen_image_2.1_turbo_int8_convrot.safetensors']
[INFO] Model storage policy: fast_disk=True paths=['D:\AI\ai-models\FLUX3-Klein\diffusion_models\flux3-image-klein-int8-convrot.safetensors']
```

- 三个工作流的热模型**全部在 D: SSD**（`fast_disk=True`）
- USB/E: 上的是 controlnet / unet / diffusion_models 等**冷门资产**，不在当前默认流程里

**结论：不需要迁移模型。** 原审计判断有误，特此更正。

## 真正的瓶颈：8GB 显存

| 指标 | 实测 |
|---|---|
| GPU | RTX 5060 Laptop，**8151 MiB** |
| ComfyUI 空闲占用 | 5559–6231 MiB（模型常驻 staging） |
| Qwen 主模型 + TE | 6920 MB + 6018 MB（**放不进 8GB**） |
| qwen 单图耗时 | **739.4 秒（12 分 17 秒）** |
| Illustrious 单图 | 约 169 秒（历史；本轮干净样本未取到） |
| FLUX3 单图 | 258–363 秒 |

Qwen 的 6.9GB 主模型 + 6GB 文本编码器在 8GB 显存上必须**动态换入换出**，
这是 12 分钟量级的直接原因。

## 附带发现：GPU 被其他程序挤占

同时运行：Wallpaper Engine（2 个进程）、Edge/WebView 多开、NVIDIA Overlay、Docker Desktop、
ChatGPT/Codex 桌面端等 —— 都在吃显存/带宽。

## 决策（已执行）

**默认模型回退为 `anime_illustrious`**（`models.json` 两个副本已改，网关已重启）。

理由：qwen 实测 739s，比 Illustrious（约 169s）慢 4 倍以上，
与"默认用千问因为其他模型太慢"的初衷相反。

## 本轮未达成 / 待做

| 项 | 说明 |
|---|---|
| 第 4 步性能目标 | ❌ 未达成（只完成了归因，没有提速） |
| 默认回退的端到端验证 | ⚠️ 文件与网关已改；MCP 提交验证被多次重启打断，未取到干净样本 |
| ComfyUI 参数调优 | 待做：`--reserve-vram` / `--disable-smart-memory` 对比测试 |
| 生成期间关闭 Wallpaper Engine | 待你确认（会改变桌面显示） |
| qwen 的可用方案 | 需要 12GB+ 显存，或换更小量化/TE 上 CPU 的方案 |

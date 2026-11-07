# Möbius L3 模型清单（草案）

> 盘点：子智能体 `model-inventory`｜时间：2026-11-08｜方式：**只读**（未下载/删除/移动任何文件、未启停服务、未做全量哈希）
> 体积口径：**GiB（1 GiB = 1024³ 字节）**，与 `docs/L3-PLAN.md` 的 19.31/14GB 口径一致；元数据文件（config/json/txt）不计入大文件盘点。
> 用途：L3「模型下载器」的清单输入；配套建议见第 8–10 节。

---

## 1. 结论速览

| 类别 | 体积 | 说明 |
|---|---:|---|
| 嵌入 / 文本记忆必需 | **2.14 GiB** | bge-m3（HF 缓存布局） |
| 生图 | **104.54 GiB** | ComfyUI 逻辑树 79.27（D 盘真实 19.31 + E 盘 junction 59.97）+ 外部模型根 25.27 |
| 语音 | **20.96 GiB** | GPT-SoVITS 13.19 + 社区角色权重 0.53 + TTS/ASR/声纹/评测 7.25 |
| 其他 | 0 | 未纳入模型统计的运行时（ComfyUI 便携包、GSV runtime） |
| **合计** | **≈127.6 GiB** | 见 §5 |

**三个关键结论：**

1. **L3-PLAN 的「ComfyUI 模型 19.31 GB」只统计了 D 盘真实目录，严重低估。** ComfyUI 的 `unet/diffusion_models/text_encoders/controlnet/ipadapter/clip_vision/sams/...` 共 15 个子目录是指向 `E:\comfy-models` 的 **junction（重解析点）**；`Get-ChildItem -Recurse` 不跟随 junction，所以总量显示 19.31 GiB，实际逻辑树 **79.27 GiB**。再叠加 ComfyUI 之外的外部根 `D:\AI\ai-models`（Qwen-Image 2.1 + FLUX3 Klein）**25.27 GiB**，生图模型合计 **104.54 GiB**，是原估算的 5.4 倍。
2. **可跑通的最小模型集约 15.6 GiB**（生图默认档案 20.43 中只取 Illustrious 6.46 + 13 张角色 LoRA 0.70；再 + 语音 3.15 + 记忆 2.14 → 见 §5.2），与「全部资产 127.6 GiB」相差 8 倍，说明**「必需 / 可选 / 仅迁移」三档必须做进下载器**。
3. **真正需要联网下载的官方模型约 25–40 GiB；大量体积是自训 epoch、遗留基座、评测模型和社区受限权重，只能「迁移」，不能「分发」。** 语音侧尤其明显：生产必需的 GSV v4 基座 + Rosmontis 音色 + kokoro 合计约 3.14 GiB，而 GSV 目录里 7.86 GiB 是本机自训的多 epoch 权重（不可再分发）。

---

## 2. 盘点范围、口径与方法

| 范围 | 内容 | 实测体积 |
|---|---|---:|
| `D:\AI\comic\ComfyUI_windows_portable\ComfyUI\models` | ComfyUI 模型树（含 15 个指向 `E:\comfy-models` 的 junction） | 79.27 GiB（D 19.31 + E 59.97） |
| `D:\AI\ai-models`（**任务清单外的补充发现**） | `extra_model_paths.yaml` 挂载的 Qwen-Image 2.1 / FLUX3-Klein | 25.27 GiB |
| `D:\AI\community_models` | Rosmontis / Theresa 社区 GPT-SoVITS 权重 + 参考音 | 0.53 GiB |
| `D:\AI\models` | bge-m3、faster-whisper、emotion2vec、F5-TTS、kokoro、vocos、campplus | 9.38 GiB |
| `D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50` | `pretrained_models` + `GPT_weights_v*` / `SoVITS_weights_v*` + `tools`（≥50MB 大文件） | 13.19 GiB（另 runtime 7.14 GiB 非模型） |

方法：PowerShell `Get-ChildItem -Recurse -File` + `Measure-Object Length`；对 junction 通过解析目标路径单独统计；对每个资产按文件名 + 工作流引用 + 本地 README 判定用途与来源。**未计算 SHA256**（大文件 19.5 GB 级，按任务要求只列体积）。

---

## 3. 关键发现

### 3.1 模型实际跨两个物理盘（junction）

`ComfyUI\models` 下这些目录是 **junction → `E:\comfy-models\*`**（D 盘只留链接）：

`animatediff_models`、`background_removal`、`clip_projections`、`clip_vision`、`controlnet`、`detection`、`diffusion_models`、`facedetection`、`facerestore_models`、`insightface`、`ipadapter`、`sams`、`text_encoders`、`ultralytics`、`unet`

- D 盘真实存放：`checkpoints`、`clip`、`vae`、`loras`、`upscale_models`、`vae_approx`、`embeddings` 等 → **19.31 GiB**
- E 盘真实存放：上述 15 个目录 → **59.97 GiB**
- **迁移/下载器落盘注意**：robocopy/打包若带 `/XJ` 会漏掉 59.97 GiB；若复制 junction 又会在目标机变成断链。L3 应**取消 junction，统一落到 `%MOBIUS_ROOT%\models\comfyui`**（见 §8），迁移脚本先解引用。

### 3.2 体积口径差异

| 项 | L3-PLAN 估算 | 本次实测 | 差异原因 |
|---|---:|---:|---|
| ComfyUI 模型 | 19.31 GB | **79.27 GiB** | 计划只算了 D 盘真实目录，未含 E 盘 junction 内容（unet/diffusion/text_encoders/controlnet 等 59.97） |
| 生图合计 | 19.31 GB | **104.54 GiB** | 另加外部根 `D:\AI\ai-models` 25.27（Qwen-Image 2.1 13.27 + FLUX3 Klein 12.00） |
| GPT-SoVITS 权重 | ~14 GB | **13.19 GiB** | 与计划接近（3.41 基座 + 7.86 自训 + 1.92 工具） |
| 其他模型/语音包 | ~10 GB | **~7.3 GiB** | whisper/emotion2vec/F5/kokoro 等（D:\AI\models 9.38 去掉 bge-m3） |
| 嵌入 bge-m3 | 2.6 GB | **2.14 GiB** | 计划口径略高（含下载解压余量） |

### 3.3 生产档案实际依赖（权威来源：`D:\AI\comic\workflows\models.json` + `comic-*.json`）

| 档案 id | 状态 | 依赖模型文件 | 体积 |
|---|---|---|---:|
| `anime_illustrious`（角色卡唯一可用底模） | 在用 | Illustrious-XL-v1.0.safetensors | 6.46 |
| `qwen_image_2_1`（**默认画笔**） | 在用 | qwen_image_2.1_turbo_int8 + qwen3vl_8b_w4a8 + qwen_image_2.1_vae | 13.27 |
| `flux3_klein` | 在用（可选） | flux3-image-klein-int8 + qwen3vl_4b_int8 + flux2-vae（+ 未用 upsampler） | 12.00 |
| `animagine_xl_3_1` | 在用（可选） | animagine unet + clip_l + clip_g + sdxl_vae | 7.91 |
| `flux1_schnell` | **已禁用**（8GB 显存跑不动） | flux1 gguf + t5-q4 + clip_l + flux-vae | 9.26 |
| `minimax_h3` | **已登记不可用**（视频+音频，需 24GB 显存） | minimax unet + qwen3vl_4b + video_vae + audio_vae（文本编码器与 flux3 共用） | 27.71（并集只计一次 22.71） |
| 角色卡（13 张） | 在用 | 13 个 Hi3 角色 LoRA | 0.70 |
| `upscale` | 在用 | 4x-UltraSharp.pth | 0.06 |

> 结论：**「默认能力」= Illustrious（角色）+ qwen_image_2_1（默认）+ 13 角色 LoRA + bge-m3 + GSV Rosmontis + kokoro ≈ 25.7 GiB**；其余档案按需勾选。MiniMax-H3（27.71）与 flux1（9.26）在 8GB 机器上属于「下载也不可用」，L3 默认不勾选。

---

## 4. 总表

> 位置：`D:` = D 盘真实文件；`E:` = 经 junction 落在 `E:\comfy-models`；`AI:` = `D:\AI\ai-models`；`M:` = `D:\AI\models`；`G:` = GPT-SoVITS 目录。
> 许可标记：✅ 可再分发（保留声明）｜⚠️ 可再分发但有限制/需核对｜⛔ 不可再分发（受限许可或角色 IP）｜❓ 待确认。
> 用途分类映射（下文各小节标题/用途栏均按此标注）：

| 用途分类 | 对应资产 |
|---|---|
| **comfyui 底模** | `checkpoints`、`unet`、`diffusion_models`（含 `D:\AI\ai-models` 的主模型） |
| **comfyui LoRA** | `loras`（含角色 LoRA；MiniMax turbo LoRA 归此） |
| **其他（生图组件）** | `clip`、`text_encoders`、`vae`、`vae_approx`、`controlnet`、`ipadapter`、`clip_vision`、`sams`、`upscale_models`、`facerestore_models`、`detection`、`ultralytics`、`background_removal`、`insightface`、`animatediff_models`、`embeddings`、`clip_projections`、`facerestore_models` |
| **语音 GPT** | `GPT_weights_v*`、社区 `*/GPT_weights/*.ckpt` |
| **语音 SoVITS** | `SoVITS_weights_v*`、社区 `*/SoVITS_weights/*.pth`、GSV 基座 `s2Gv*/s2D*/vocoder` |
| **嵌入** | `bge-m3` |
| **其他（语音/工具）** | GSV 基座 BERT/HuBERT/G2PW/lid、kokoro、F5-TTS、vocos、whisper、emotion2vec、campplus、UVR5/paraformer/AP_BWE 工具权重 |

### 4.1 嵌入 / 文本记忆（分类：**嵌入**）

| 文件 | 体积 | 位置 | 用途 | 可能来源 | 许可 |
|---|---:|---|---|---|---|
| `bge-m3-st\`（HF 缓存布局：模型 blob 2165.93 MiB + tokenizer 16.31 + sentencepiece 4.83，共 40 文件） | **2.14 GiB** | M: | 记忆嵌入（`ops/memory/embed_server.py`、Graphiti 冒烟） | https://huggingface.co/BAAI/bge-m3 | ✅ MIT |

### 4.2 ComfyUI 生图资产（按目录逐文件；分类：**comfyui 底模 / comfyui LoRA / 其他（生图组件）**）

#### checkpoints（底模）｜分类：**comfyui 底模**
| 文件 | 体积 | 位置 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|---|
| `Illustrious-XL-v1.0.safetensors` | 6.46 GiB | D: | 生图底模：角色卡唯一支持 LoRA/角色注入的底模 | https://huggingface.co/OnomaAIResearch/Illustrious-XL-v1.0 | ⚠️ Fair AI Public License 1.0-SD（可再分发需附许可；商用条款受限） |

#### unet｜分类：**comfyui 底模**
| 文件 | 体积 | 位置 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|---|
| `flux1-schnell-Q4_K_S.gguf` | 6.32 GiB | E: | flux1_schnell 档案主模型（**已禁用**，8GB 跑不动） | https://huggingface.co/city96/FLUX.1-schnell-gguf | ✅ Apache-2.0（FLUX.1-schnell） |
| `animagine-xl-3.1_unet.safetensors` | 4.78 GiB | E: | animagine 档案主模型（通用二次元备选） | https://huggingface.co/cagliostrolab/animagine-xl-3.1 | ⚠️ Fair AI Public License 1.0-SD |

#### diffusion_models｜分类：**comfyui 底模**
| 文件 | 体积 | 位置 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|---|
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 19.53 GiB | E: | MiniMax H3 视频+音频生成主模型（**本机登记不可用**） | ❓ 待确认（社区 ComfyUI 重打包线索：https://huggingface.co/joeygambino/MiniMax-H3-comfy-native-fl2va ） | ❓ 待确认（须查 MiniMax 模型卡） |

#### clip｜分类：**其他（生图文本编码器）**
| 文件 | 体积 | 位置 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|---|
| `clip_g.safetensors` | 2.59 GiB | D: | SDXL 文本编码器（Illustrious/Animagine） | https://huggingface.co/laion/CLIP-ViT-bigG-14-laion2B-39B-b160k | ⚠️ MIT（open_clip）— 权重页许可待核对 |

#### text_encoders｜分类：**其他（生图文本编码器）**
| 文件 | 体积 | 位置 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|---|
| `qwen3vl_4b_int8_convrot.safetensors` | 5.00 GiB | E: | FLUX3 Klein 文本编码器；MiniMax H3 也登记复用 | https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy | ⚠️ Apache-2.0（Qwen3-VL-4B，BFL 仓库内声明） |
| `t5-v1_1-xxl-encoder-Q4_K_S.gguf` | 2.55 GiB | E: | FLUX.1 Schnell 文本编码器 | https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf | ✅ Apache-2.0（google/t5-v1_1-xxl） |
| `clip_l.safetensors` | 0.23 GiB | D: | SDXL/FLUX 文本编码器 | https://huggingface.co/openai/clip-vit-large-patch14 | ✅ MIT |

#### vae｜分类：**其他（生图 VAE）**
| 文件 | 体积 | 位置 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|---|
| `minimax_h3_video_vae_int8_convrot.safetensors` | 2.62 GiB | D: | MiniMax H3 视频 VAE | ❓ 待确认（同 H3 包） | ❓ 待确认 |
| `minimax_h3_audio_vae_fp32.safetensors` | 0.56 GiB | D: | MiniMax H3 音频 VAE | ❓ 待确认（同 H3 包） | ❓ 待确认 |
| `sdxl_vae.safetensors` | 0.31 GiB | D: | SDXL VAE（Animagine/旧工作流显式引用） | ❓ 待确认（SDXL 官方 VAE / madebyollin fp16-fix 之一） | ⚠️ 随 SDXL（CreativeML OpenRAIL++-M）或 MIT，待核对 |
| `flux-vae-bf16.safetensors` | 0.16 GiB | D: | FLUX.1 VAE | https://huggingface.co/black-forest-labs/FLUX.1-schnell | ✅ Apache-2.0 |

#### loras（33 文件 6.40 GiB）｜分类：**comfyui LoRA**
**① 角色卡必需（13 个，合计 0.70 GiB）**——来源均为 Civitai/社区，未核实原作者，**不可再分发**：

| 文件 | 体积 | 对应角色卡 |
|---|---:|---|
| `Theresa_Apocalypse28in1_Hi3_IL-03.safetensors` | 54.82 MiB | theresa |
| `Kiana_Kaslana24in1_Hi3_IL-03.safetensors` | 54.81 MiB | kiana |
| `FuHua24in1_Hi3_ILV2-03.safetensors` | 54.81 MiB | fuhua |
| `Bronya_Zaychik24in1_Hi3_IL-03.safetensors` | 54.80 MiB | bronya |
| `Durandal20in1_Hi3_IL-03.safetensors` | 54.79 MiB | durandal |
| `Elysia12in1_Hi3V7_IL-03.safetensors` | 54.79 MiB | elysia |
| `Griseo10in1_Hi3_ILV3-03.safetensors` | 54.79 MiB | griseo |
| `LiSushang_Hi3_ILV4-03.safetensors` | 48.61 MiB | lisushang |
| `Pardofelis_Hi3_ILV2-03.safetensors` | 48.61 MiB | pardofelis |
| `rosmontis_airydream_v0.3.safetensors` | 94.16 MiB | rosmontis |
| `Senti_Hi3_IL-05.safetensors` | 48.59 MiB | senti |
| `Aponia_Hi3_ILV2-03.safetensors` | 48.59 MiB | aponia |
| `VitaV86_Hi3_IL-03.safetensors` | 48.57 MiB | vita |

**② 其他 LoRA（20 个，合计 5.70 GiB）**——旧工作流/风格增强/未挂角色卡，L3 首批不需要：

| 文件（体积） | 说明 |
|---|---|
| `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors`（1.82 GiB）、`minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors`（1.82 GiB） | MiniMax H3 加速 LoRA，随 H3 包，本机不可用 |
| `xl_more_art-full_v1.safetensors`（0.67 GiB） | SDXL 风格 LoRA（Civitai，待确认） |
| `ip-adapter-faceid-plusv2_sdxl_lora.safetensors`（0.35 GiB） | IP-Adapter FaceID 配套 LoRA（依赖 insightface 非商用） |
| `AddMicroDetails_Illustrious_v6`（0.21 GiB）、`stabilizer_noobAI_ep11_v0.205`（0.08 GiB）、`Elysia-noob-Tanger`（0.11 GiB）、`Ely-000019`（0.21 GiB）、`anime-detailer-xl`（41.17 MiB）、`sdxl-extremely-detailed`（8.38 MiB）、`DynamicPose_slider`（8.38 MiB）、`kiana_kaslana-lora-nochekaiser`（36.14 MiB） | 画质/风格/姿态 LoRA，NoobAI/Illustrious 系，非角色卡依赖 |
| `KianaHoFin_Hi3_ILV3-03`（48.61 MiB）、`TeriSlep_Hi3_ILV4-03`、`DurandalDeaAnchora_Hi3_ILV3-03`、`FuHuaGaruda_Hi3_ILV2-04`、`GriseoSWM_Hi3_IL-03`、`VitaLonePlanetfarer_Hi3_ILV2-03`、`LiSusQuickTrip_Hi3_IL-03`（均约 48.6 MiB） | 同作者 Hi3 系列 10in1 LoRA，当前角色卡未使用 |

### 4.3 外部模型根 `D:\AI\ai-models`（ComfyUI extra_model_paths 挂载；分类：**comfyui 底模 / 其他（VAE/文本编码器）**）

| 文件 | 体积 | 位置 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|---|
| `Qwen-Image-2.1\diffusion_models\qwen_image_2.1_turbo_int8_convrot.safetensors` | 6.76 GiB | AI: | 默认画笔主模型（qwen_image_2_1） | ❓ 待确认（推测 Comfy-Org 重打包；上游 Qwen-Image 2.1） | ⚠️ Apache-2.0（Qwen-Image 系列） |
| `Qwen-Image-2.1\text_encoders\qwen3vl_8b_w4a8.safetensors` | 5.88 GiB | AI: | 同上文本编码器 Qwen3-VL-8B | ❓ 待确认 | ⚠️ Apache-2.0（Qwen3-VL-8B） |
| `Qwen-Image-2.1\vae\qwen_image_2.1_vae_bf16.safetensors` | 0.63 GiB | AI: | 同上 VAE | ❓ 待确认 | ⚠️ 随 Qwen-Image（Apache-2.0 待核对） |
| `FLUX3-Klein\diffusion_models\flux3-image-klein-int8-convrot.safetensors` | 5.43 GiB | AI: | flux3_klein 档案主模型 | https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy | ⛔ FLUX Kommunity License v1.0（**非商用**，<$5M 收入例外） |
| `FLUX3-Klein\text_encoders\qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors` | 6.26 GiB | AI: | FLUX3 提示词增强器（生产未用，agent 直接写 caption） | 同上（prompt upsample 子仓库） | ⚠️ Apache-2.0（upsampler 单独声明） |
| `FLUX3-Klein\vae\flux2-vae.safetensors` | 0.31 GiB | AI: | FLUX3 图像 VAE | 同上 | ⚠️ 随 FLUX（待核对） |
| `_meta\README.md` / workflows_*.json | <0.1 MiB | AI: | **来源证据**：BFL 官方 README 副本，含完整文件清单与许可说明 | 见上 | — |

### 4.4 其他 ComfyUI 资产（本次不逐条清单，供体积核对；分类：**其他（生图组件）**）

| 目录 | 文件数 | 体积 | 用途/备注 |
|---|---:|---:|---|
| `controlnet`（E:） | 7 | 11.35 GiB | 旧工作流用：OpenPoseXL2 4.66、union-promax 2.34、canny 2.22、SD1.5 depth/canny/openpose 各 0.67。生产档案不依赖 |
| `sams`（E:） | 1 | 2.39 GiB | `sam_vit_h_4b8939` 抠图/分割（SAM，Apache-2.0） |
| `clip_vision`（E:） | 2 | 2.35 GiB | CLIP-ViT-H-14（IP-Adapter/风格参考） |
| `ipadapter`（E:） | 2 | 2.17 GiB | FaceID plusv2 1.39 + plus vit-h 0.79（panel-faceid 工作流，已标记不支持） |
| `animatediff_models`（E:） | 1 | 1.69 GiB | `mm_sd_v15_v2.ckpt`，当前无工作流使用 |
| `clip_projections`（E:） | 1 | 0.47 GiB | mmh3-4b ClipProj（MiniMax H3 配套，疑） |
| `facerestore_models`（E:） | 1 | 0.35 GiB | codeformer |
| `insightface`（E:） | 6 | 0.32 GiB | buffalo_l（**非商用研究许可**，FaceID 依赖） |
| `facedetection`（E:） | 3 | 0.18 GiB | Resnet50 / parsenet / yolov5n-face |
| `background_removal`（E:） | 2 | 0.17 GiB | isnet-general-use（remove-bg 工作流可用） |
| `upscale_models`（D:） | 4 | 0.19 GiB | 4x-UltraSharp（upscale 工作流）、4x-AnimeSharp、RealESRGAN_x4 |
| `vae_approx`（D:） | 9 | 0.02 GiB | TAESD 预览解码器 |
| `detection` / `ultralytics`（E:） | 各 1 | 0.10 GiB | face_yolov8m（faceid 工作流） |
| `embeddings`（D:） | 3 | <0.01 GiB | EasyNegative、bad-hands-5（Civitai，待确认） |

### 4.5 语音：GPT-SoVITS 官方基座（`G:\GPT_SoVITS\pretrained_models` + `text\G2PWModel`，3.41 GiB / 20 文件；分类：**语音 GPT / 语音 SoVITS / 其他（语音组件）**）

| 文件 | 体积 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|
| `gsv-v4-pretrained\s2Gv4.pth` | 733.40 MiB | **v4 SoVITS 基座（生产在用）** | https://huggingface.co/lj1995/GPT-SoVITS | ⚠️ 代码 MIT；权重许可待核对 |
| `s2Gv3.pth` | 733.41 MiB | v3 SoVITS 基座（遗留） | 同上 | 同上 |
| `chinese-roberta-wwm-ext-large\pytorch_model.bin` | 621.06 MiB | BERT 文本前端（必需） | https://huggingface.co/hfl/chinese-roberta-wwm-ext-large | ⚠️ Apache-2.0 |
| `text\G2PWModel\g2pW.onnx` | 605.79 MiB | 中文多音字前端（必需） | https://github.com/GitYCC/g2pW | ⚠️ Apache-2.0 |
| `models--nvidia--bigvgan_v2_24khz_100band_256x\bigvgan_generator.pt` | 214.75 MiB | 声码器（v2 路径用） | https://huggingface.co/nvidia/bigvgan_v2_24khz_100band_256x | ⚠️ MIT |
| `chinese-hubert-base\pytorch_model.bin` | 180.06 MiB | 特征提取（必需） | https://huggingface.co/TencentGameMate/chinese-hubert-base | ⚠️ 待核对 |
| `gsv-v2final-pretrained\s1bert25hz-5kh...ckpt` | 148.12 MiB | v2 GPT 基座（遗留） | lj1995/GPT-SoVITS | ⚠️ 待核对 |
| `s1v3.ckpt` | 148.09 MiB | **v4 GPT 基座（生产在用）** | 同上 | ⚠️ 待核对 |
| `s1bert25hz-2kh-longer...ckpt` | 147.91 MiB | v2 GPT 基座（遗留） | 同上 | ⚠️ 待核对 |
| `fast_langdetect\lid.176.bin` | 125.19 MiB | 语种识别 | fasttext lid.176（Meta） | ⚠️ CC BY-SA 3.0 |
| `gsv-v2final-pretrained\s2G2333k.pth` / `s2D2333k.pth` | 101.12 / 89.20 MiB | v2 生成器/判别器（遗留） | 同上 | ⚠️ 待核对 |
| `s2G488k.pth` / `s2D488k.pth` | 101.06 / 89.20 MiB | v1/v2 生成器/判别器（遗留） | 同上 | ⚠️ 待核对 |
| `text\G2PWModel_1.1.zip` | 83.06 MiB | G2PW 压缩包（与目录重复） | 同上 | ⚠️ Apache-2.0 |

> v4 生产**必需子集**：`s1v3.ckpt` + `s2Gv4.pth` + `vocoder.pth` + `chinese-hubert-base` + `chinese-roberta-wwm-ext-large` + `bigvgan` + `G2PWModel` + `lid.176.bin` = **2.62 GiB**。其余 ~0.79 GiB 为 v1/v2/v3 遗留，可裁剪。

### 4.6 语音：本机自训角色权重（`G:` 下 4 个权重目录，7.86 GiB / 73 文件，⛔ 不可再分发；分类：**语音 GPT / 语音 SoVITS**，目录名即分类：`GPT_weights_*` → 语音 GPT，`SoVITS_weights_*` → 语音 SoVITS）

| 目录 | 文件数 | 体积 | 角色 / epoch | 说明 |
|---|---:|---:|---|---|
| `GPT_weights_v2` | 29 | 4.20 GiB | yae×12（e1–e12）、yae2×5、yae3×4、kiana×4、teresa×4，各 148.12 MiB | v2 GPT 微调，旧角色实验 |
| `SoVITS_weights_v2` | 31 | 2.45 GiB | 同上 5 角色，各 81.07 MiB | v2 SoVITS 微调 |
| `GPT_weights_v4` | 4 | 0.58 GiB | teresa4 e5/e10/e15/e20，各 148.12 MiB | **德丽莎 v4 回滚方案所用 GPT** |
| `SoVITS_weights_v4` | 9 | 0.63 GiB | teresa4（rank32）×3、teresa44×6，各 72.05 MiB | v4 LoRA 权重，生产已弃用（当前用 v4 基座） |
| `GPT_weights` / `GPT_weights_v3` / `SoVITS_weights` / `SoVITS_weights_v3` | 0 | 0 | — | 空目录 |

> 训练脚本在仓库内可查：`ops/teresa*_train.py`、`ops/yae_*`、`ops/kiana_stepB_train.py`。**这些权重源自角色音色，迁移可用、分发不可。**

### 4.7 语音：社区角色权重（`D:\AI\community_models`，0.53 GiB；分类：**语音 GPT / 语音 SoVITS**）

| 文件 | 体积 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|
| `Rosmontis\GPT_weights\Rosmontis-e10.ckpt` | 147.90 MiB | **生产 TTS 音色（迷迭香）GPT** | ❓ 待确认（社区分享模型，疑 AI-Hobbyist 渠道） | ⛔ 角色 IP（明日方舟/鹰角）+ 社区权重未授权再分发 |
| `Rosmontis\SoVITS_weights\Rosmontis_e200_s5600.pth` | 81.00 MiB | 生产音色 SoVITS | 同上 | ⛔ 同上 |
| `Rosmontis\SoVITS_weights\Rosmontis_e10_s280.pth` | 81.00 MiB | 备用/早期 SoVITS | 同上 | ⛔ 同上 |
| `Theresa\GPT_weights\Theresa-e20.ckpt` | 147.90 MiB | 德丽莎回滚 GPT | 同上 | ⛔ 角色 IP（崩坏3/米哈游） |
| `Theresa\SoVITS_weights\Theresa_e100_s2700.pth` | 81.00 MiB | 德丽莎回滚 SoVITS | 同上 | ⛔ 同上 |
| `Rosmontis\Data\问候.wav`（319 KB，生产参考音）、`交谈1.wav`；`Theresa\Data\*.wav`；`*/out/*.wav` 试听样音 | ~8 MiB | 参考音/试听 | 社区包附带 | ⛔ 含角色语音 |

> 依据：`docs/VOICE-TTS-DECISIONS.md` — 生产=Rosmontis，回滚=Theresa；参考音固定 `Rosmontis\Data\问候.wav`。

### 4.8 语音：TTS / ASR / 声纹 / 评测（`D:\AI\models`，7.25 GiB；分类：**其他（语音/评测）**）

| 文件/目录 | 体积 | 用途 | 来源 | 许可 |
|---|---:|---|---|---|
| `faster-whisper-large-v3\`（model.bin 2944.26 MiB + tokenizer/vocab） | 2.87 GiB | ASR：语音评测 WER、字幕/数据切片（`ops/voice_eval/*`、`ops/yae_*`） | https://huggingface.co/Systran/faster-whisper-large-v3 | ✅ MIT（OpenAI Whisper large-v3 权重） |
| `modelscope\iic\emotion2vec_plus_large\model.pt` | 1.81 GiB | 情绪识别/标注（`ops/voice_eval/label_emotions.py`） | https://modelscope.cn/models/iic/emotion2vec_plus_large | ❓ 待确认（ModelScope 页面） |
| `F5-TTS\F5TTS_v1_Base\model_1250000.safetensors` | 1.26 GiB | F5-TTS 引擎权重（网关 `f5` 引擎，非默认） | https://huggingface.co/SWivid/F5-TTS | ⛔ CC-BY-NC-4.0（非商用） |
| `kokoro\kokoro-v1_1-zh.pth` | 319.54 MiB | kokoro 中文音色（生产默认 TTS） | https://huggingface.co/hexgrad/Kokoro-82M | ✅ Apache-2.0 |
| `kokoro\kokoro-v1_0.pth` | 319.50 MiB | kokoro 基座 | 同上 | ✅ Apache-2.0 |
| `kokoro\hf_home\models\kokoro-v1_0.pth` | 319.50 MiB | **重复副本** | 同上 | ✅ Apache-2.0 |
| `kokoro\hf_home\hub\...\blobs\496dba...` | 319.50 MiB | **重复副本（HF cache）** | 同上 | ✅ Apache-2.0 |
| `vocos-mel-24khz\pytorch_model.bin` | 51.85 MiB | F5-TTS 声码器 | https://huggingface.co/charactr/vocos-mel-24khz | ❓ 待确认 |
| `modelscope\iic\speech_campplus_sv_zh-cn_16k-common\campplus_cn_common.bin` | 26.74 MiB | 说话人相似度评测（`ops/voice_eval/metrics.py`） | ModelScope iic | ❓ 待确认（疑 Apache-2.0） |

### 4.9 语音：GPT-SoVITS 训练/预处理工具权重（`G:\tools`，1.92 GiB，非推理必需；分类：**其他（工具）**）

| 目录 | 体积 | 内容 | 许可 |
|---|---:|---|---|
| `tools\asr`（35 文件） | 1.11 GiB | paraformer-large（839.71 MiB）+ CT-Transformer 标点（278.45 MiB）+ 其他 | ❓ 待确认（FunASR/ModelScope） |
| `tools\uvr5`（70 文件） | 0.70 GiB | UVR5 人声分离权重（bs_roformer 304.94 MiB、VR-DeEcho×3、HP2/HP5、onnx dereverb） | ❓ 待确认（UVR GUI 生态，各权重来源不同） |
| `tools\AP_BWE_main`（13 文件） | 0.11 GiB | 24k→48k 带宽扩展（g_24kto48k.zip 113.58 MiB） | ❓ 待确认 |

---

## 5. 按用途汇总体积

### 5.1 总量表

| 用途 | 体积 | 构成 |
|---|---:|---|
| **文本记忆必需（嵌入）** | **2.14 GiB** | bge-m3 |
| **生图** | **104.54 GiB** | 底模 49.28｜文本编码器 22.51｜VAE 4.59｜LoRA 6.40｜控制/视觉辅助 21.55｜放大/近似 0.21 |
| **语音** | **20.96 GiB** | GSV 基座 3.41｜GSV 自训 7.86｜GSV 工具 1.92｜社区角色 0.53｜TTS 2.53｜ASR/情绪 4.69｜声纹 0.03 |
| **其他** | 0 | ComfyUI/GSV 运行时（6.98 + 7.14 GiB）属组件包，不计入模型 |
| **合计** | **≈127.64 GiB** | 分项四舍五入，合计 ±0.02 |

### 5.2 按「下载器勾选档位」汇总（建议 L3 采用）

| 档位 | 增量内容 | 生图 | 语音 | 记忆 | 合计 |
|---|---|---:|---:|---:|---:|
| **A. 极简核心（无本地模型）** | 仅 LLM API | 0 | 0 | 0 | **0** |
| **B. 记忆（标准）** | bge-m3 | — | — | 2.14 | **2.14** |
| **C. 默认能力（推荐首装）** | Illustrious 6.46 + 13 角色 LoRA 0.70 + qwen_image_2_1 13.27；GSV v4 基座 2.62 + Rosmontis 0.24 + kokoro 0.30 | 20.43 | 3.15 | 2.14 | **25.72** |
| **C+D. + 生图备选档案** | + animagine 7.91 + flux3 10.74（unet+clip+vae）+ flux1 9.25 | 48.33 | 3.15 | 2.14 | **53.62** |
| **C+D+E. + 视频能力** | + MiniMax H3 专属 3 文件 22.71 + turbo LoRA 3.64（文本编码器复用 flux3；需 ≥24GB 显存） | 74.68 | 3.15 | 2.14 | **79.97** |
| **C+F. + 语音评测/训练依赖** | + whisper 2.87 + emotion2vec 1.81 + F5 1.26 + campplus 0.03 + vocos 0.05 | 20.43 | 9.17 | 2.14 | **31.74** |
| **C+G. + 视觉辅助/未用增项** | + controlnet/ipadapter/sam/clip_vision/animatediff/insightface/检测/抠图/放大合计 21.76 + FLUX3 未用 upsampler 6.26 + 遗留 LoRA 2.06（分项四舍五入） | 50.29 | 3.15 | 2.14 | **≈55.6** |
| **C+H. + 仅迁移项** | + GSV 自训 7.86 + GSV 工具 1.92 + GSV 旧基座 0.79 + Theresa 回滚 0.24 + kokoro 重复副本 0.92 + 社区试听样音 0.05 | 20.43 | 14.93 | 2.14 | **≈37.5** |
| **全部现有资产** | 以上并集（去重） | **104.54** | **20.96** | **2.14** | **≈127.64** |

> ⚠️ 「C. 默认能力」的 20.43 生图里已含 13 角色 LoRA；角色卡依赖的底模只有 Illustrious，`sdxl_vae` 仅 Animagine 档案需要（D 档）。
> ⚠️ `flux3_klein` 档案在 `models.json` 里只要 unet + clip + vae 三件（10.74）；`qwen3.5_9b_flux3_prompt_upsampler`（6.26）当前生产不用（agent 直接写 caption），体积计入 G 档。
> ⚠️ `minimax_h3` 的文本编码器 `qwen3vl_4b_int8` 与 flux3 共用，并集里只算一次；E 档按「已装 D 档」叠加。

---

## 6. 重复、冗余与可裁剪

| 项 | 现状 | 建议 | 可省 |
|---|---:|---|---:|
| kokoro 权重 4 份（顶目录 2：v1_0/v1_1-zh + hf_home/models 1 + hf_home/hub blob 1） | 4×319.5 MiB | 只留 1 份 + 统一 `models/tts/kokoro` | ~0.90 GiB |
| GSV v1/v2/v3 遗留基座（s2Gv3、v2final、s2G488k/s2D488k、s1bert2kh、G2PW zip） | ~1.41 GiB | v4 生产只需 §4.5 必需子集；保留 v2 供回滚则留 338 MiB | ~1.07 GiB |
| GSV 自训 73 个 epoch 权重 | 7.86 GiB | 每角色只保留生产 epoch + 1 备份 epoch（约 6–9 个文件） | ~6.5 GiB |
| 旧工作流 LoRA（20 个） | 5.70 GiB | 仅保留 minimax（若装 H3）；其余按需 | ~2.06 GiB |
| MiniMax H3（专属 3 文件 22.71 + turbo LoRA 3.64；文本编码器与 flux3 共用） | 已装但 8GB 不可用 | 默认不下载；`available:false` 在下载器中灰显 | 26.35 GiB |
| flux1_schnell 栈（9.26） | 已禁用 | 默认不下载，≥12GB 显存可选 | 9.26 GiB |
| `G2PWModel_1.1.zip` | 83 MiB | 与目录重复，可不随下载 | 0.08 GiB |
| ComfyUI junction 与 `E:\comfy-models` | 同一份数据两处路径 | L3 取消 junction，统一 models 根；避免迁移时重复拷贝 | 59.97 GiB 误拷风险 |

---

## 7. 生产档案 ↔ 模型文件 ↔ manifest id 映射

`D:\AI\comic\workflows\models.json` 是运行时权威注册表；L3 下载器落盘后应自动生成/合并该文件（不建议手工维护两套）。

| profile（models.json id） | 依赖 manifest id | 状态 |
|---|---|---|
| `anime_illustrious` | `comfy-illustrious-xl-v1` + `comfy-lora-hi3`（13 个 BYO） | 在用/默认角色 |
| `qwen_image_2_1` | `comfy-qwen-image-2.1`（3 文件） | **默认画笔** |
| `flux3_klein` | `comfy-flux3-klein`（3 文件） | 在用/可选 |
| `animagine_xl_3_1` | `comfy-animagine-xl-3.1` + `comfy-clip-l` + `comfy-clip-g` + `comfy-sdxl-vae` | 在用/可选 |
| `flux1_schnell` | `comfy-flux1-schnell`（4 文件） | 已禁用 |
| `minimax_h3` | `minimax-h3`（3 文件；文本编码器复用 `comfy-qwen3vl-4b-int8`） | 已登记不可用 |
| `upscale` | `comfy-upscale-4x-ultrasharp` | 在用 |
| `removebg` | `comfy-isnet-general-use` | 可用 |
| 角色卡 13 张 | `comfy-lora-hi3`（BYO） | 在用 |
| TTS 默认 kokoro | `tts-kokoro` | 在用 |
| TTS mobius（克隆） | `gsv-pretrained-v4` + `gsv-voice-rosmontis` | 在用 |
| TTS 回滚德丽莎 | `gsv-pretrained-v4` + `gsv-voice-theresa` | 回滚 |
| 记忆 | `embed-bge-m3` | 用在冒烟，未接生产检索 |

---

## 8. 建议 L3 `models` 目录布局

```
%MOBIUS_ROOT%\models\
├─ embed\
│  └─ bge-m3\                      # BAAI/bge-m3（记忆必需）
├─ comfyui\                        # 对应 ComfyUI\models\*（取消 junction，全部真实落盘）
│  ├─ checkpoints\                 # Illustrious-XL-v1.0.safetensors
│  ├─ unet\                        # animagine unet / flux1-schnell-q4
│  ├─ diffusion_models\            # minimax_h3 / qwen_image_2.1 / flux3-image-klein
│  ├─ text_encoders\               # clip_l / t5-xxl-q4 / qwen3vl_4b / qwen3vl_8b / upsampler
│  ├─ clip\                        # clip_g
│  ├─ vae\                         # sdxl_vae / flux-vae / flux2-vae / qwen vae / minimax vae
│  ├─ vae_approx\                  # TAESD 预览（约 20 MiB）
│  ├─ loras\
│  │  ├─ characters\               # 13 个 Hi3 角色 LoRA（BYO/迁移，不下载）
│  │  └─ style\                    # 风格/画质 LoRA（可选，BYO）
│  ├─ controlnet\  ipadapter\  clip_vision\  sams\
│  ├─ upscale_models\  facerestore_models\  detection\  ultralytics\
│  ├─ background_removal\  insightface\  animatediff_models\
│  └─ embeddings\
├─ gsv\                            # GPT-SoVITS
│  ├─ pretrained\                  # v4 必需子集（s1v3/s2Gv4/vocoder/hubert/roberta/bigvgan/G2PW/lid）
│  ├─ legacy\                      # v1/v2/v3 基座（可选）
│  └─ voices\
│     ├─ rosmontis\                # GPT + SoVITS + 参考音（BYO，不可分发）
│     └─ theresa\                  # 回滚（BYO，不可分发）
├─ tts\
│  ├─ kokoro\                      # kokoro-v1_0/v1_1-zh
│  ├─ f5-tts\                      # F5TTS_v1_Base（可选，非商用）
│  └─ vocos\                       # vocos-mel-24khz（F5 声码器）
├─ asr\
│  ├─ faster-whisper-large-v3\     # 评测/切片（可选）
│  └─ emotion2vec_plus_large\      # 情绪标注（可选）
├─ sv\
│  └─ campplus\                    # 说话人相似度（可选）
└─ cache\                          # 下载缓存（可导出离线复用；不随包）
```

设计要点：

1. `models\comfyui` 的目录名与 ComfyUI 约定一致，`extra_model_paths.yaml` 只需一行指向它；**qwen_image_2_1 / flux3_klein 不再外挂 `D:\AI\ai-models`**，改为 `models\comfyui\...` 下子目录 + 两个 profile 的 `base_path` 覆盖（或直接并入主目录并去掉 extra yaml）。
2. `models\gsv\pretrained` 只放 v4 必需子集；`legacy` 允许用户不装。
3. `cache` 与 `models` 分离：下载缓存可导出/复用，卸载模型不删缓存。
4. 所有路径只允许 `%MOBIUS_ROOT%` 相对路径（与 L3 根目录契约一致），旧机迁移用 `robocopy /MIR` 前先解 junction。

---

## 9. Manifest 条目草案

约定：`id` 全局唯一；`version` 用上游版本或 `2026.11` 基线（精确 revision 待定）；`sha256` 一律 **待计算**（下载首发版前用 `Get-FileHash -Algorithm SHA256` 离线批量生成后回填）；`size` 为字节数；`required` 相对「标准安装」；`kind` 区分 `download` / `byo`（仅迁移，不提供 URL）。

### 9.1 可下载条目（官方/原始来源）

| id | version | size (B) | sha256 | license | source | 落盘 | required |
|---|---|---:|---|---|---|---|---|
| `embed-bge-m3` | 2026.11 | 2,271,145,830（+tokenizer 22,186,310） | 待计算 | MIT | HF `BAAI/bge-m3` | `embed/bge-m3` | standard（记忆） |
| `comfy-illustrious-xl-v1` | v1.0 | 6,938,040,736 | 待计算 | Fair AI Public License 1.0-SD | HF `OnomaAIResearch/Illustrious-XL-v1.0` | `comfyui/checkpoints` | yes（生图） |
| `comfy-qwen-image-2.1` | 2026.11 | 14,244,402,881（3 文件） | 待计算 | Apache-2.0（Qwen-Image） | ❓ 待确认（Comfy-Org 重打包） | `comfyui/{diffusion_models,text_encoders,vae}` | yes（默认画笔） |
| `comfy-flux3-klein` | 2026.11 | 12,888,176,851（3 文件，含未用 upsampler；profile 另需 `comfy-qwen3vl-4b-int8`） | 待计算 | ⛔ FLUX Kommunity（非商用） | HF `black-forest-labs/flux-3-image-klein-comfy`（只链接） | `comfyui/...` | optional |
| `comfy-animagine-xl-3.1` | 3.1 | 5,135,174,520 | 待计算 | Fair AI Public License 1.0-SD | HF `cagliostrolab/animagine-xl-3.1` | `comfyui/unet` | optional |
| `comfy-flux1-schnell` | 2026.11 | 9,936,327,102（gguf+t5+clip_l+vae，4 文件） | 待计算 | Apache-2.0 | HF `city96/FLUX.1-schnell-gguf` 等 | `comfyui/...` | optional（≥12GB） |
| `comfy-minimax-h3` | 2026.11 | 24,386,699,608（3 文件；text encoder 复用） | 待计算 | ❓ 待确认 | ❓ 待确认 | `comfyui/...` | optional（≥24GB） |
| `comfy-clip-l` | — | 246,144,152 | 待计算 | MIT | HF `openai/clip-vit-large-patch14` | `comfyui/text_encoders` | yes（animagine/flux1） |
| `comfy-clip-g` | — | 2,778,702,264 | 待计算 | ⚠️ MIT（权重页待核对） | HF `laion/CLIP-ViT-bigG-14-laion2B-39B-b160k` | `comfyui/clip` | optional |
| `comfy-sdxl-vae` | — | 334,641,162 | 待计算 | ❓ 待确认 | ❓ 待确认 | `comfyui/vae` | optional（animagine） |
| `comfy-flux-vae` | — | 167,664,710 | 待计算 | Apache-2.0 | HF `black-forest-labs/FLUX.1-schnell` | `comfyui/vae` | optional |
| `comfy-qwen3vl-4b-int8` | 2026.11 | 5,374,169,456 | 待计算 | Apache-2.0 | HF `black-forest-labs/flux-3-image-klein-comfy` | `comfyui/text_encoders` | optional（flux3/minimax） |
| `gsv-pretrained-v4` | 20250529 | 2,813,786,687（8 文件） | 待计算 | ⚠️ 代码 MIT；权重待核对 | HF `lj1995/GPT-SoVITS` + 各上游 | `gsv/pretrained` | yes（语音） |
| `tts-kokoro` | v1.0/v1.1-zh | 327,212,226 | 待计算 | Apache-2.0 | HF `hexgrad/Kokoro-82M` | `tts/kokoro` | yes（语音） |
| `tts-f5-base` | v1 | 1,348,435,761 | 待计算 | ⛔ CC-BY-NC-4.0 | HF `SWivid/F5-TTS` | `tts/f5-tts` | optional |
| `vocos-mel-24khz` | — | 54,365,991 | 待计算 | ❓ 待确认 | HF `charactr/vocos-mel-24khz` | `tts/vocos` | optional |
| `asr-faster-whisper-large-v3` | v3 | 3,087,284,237 | 待计算 | MIT | HF `Systran/faster-whisper-large-v3` | `asr/...` | optional（评测） |
| `asr-emotion2vec-plus-large` | — | 1,945,790,254 | 待计算 | ❓ 待确认 | ModelScope `iic/emotion2vec_plus_large` | `asr/...` | optional（评测） |
| `sv-campplus` | — | 28,036,335 | 待计算 | ❓ 待确认 | ModelScope `iic/speech_campplus_sv...` | `sv/campplus` | optional（评测） |
| `comfy-upscale-4x-ultrasharp` | — | 66,961,958 | 待计算 | ❓ 待确认 | ❓ 待确认（Civitai/upscale wiki） | `comfyui/upscale_models` | yes（upscale 工作流） |
| `comfy-isnet-general-use` | — | 178,648,008 | 待计算 | ❓ 待确认 | ❓ 待确认（remove-bg 节点自带） | `comfyui/background_removal` | optional |
| `comfy-lora-minimax-turbo` | v1.0 | 3,912,385,992（2 文件） | 待计算 | ❓ 待确认 | ❓ 待确认 | `comfyui/loras` | optional（H3） |
| `comfy-sam` | — | 2,564,432,288 | 待计算 | Apache-2.0 | HF `facebook/sam-vit-huge` / 官方权重 | `comfyui/sams` | optional |
| `comfy-clip-vision` | — | 2,528,373,448 | 待计算 | ⚠️ MIT（open_clip，权重页待核对） | HF `laion/CLIP-ViT-H-14-laion2B-s32B-b79K` | `comfyui/clip_vision` | optional |

### 9.2 仅迁移条目（BYO / 不可分发，不提供 URL）

| id | size (B) | 说明 | 许可 |
|---|---:|---|---|
| `comfy-lora-hi3` | 755,736,760（13 文件） | 13 张角色卡依赖的 Hi3 角色 LoRA；需用户从 Civitai/原发布页自行获取或从旧机迁移 | ⛔ Civitai 条款 + 角色 IP（米哈游）；**不得随包/镜像** |
| `gsv-voice-rosmontis` | 240,019,574（GPT+SoVITS）+ 参考音 | 生产克隆音色（迷迭香） | ⛔ 角色 IP（鹰角）+ 社区权重 |
| `gsv-voice-theresa` | 240,018,602（GPT+SoVITS）+ 参考音 | 回滚音色（德丽莎） | ⛔ 角色 IP（米哈游） |
| `gsv-selftrain-epochs` | 8,440,544,677（73 文件） | yae/kiana/teresa 系列自训 epoch（v2/v4） | ⛔ 自训 + 角色 IP |
| `comfy-lora-legacy` | 2,205,572,640（18 文件，不含 MiniMax） | 风格/画质/10in1 遗留 LoRA | ⛔/❓ Civitai 各原作者条款 |
| `insightface-buffalo-l` | ≈341,000,000（6 文件） | FaceID 依赖 | ⛔ InsightFace 非商用研究许可（不可分发） |
| `comfy-controlnet-pack` | ≈12,188,000,000（7 文件；精确字节待补） | OpenPoseXL2/union-promax/canny/SD15 三件套 | ⚠️/❓ 各自许可（lllyasviel openrail、xinsir 待核对） |
| `comfy-ipadapter-pack` | 2,335,072,693（2 文件） | FaceID plusv2 + plus vit-h | ⛔/❓ FaceID 研究用途（依赖 insightface） |

### 9.3 manifest 条目示例（JSON，供下载器实现参考）

```json
{
  "version": 1,
  "components": {
    "model-bge-m3": {
      "type": "model",
      "version": "2026.11",
      "kind": "download",
      "size": 2293332140,
      "sha256": "待计算",
      "license": { "id": "MIT", "url": "https://huggingface.co/BAAI/bge-m3" },
      "source": {
        "provider": "huggingface",
        "repo": "BAAI/bge-m3",
        "files": ["model.safetensors", "tokenizer.json", "sentencepiece.bpe.model"]
      },
      "required": "standard",
      "install": { "type": "model", "target": "models/embed/bge-m3" }
    },
    "comfy-lora-hi3": {
      "type": "model",
      "version": "byo",
      "kind": "byo",
      "size": 755736760,
      "sha256": null,
      "license": { "id": "Civitai+CharacterIP", "redistribute": false },
      "source": { "provider": "local", "hint": "从旧机迁移或从 Civitai 原页获取" },
      "required": "standard",
      "install": { "type": "model", "target": "models/comfyui/loras/characters" }
    }
  }
}
```

> 建议：对 `kind=byo` 条目，下载器只做「迁移/指定路径/校验」，UI 显示原页链接与许可提示，**永不自动下载**；对 `redistribute=false` 的条目，L3 发布包与镜像中一律不得出现。

---

## 10. 许可与再分发清单

### 10.1 ⛔ 不适合再分发（只允许迁移/用户自取，禁止随包/镜像/内置 URL）

| 资产 | 体积 | 原因 |
|---|---:|---|
| 13 个 Hi3 角色 LoRA（`comfy-lora-hi3`） | 0.70 GiB | Civitai 社区模型，原页条款通常禁止再分发；角色 IP 属米哈游；且不允许商用 |
| 其余 20 个 LoRA（社区/风格 + MiniMax turbo） | 5.70 GiB | Civitai 条款、原作者未授权；`ip-adapter-faceid LoRA` 还受 insightface 限制；MiniMax turbo 许可随上游待确认 |
| Rosmontis / Theresa 社区 GSV 权重 | 0.53 GiB | 角色 IP（鹰角/米哈游）+ 社区分享模型，不可再分发 |
| 本机自训 GSV 73 个 epoch 权重 | 7.86 GiB | 训练数据为角色语音；权重不可分发 |
| FLUX3-Klein（3 文件） | 12.00 GiB | FLUX Kommunity License：非商用；商用需 Qualifying User（<$5M 收入）或另行授权 |
| F5-TTS v1 Base | 1.26 GiB | CC-BY-NC-4.0（非商用） |
| MiniMax H3（专属 3 文件 22.71 + turbo LoRA 3.64；文本编码器复用 flux3） | 26.35 GiB | 许可待确认；视频/音频生成模型通常附带严格条款 |
| InsightFace buffalo_l | 0.32 GiB | InsightFace 模型为「非商用研究」许可 |
| 4x-UltraSharp / codeformer / isnet / EasyNegative / bad-hands-5 | ~0.3 GiB | 来源/许可待确认，按「不可再分发」保守处理 |

### 10.2 ⚠️ 可再分发但需保留声明/有限制

| 资产 | 许可 | 义务 |
|---|---|---|
| Illustrious-XL v1.0、Animagine XL 3.1 | Fair AI Public License 1.0-SD | 再分发需附许可副本、保留署名与使用限制；商用有额外条款 |
| SDXL VAE / sdxl_vae | CreativeML OpenRAIL++-M（待核对） | 附许可与使用限制声明 |
| GPT-SoVITS 预训练权重 | 代码 MIT；权重待核对 | 附 MIT 与上游说明；`lj1995/GPT-SoVITS` 页面为准 |
| CLIP / T5 / Qwen3-VL / kokoro / whisper / bge-m3 / SAM | MIT / Apache-2.0 | 保留版权与许可声明 |
| bge-m3 | MIT | — |

### 10.3 ✅ 可直接由下载器从官方源获取（首推）

`bge-m3`、`Illustrious-XL-v1.0`、`Animagine XL 3.1 unet`、`clip_l`、`clip_g`、`t5-v1_1-xxl` GGUF、`FLUX.1-schnell` GGUF + VAE、`Qwen3-VL` 编码器、`kokoro`、`faster-whisper-large-v3`、SAM、`4x-Upscale`（待核源）。

> 即便允许再分发，L3 也建议**只链接原始发布页**（HuggingFace/Civitai/官方仓库），不自建镜像；镜像仅用于必要的组件包（runtime），模型交给原始源 + SHA256 校验。

---

## 11. 缺口与待确认

1. **旧工作流引用的 4 个模型文件在已盘点根中不存在**（工作流 JSON 里仍写着，L3 迁移后会直接失败）：
   - `animagine-xl-3.1.safetensors`（SDXL 版 checkpoint，现存的是 `_unet` 版）→ 生产 `comic-animagine.json` 用 unet 版，不受影响；旧 `0.json`/`三角色一致性` 受影响。
   - `NoobAI-XL-v1.1.safetensors`（旧底模，未找到）
   - `4x_NMKD-Superscale-SP_178000_G.pth`（旧放大模型，未找到）
   - `depth_anything_vitl14.pth`（`ComfyUI工作流-动漫4x高清.json` 引用，未找到）
   → 建议 L3 首批不迁移这些旧工作流；如需保留，先在旧机定位（可能在未盘点盘）。
2. **来源待确认（影响许可判定，需 Lead/人工核实）**：MiniMax H3 全家族、Qwen-Image 2.1 三个文件、`sdxl_vae`、`4x-UltraSharp`、`codeformer`、`isnet`、`EasyNegative`、`bad-hands-5`、emotion2vec / campplus / paraformer / UVR5 的权重许可、社区 Rosmontis/Theresa 原始发布页、13 个 Hi3 LoRA 的 Civitai 页面与作者。
3. **SHA256 全部待计算**：建议在旧机（有全部文件）用 `Get-FileHash -Algorithm SHA256` 离线跑一批，回填 manifest；40GB 级不阻塞，按 2GB/卷或逐文件计算。
4. **`D:\AI\ai-models` 是否纳入下载器**：目前是 ComfyUI 外挂根，若 L3 要「Qwen/FLUX3 默认可用」，必须把 25.27 GiB 纳入清单与落盘；否则默认画笔会在新机上缺失（降级到 Illustrious）。需 Lead 决策。
5. **junction 迁移风险**：见 §3.1，迁移脚本必须先处理。
6. **8GB 显存机器上的「下载也不会用」项**：flux1_schnell（已禁用）、minimax_h3（24GB）、F5-TTS（可选慢）——下载器 UI 应按 GPU 显存给提示，默认不勾选。
7. **`models.json` 同步**：manifest 落盘后应生成 `workflows/models.json` 的 `models_needed` 映射，避免两处漂移。

---

## 12. 给 Lead 的下一步建议

1. **先定档位**：采用 §5.2 的 A–H 分层；默认安装建议 **B（记忆 2.14）+ C（默认能力 25.7）**，生图备选/视频/评测/仅迁移项全部 opt-in。
2. **再定清单边界**：确认 `D:\AI\ai-models`（25.27 GiB）是否进入 L3 模型下载器（否则默认画笔失效）。
3. **许可先行**：把 §10.1 的 `kind=byo` 条目从可下载清单中剔除；`FLUX3`/`F5`/`H3` 标「非商用/待确认」，下载前二次确认。
4. **补齐哈希**：在旧机批量计算 manifest 中所有 `kind=download` 文件的 SHA256。
5. **目录契约**：按 §8 落盘，ComfyUI/GSV 运行时与 `models` 解耦；迁移脚本先解 junction。

---

## 附：盘点命令（可复现，只读）

```powershell
# ComfyUI 模型树（注意 junction：先取目标再统计）
$m='ComfyUI\models'
Get-ChildItem $m -Directory | Select Name,@{n='Target';e={$_.Target}}
Get-ChildItem "$m\checkpoints" -Recurse -File | Measure-Object Length -Sum

# 各根总体积
Get-ChildItem 'D:\AI\community_models' -Recurse -File | Measure-Object Length -Sum
Get-ChildItem 'D:\AI\models' -Recurse -File | Measure-Object Length -Sum
Get-ChildItem 'GPT-SoVITS-v4-...\GPT_SoVITS\pretrained_models' -Recurse -File | Measure-Object Length -Sum
```

> 本报告为**只读盘点**结果：未创建/移动/删除任何模型文件，未启停任何服务，未联网下载；仅在本仓库 `ops/l3/` 下新增本 Markdown。

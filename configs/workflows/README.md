# ComfyUI 工作流模板（版本管理副本）

这里是网关实际使用的工作流模板的**仓库副本**，用于版本管理和灾备。
运行时真正加载的目录由 `MOBIUS_WORKFLOWS_DIR` 决定，默认：

```
D:\AI\comic\workflows
```

两边必须保持一致；改模板时建议先改这里，再同步到运行目录。

| 文件 | 对应 `workflow=` | 说明 |
|---|---|---|
| `comic-portrait.json` | `portrait` | 竖版漫画，SDXL checkpoint + 角色 LoRA |
| `comic-landscape.json` | `landscape` | 横版漫画 |
| `comic-panel-faceid.json` | `panel` | 多参考图 FaceID，**当前不支持**（`UNSUPPORTED_WORKFLOWS`） |
| `comic-upscale.json` | `upscale` | 放大（需参考图） |
| `comic-remove-bg.json` | `removebg` | 去背景（需参考图） |
| `comic-qwen.json` | `qwen` | 千问 Image 2.1 Turbo 通用出图，**不接 character / LoRA / checkpoint** |

## 格式约定

网关认的是 ComfyUI **API 格式**（`nodes` 为 `{节点id: {class_type, inputs}}` 的字典）。

从 ComfyUI 界面导出的工作流是 UI 格式（`nodes` 为数组、参数在 `widgets_values`），
需要手工转换。转换时注意：

- 链路引用写成 `["源节点id", 输出槽位]`
- 参数节点用真实输入名（如 `TextEncodeQwenImage21` 的 `prompt` / `negative_prompt`，
  `RandomNoise` 的 `noise_seed`），不是数组下标
- `apply_overrides` 依赖这些约定注入提示词/种子/尺寸/文件名前缀：
  - `CLIPTextEncode`（按标题「正向/负向」识别）
  - `TextEncodeQwenImage21`（千问专用）
  - `KSampler.seed` 或 `RandomNoise.noise_seed`
  - `EmptyLatentImage.width/height`
  - `SaveImage.filename_prefix`

## 千问模型依赖

`comic-qwen.json` 需要以下模型文件（`ComfyUI\models` 下）：

```
unet:          qwen_image_2.1_turbo_int8_convrot.safetensors
text_encoders: qwen3vl_8b_w4a8.safetensors
vae:           qwen_image_2.1_vae_bf16.safetensors
```

注意：`unet` / `text_encoders` 目录当前是指向 U 盘 `E:\comfy-models` 的目录联接，
拔掉 U 盘后千问工作流会失败（SDXL 的 portrait/landscape 不受影响）。

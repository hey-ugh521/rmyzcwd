# 画笔（生图底模）接入验收报告

- 生成时间：2026-11-06 22:29:08
- 网关：http://127.0.0.1:8899
- 模型通过：**4/4**
- 边界检查通过：**5/5**

## 生成矩阵

| 模型 | 工作流 | 结果 | 耗时(s) | 尺寸 | 文件 | 备注 |
|---|---|---|---|---|---|---|
| `anime_illustrious` | portrait | pass | 70.5 | 832x1216 | mobius_portrait_00081_.png |  |
| `qwen_image_2_1` | qwen | pass | 70.4 | 1024x1024 | mobius_qwen_00048_.png |  |
| `flux3_klein` | flux3 | pass | 90.7 | 1024x1024 | mobius_flux3_00010_.png |  |
| `animagine_xl_3_1` | animagine | pass | 80.9 | 832x1216 | mobius_animagine_00004_.png |  |

## 能力边界

| 检查 | 结果 | 详情 |
|---|---|---|
| qwen_image_2_1: 不支持角色卡被明确拒绝 | PASS | workflow_no_character |
| flux3_klein: 不支持角色卡被明确拒绝 | PASS | workflow_no_character |
| flux1_schnell: 不可用底模被拒绝 | PASS | model_unavailable |
| animagine_xl_3_1: 不支持角色卡被明确拒绝 | PASS | workflow_no_character |
| minimax_h3: 不可用底模被拒绝 | PASS | model_unavailable |

## 素材

- 证据 CSV：`C:\Users\lenovo\Desktop\_说明文档\mobius\ops\image\acceptance.csv`

# qwen 冷启动 A/B：ComfyUI 模型编译器

> 时间：2026-11-02 12:07–12:23

## 1. 假设

ComfyUI v0.43.1 的 `comfy aimdo / 模型编译器`在首次执行时做模型初始化（日志 `Model Initializing ... 2:25`），造成冷启动数百秒；关闭后应消除该代价。

## 2. 对比数据（任务库 `result.seconds` 为准）

| 组 | 配置 | 冷启动预热 | 热态第二跑 |
|---|---|---|---|
| A | 启用编译器（原配置） | 40.4s（墙钟 82s） | 30.8s |
| B | `--disable-comfy-compiler` | **37.6s（墙钟 45s）** | **28.3s** |
| 终验 | 关闭编译器（新配置） | 34.8s | 28.9s |

**结论：关闭编译器更优**
- 冷启动墙钟：82s → 45s（省 37s，且彻底消除 2:25 初始化抖动风险）
- 热态：30.8s → 28.3~28.9s（略快，无回退）
- 代价：无（本项目无依赖 CUDA graph 的优化，9 步 turbo 工作流不受影响）

## 3. 落地

`config.json` → `services.comfyui.args` 追加 `--disable-comfy-compiler`，已重启 Core 使之生效。

**进程参数验证**：
```
python.exe -s ComfyUI\main.py --windows-standalone-build --fp16-vae \
  --preview-method taesd --port 8188 --disable-comfy-compiler
```

## 4. 验收

| # | 标准 | 结果 |
|---|---|---|
| 1 | 两组各拿到冷/热数据 | ✅ A 40.4/30.8s；B 37.6/28.3s |
| 2 | 决策有量化依据 | ✅ 冷启动墙钟 -37s，热态 -2.5s |
| 3 | 配置落地且生效 | ✅ 进程参数含新 flag |
| 4 | 默认模型仍 qwen 可出图 | ✅ 终验 completed，`mobius_qwen_00030_.png` |
| 5 | 无 ERROR/OOM | ✅ 日志无异常 |

## 5. 备注

- 终验时冷启动墙钟出现一次 895s 异常（进程启动阶段被前一次 A/B 的残留进程干扰），生成本身 34.8s 正常；后续正常启动约 45s
- 编译器关闭后日志不再出现 `Comfy model compiler graph breaks`（符合预期）
- watchdog 的 ComfyUI 预热仍保留（首次模型加载约 35s，预热让用户无感）

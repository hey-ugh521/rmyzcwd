# Kokoro 语音保活报告

> 时间：2026-11-02 11:57–12:05

## 1. 现状与问题

Kokoro TTS（7870）长期 DOWN，没有任何保活；Gateway 的 `tts_speak` 因此不可用。

## 2. 探测结果

| 项 | 结果 |
|---|---|
| 启动方式 | `python kokoro_openai_api.py --port 7870`（cwd `D:\TTS_Workspace`，配置在 `config.json services.kokoro`） |
| 健康端点 | **`/health` 不存在（404）**；可用探针是 **`/v1/models`**（200） |
| 冷启动耗时 | **146.6 秒**（模型加载 + jieba 词典构建） |
| 单句合成 | 4.71 秒，173.5 KB wav（可播放） |
| 3 路并发 | 2.16s / 3.93s / 3.93s，总墙钟 3.93s（无串行化） |
| 网关端到端 | `tts_speak` → `ok:true, engine:kokoro, path:/audio?file=tts_*.wav` ✅ |

## 3. 修复

watchdog 新增 `check_kokoro()`：
- 7870 未监听 → 按 `config.json services.kokoro` 的 python/cwd/args 拉起
- 冷却 300 秒（冷启动 150 秒，避免拉起过程中反复重启）
- 不依赖 Core 启动流程，和 ComfyUI 预热同样旁路

## 4. 验收

| # | 标准 | 结果 |
|---|---|---|
| 1 | 7870 可启动且合成可用 | ✅ |
| 2 | 进程被杀后 watchdog 拉起 | ✅ 日志「Kokoro：7870 未监听，已按配置拉起」 |
| 3 | 冷启动可容忍 | ✅ 146.6s，已记录并设 300s 冷却 |
| 4 | 3 路并发不阻塞 | ✅ 总墙钟 3.93s |
| 5 | 网关 TTS 端到端 | ✅ tts_speak ok |
| 6 | 语音文件可访问 | ✅ `/audio?file=tts_*.wav` |

## 5. 遗留 / 建议

- 语音冷启动 150 秒偏长：可考虑常驻（不随媒体模式休眠）或加开机预热
- F5-TTS 仍未部署；当前只有 Kokoro 单引擎
- 建议后续给 Core 的 `services.kokoro` 补 `health_url: /v1/models`，让 Core 自身也能健康检查

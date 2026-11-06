# 开源组件默认配置审计（2026-11-01 03:10）

> 范围：ComfyUI / NapCat / LangBot / 语音（Kokoro + F5）
> 方式：只读检查本机实际配置与运行参数（不是通用建议）
> 结论：**LangBot 的 SQLite 默认配置是今晚 run 失败的直接根因；NapCat 默认无鉴权且端口全开；ComfyUI 的瓶颈在 USB 模型盘；语音服务未预热、当前未运行。**

---

## 1. LangBot（最严重）

### 实际配置（节选）

```yaml
database:
  sqlite:
    path: data/langbot.db          # ← 单写者 SQLite，45.6MB
  # postgresql: 段存在但未启用
output.misc.exception-handling: show-error   # 异常原文直接回给用户
dashboard:
  blocking_executor: { max_workers: 32, max_inflight_per_scope: 16, max_pending: 256 }
  lifecycle_concurrency: 16
monitoring: { enabled: true, track-function-calls: true }
```

### 问题

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | **SQLite 单写者** | 探针抓到 `(sqlite3.OperationalError) database is locked [SQL: INSERT INTO agent_run_eve…]` | run 直接失败（02:55 那次）：图照出、对话失败 |
| 2 | **写放大** | 每次工具调用/消息/lease 都写库；一轮 57 次 status 调用 = 57 次写入 | 与 SQLite 单写叠加 → 锁冲突概率随轮询次数线性上升 |
| 3 | **`exception-handling: show-error`** | 你看到的"模型返回 `database is locked`"就是它 | 内部异常泄漏给用户，且被误读成模型报错 |
| 4 | **阻塞池 + 慢工具** | `comfyui_queue_status` 单次 5–10s，`BlockingWorkCapacityError` 反复出现 | 阻塞线程被慢调用占满 → MCP 清理失败 → watchdog 重启 |
| 5 | **默认不加 `busy_timeout` 配置** | 配置里 lock_timeout 只对 postgres 生效 | SQLite 侧无等待窗口调优手段 |

### 建议（按优先级）

1. **迁 PostgreSQL**（本地 PG 18 已在跑，配置段现成）→ 根治锁冲突
2. `exception-handling: hide-error` → 不再把内部异常发给用户
3. 减少写放大：关 `track-function-calls` 或降低监控写入（需评估对探针的影响）
4. 下架/禁用 `comfyui_queue_status`（对 Agent 无必要，且是慢调用）

---

## 2. NapCat

### 实际配置

```json
// onebot11_3947356403.json
httpServers[0]:  { host: "0.0.0.0", port: 3000, token: "" }
websocketServers[0]: { host: "0.0.0.0", port: 3001, token: "" }
imageDownloadProxy: "", downloadSpeedKBps: 256, parseMultMsg: false
// webui.json
host: "::", accessControlMode: "none", enable2FA: false, token: "<REDACTED>"
```

### 问题

| # | 问题 | 影响 |
|---|---|---|
| 1 | OneBot HTTP/WS **token 为空 + 监听 0.0.0.0** | 局域网内任何人可冒充 LangBot 收发消息、控制机器人 |
| 2 | 端口发布 **0.0.0.0:6099/13000/13001**（LangBot 已收 127.0.0.1，NapCat 没动） | WebUI 与 OneBot 对外暴露 |
| 3 | WebUI `accessControlMode: none` + 12 位 token + 无 2FA | 一旦扫到端口即可登录（可扫码接管账号） |
| 4 | `downloadSpeedKBps: 256` | 收图/参考图下载被限速，影响出图体验 |
| 5 | `parseMultMsg: false` | 合并转发消息解析缺失 |
| 6 | `enableLocalFile2Url: false` | 安全上是对的，但本地文件发送受限（已知） |

### 建议

1. OneBot token 设为强随机值，LangBot 侧同步配置（**必须同时改**，否则断连）
2. 端口改成 `127.0.0.1:6099/13000/13001`（LangBot 走 docker 内网 `ws://napcat:3001`，不需要宿主映射）
3. WebUI 开启 `accessControlMode: whitelist` + 2FA
4. `downloadSpeedKBps` 提到 1024+

---

## 3. ComfyUI

### 实际启动参数

```
python.exe -s ComfyUI\main.py --windows-standalone-build --fp16-vae --preview-method taesd --port 8188
```

### 问题

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | **模型盘在 USB（E:）** | `models/` 下 15 个目录全是 Junction → `E:\comfy-models\...` | 冷启动 112–190s；出现过单张 prompt **316s**；这是"慢"的最大来源 |
| 2 | 8GB 显存无调优参数 | 日志：Qwen 模型 staged **6920MB**（贴 8GB 上限）；FLUX3 6.6GB | 易 OOM/换入换出，生成慢 |
| 3 | 未启用 `--fast` / 未设 `--reserve-vram` | 启动参数里没有 | 未榨干 50 系 fp16 加速；系统占用显存时可能失败 |
| 4 | `Comfy.ModelLibrary.AutoLoadAll: true` | comfy.settings.json | 启动扫描全部模型，拖慢首次加载 |
| 5 | 25 个自定义节点 | custom_nodes 计数 | 启动扫描 + 依赖风险 |
| 6 | 无 `--max-upload-size` 限制 | 默认 | 若暴露端口可被大文件上传打爆 |
| 7 | history/队列无界 | 默认 | 长跑内存增长 |

### 建议

1. **把热模型从 USB 迁到本地 SSD**（Qwen 6.9GB + 常用 SDXL checkpoint + VAE），USB 只留冷门模型
2. 启动加 `--fast`；显存吃紧再加 `--reserve-vram 1`；不要盲目 `--lowvram`（会更慢）
3. 关 `Comfy.ModelLibrary.AutoLoadAll`
4. 给 ComfyUI 端口加 `--listen 127.0.0.1`（若不需远程访问）

---

## 4. 语音（Kokoro + F5，未完全装上）

### 现状

| 项 | 状态 |
|---|---|
| Kokoro `7870` | **DOWN**（进程未运行；日志显示曾成功启动过） |
| F5 `7871` | `enabled: false`，未部署 |
| 工作目录 | `D:\TTS_Workspace`（脚本、音色 .bin 在，**模型权重不在本地**） |
| 默认行为 | `kookoro_openai_api.py` 首次请求时 `hf_hub_download` 拉模型 |

### 问题

| # | 问题 | 影响 |
|---|---|---|
| 1 | 首次请求才从 HuggingFace 下载模型 | 首次语音慢/失败；离线不可用 |
| 2 | 无预热、无保活 | 现在就是 DOWN 状态，机器人语音能力实际不可用 |
| 3 | `local_dir_use_symlinks` 已弃用 | 每次启动有警告，未来 HF 库升级会报错 |
| 4 | 音色文件在本机但模型权重走 HF 缓存 | 换机器/清缓存后需重新下载 |

### 建议

1. 预下载模型到本地并设 `HF_HUB_OFFLINE=1`（或本地路径）
2. Core 启动时预热 Kokoro（复用现有 `services` 机制，加 `prewarm_on_start`）
3. 给 7870/7871 加进程保活（watchdog 已有端口检测模式，可复用）

---

## 5. 优先级汇总

| 优先级 | 事项 | 组件 | 预期收益 |
|---|---|---|---|
| **P0** | LangBot 迁 PostgreSQL | LangBot | 根治 `database is locked`（今晚 4+ 次 run 失败） |
| **P0** | `exception-handling: hide-error` | LangBot | 不再向用户泄漏内部异常 |
| **P1** | ComfyUI 热模型迁 SSD | ComfyUI | 冷启动 190s → 预计 40–60s；生成提速 |
| **P1** | NapCat token + 端口收 127.0.0.1 | NapCat | 消除局域网未授权控制 |
| **P2** | 下架 `comfyui_queue_status` | Gateway | 消除 5–10s 慢调用，缓解阻塞池 |
| **P2** | Kokoro 预热 + 保活 | 语音 | 语音真正可用 |
| **P3** | ComfyUI 参数调优（`--fast` / reserve-vram） | ComfyUI | 小幅提速 |

> 以上全部为**配置层改动**，不改任何组件源码。
> 每项改动前需确认：P0-1 涉及数据迁移与停机；P1-2 需同步改 LangBot 连接配置，否则断连。

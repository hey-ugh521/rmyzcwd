# NapCat 配置（版本控制副本）

本目录是 NapCat 运行配置的**脱敏副本**，用于版本追踪与灾难恢复。

| 文件 | 说明 |
|---|---|
| `docker-compose.yml` | 容器定义（端口已绑定 127.0.0.1，固定账号快速登录） |
| `onebot11.example.json` | OneBot11 网络配置模板（token 已替换为占位符） |

## 真实值的来源

| 占位符 | 真实值位置 |
|---|---|
| `<NAPCAT_HTTP_TOKEN>` | `D:\AI\mobius\.env` 的 `NAPCAT_HTTP_TOKEN` |
| `<NAPCAT_WS_TOKEN>` | NapCat 配置实际值（`napcat/config/onebot11_3947356403.json`），当前与 LangBot 侧一致 |

运行目录：`C:\Users\lenovo\Desktop\_说明文档\qqbot\napcat\`（挂载 `./napcat/config` 与 `./napcat/qq`）。

## 端口（宿主机）

| 端口 | 用途 | 绑定 |
|---|---|---|
| 6099 | WebUI（扫码/配置） | 127.0.0.1 |
| 13000 | OneBot HTTP | 127.0.0.1 |
| 13001 | OneBot WebSocket | 127.0.0.1 |

容器网络内 LangBot 使用 `ws://napcat:3001`，不依赖宿主机端口映射。

## 变更流程

1. 改真实配置（WebUI 或容器内文件）
2. 同步本目录副本（重新脱敏）
3. 提交

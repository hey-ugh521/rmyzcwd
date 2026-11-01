# NapCat 鉴权与端口收口 执行报告

> 执行时间：2026-11-01 23:44–23:52
> 结果：**全部验收通过**（含一次"假故障"排查，见第 4 节）

---

## 1. 变更内容

| 项 | 变更前 | 变更后 |
|---|---|---|
| OneBot HTTP token | 空 | 32 位强随机（存 `D:/AI/mobius/.env` 的 `NAPCAT_HTTP_TOKEN`） |
| OneBot WS token | 空 | `<REDACTED>`（与 LangBot bot 配置中的 `access-token` 对齐） |
| 宿主机端口绑定 | `0.0.0.0:6099/13000/13001` | **`127.0.0.1:6099/13000/13001`** |
| watchdog 的 QQ 检查 | 无 token 调用 | 读取 `.env` 并带 `Authorization: Bearer` |
| watchdog 版本控制 | 运行时文件未入库 | 仓库副本 + 启动自检 + `--capture/--apply` 同步 |

配置文件：
- `qqbot/napcat/napcat/config/onebot11_3947356403.json`（已备份 `.bak-*`）
- `qqbot/napcat/docker-compose.yml`（已备份 `.bak-*`）

## 2. 验收

| # | 标准 | 结果 |
|---|---|---|
| 1 | 无 token 调 OneBot HTTP 被拒 | ✅ **403** |
| 2 | 带 token 正常 | ✅ `online=True` |
| 3 | 端口仅本机监听 | ✅ 6099/13000/13001 均 `127.0.0.1` |
| 4 | QQ 在线 | ✅ `online=True` |
| 5 | LangBot ↔ NapCat WS 正常 | ✅ `/proc/net/tcp6` 显示 **ESTABLISHED**（172.20.0.3 → 172.20.0.2:3001） |
| 6 | 出站发送 | ✅ `send_private_msg` retcode=0 |
| 7 | watchdog QQ 检查仍正常 | ✅ 日志「QQ 登录：正常」 |

## 3. 安全收益

- 局域网内不再能直接调用 OneBot HTTP（之前任何人可冒充 LangBot 收发消息）
- WebUI / WS / HTTP 全部只监听回环
- WS 与 HTTP 均有 token 校验

## 4. 排查记录：一次"假故障"

过程中一度以为 LangBot 没连上 NapCat（连接数 0），原因：

> NapCat 容器内**没有 `ss` / `netstat` / `lsof`**，
> `ss ... | grep -c` 因命令不存在而恒为 0 —— **是检测方式错了，不是连接断了**。

改用 `/proc/net/tcp{,6}` 后确认连接状态为 `01`（ESTABLISHED），一切正常。

## 5. 遗留

- NapCat 的 `napcat/` 目录不在 git 仓库内（与 watchdog 相同问题），配置变更未纳入版本控制
- WebUI 仍为单 token + 无 2FA（已限制回环，可接受；后续可加）
- `downloadSpeedKBps: 256` 未调整（性能优化项，非本轮范围）

# D1 报告：DSH 只读运维核心接入

> 执行时间：2026-11-02 00:31–00:40
> 结果：**D1 验收通过**（DSH 已能通过只读 MCP 查询系统状态）

---

## 1. 前提确认：DSH 是开源的

```
"license": "MIT"
"repository": "git+https://github.com/deepseek-ai/deepseek-harness.git"
"directory": "apps/cli"
```

依赖树里还有关键能力包：`dsh-mcp-client`、`dsh-webhook`、`dsh-sdk-app`、
`dsh-session-persistence-jsonl`、`dsh-schedule`、`dsh-tool-workflow`。

**因此风险从"闭源黑盒"降为"RC 成熟度"**，可以锁定版本、读源码、分叉打补丁。

## 2. 本轮交付

| 文件 | 作用 |
|---|---|
| `ops/mcp_ops_server.py` | **只读**运维 MCP 服务（127.0.0.1:8901），6 个工具 |
| `ops/dsh/mobius-ops.yml` | DSH headless 的 patch 叠加层：插入 MCP 客户端 + 禁用写工具 |
| `ops/watchdog/watchdog.py` | watchdog 增加 ops MCP 自启检查（8901 未监听则拉起） |

### ops MCP 的 6 个只读工具

| 工具 | 内容 |
|---|---|
| `mobius_status` | 端口/容器/QQ/PG 备份新鲜度 |
| `task_list` / `task_detail` | Gateway 任务（mobius 库） |
| `message_state` | 探针消息过程树 |
| `log_tail` | 白名单日志尾部（gateway/comfyui/core/watchdog/boot） |
| `pg_health` | 三个 PG 库的连接数与表数 |

**没有任何写操作、不接受任意 SQL、不执行命令。**

## 3. 只读约束（通过 DSH patch 实现）

```yaml
- insert:
    - id: mcp-mobius-ops
      name: '@deepseek-ai/dsh-mcp-client'
      config: { serverName: mobius, transport: streamable-http, url: http://127.0.0.1:8901/mcp }
- id: tool-pwsh
  disabled: true
- id: tool-bash
  disabled: true
- id: plugin-manager
  disabled: true
```

## 4. 验收

| # | 标准 | 结果 |
|---|---|---|
| D1.1 | `dsh --profile headless --patch ... "查询状态"` 能调用 MCP 工具 | ✅ 模型调用 `mcp__mobius__mobius_status` 并正确总结端口/QQ/备份 |
| D1.2 | MCP 工具全部只读 | ✅ 6 个工具均无写操作 |
| D1.3 | shell/写工具已禁用 | ✅ pwsh/bash/plugin-manager disabled |
| D1.4 | 版本锁定 | ✅ npm CLI 精确 `0.2.0-rc.2`；桌面端 nightly 更新不影响 CLI 路径 |
| D1.5 | ops MCP 自启 | ✅ watchdog 检测 8901 未监听 → 拉起成功 |
| D1.6 | 不影响用户链路 | ✅ 用户对话仍走 LangBot → Gateway（DSH 为旁路） |

**实测输出**（DSH 回答节选）：

> 1. 服务端口：Core 8900、Gateway 8899、LangBot 5300、ComfyUI 8188、OneBot 13000 均在线
> 2. QQ：NapCat 容器 running，QQ 在线
> 3. 最近备份：`mobius_core-20261102-000742.dump`，约 30 分钟前生成

## 5. 使用方式

```powershell
# 一次性只读诊断
dsh --profile headless --patch ops/dsh/mobius-ops.yml "用 mobius 工具诊断当前系统状态"

# 机器可读事件流
dsh --profile headless --patch ops/dsh/mobius-ops.yml --json "..."
```

## 6. 遗留 / 下一步

- DSH 是 **rc.2**：升级前必须跑回归（MCP 工具名、headless JSON 事件格式）
- 桌面端自动更新（nightly）与 CLI 路径独立；CLI 已锁版本，但**建议同时在桌面设置里关闭自动更新**，避免行为漂移
- 待办 D2：watchdog 两段式自愈（同因故障第 2 次 → 唤起 DSH 诊断，只写报告不执行）
- 待办 D3：定时巡检（每日健康报告、周审计、月度恢复演练）
- `~/.dsh/sessions`、`~/.dsh/credentials.yaml` 需纳入备份清单

# ADR-005：媒体默认模式定为 prewarm（ComfyUI 开机常驻）

- 状态：已采纳（2026-11-06，产品经理拍板）
- 决策：**保持 `prewarm`**，不改回 `on_demand`
- 相关：`docs/STATUS.md` §3 铁律 2、§5；`docs/ARCHITECTURE-v1.1.md`（原始铁律出处）

## 背景

1. Core 提供三档媒体总开关（`core/services.py:29` `MEDIA_MODES = ("off","on_demand","prewarm")`），
   运行值存在 PG `kv.media_mode`，Console「设置」页可切。
2. 原设计铁律写的是「**ComfyUI 不常驻，默认按需唤醒，闲置休眠**」，
   理由是 8GB 显存 + 单机多用途，不希望媒体栈在闲暇时占资源。
3. 实测代价落在用户可感知的延迟上：
   - 唤醒一次（文件缓存热）约 **32.6s**；机器刚重启后首启 **4–6 分钟**
     （Defender 扫 custom nodes），配置 `start_timeout_sec=420`。
   - 这段时间群友看到的是「正在唤醒…」，然后是一条已经发出去的请求要等几分钟。
   - 11-06 起群聊**默认走语音**，媒体调用频次显著上升，"每次都要先唤醒"变得更难接受。
4. 历史还踩过一次反向坑：生图成功后立刻被闲置逻辑休眠（见
   `docs/archive/reports-history/POSTMORTEM-20261028.md` §B.1），
   因为 Core 看不到 QQ 侧的媒体活动。该问题已通过网关 `activity` 记账 + `min(Core 闲置, 网关闲置)` 修好。

## 决策与理由

**定论：默认 `prewarm`，开机即把 ComfyUI 拉起来常驻。**

- 这是产品取舍，不是技术缺陷：**用固定常驻成本换掉用户侧的不确定性延迟**。
  面向群友的即时体验是这个产品的主要价值，等待唤醒是不可接受的。
- 机器基本由这套系统独占，显存"占着"并不与别的工作冲突。
- 决策由产品经理在 2026-11-06 明确做出，因此铁律 2 中被废止的部分是**有意的**，
  后续不应被"自动优化"回 on_demand。

## 实际语义（容易误读，写清楚）

| 事实 | 出处 |
|---|---|
| `prewarm_on_start=true` → Core 启动时调 `_prewarm()` 拉起 comfyui + gsv | `core/server.py:85`、`core/server.py:89` |
| 切到 `prewarm` 模式时也会立即预热一次 | `core/server.py:283` |
| **闲置休眠只在 `on_demand` 下发生**：`idle_loop` 开头 `if mode != "on_demand": continue` | `core/services.py:487-491` |
| 因此 prewarm 下 `idle_sleep_sec=600` **不生效**，ComfyUI 进程与显存全天常驻 | 同上 |
| 代码兜底默认值仍是 `on_demand`（`kv_get("media_mode","on_demand")`）| `core/services.py:115` |

> 也就是说：prewarm 由 **PG `kv` 里的运行值** + **config 的 `prewarm_on_start`** 共同决定，
> 而**不是**由代码默认值决定。清库/换库/重装后会自动掉回 `on_demand`，
> 这是本决策当前唯一的静默失效路径。

## 后果与待办

1. **（一致性待收口）** 把代码兜底默认改成与决策一致，或在 boot 自检里断言
   `mode == prewarm` 否则告警。否则一次数据库重建就让产品行为悄悄变了。
2. **（显存预算）** prewarm 下 SDXL + LoRA 约 6.4GB 常驻，队列并发仍锁死 1；
   若将来要并行，必须另做显存策略，不能靠休眠。
3. **（可选优化，需另行决策）** 若要兼顾"省电时段"，正解是**按日程切档**
   （例如深夜 `on_demand`、白天 `prewarm`），而不是回到全天 `on_demand`。
   这需要 Core 增加定时切档能力，目前没有。
4. **（文档）** `docs/ARCHITECTURE-v1.1.md` 的铁律 2 与本决策冲突，
   本文即为该条的正式废止记录；`docs/STATUS.md` §3 已就地标注。

## 回滚

Console「设置」把媒体模式切回 `on_demand` 即可（`PUT /api/media/mode`），
或关掉 `prewarm_on_start`。行为回到"按需唤醒 + 闲置 10 分钟卸载"，无需改代码。

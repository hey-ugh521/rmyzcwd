# 4F-2C 生产部署授权与门禁裁决记录

- 日期：2026-11-14
- 授权人：用户（本次会话）
- 记录人：实施者
- 范围：**仅部署 `/api/identity/me` endpoint**
- 状态：**部署已执行；部署门禁未全部闭合**

## 1. 授权内容

用户在 4F-2C 代码审查通过后，明确回复“授权”。

按最小授权解释，授权范围限定为：

- 部署 `core/identity_api.py`
- 部署 `core/server.py` 中的 identity router include
- 验证生产 endpoint 200/401 只读行为

不包含：

- 开启 Shadow
- 修改 4F-1 / 4F-2A / 4F-2B 冻结内容
- Admin Login / 路由授权 / Default Deny
- 生产故障注入
- 2B 可观测性/worker/latency 修复

## 2. 部署前门禁状态

部署前 4F-2C 前置清单 §E 未全部闭合：

| 项 | 状态 |
|---|---|
| B1 身份来源裁决 | 已通过 |
| B2 关联/可观测性 | 未勾 |
| B3 线程生命周期 | 未勾 |
| B4 配置/审计 | 未勾 |
| C 响应基线补采 | 未勾 |
| 4F-2C followups §3 六项 | 未勾 |
| 生产部署授权 | 用户明确授权 |

**结论：本次部署不满足 §E 的全部门禁条件。**

## 3. 风险处置

- 这是一次**明确授权下的例外部署**，不是门禁通过
- 未闭合的 B2/B3/B4/C 与线程池 followup 保留为部署后事项
- 本次部署不构成后续部署先例
- 未开启 Shadow；endpoint 只读；没有改变 Legacy 放行
- 若资源/可观测性问题未收口，不得扩大使用或开启 Shadow

## 4. 执行证据

- 备份：`D:\AI\Mobius\core\server.py.bak-20261114-141306`
- 部署后 Core PID：25988，启动 2026-11-14 14:16:52
- 生产文件哈希与仓库 HEAD 一致：
  - `identity_api.py` E3FD63AD51398F9F
  - `server.py` 7F77BB2B77BEBA4F
- 生产 `config.json`：
  - SHA256 前缀 80F934C4933E0557
  - mtime 2026-11-13 12:37:15
  - 无 `identity` 段
- `shadow_enabled()` 为 False（配置无 identity 段，隐式关闭）
- Shadow Event 8 条，部署后无新增
- Principal 仅 `service-legacy`

## 5. 结论

部署链可追溯、生产只读行为已验证、无身份/权限写入。

但 §E 的 B2/B3/B4/C 与资源 followup 仍未闭合；本记录不把它们标记为通过，
也不授权开启 Shadow 或扩大流量。

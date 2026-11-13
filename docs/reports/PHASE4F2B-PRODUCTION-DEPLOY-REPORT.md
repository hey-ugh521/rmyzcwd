# Phase 4F-2B 生产部署报告（Shadow 关闭）

- 日期：2026-11-13
- 范围：生产 PG 部署 2B-1~2B-4 代码；**Shadow 保持关闭**
- 目标：验证启动、Schema、响应兼容性；不开启真实 Shadow
- 生产路径：`D:\AI\Mobius`
- 代码提交：`4ab3f32`（本地）；生产文件副本已核对

## 1. 部署文件

| 文件 | 说明 |
|---|---|
| `core/identity.py` | 只读 `inspect_legacy_api_key()` |
| `core/identity_shadow.py` | Shadow Event / evaluator / retention |
| `core/server.py` | Shadow 外层 middleware + legacy_auth 只读状态 |

未部署 `store.py`：

- 生产是 PG
- 本阶段 SQLite anchor/lock 修复不影响 PG 路径
- 避免无关变更进入生产

备份：

```text
D:\AI\Mobius\core\identity.py.bak-20261113-232802
D:\AI\Mobius\core\server.py.bak-20261113-232802
```

`identity_shadow.py` 为新增文件，无旧版本。

## 2. 部署后验证

### 代码来源

生产文件 SHA256 与本地当前文件一致：

```text
identity.py       A44E7CD2AA40B55C...
identity_shadow.py ED33B94FB7424352...
server.py         EF69986A526E3B33...
```

GitHub `f54c4e8` 只包含 README 和部署报告，不包含生产代码重新提交。
生产代码来源是工作树中与 `4ab3f32` 一致的 2B 文件；后续报告必须分别追踪
“Git 提交”和“生产实际文件哈希”。

### Schema

```text
identity_shadow_events: exists
rows: 0
```

### 配置

```text
CFG.identity: None
shadow_enabled: False
```

即生产没有开启 Shadow。

### 响应兼容

部署前后：

| endpoint | 结果 |
|---|---|
| `/api/capabilities/permissions` | 200，body hash 相同 `acf335ffe0e6e796` |
| `/studio/` | 200，body hash 相同 `d2856e3e03ad833e` |
| `/api/status` | 200；含 uptime/config 等动态字段；本次冷启动服务探测约 7.6s，未做静态 body 比较 |

服务状态：

```text
gateway: ready
comfyui: ready
kokoro: ready
langbot: down（保持关闭）
```

### Shadow 关闭

- 请求前后 `identity_shadow_events` 保持 0
- 没有创建 Shadow 线程
- 没有写身份表

### Shadow 开启时的作用范围（重要）

当前实现**没有请求级隔离**：

- `identity.shadow.enabled=True` 后，所有 `/api` 和 `/v1` 请求都会进入观察
- 覆盖包括 `/api/status`、`/api/tasks`、`/api/services` 等非管理请求
- 未覆盖路由会写 `not_evaluated` 事件，仍会触发 DB 写入
- 没有 header/方法/路径白名单
- 因此不能直接在生产打开全局开关做“受控测试”

PG Shadow 验证必须先选择：

- 维护窗口：暂停其他入口，只发测试请求
- 或先实现请求级隔离（属于新增能力，需单独批准）

当前不做请求级隔离，也不打开全局 Shadow。

## 3. 回滚

```powershell
Copy-Item D:\AI\Mobius\core\identity.py.bak-20261113-232802 D:\AI\Mobius\core\identity.py -Force
Copy-Item D:\AI\Mobius\core\server.py.bak-20261113-232802 D:\AI\Mobius\core\server.py -Force
Remove-Item D:\AI\Mobius\core\identity_shadow.py -Force
# 重启 Core
```

回滚不删除 `identity_shadow_events`；该表为空且不参与认证，可保留。

## 4. 未做

- 未开启 Shadow
- 未在 PG 上产生 Shadow 事件
- 未做 PG Shadow 语义验证
- 未做生产长稳
- 未改 4F-2C / Admin Login / Default Deny

## 5. 结论

生产 PG 已完成 **Shadow 关闭** 部署，启动、Schema、核心响应正常。

按 4F-2B 冻结原则，不进入“小范围开启 Shadow”。下一步只能是：

1. 生产 PG Shadow 验证（仍关闭真实放行影响）
2. 有监控/关闭机制后，才允许小范围开启
3. 4F-2C 另立设计审查

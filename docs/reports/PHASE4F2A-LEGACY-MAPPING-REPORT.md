# Phase 4F-2A 实施报告：Legacy Identity Mapping

- 日期：2026-11-13
- 状态：4F-2A 完成；未接入 HTTP 认证，不改变请求放行结果
- 上一阶段：[4F-1 报告](PHASE4F1-IDENTITY-DATA-REPORT.md)
- 数据契约：[Identity Data Model Contract](../current/IDENTITY-DATA-MODEL.md)

## 1. 映射模型

```text
configured core.api_key
  -> principal: service-legacy
  -> kind: machine
  -> role: service
  -> credential.lookup_prefix = sha256(api_key)[:16]
  -> credential.kind = service_token
```

- Principal ID 固定为 `service-legacy`
- 同一个 key 每次映射到同一个 Credential
- 不同 key 生成不同 lookup_prefix / Credential
- 映射只创建 service 身份，绝不创建 admin

## 2. 安全边界

- 只接受当前 `core.api_key`；未知 key 返回 `None`，不写身份表
- 使用 `hmac.compare_digest` 比较旧 key
- 数据库只存 key 的 salted SHA-256，不存明文
- `lookup_prefix` 只用于查找，不能恢复 key
- 已存在 `service-legacy` 但 kind/role 不是 machine/service → 拒绝
- Credential 已撤销/过期 → 拒绝，不自动复活
- 映射创建与审计同事务；审计失败全部回滚
- 4F-2A 不做 HTTP 认证；只是数据映射

## 3. CLI

```text
python -m core.identity_cli map-legacy-key
```

输出 principal/credential id，不输出 secret。

## 4. 验收结果

| 验收 | 结果 |
|---|---|
| 同一旧 API_KEY → 同一 Principal | 通过 |
| 同一旧 API_KEY → 同一 Credential | 通过 |
| 不同旧 API_KEY → 不同 Credential | 通过 |
| 未知 API_KEY 不创建 Principal | 通过 |
| 旧 API_KEY 不会变成 admin | 通过；固定 role=service |
| 映射失败不污染身份表 | 通过；真实 audit trigger 回滚 |
| 不改变 HTTP 认证/放行结果 | 通过；无路由改动 |

## 5. 生产验证

已部署 `core/identity.py` / `core/identity_cli.py` 并执行两次：

```text
python -m core.identity_cli map-legacy-key
```

结果：

```text
principal: service-legacy
kind: machine
role: service
credential: cred_a466692a452b
lookup_prefix: fd6e66f08854a536
created: true → false
```

- 同一 key 两次映射到同一 Credential
- 生产 `admins = 0`
- 未知 key 返回 `None`，Principal 数量不变（1 → 1）
- 未接入 HTTP，未改变请求放行结果

## 6. 测试

`tests/test_identity.py`：25/25

新增覆盖：

- 稳定映射
- 未知 key 不落库
- 不同 key 不同 Credential
- service-only / 非 admin
- 真实 audit trigger 失败回滚
- secret 不落库

## 7. 限制

- 尚未接入 HTTP 认证管线
- 尚未创建管理员
- 尚未消费 bootstrap/recovery token
- 尚未实现路由授权
- 映射 CLI 尚未编译进 `MobiusCLI.exe`
- `service-legacy` 的实际调用授权仍待 4F-2B/4F-2C

## 8. 停止点

4F-2A 到此停止。

下一步需等待 4F-2B shadow 设计审查。

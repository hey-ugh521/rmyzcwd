# Phase 4F-2B-1 报告：Shadow Event 数据模型

- 日期：2026-11-13
- 状态：2B-1 完成；未接入中间件、未改变认证/放行
- 设计：[4F-2B Shadow 设计稿](../aegis/plans/2026-11-13-phase4f2b-shadow-design.md)
- 计划：[4F-2B 实施计划](../aegis/plans/2026-11-13-phase4f2b-implementation-plan.md)

## 1. 范围

只做 Shadow Event 数据模型：

- 新表 `identity_shadow_events`
- 索引
- 插入/读取接口
- 有界保留清理
- 不含中间件、身份解析、策略评估、HTTP 接线

## 2. Schema

字段：

```text
id
ts
request_id
method
path
route_id
route_action
resource_type
resource_id
legacy_authenticated
legacy_actor
identity_state
evaluation_state
policy_domain
principal_id
credential_id
role
action
policy_result
policy_version
reason
error_class
latency_ms
```

索引：

- `ts`
- `request_id`
- `policy_version`
- `(evaluation_state, ts)`

## 3. 安全边界

- 表与安全审计表分离；不写 `audit`
- 插入接口使用字段白名单：
  - 拒绝未知字段
  - 不能传 Authorization header、原始 Key、Token 哈希
- 不创建 Principal/Credential
- 没有外键到身份表；Shadow 是观察数据，不是身份事实
- 没有中间件，因此没有任何请求路径行为变化

## 4. 保留策略

`prune_shadow_events()`：

- 30 天或 10000 条，先到先清
- 每批最多 500 条
- 只删除 `identity_shadow_events`
- 不删除 audit 或其他表
- 启动时执行一个批次；失败不阻塞启动

## 5. 测试

`tests/test_identity_shadow.py`：6/6

覆盖：

- schema 幂等
- 无敏感列
- 插入/读取
- 未知字段拒绝
- 按时间清理且 audit 不受影响
- max_rows 分批清理
- batch_size 上限

完整入口：

```text
Ran 101 tests ... OK
webhook 5/5
```

## 6. 当前未做

- 未实现 RouteContextResolver / RoutePolicyEvaluator
- 未实现 Legacy auth 只读状态
- 未实现 Shadow middleware
- 未计算 policy_version
- 未部署生产

## 7. 停止点

2B-1 到此停止。等待验收后进入 2B-2。

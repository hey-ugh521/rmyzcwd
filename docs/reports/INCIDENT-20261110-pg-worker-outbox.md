# INCIDENT-20261110：PG 生产路径任务永久 pending / outbox 队头阻塞

- 时间：2026-11-10 23:32–23:48（Asia/Shanghai）
- 影响：QQ 生图任务 `t_2a807099` 停在 pending；本地网关任务 running；ComfyUI 队列为空。
- 状态：已修复并热补丁生产；任务标记 failed，用户可重试。

## 症状

- Core 任务 pending、attempts 0、无 execution_id；本地 `task_snapshot` running、attempts 1、无 prompt_id。
- ComfyUI ready、队列为空；资源租约为空；Gateway 无任务执行日志。

## 根因（两层）

1. **PG 运行时方法漏挂**：`PgGatewayStore` 用**手写清单**从 `TaskStore` 复制网关方法，M1c/M2c 新增的
   `_spawn_lease_renewal`、`_lease_renewal_loop`、`_reconcile_core_running`、`_run_image_impl`、
   `_resume_running_impl` 未复制。worker 在 `try` 之前调用 `self._spawn_lease_renewal` → AttributeError，
   worker 协程死亡，任务永久 running。启动日志的
   `'PgGatewayStore' object has no attribute '_reconcile_core_running'` 是同一漏挂的另一表现。
2. **outbox 队头阻塞**：修复 worker 后，生图实际完成，但 outbox 队头是一条已失效任务的 404 事件
   （`v_17309dd8`），`flush_outbox` 遇到失败即停，后续 `t_652e75b1` 的 running/completed 事件永远发不出去，
   Core 仍显示 pending。

## 修复

- `PgGatewayStore` 改为**动态复制** `TaskStore` 非 dunder 方法，并在启动时断言必需方法存在；
  `media_gateway/server.py` 新增 `_wire_gateway_runtime()`。
- worker 的 `_spawn_lease_renewal` 移入 `try`，异常会收敛为 failed，不再留下 running。
- 启动时在 `recover()` 之前清理「running 且无 remote_id」的残留（failed + outbox 上报）。
- `CoreTaskClient.send_event`：5xx/408/429 保留重试；其余 4xx（404/409/401 等）作为**死信丢弃**并告警，
  避免永久堵住 outbox 队头。
- 回归：`tests/test_m1_gateway_proxy.py` 34/34；`media_gateway/test_m2c_pg_runtime_surface.py` 6/6；
  M2 全套 24/19/16；M1 E2E 14/14、recovery 17/17。

## 验证

- 修复后生产测试图 `t_652e75b1` 完成：`mobius_qwen_00051_.png`；Core 状态 completed。
- 原卡死任务 `t_2a807099`：Core/local 均 failed(worker_lost_before_submit)，outbox 清空。
- 生产热补丁文件已加载；安装器随后重建。

## 遗留

- `v_17309dd8` 是 M1 期间本地语音任务无对应 Core 任务的旧账，事件按死信丢弃；不需要恢复。
- 后续新增网关运行时方法由动态复制自动覆盖；若引入非方法属性需单独审查。

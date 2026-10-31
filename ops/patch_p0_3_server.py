"""把 P0.3 持久化任务层接进 media_gateway/server.py。

安全策略：先备份 → 精确锚点替换（每处断言命中且只命中一次）→ 语法检查。
"""

from __future__ import annotations

import shutil
import time

SERVER = r"C:\Users\lenovo\Desktop\_说明文档\mobius\media_gateway\server.py"
BLOCK = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\p0_3_block.py.txt"

src = open(SERVER, encoding="utf-8").read()
new_block = open(BLOCK, encoding="utf-8").read()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"[ABORT] {label}: 期望命中 1 次，实际 {count} 次")
    return text.replace(old, new, 1)


# 1) 替换 Task/TaskStore 整块
start_marker = "\n@dataclass\nclass Task:\n"
end_marker = "\ndef _check_cancel(task: Task | None) -> None:\n"
i = src.find(start_marker)
j = src.find(end_marker)
if i < 0 or j < 0 or j <= i:
    raise SystemExit(f"[ABORT] 找不到 Task/TaskStore 区块边界 i={i} j={j}")
src = src[: i + 1] + new_block + src[j:]

# 2) _check_cancel 适配 TaskRecord / _CancelView
src = replace_once(
    src,
    'def _check_cancel(task: Task | None) -> None:\n'
    '    if task is not None and (task.cancel_requested or task.status == "canceled"):\n'
    '        raise _TaskCanceled()\n',
    'def _check_cancel(task) -> None:\n'
    '    """执行期间检查取消标记（兼容 TaskRecord / _CancelView）。"""\n'
    '    if task is not None and (getattr(task, "cancel_requested", False) or\n'
    '                             getattr(task, "status", "") in ("canceled", STATUS_CANCELLED)):\n'
    '        raise _TaskCanceled()\n',
    "_check_cancel",
)

# 3) 导入 taskstore
if "from taskstore import" not in src and "from .taskstore import" not in src:
    anchor = "from starlette.responses import JSONResponse, Response\n"
    import_block = (
        "from starlette.responses import JSONResponse, Response\n"
        "\n"
        "try:  # 既支持 `python media_gateway/server.py`，也支持包方式导入\n"
        "    from taskstore import (  # type: ignore[import-not-found]\n"
        "        DurableTaskStore, TaskConflict, TaskRecord, TERMINAL,\n"
        "        STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,\n"
        "        STATUS_PENDING, STATUS_RUNNING, STATUS_UNKNOWN, normalize_status,\n"
        "    )\n"
        "except ImportError:  # pragma: no cover\n"
        "    from .taskstore import (  # type: ignore[no-redef]\n"
        "        DurableTaskStore, TaskConflict, TaskRecord, TERMINAL,\n"
        "        STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,\n"
        "        STATUS_PENDING, STATUS_RUNNING, STATUS_UNKNOWN, normalize_status,\n"
        "    )\n"
    )
    src = replace_once(src, anchor, import_block, "导入 taskstore")

# 4) submit 调用点：新签名 + 幂等键 + 冲突处理
old_submit = '''    task = store.submit(
        "image",
        spec,
        body,
        source=str(body.get("source") or "console"),
        user_id=body.get("user_id"),
        session_id=body.get("session_id"),
    )
'''
new_submit = '''    idem = str(body.get("idempotency_key") or "").strip() or None
    try:
        task, idempotent_hit = store.submit(
            kind="image",
            payload={
                key: value
                for key, value in body.items()
                if key not in ("source", "user_id", "session_id")
            },
            spec=spec,
            source=str(body.get("source") or "console"),
            user_id=body.get("user_id"),
            session_id=body.get("session_id"),
            idempotency_key=idem,
        )
    except TaskConflict as exc:
        raise ApiError(
            "idempotency_conflict",
            f"幂等键已被任务 {exc.task_id} 使用，但请求内容不同",
            409,
            existing_task_id=exc.task_id,
        ) from exc
'''
src = replace_once(src, old_submit, new_submit, "submit 调用点")

# 5) async 返回体补 idempotent_hit
src = replace_once(
    src,
    '''        return {
            "ok": True,
            "task_id": task.id,
            "status": task.status,''',
    '''        return {
            "ok": True,
            "task_id": task.id,
            "status": task.status,
            "idempotent_hit": idempotent_hit,''',
    "async 返回体",
)

# 6) 同步等待分支：succeeded -> completed
src = replace_once(
    src, 'if finished.status == "succeeded":', 'if finished.status == STATUS_COMPLETED:', "同步状态判断"
)
src = replace_once(src, '"status": "succeeded",', '"status": STATUS_COMPLETED,', "同步状态返回")

# 7) 队列长度
if "store._queue.qsize()" in src:
    src = src.replace("store._queue.qsize()", "store.pending_count()")

# 8) lifespan：先恢复再拉 worker
src = replace_once(
    src,
    '''        if not self._started:
            self._started = True
            store.ensure_worker()''',
    '''        if not self._started:
            self._started = True
            await store.bootstrap()''',
    "lifespan bootstrap",
)

# 9) MCP submit：增加 idempotency_key 参数
src = replace_once(
    src,
    '''    reference_image_url: str = "",
) -> str:
    """异步提交生图任务。参数同 comfyui_generate。"""''',
    '''    reference_image_url: str = "",
    idempotency_key: str = "",
) -> str:
    """异步提交生图任务。参数同 comfyui_generate。

    idempotency_key：同一个逻辑请求的重试请带上同一个 key，避免重复出图。
    """''',
    "MCP 签名",
)
src = replace_once(
    src,
    '''        result = await submit_generate(
            settings,
            _tool_body(
                prompt, negative, workflow, width, height, seed,
                character, loras, reference_image_url,
            ),
            async_mode=True,
        )''',
    '''        body = _tool_body(
            prompt, negative, workflow, width, height, seed,
            character, loras, reference_image_url,
        )
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        result = await submit_generate(settings, body, async_mode=True)''',
    "MCP 提交体",
)

# 10) 备份并写回
backup = f"{SERVER}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(SERVER, backup)
open(SERVER, "w", encoding="utf-8").write(src)
print(f"已写入；备份：{backup}")
print("替换项：Task/TaskStore 区块、_check_cancel、taskstore 导入、submit 调用点、"
      "async 返回体、同步状态词汇、队列长度、lifespan、MCP submit 签名与提交体")

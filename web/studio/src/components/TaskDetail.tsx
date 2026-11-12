import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Task } from "../api/types";
import { useTask, useCancelTask } from "../api/hooks";
import { normalizeStatus, statusText, kindText, friendlyError, isActive } from "../api/normalize";
import { useLibrary } from "../store/library";
import { confirmAction, toast } from "../store/feedback";
import { playSound } from "../lib/sound";
import { cn, fmtTime, fmtDuration, toMs, copyText, downloadUrl } from "../lib/utils";
import { Drawer } from "./overlays";
import { Button, StatusBadge, KindBadge, Badge, Skeleton, ProgressBar } from "./ui";
import { ImageTile, AudioPlayer, Lightbox, mediaSrc } from "./media";
import {
  IconClose, IconStop, IconWand, IconDownload, IconCopy, IconStar,
  IconClock, IconUser, IconTerminal, IconLayers, IconDice, IconWarning,
} from "./icons";

function taskDuration(t: Task): string {
  const s = toMs(t.started_at), f = toMs(t.finished_at);
  if (!Number.isNaN(s) && !Number.isNaN(f)) return fmtDuration((f - s) / 1000);
  if (!Number.isNaN(s) && isActive(t.status)) return fmtDuration((Date.now() - s) / 1000) + "…";
  if (t.result?.seconds != null) return fmtDuration(t.result.seconds);
  return "—";
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <span className="flex-none text-xs text-faint">{label}</span>
      <span className="min-w-0 text-right text-xs text-ink">{children}</span>
    </div>
  );
}

export function TaskDetail({ taskId, onClose }: { taskId: string | null; onClose: () => void }) {
  const { data: task, isLoading } = useTask(taskId);
  const cancel = useCancelTask();
  const navigate = useNavigate();
  const favorites = useLibrary((s) => s.favorites);
  const toggleFav = useLibrary((s) => s.toggleFav);
  const [lightbox, setLightbox] = useState(false);

  const open = !!taskId;
  const st = task ? normalizeStatus(task.status) : "pending";
  const active = task ? isActive(task.status) : false;
  const isFav = task ? favorites.includes(task.id) : false;
  const p = (task?.params ?? {}) as Record<string, any>;
  const resultUrl = task?.result?.url || task?.result?.audio_url;

  const doCancel = async () => {
    if (!task) return;
    const ok = await confirmAction({
      title: "取消任务", tone: "danger", confirmLabel: "取消任务", cancelLabel: "保留",
      message: `确定取消「${(task.prompt || task.id).slice(0, 30)}」？已消耗的算力不会退回，终态由执行端确认。`,
    });
    if (!ok) return;
    playSound("cancel");
    cancel.mutate(task.id);
  };

  const reCreate = () => {
    if (!task) return;
    const tab = task.kind === "image" ? "image" : "voice";
    navigate(`/create?tab=${tab}`, { state: { prefill: task } });
    onClose();
    toast.info("已载入创作台", "参数已预填，可调整后重新提交。");
  };

  const download = async () => {
    if (!resultUrl) return;
    const name = task?.result?.filename || (task?.kind === "image" ? "mobius.png" : "mobius.wav");
    await downloadUrl(mediaSrc(resultUrl), name);
    toast.success("已开始下载", name);
  };

  return (
    <>
      <Drawer
        open={open} onClose={onClose} width={480}
        title={task ? `${kindText(task.kind)}任务` : "任务详情"}
        subtitle={task ? <span className="tnum text-[11px] text-faint">{task.id}</span> : undefined}
        headerExtra={task ? <StatusBadge status={task.status} /> : undefined}
        footer={
          task ? (
            <div className="flex flex-wrap items-center gap-2">
              {active && (
                <Button variant="danger" size="sm" icon={<IconStop size={15} />} loading={cancel.isPending} onClick={doCancel}>取消任务</Button>
              )}
              <Button variant="soft" size="sm" icon={<IconWand size={15} />} onClick={reCreate}>重新创作</Button>
              {task.kind === "image" && st === "completed" && (
                <Button variant="ghost" size="sm" icon={<IconStar size={15} className={cn(isFav && "fill-warning text-warning")} />} onClick={() => { toggleFav(task.id); toast.success(isFav ? "已取消收藏" : "已收藏"); }}>
                  {isFav ? "已收藏" : "收藏"}
                </Button>
              )}
              {resultUrl && (
                <Button variant="ghost" size="sm" className="ml-auto" icon={<IconDownload size={15} />} onClick={download}>下载</Button>
              )}
            </div>
          ) : undefined
        }
      >
        {isLoading || !task ? (
          <div className="space-y-3">
            <Skeleton className="h-48 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <div className="space-y-5">
            {/* 结果媒体 */}
            {st === "completed" && task.kind === "image" && resultUrl && (
              <ImageTile src={resultUrl} alt={task.prompt || "生成结果"} eager rounded="rounded-xl"
                className="aspect-square w-full ring-1 ring-line-soft" onClick={() => setLightbox(true)} />
            )}
            {st === "completed" && task.kind !== "image" && resultUrl && (
              <AudioPlayer src={resultUrl} />
            )}
            {active && (
              <div className="rounded-xl border border-brand/25 bg-brand/8 p-4">
                <div className="flex items-center gap-2 text-sm text-brand-soft">
                  <span className="led led-live bg-brand" />
                  {st === "pending" ? "排队等待执行端领取…" : "执行中，正在生成…"}
                </div>
                <ProgressBar indeterminate className="mt-3" />
                <p className="mt-2 text-[11px] text-faint">后端未提供精确百分比，此处仅显示真实状态，不做假进度。</p>
              </div>
            )}
            {(st === "failed" || st === "submission_unknown") && (
              <div className="rounded-xl border border-danger/25 bg-danger/8 p-4">
                <div className="flex items-center gap-2 text-sm font-medium text-danger">
                  <IconWarning size={16} /> {statusText(st)}
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-muted">
                  {friendlyError(task.error_code || task.error, task.error || undefined)}
                </p>
                {task.hint && <p className="mt-2 text-[11px] leading-relaxed text-faint">{task.hint}</p>}
              </div>
            )}
            {st === "cancelled" && (
              <div className="rounded-xl border border-line bg-raised/50 p-4 text-xs text-muted">该任务已取消。</div>
            )}

            {/* 提示词 */}
            <div>
              <div className="mb-1.5 flex items-center gap-2">
                <p className="section-title">提示词 / 文本</p>
                <button className="ml-auto text-faint hover:text-brand-soft" aria-label="复制提示词"
                  onClick={async () => { if (await copyText(task.prompt || "")) toast.success("已复制提示词"); }}>
                  <IconCopy size={14} />
                </button>
              </div>
              <p className="rounded-lg border border-line-soft bg-base/50 p-3 text-sm leading-relaxed text-ink whitespace-pre-wrap break-words">
                {task.prompt || "—"}
              </p>
            </div>

            {/* 参数 */}
            <div>
              <p className="section-title mb-1.5 flex items-center gap-1.5"><IconLayers size={13} /> 参数</p>
              <div className="rounded-lg border border-line-soft bg-base/40 px-3 py-1.5 divide-y divide-line-soft">
                {task.kind === "image" ? (
                  <>
                    {p.model && <Row label="底模">{String(p.model)}</Row>}
                    {p.workflow && <Row label="工作流">{String(p.workflow)}</Row>}
                    {(p.width || p.height) && <Row label="尺寸"><span className="tnum">{p.width}×{p.height}</span></Row>}
                    {p.seed != null && <Row label="种子"><span className="tnum inline-flex items-center gap-1"><IconDice size={12} />{p.seed}</span></Row>}
                    {p.steps != null && <Row label="步数"><span className="tnum">{p.steps}</span></Row>}
                    {p.cfg != null && <Row label="CFG"><span className="tnum">{p.cfg}</span></Row>}
                    {Array.isArray(p.loras) && p.loras.length > 0 && (
                      <Row label="LoRA">{p.loras.map((l: any) => `${l.name}${l.strength != null ? ` (${l.strength})` : ""}`).join(", ")}</Row>
                    )}
                    {p.negative && <Row label="负向">{String(p.negative)}</Row>}
                  </>
                ) : (
                  <>
                    {p.voice && <Row label="音色">{String(p.voice)}</Row>}
                    {p.engine && <Row label="引擎">{String(p.engine)}</Row>}
                    {p.speed != null && <Row label="语速"><span className="tnum">{p.speed}×</span></Row>}
                  </>
                )}
                {task.character && <Row label="角色">{task.character}</Row>}
              </div>
            </div>

            {/* 元信息 */}
            <div>
              <p className="section-title mb-1.5 flex items-center gap-1.5"><IconTerminal size={13} /> 执行信息</p>
              <div className="rounded-lg border border-line-soft bg-base/40 px-3 py-1.5 divide-y divide-line-soft">
                <Row label="来源"><Badge tone="muted">{task.source || "web"}</Badge></Row>
                <Row label="耗时"><span className="tnum inline-flex items-center gap-1"><IconClock size={12} />{taskDuration(task)}</span></Row>
                <Row label="创建"><span className="tnum">{fmtTime(task.created_at)}</span></Row>
                {task.started_at && <Row label="开始"><span className="tnum">{fmtTime(task.started_at)}</span></Row>}
                {task.finished_at && <Row label="结束"><span className="tnum">{fmtTime(task.finished_at)}</span></Row>}
                {task.remote_id && <Row label="执行端 ID"><span className="tnum text-[11px]">{task.remote_id}</span></Row>}
                {task.user_id && <Row label="用户"><span className="inline-flex items-center gap-1"><IconUser size={12} />{task.user_id}</span></Row>}
                {task.version != null && <Row label="版本"><span className="tnum">v{task.version}</span></Row>}
              </div>
            </div>
          </div>
        )}
      </Drawer>

      {task && (
        <Lightbox open={lightbox} onClose={() => setLightbox(false)}
          src={task.result?.url} title={task.prompt?.slice(0, 40) || "作品"}
          subtitle={`${p.width || "?"}×${p.height || "?"} · 种子 ${p.seed ?? "—"}`}
          onDownload={download} />
      )}
    </>
  );
}
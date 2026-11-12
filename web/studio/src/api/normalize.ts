import type { TaskStatus, TaskKind } from "./types";

// ADR-002 规范六词；历史词读时归一（与 core/store.normalize_task_status 一致）
const ALIASES: Record<string, TaskStatus> = {
  queued: "pending",
  pending: "pending",
  running: "running",
  succeeded: "completed",
  completed: "completed",
  canceled: "cancelled",
  cancelled: "cancelled",
  failed: "failed",
  submission_unknown: "submission_unknown",
};

export function normalizeStatus(s: unknown): TaskStatus {
  const key = String(s ?? "").trim().toLowerCase();
  return ALIASES[key] ?? "submission_unknown";
}

export const STATUS_TEXT: Record<TaskStatus, string> = {
  pending: "排队",
  running: "运行中",
  completed: "成功",
  failed: "失败",
  cancelled: "已取消",
  submission_unknown: "提交未知",
};

export function statusText(s: unknown): string {
  return STATUS_TEXT[normalizeStatus(s)] ?? String(s ?? "未知");
}

// 状态 → 语义色（CSS 变量名），用于 badge / LED / 图表
export type StatusTone = "brand" | "warning" | "success" | "danger" | "muted" | "info" | "magenta" | "cyan";

export const STATUS_TONE: Record<TaskStatus, StatusTone> = {
  pending: "warning",
  running: "brand",
  completed: "success",
  failed: "danger",
  cancelled: "muted",
  submission_unknown: "info",
};

export function statusTone(s: unknown): StatusTone {
  return STATUS_TONE[normalizeStatus(s)];
}

export const TERMINAL: TaskStatus[] = ["completed", "failed", "cancelled"];
export function isTerminal(s: unknown): boolean {
  return TERMINAL.includes(normalizeStatus(s));
}
export function isActive(s: unknown): boolean {
  const n = normalizeStatus(s);
  return n === "pending" || n === "running";
}

export const KIND_TEXT: Record<string, string> = {
  image: "图片",
  speech: "语音",
  voice: "语音",
};
export function kindText(k: unknown): string {
  return KIND_TEXT[String(k ?? "")] ?? String(k ?? "任务");
}
export function kindTone(k: unknown): StatusTone {
  return String(k) === "image" ? "magenta" : "cyan";
}

export const ALL_STATUSES: TaskStatus[] = [
  "pending", "running", "completed", "failed", "cancelled", "submission_unknown",
];

// 服务状态 → 语义色
export function serviceTone(state: unknown): StatusTone {
  const s = String(state ?? "").toLowerCase();
  if (s === "ready") return "success";
  if (s === "starting" || s === "busy") return "warning";
  if (s === "error") return "danger";
  if (s === "down" || s === "disabled" || s === "off") return "muted";
  return "info";
}
export function serviceText(state: unknown): string {
  const map: Record<string, string> = {
    ready: "就绪", starting: "启动中", busy: "繁忙", down: "未运行",
    error: "错误", disabled: "已禁用", off: "已关闭", unknown: "未知",
  };
  return map[String(state ?? "").toLowerCase()] ?? String(state ?? "未知");
}

// 错误码 → 友好文案（不泄露完整路径 / token）
export function friendlyError(code: unknown, fallback?: string): string {
  const c = String(code ?? "").toLowerCase();
  const map: Record<string, string> = {
    core_unavailable: "Core 权威任务服务不可用，已拒绝提交。请确认 Core 正在运行。",
    comfyui_unavailable: "ComfyUI 不可达，无法生成图片。请在「系统」页唤醒媒体服务。",
    media_disabled: "媒体能力已关闭，请先在「系统」页切换媒体模式。",
    resource_busy: "媒体资源繁忙（GPU/CPU 占用中），请稍后重试。",
    idempotency_conflict: "幂等键冲突：相同键已用于不同内容的任务。",
    unknown_model: "未找到所选模型，请刷新模型列表。",
    unknown_character: "未找到所选角色。",
    missing_prompt: "提示词不能为空。",
    invalid_size: "尺寸超出允许范围（64–4096）。",
    model_unavailable: "该底模本机不可用（显存/依赖不满足）。",
    workflow_unsupported: "该工作流超出当前范围（需多参考图 / FaceID）。",
    reference_image_required: "该工作流需要先上传参考图。",
    unauthorized: "本机凭证失效，请打开 Console 刷新会话后重试。",
    lease_expired: "执行租约过期（执行端可能已崩溃），任务已判定失败。",
    submission_unknown_timeout: "提交状态长时间未知，已判定失败。",
  };
  return map[c] ?? fallback ?? (c ? `执行失败（${c}）` : "执行失败");
}
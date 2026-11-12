import { create } from "zustand";
import type { ApiMode } from "./types";

const DEV = import.meta.env.DEV;
export const CORE_BASE = (import.meta.env.VITE_CORE_BASE || (DEV ? "/__core" : "")).replace(/\/$/, "");
export const GW_BASE = (import.meta.env.VITE_GATEWAY_BASE || (DEV ? "/__gw" : "")).replace(/\/$/, "");
const TOKEN = import.meta.env.VITE_CORE_TOKEN || "";

export class ApiError extends Error {
  code: string;
  status: number;
  hint?: string;
  constructor(code: string, message: string, status = 0, hint?: string) {
    super(message);
    this.code = code; this.status = status; this.hint = hint;
  }
}

interface ConnectionState {
  mode: "mock" | "live";
  requested: ApiMode;
  coreOk: boolean;
  gatewayOk: boolean;
  probing: boolean;
  lastError: string | null;
  set: (p: Partial<ConnectionState>) => void;
}

export const useConnection = create<ConnectionState>((set) => ({
  mode: "mock",
  requested: (import.meta.env.VITE_API_MODE as ApiMode) || "auto",
  coreOk: false,
  gatewayOk: false,
  probing: true,
  lastError: null,
  set: (p) => set(p),
}));

function headers(json = true): Record<string, string> {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  if (TOKEN) h["Authorization"] = `Bearer ${TOKEN}`;
  return h;
}

async function request(base: string, path: string, init?: RequestInit & { json?: boolean }): Promise<any> {
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      ...init,
      headers: { ...headers(init?.json !== false), ...(init?.headers || {}) },
      credentials: "include",
    });
  } catch (e) {
    throw new ApiError("network_error", "无法连接本机服务（网络/进程不可达）", 0);
  }
  if (res.status === 401 || res.status === 403) {
    throw new ApiError("unauthorized", "本机凭证失效或权限不足", res.status,
      "请打开 Console 刷新会话，或在 .env.local 配置 VITE_CORE_TOKEN");
  }
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const code = data?.error || data?.error_code || `http_${res.status}`;
    const msg = data?.hint || data?.message || (typeof data === "string" ? data : `请求失败（HTTP ${res.status}）`);
    throw new ApiError(String(code), String(msg), res.status, data?.hint);
  }
  // 静态托管 SPA fallback 会以 200 返回 HTML；非 JSON 一律视为未连接到真实后端。
  if (typeof data === "string") {
    throw new ApiError("invalid_response", "服务返回了非 JSON 响应（可能未连接到真实后端）", res.status);
  }
  return data;
}

export const coreFetch = (path: string, init?: RequestInit & { json?: boolean }) => request(CORE_BASE, path, init);
export const gwFetch = (path: string, init?: RequestInit & { json?: boolean }) => request(GW_BASE, path, init);

/** 探活单个服务：必须是真正的 JSON 健康响应。
 *  静态托管的 SPA fallback 会对 /health 返回 200 + index.html，
 *  仅看 r.ok 会误判为“本机在线”，故校验 content-type 与 JSON 结构。 */
async function probeOne(base: string): Promise<boolean> {
  try {
    const r = await fetch(`${base}/health`, {
      signal: AbortSignal.timeout(2500),
      headers: { Accept: "application/json" },
    });
    if (!r.ok) return false;
    const ct = r.headers.get("content-type") || "";
    if (!ct.includes("application/json")) return false;
    const data = await r.json();
    return !!data && typeof data === "object";
  } catch {
    return false;
  }
}

export async function probeHealth(): Promise<{ core: boolean; gateway: boolean }> {
  const [core, gateway] = await Promise.all([probeOne(CORE_BASE), probeOne(GW_BASE)]);
  return { core, gateway };
}
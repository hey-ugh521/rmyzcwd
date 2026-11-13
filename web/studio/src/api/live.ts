import type { Api, LogsResponse, HistoryItem } from "./index";
import { coreFetch, gwFetch, CORE_BASE, ApiError } from "./client";
import type {
  SystemStatus, ServicesResponse, MediaConfig, MediaResources, Task,
  GenerateRequest, GenerateResponse, TtsRequest, TtsResponse, Character, ConstitutionResponse,
  RegistryIndex, RegistryResourceType, RegistryResourceDetail, RegistryRevisionList,
  ChatSession, ChatMessage, ChatRequest, ChatEvent,
} from "./types";
import { uid } from "../lib/utils";

const arr = (x: any, key: string): any[] =>
  Array.isArray(x) ? x : Array.isArray(x?.[key]) ? x[key] : [];

export class LiveBackend implements Api {
  async health() {
    const out = { core: false, gateway: false };
    try { out.core = !!(await coreFetch("/health")); } catch { /* ignore */ }
    try { await gwFetch("/health"); out.gateway = true; } catch { /* ignore */ }
    return out;
  }
  getStatus() { return coreFetch("/api/status") as Promise<SystemStatus>; }
  getServices() { return coreFetch("/api/services") as Promise<ServicesResponse>; }
  async serviceAction(name: string, action: "start" | "stop" | "restart") {
    return coreFetch(`/api/services/${name}/${action}`, { method: "POST" });
  }
  async getServiceLogs(name: string, lines = 120): Promise<LogsResponse> {
    const r = await coreFetch(`/api/services/${name}/logs?lines=${lines}`);
    return { ok: true, logs: arr(r, "logs").length ? r.logs : arr(r, "lines"), name, truncated: r.truncated };
  }
  async getServiceHistory(limit = 30): Promise<{ ok: boolean; history: HistoryItem[] }> {
    const r = await coreFetch(`/api/services/history?limit=${limit}`);
    return { ok: true, history: arr(r, "history") };
  }
  getMediaConfig() { return coreFetch("/api/media/config") as Promise<MediaConfig>; }
  async setMediaConfig(patch: Partial<MediaConfig>) {
    return coreFetch("/api/media/config", { method: "PUT", body: JSON.stringify(patch) });
  }
  async setMediaMode(mode: string) { return coreFetch("/api/media/mode", { method: "PUT", body: JSON.stringify({ mode }) }); }
  async mediaWake() { return coreFetch("/api/media/wake", { method: "POST" }); }
  async mediaSleep() { return coreFetch("/api/media/sleep", { method: "POST" }); }
  getResources() { return coreFetch("/api/media/resources") as Promise<MediaResources>; }
  async getProviders() { const r = await coreFetch("/api/media/providers"); return { providers: arr(r, "providers") }; }
  async getModels() { const r = await coreFetch("/api/models"); return { models: arr(r, "models") }; }
  async getLoras(q = "") { const r = await coreFetch(`/api/loras?q=${encodeURIComponent(q)}`); return { loras: arr(r, "loras") }; }
  async getVoices() { const r = await coreFetch("/api/voices"); return { voices: arr(r, "voices") }; }
  async getWorkflows() { const r = await gwFetch("/api/workflows"); return { workflows: arr(r, "workflows") }; }
  async getCharacters() { const r = await coreFetch("/api/characters"); return { characters: arr(r, "characters") }; }
  async getCharacter(id: string) { return coreFetch(`/api/characters/${encodeURIComponent(id)}`) as Promise<Character>; }
  async saveCharacter(id: string, body: Partial<Character>) {
    return coreFetch(`/api/characters/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }) as Promise<{ ok: boolean; character: Character }>;
  }
  async getConstitution(id: string) {
    return coreFetch(`/api/characters/${encodeURIComponent(id)}/constitution`) as Promise<ConstitutionResponse>;
  }
  async saveConstitution(id: string, text: string, note = "") {
    return coreFetch(`/api/characters/${encodeURIComponent(id)}/constitution`, {
      method: "PUT", body: JSON.stringify({ text, note }),
    }) as Promise<{ ok: boolean; constitution: unknown }>;
  }
  async getRegistryIndex() { return coreFetch("/api/registry/index") as Promise<RegistryIndex>; }
  async getRegistryResources(type: RegistryResourceType, status?: string) {
    const qs = new URLSearchParams({ limit: "100" });
    if (status) qs.set("status", status);
    return coreFetch(`/api/registry/${type}?${qs}`) as Promise<{ items: any[]; total: number }>;
  }
  async getRegistryResource(type: RegistryResourceType, id: string) {
    return coreFetch(`/api/registry/${type}/${encodeURIComponent(id)}`) as Promise<RegistryResourceDetail>;
  }
  async getRegistryRevisions(type: RegistryResourceType, id: string) {
    return coreFetch(`/api/registry/${type}/${encodeURIComponent(id)}/revisions`) as Promise<RegistryRevisionList>;
  }
  async saveRegistryResource(type: RegistryResourceType, id: string, payload: Record<string, unknown>, expectedRevision: number) {
    return coreFetch(`/api/registry/${type}/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ payload, expected_revision: expectedRevision }),
    }) as Promise<RegistryResourceDetail>;
  }
  async publishRegistryResource(type: RegistryResourceType, id: string, revision: number, expectedCurrent: number, expectedPublished: number | null) {
    return coreFetch(`/api/registry/${type}/${encodeURIComponent(id)}/publish`, {
      method: "POST",
      body: JSON.stringify({ revision, expected_current_revision: expectedCurrent, expected_published_revision: expectedPublished }),
    }) as Promise<RegistryResourceDetail>;
  }
  async importLegacyRegistry() {
    return coreFetch("/api/registry/manage/import/legacy", { method: "POST" }) as Promise<Record<string, unknown>>;
  }
  async importRegistryCharacter(card: Record<string, unknown>, id?: string) {
    return coreFetch("/api/registry/manage/characters/import", {
      method: "POST", body: JSON.stringify({ card, id }),
    }) as Promise<Record<string, unknown>>;
  }
  async validateRegistryWorkflow(payload: Record<string, unknown>) {
    return coreFetch("/api/registry/manage/workflows/validate", {
      method: "POST", body: JSON.stringify({ payload }),
    }) as Promise<Record<string, unknown>>;
  }
  async importRegistryWorkflow(payload: Record<string, unknown>, id?: string, name?: string) {
    return coreFetch("/api/registry/manage/workflows/import", {
      method: "POST", body: JSON.stringify({ payload, id, name }),
    }) as Promise<Record<string, unknown>>;
  }
  async getTasks(params?: { limit?: number; status?: string; source?: string }) {
    const qs = new URLSearchParams();
    qs.set("limit", String(params?.limit ?? 100));
    if (params?.status) qs.set("status", params.status);
    if (params?.source) qs.set("source", params.source);
    const r = await coreFetch(`/api/tasks?${qs}`);
    return { tasks: arr(r, "tasks") as Task[] };
  }
  async getTask(id: string) {
    const r = await coreFetch(`/api/tasks/${id}`);
    return (r.task ?? r) as Task;
  }
  async cancelTask(id: string) { return coreFetch(`/api/tasks/${id}/cancel`, { method: "POST" }); }
  async generate(req: GenerateRequest) { return gwFetch("/api/generate", { method: "POST", body: JSON.stringify(req) }) as Promise<GenerateResponse>; }
  async tts(req: TtsRequest) { return gwFetch("/api/tts", { method: "POST", body: JSON.stringify(req) }) as Promise<TtsResponse>; }
  async upload(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    return gwFetch("/api/upload", { method: "POST", body: fd, json: false });
  }
  async getSessions() {
    const r = await coreFetch("/api/context/sessions");
    return { sessions: arr(r, "sessions") as ChatSession[] };
  }
  async getMessages(sessionId: string) {
    const r = await coreFetch(`/api/context/sessions/${encodeURIComponent(sessionId)}/messages`);
    return { messages: arr(r, "messages") as ChatMessage[] };
  }
  async resetSession(sessionId: string) {
    return coreFetch(`/api/context/sessions/${encodeURIComponent(sessionId)}/reset`, { method: "POST" });
  }
  async chatStream(req: ChatRequest, onEvent: (ev: ChatEvent) => void, signal?: AbortSignal) {
    const res = await fetch(`${CORE_BASE}/v1/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(import.meta.env.VITE_CORE_TOKEN ? { Authorization: `Bearer ${import.meta.env.VITE_CORE_TOKEN}` } : {}) },
      credentials: "include",
      body: JSON.stringify({ source: "web", user_id: "web:local", session_id: req.session_id || "session:web:local", text: req.text, character: req.character ?? null }),
      signal,
    });
    if (res.status === 401 || res.status === 403) throw new ApiError("unauthorized", "本机凭证失效", res.status);
    if (!res.ok || !res.body) throw new ApiError("chat_failed", `聊天接口失败（HTTP ${res.status}）`, res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const handleBlock = (block: string) => {
      let event = "text";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) return;
      let payload: any = {};
      try { payload = JSON.parse(dataLines.join("\n")); } catch { payload = { delta: dataLines.join("\n") }; }
      onEvent({ type: event, ...payload } as ChatEvent);
    };
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (block.trim()) handleBlock(block);
      }
    }
    if (buffer.trim()) handleBlock(buffer);
  }
}
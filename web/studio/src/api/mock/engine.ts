import type { Api, LogsResponse, HistoryItem } from "../index";
import type {
  SystemStatus, ServicesResponse, ServiceInfo, MediaConfig, MediaResources,
  Task, GenerateRequest, GenerateResponse, TtsRequest, TtsResponse,
  ChatSession, ChatMessage, ChatRequest, ChatEvent, Character, ConstitutionResponse, RegistryIndex,
  RegistryResourceType, RegistryResourceDetail, RegistryRevisionList,
} from "../types";
import { ApiError } from "../client";
import { normalizeStatus } from "../normalize";
import { uid, sleep } from "../../lib/utils";
import { generateArtwork, generateWav } from "./media";
import {
  MODELS, LORAS, VOICES, WORKFLOWS, PROVIDERS, CHARACTERS, SERVICES,
  MEDIA_CONFIG, RESOURCES, buildSeedTasks, CHAT_SESSIONS, CHAT_MESSAGES, STATE_HISTORY,
} from "./fixtures";

interface Schedule { runAt?: number; doneAt?: number; fail?: string }

const LOG_LINES: Record<string, string[]> = {
  core: ["INFO core.server: Uvicorn running on http://127.0.0.1:8900", "INFO core.store: schema v2 就绪 (tasks)", "INFO core.agent: provider=openai-compatible model=gpt-4o-mini", "INFO core.server: GET /api/status 200 12ms", "INFO core.task: 收敛循环 interval=30s", "INFO core.server: POST /v1/chat 200 (SSE)"],
  gateway: ["INFO mobius.gateway: Media Gateway 监听 127.0.0.1:8899", "INFO mobius.gateway: task_owner=core", "INFO mobius.gateway: ComfyUI 探活 ok (8188)", "INFO mobius.gateway: outbox flush sent=3 pending=0", "INFO mobius.gateway: POST /api/generate 200 task=t_…", "INFO mobius.gateway: lease renew ok ttl=60s"],
  comfyui: ["INFO comfy: loaded checkpoint Illustrious-XL-v1.0.safetensors", "INFO comfy: VRAM 5240/12288 MB", "INFO comfy: prompt queued p_mock_…", "INFO comfy: sampling 28/28 steps", "INFO comfy: saved mobius_….png"],
  kokoro: ["INFO kokoro: model loaded (cpu)", "INFO kokoro: synth voice=zf_xiaobei len=42", "INFO kokoro: wav written tts_….wav (3.1s)"],
  gsv: ["WARN gsv: 进程未运行（空闲休眠）", "INFO gsv: 等待唤醒…"],
  postgres: ["LOG: database system is ready to accept connections", "LOG: checkpoint complete", "LOG: autovacuum: table tasks"],
  langbot: ["WARN langbot: 未启用（可选 QQ 入口）"],
  napcat: ["WARN napcat: 未启用"],
};

export class MockBackend implements Api {
  private tasks: Task[] = [];
  private services: ServiceInfo[] = SERVICES.map((s) => ({ ...s }));
  private config: MediaConfig = { ...MEDIA_CONFIG };
  private sessions: ChatSession[] = [];
  private messages: Record<string, ChatMessage[]> = {};
  private characters: Character[] = CHARACTERS.map((c) => ({ ...c }));
  private constitutions: Record<string, { text: string; version: number; note: string }> = {};
  private history: HistoryItem[] = [];
  private schedules = new Map<string, Schedule>();
  private idem = new Map<string, string>();
  private uploads = new Map<string, string>();
  private timer: number | null = null;
  private gpuBase = 38;

  constructor() {
    this.seed();
    this.start();
  }

  private seed() {
    const now = Date.now();
    this.services = SERVICES.map((s) => ({ ...s }));
    this.config = { ...MEDIA_CONFIG };
    this.characters = CHARACTERS.map((c) => ({ ...c }));
    this.constitutions = {};
    for (const c of this.characters) {
      if (c.constitution) this.constitutions[c.id] = { text: c.constitution, version: 1, note: "内置角色卡" };
    }
    this.tasks = buildSeedTasks(now, generateArtwork, generateWav);
    this.sessions = CHAT_SESSIONS.map((s, i) => ({ ...s, updated_at: now - i * 9 * 60000 }));
    this.messages = {};
    for (const [k, msgs] of Object.entries(CHAT_MESSAGES)) {
      this.messages[k] = msgs.map((m, i) => ({ ...m, created_at: now - (msgs.length - i) * 60000 }));
    }
    this.history = STATE_HISTORY.map((h, i) => ({ ...h, ts: now - (i + 1) * 7 * 60000 }));
    this.schedules.clear();
    this.idem.clear();
    this.uploads.clear();
    // 让种子里的 running/pending 任务也“活”起来
    for (const t of this.tasks) {
      const st = normalizeStatus(t.status);
      if (st === "running") this.schedules.set(t.id, { doneAt: now + 5000 + Math.random() * 4000 });
      else if (st === "pending") this.schedules.set(t.id, { runAt: now + 1200, doneAt: now + 7000 + Math.random() * 4000 });
    }
  }

  /** 重新生成演示数据（保留实例 / 订阅 / 计时器）。 */
  reset() { this.seed(); this.bump(); }

  private start() {
    if (this.timer != null) return;
    this.timer = window.setInterval(() => this.tick(), 300);
  }
  dispose() { if (this.timer != null) { clearInterval(this.timer); this.timer = null; } }

  private tick() {
    const now = Date.now();
    let changed = false;
    for (const t of this.tasks) {
      const sch = this.schedules.get(t.id);
      if (!sch) continue;
      const st = normalizeStatus(t.status);
      if (st === "pending" && sch.runAt && now >= sch.runAt) {
        t.status = "running"; t.started_at = now; t.remote_id = `p_mock_${Math.floor(Math.random() * 1e6)}`;
        sch.runAt = undefined; changed = true;
      } else if (st === "running" && sch.doneAt && now >= sch.doneAt) {
        if (t.cancel_requested) { t.status = "cancelled"; }
        else if (sch.fail) { t.status = "failed"; t.error = sch.fail; t.error_code = sch.fail; }
        else {
          t.status = "completed";
          t.result = this.makeResult(t);
        }
        t.finished_at = now; t.version = (t.version ?? 0) + 1;
        this.schedules.delete(t.id); changed = true;
      }
    }
    if (changed) this.bump();
  }

  private listeners = new Set<() => void>();
  subscribe(fn: () => void) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  private bump() { this.listeners.forEach((f) => f()); }

  private makeResult(t: Task): Task["result"] {
    const p = (t.params ?? {}) as any;
    if (t.kind === "image") {
      const w = p.width ?? 1024, h = p.height ?? 1024, seed = p.seed ?? Math.floor(Math.random() * 1e6);
      return { url: generateArtwork({ seed, prompt: t.prompt ?? "", width: w, height: h }), filename: `mobius_${seed}.png`, width: w, height: h, seed };
    }
    const voice = p.voice ?? "zf_xiaobei";
    return { url: generateWav(t.prompt ?? "", voice, p.speed ?? 1), filename: `tts_${Date.now()}.wav`, seconds: 3 };
  }

  private svc(name: string) { return this.services.find((s) => s.name === name); }
  private pushHistory(service: string, from: string, to: string, detail: string) {
    this.history.unshift({ ts: Date.now(), service, from, to, detail });
    this.history = this.history.slice(0, 60);
  }

  async health() { return { core: true, gateway: true }; }

  async getStatus(): Promise<SystemStatus> {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const todays = this.tasks.filter((t) => Number(t.created_at ?? 0) >= today.getTime());
    const count = (s: string) => todays.filter((t) => normalizeStatus(t.status) === s).length;
    const comfy = this.svc("comfyui");
    const running = this.tasks.filter((t) => normalizeStatus(t.status) === "running").length;
    const pending = this.tasks.filter((t) => normalizeStatus(t.status) === "pending").length;
    return {
      core: { ok: true, uptime_sec: 3600 + Math.floor((Date.now() % 86400000) / 1000), config: "config.json" },
      media: { state: comfy?.state ?? "down", mode: this.config.mode, active_tasks: running + pending, comfyui: "http://127.0.0.1:8188" },
      counters: { pending, running, completed: count("completed"), failed: count("failed"), cancelled: count("cancelled"), total: todays.length },
      langbot: { reachable: this.svc("langbot")?.state === "ready" },
    };
  }

  async getServices(): Promise<ServicesResponse> {
    return { ok: true, autostart: this.services.filter((s) => s.enabled && s.autostart).map((s) => s.name), services: this.services.map((s) => ({ ...s })) };
  }
  async serviceAction(name: string, action: "start" | "stop" | "restart") {
    const s = this.svc(name);
    if (!s) throw new ApiError("unknown_service", `未知服务 ${name}`, 404);
    const from = s.state;
    if (action === "stop") { s.state = "down"; this.pushHistory(name, from, "down", "手动停止"); }
    else {
      s.state = "starting"; this.pushHistory(name, from, "starting", action === "restart" ? "重启中" : "启动中");
      window.setTimeout(() => { s.state = "ready"; this.pushHistory(name, "starting", "ready", "健康检查通过"); this.bump(); }, 1400);
    }
    this.bump();
    return { ok: true, service: { ...s } };
  }
  async getServiceLogs(name: string, lines = 120): Promise<LogsResponse> {
    const base = LOG_LINES[name] ?? [`INFO ${name}: no logs`];
    const logs = Array.from({ length: Math.min(lines, 40) }, (_, i) => {
      const t = new Date(Date.now() - (40 - i) * 4000).toISOString().slice(11, 19);
      return `${t} ${base[i % base.length]}`;
    });
    return { ok: true, logs, name, truncated: false };
  }
  async getServiceHistory(limit = 30) { return { ok: true, history: this.history.slice(0, limit) }; }

  async getMediaConfig() { return { ...this.config }; }
  async setMediaConfig(patch: Partial<MediaConfig>) { this.config = { ...this.config, ...patch }; this.bump(); return { ...this.config }; }
  async setMediaMode(mode: string) {
    this.config.mode = mode;
    const comfy = this.svc("comfyui");
    if (comfy) {
      if (mode === "off") { comfy.state = "down"; this.pushHistory("comfyui", comfy.state, "down", "媒体关闭"); }
      else if (mode === "prewarm") { comfy.state = "ready"; this.pushHistory("comfyui", comfy.state, "ready", "预热完成"); }
    }
    this.bump();
    return { ok: true, mode };
  }
  async mediaWake() {
    const comfy = this.svc("comfyui");
    if (this.config.mode === "off") throw new ApiError("media_disabled", "媒体能力已关闭，请先切换模式", 409);
    if (comfy) { comfy.state = "starting"; this.bump(); await sleep(900); comfy.state = "ready"; this.pushHistory("comfyui", "starting", "ready", "唤醒成功"); this.bump(); }
    return { ok: true, state: { state: "ready", seconds: 0.9 } };
  }
  async mediaSleep() { const comfy = this.svc("comfyui"); if (comfy) { comfy.state = "down"; this.pushHistory("comfyui", "ready", "down", "空闲休眠"); this.bump(); } return { ok: true, state: { state: "down" } }; }

  async getResources(): Promise<MediaResources> {
    const running = this.tasks.filter((t) => normalizeStatus(t.status) === "running").length;
    const util = Math.min(98, this.gpuBase + running * 34 + Math.floor(Math.random() * 6));
    const used = Math.min(11800, 4200 + running * 2600);
    return {
      gpu: { ...RESOURCES.gpu, utilization: util, vram_used_mb: used, vram_free_mb: 12288 - used, temperature: 50 + running * 6 },
      queue: { running, pending: this.tasks.filter((t) => normalizeStatus(t.status) === "pending").length },
      leases: RESOURCES.leases,
    };
  }
  async getProviders() { return { providers: PROVIDERS.map((p) => ({ ...p })) }; }
  async getModels() { return { models: MODELS.map((m) => ({ ...m })) }; }
  async getLoras(q = "") { const l = LORAS.filter((x) => !q || x.name.toLowerCase().includes(q.toLowerCase())); return { loras: l.map((x) => ({ ...x })) }; }
  async getVoices() { return { voices: VOICES.map((v) => ({ ...v })) }; }
  async getWorkflows() { return { workflows: WORKFLOWS.map((w) => ({ ...w })) }; }
  async getCharacters() { return { characters: this.characters.map((c) => ({ ...c })) }; }
  async getRegistryResources(_type: RegistryResourceType, _status?: string): Promise<{ items: any[]; total: number }> {
    return { items: [], total: 0 };
  }
  async getRegistryResource(_type: RegistryResourceType, id: string): Promise<RegistryResourceDetail> {
    throw new ApiError("registry_mock_empty", `演示数据没有 Registry 资源 ${id}`, 404);
  }
  async getRegistryRevisions(_type: RegistryResourceType, _id: string): Promise<RegistryRevisionList> {
    return { items: [] };
  }
  async saveRegistryResource(_type: RegistryResourceType, id: string, _payload: Record<string, unknown>, _expectedRevision: number): Promise<RegistryResourceDetail> {
    throw new ApiError("registry_mock_empty", `演示数据没有 Registry 资源 ${id}`, 404);
  }
  async publishRegistryResource(_type: RegistryResourceType, id: string): Promise<RegistryResourceDetail> {
    throw new ApiError("registry_mock_empty", `演示数据没有 Registry 资源 ${id}`, 404);
  }
  async importLegacyRegistry() { return { ok: true, mocked: true }; }
  async importRegistryCharacter() { return { ok: true, mocked: true }; }
  async validateRegistryWorkflow(_payload: Record<string, unknown>, _liveCheck = false) {
    return { ok: true, validation: { status: "invalid", errors: ["演示模式不执行工作流验证"] } };
  }
  async importRegistryWorkflow() { return { ok: true, mocked: true }; }
  async getRegistryIndex(): Promise<RegistryIndex> {
    const chars = this.characters.map((c) => ({
      id: c.id, name: c.name || c.id, aliases: c.aliases ?? [],
      workflow: c.workflow, checkpoint: c.checkpoint, size: c.size,
      loras: c.loras ?? [], voice: c.voice ?? {}, media_policy: c.media_policy ?? {},
      notes: c.notes ?? "", source: `data/characters/${c.id}.json`,
      source_kind: "legacy_character_json", status: "legacy", payload: c as Record<string, unknown>,
    }));
    const voiceMap = new Map<string, any>();
    for (const c of chars) {
      const v = c.voice;
      if (!v.engine && !v.voice) continue;
      const key = `${v.engine}|${v.voice}|${v.speed ?? 1}`;
      const item = voiceMap.get(key) ?? {
        id: `voice_${Math.abs([...key].reduce((a, ch) => ((a * 31 + ch.charCodeAt(0)) | 0), 7)).toString(36)}`,
        name: v.voice || v.engine, engine: v.engine, voice: v.voice, speed: v.speed ?? 1,
        status: "legacy", used_by: [],
      };
      item.used_by.push(c.id);
      voiceMap.set(key, item);
    }
    const workflowUse = new Map<string, string[]>();
    for (const c of chars) {
      if (c.workflow) workflowUse.set(c.workflow, [...(workflowUse.get(c.workflow) ?? []), c.id]);
    }
    const workflows = WORKFLOWS.map((w) => ({
      id: w.id, name: w.name || w.id, source: `configs/workflows/${w.id}.json`,
      source_kind: "legacy_workflow_json", status: "legacy", format: "api_prompt" as const,
      node_count: 5, class_types: ["KSampler", "CheckpointLoaderSimple", "VAEDecode"],
      used_by: [...(workflowUse.get(w.id) ?? []), ...(workflowUse.get(w.id.replace(/^comic-/, "")) ?? [])]
        .filter((x, i, a) => a.indexOf(x) === i),
    }));
    return {
      summary: {
        characters: chars.length, voices: voiceMap.size, workflows: workflows.length,
        image_models: MODELS.length, read_only: true, workflow_dir: "configs/workflows",
      },
      characters: chars,
      voices: [...voiceMap.values()],
      workflows,
      image_models: MODELS.map((m) => ({
        id: m.id, name: m.name || m.id, workflow: m.workflow, checkpoint: m.checkpoint,
        style: m.notes ?? "", available: m.available !== false, default: !!m.default,
        vram_gb: undefined, source: "configs/workflows/models.json", status: "legacy",
      })),
    };
  }
  async getCharacter(id: string) {
    const c = this.characters.find((x) => x.id === id);
    if (!c) throw new ApiError("character_not_found", `未知角色 ${id}`, 404);
    return { ...c };
  }
  async saveCharacter(id: string, body: Partial<Character>) {
    if (!/^[a-zA-Z0-9_-]+$/.test(id)) throw new ApiError("invalid_character_id", "角色 id 只能包含字母、数字、下划线、连字符", 400);
    const idx = this.characters.findIndex((x) => x.id === id);
    const next = { ...(idx >= 0 ? this.characters[idx] : { id }), ...body, id } as Character;
    if (idx >= 0) this.characters[idx] = next; else this.characters.push(next);
    if (next.constitution && !this.constitutions[id]) {
      this.constitutions[id] = { text: next.constitution, version: 1, note: "角色卡导入" };
    }
    this.bump();
    return { ok: true, character: { ...next } };
  }
  async getConstitution(id: string): Promise<ConstitutionResponse> {
    const c = this.characters.find((x) => x.id === id);
    const saved = this.constitutions[id];
    const text = saved?.text || c?.constitution || "";
    const active = text ? { text, version: saved?.version ?? 1, note: saved?.note ?? "", active: true } : null;
    const versions = saved ? [{ version: saved.version, note: saved.note, active: true }] : [];
    return { active, versions, fallback: c?.constitution || "" };
  }
  async saveConstitution(id: string, text: string, note = "") {
    if (text.trim().length < 10) throw new ApiError("constitution_too_short", "系统提示词至少需要 10 个字符", 400);
    const current = this.constitutions[id];
    const next = { text: text.trim(), version: (current?.version ?? 0) + 1, note };
    this.constitutions[id] = next;
    const c = this.characters.find((x) => x.id === id);
    if (c) c.constitution = next.text;
    this.bump();
    return { ok: true, constitution: { ...next, active: true } };
  }

  async getTasks(params?: { limit?: number; status?: string; source?: string }) {
    let list = [...this.tasks];
    if (params?.status) list = list.filter((t) => normalizeStatus(t.status) === normalizeStatus(params.status));
    if (params?.source) list = list.filter((t) => t.source === params.source);
    list.sort((a, b) => Number(b.created_at ?? 0) - Number(a.created_at ?? 0));
    return { tasks: list.slice(0, params?.limit ?? 100).map((t) => ({ ...t })) };
  }
  async getTask(id: string) {
    const t = this.tasks.find((x) => x.id === id);
    if (!t) throw new ApiError("unknown_task", `未知任务 ${id}`, 404);
    return { ...t };
  }
  async cancelTask(id: string) {
    const t = this.tasks.find((x) => x.id === id);
    if (!t) throw new ApiError("unknown_task", `未知任务 ${id}`, 404);
    const st = normalizeStatus(t.status);
    if (st === "completed" || st === "failed" || st === "cancelled") return { ok: true, task: { ...t }, note: "任务已是终态" };
    if (st === "pending") { t.status = "cancelled"; t.finished_at = Date.now(); t.cancel_requested = true; this.schedules.delete(id); }
    else { t.cancel_requested = true; const sch = this.schedules.get(id); if (sch) sch.doneAt = Date.now() + 700; else { t.status = "cancelled"; t.finished_at = Date.now(); } }
    this.bump();
    return { ok: true, task: { ...t }, note: "已登记取消，终态由执行端确认" };
  }

  async generate(req: GenerateRequest): Promise<GenerateResponse> {
    if (!req.prompt?.trim()) throw new ApiError("missing_prompt", "提示词不能为空", 400);
    const model = MODELS.find((m) => m.id === req.model) ?? MODELS.find((m) => m.default)!;
    if (model.available === false) throw new ApiError("model_unavailable", `底模 ${model.name} 本机不可用：${model.notes}`, 400);
    if (req.width && (req.width < 64 || req.width > 4096)) throw new ApiError("invalid_size", "尺寸超出 64–4096", 400);
    const comfy = this.svc("comfyui");
    if (this.config.mode === "off") throw new ApiError("media_disabled", "媒体能力已关闭", 403);
    if (comfy && comfy.state !== "ready" && comfy.state !== "busy") throw new ApiError("comfyui_unavailable", "ComfyUI 不可达，请先唤醒媒体服务", 503);

    const idemKey = req.idempotency_key;
    if (idemKey && this.idem.has(idemKey)) {
      const existing = this.tasks.find((t) => t.id === this.idem.get(idemKey));
      if (existing) return { ok: true, task_id: existing.id, status: existing.status, idempotent_hit: true, workflow: model.workflow };
    }
    const id = uid("t");
    const w = req.width ?? model.size?.[0] ?? 1024;
    const h = req.height ?? model.size?.[1] ?? 1024;
    const seed = req.seed ?? Math.floor(Math.random() * 2 ** 31);
    const fail = /FAIL|失败测试/i.test(req.prompt) ? "comfyui_unavailable" : undefined;
    const slow = /SLOW|慢速测试/i.test(req.prompt);
    const task: Task = {
      id, kind: "image", status: "pending", source: req.source ?? "web",
      user_id: req.user_id ?? "web:local", session_id: req.session_id ?? "session:web:local",
      character: req.character ?? null, prompt: req.prompt,
      params: { model: model.id, workflow: req.workflow ?? model.workflow, width: w, height: h, seed, steps: req.steps ?? 28, cfg: req.cfg ?? 5.5, negative: req.negative, loras: req.loras ?? [] },
      result: null, error: null, remote_id: null, created_at: Date.now(),
      started_at: null, finished_at: null, cancel_requested: false, attempts: 1, version: 0,
    };
    this.tasks.unshift(task);
    if (req.idempotency_key) this.idem.set(req.idempotency_key, id);
    const base = model.id === "flux3" ? 8000 : 3800;
    const dur = slow ? 22000 : base + (req.steps ?? 28) * 55;
    this.schedules.set(id, { runAt: Date.now() + 500, doneAt: Date.now() + 500 + dur, fail });
    this.bump();
    return { ok: true, task_id: id, status: "pending", idempotent_hit: false, workflow: task.params!.workflow as string, checkpoint: model.checkpoint ?? null, loras: req.loras ?? [], warnings: [] };
  }

  async tts(req: TtsRequest): Promise<TtsResponse> {
    if (!req.text?.trim()) throw new ApiError("missing_prompt", "文本不能为空", 400);
    const engine = req.engine ?? "kokoro";
    if (engine === "gpt-sovits" && this.svc("gsv")?.state !== "ready") {
      // 自动唤醒 GSV（演示）
      const g = this.svc("gsv"); if (g) { g.state = "ready"; this.pushHistory("gsv", "down", "ready", "TTS 唤醒"); }
    }
    await sleep(700 + Math.random() * 500);
    const url = generateWav(req.text, req.voice ?? "zf_xiaobei", req.speed ?? 1);
    const id = uid("t");
    const seconds = Math.max(1.5, Math.min(6, req.text.length / 8));
    const task: Task = {
      id, kind: "speech", status: "completed", source: req.source ?? "web",
      user_id: req.user_id ?? "web:local", session_id: req.session_id ?? "session:web:local",
      character: req.character ?? null, prompt: req.text,
      params: { voice: req.voice, engine, speed: req.speed ?? 1 },
      result: { url, filename: `tts_${id}.wav`, seconds }, error: null, remote_id: null,
      created_at: Date.now(), started_at: Date.now() - 800, finished_at: Date.now(),
      cancel_requested: false, attempts: 1, version: 1,
    };
    this.tasks.unshift(task);
    this.bump();
    return { audio_url: url, url, filename: task.result!.filename, seconds, task_id: id };
  }

  async upload(file: File) {
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result));
      r.onerror = () => reject(new ApiError("upload_failed", "读取文件失败", 400));
      r.readAsDataURL(file);
    });
    const upload_id = uid("up");
    this.uploads.set(upload_id, dataUrl);
    return { ok: true, upload_id, url: dataUrl, filename: file.name, size: file.size };
  }

  async getSessions() {
    const list = [...this.sessions].sort((a, b) => Number(b.updated_at ?? 0) - Number(a.updated_at ?? 0));
    return { sessions: list.map((s) => ({ ...s, message_count: this.messages[s.session_id]?.length ?? 0 })) };
  }
  async getMessages(sessionId: string) { return { messages: (this.messages[sessionId] ?? []).map((m) => ({ ...m })) }; }
  async resetSession(sessionId: string) { this.messages[sessionId] = []; this.bump(); return { ok: true }; }

  private replyFor(text: string, character?: string | null): { reply: string; draw: boolean } {
    const t = text.toLowerCase();
    const draw = /画|生成|绘制|来一张|draw|generate|image|图/.test(t) && !/不要|别/.test(t);
    const rin = character === "rin";
    if (draw) {
      return { reply: rin ? "好呀～我这就用我的角色档案帮你画一张，稍等几秒哦。生成完会直接出现在下面，你也可以去「作品」里看大图。" : "好的，我已提交图像生成任务。完成后结果会显示在下方，并自动收录到「作品」库。你可以在「任务」页查看进度或取消。", draw: true };
    }
    if (/你好|您好|hi|hello|嗨|在吗/.test(t)) return { reply: rin ? "嗨～我在呢！今天想聊点什么，还是要我帮你画张图？" : "你好！我是小莫，Möbius 的本机创作助手。我可以帮你生成图片、合成语音，或回答关于本机服务状态的问题。试试说「画一张赛博朋克都市夜景」。", draw: false };
    if (/天气|weather/.test(t)) return { reply: "我运行在你的本机，没有联网天气源哦。不过如果你想要一张「雨后都市」或「黄昏海边」的图，我可以马上帮你生成。", draw: false };
    if (/服务|状态|system|health|gpu|显存/.test(t)) return { reply: "目前 Core 与 Media Gateway 均就绪，ComfyUI 在线，GPU 显存占用约 5GB/12GB。GPT-SoVITS 处于空闲休眠，需要克隆音色时会自动唤醒。详情可在「系统」页查看。", draw: false };
    if (/谢谢|thanks|感谢/.test(t)) return { reply: rin ? "不客气～能帮上忙就好！还要再来一张吗？" : "不客气！需要继续创作或调整参数，随时告诉我。", draw: false };
    return { reply: rin ? `嗯嗯，我听到啦：“${text.slice(0, 40)}”。要不要我把这个画面画出来？或者换个风格试试？` : `我理解你说的是“${text.slice(0, 40)}”。作为本机助手，我可以把它变成一张图（说「画…」），合成一段语音，或帮你查看系统状态。你想怎么做？`, draw: false };
  }

  async chatStream(req: ChatRequest, onEvent: (ev: ChatEvent) => void, signal?: AbortSignal) {
    const sid = req.session_id || "session:web:local";
    if (!this.sessions.find((s) => s.session_id === sid)) {
      this.sessions.unshift({ session_id: sid, title: req.text.slice(0, 18) || "新会话", user_id: req.user_id ?? "web:local", character: req.character ?? null, updated_at: Date.now(), preview: req.text });
    }
    (this.messages[sid] ||= []).push({ id: uid("m"), role: "user", text: req.text, created_at: Date.now() });
    const replyId = uid("r");
    onEvent({ type: "meta", reply_id: replyId, source: "web" });
    const { reply, draw } = this.replyFor(req.text, req.character);
    await sleep(280);
    let acc = "";
    const chunks = reply.match(/.{1,3}/g) ?? [];
    for (let i = 0; i < chunks.length; i++) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      acc += chunks[i];
      onEvent({ type: "text", delta: chunks[i] });
      if (i % 14 === 13) { onEvent({ type: "pause" }); await sleep(60); }
      await sleep(18 + Math.random() * 22);
    }
    const taskIds: string[] = [];
    if (draw) {
      try {
        const g = await this.generate({ prompt: req.text.replace(/^(画|生成|绘制|来一张|帮我画|帮我生成)\s*/i, "") || req.text, character: req.character ?? undefined, source: "web", session_id: sid, async: true });
        taskIds.push(g.task_id);
      } catch { /* 媒体关闭等场景：聊天仍完成 */ }
    }
    const msg: ChatMessage = { id: replyId, role: "assistant", text: acc, created_at: Date.now(), task_ids: taskIds };
    this.messages[sid].push(msg);
    const sess = this.sessions.find((s) => s.session_id === sid);
    if (sess) { sess.updated_at = Date.now(); sess.preview = acc.slice(0, 40); }
    this.bump();
    onEvent({ type: "done", reply: acc, task_ids: taskIds });
  }
}
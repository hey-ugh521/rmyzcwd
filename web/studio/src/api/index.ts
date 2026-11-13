import type {
  SystemStatus, ServicesResponse, ServiceInfo, MediaConfig, MediaResources,
  ProviderInfo, ModelProfile, LoraInfo, VoiceInfo, WorkflowInfo, Character,
  ConstitutionResponse, RegistryIndex, RegistryResourceSummary, RegistryResourceDetail,
  RegistryRevisionList, RegistryResourceType, VoiceAsset,
  CapabilityPermission, CapabilityProvider, CapabilityProviderHealth, CapabilityInvocation,
  Task, GenerateRequest, GenerateResponse, TtsRequest, TtsResponse,
  ChatSession, ChatMessage, ChatRequest, ChatEvent,
} from "./types";
import { useConnection, probeHealth } from "./client";
import { useUI } from "../store/ui";
import { MockBackend } from "./mock/engine";
import { LiveBackend } from "./live";

export interface LogsResponse { ok: boolean; logs: string[]; name?: string; truncated?: boolean }
export interface HistoryItem { ts: number; service: string; from?: string; to?: string; detail?: string }

export interface Api {
  health(): Promise<{ core: boolean; gateway: boolean }>;
  getStatus(): Promise<SystemStatus>;
  getServices(): Promise<ServicesResponse>;
  serviceAction(name: string, action: "start" | "stop" | "restart"): Promise<{ ok: boolean; service?: ServiceInfo }>;
  getServiceLogs(name: string, lines?: number): Promise<LogsResponse>;
  getServiceHistory(limit?: number): Promise<{ ok: boolean; history: HistoryItem[] }>;
  getMediaConfig(): Promise<MediaConfig>;
  setMediaConfig(patch: Partial<MediaConfig>): Promise<MediaConfig>;
  setMediaMode(mode: string): Promise<{ ok: boolean; mode: string }>;
  mediaWake(): Promise<{ ok: boolean; state?: unknown }>;
  mediaSleep(): Promise<{ ok: boolean; state?: unknown }>;
  getResources(): Promise<MediaResources>;
  getProviders(): Promise<{ providers: ProviderInfo[] }>;
  getModels(): Promise<{ models: ModelProfile[] }>;
  getLoras(q?: string): Promise<{ loras: LoraInfo[] }>;
  getVoices(): Promise<{ voices: VoiceInfo[] }>;
  getWorkflows(): Promise<{ workflows: WorkflowInfo[] }>;
  getCharacters(): Promise<{ characters: Character[] }>;
  getCharacter(id: string): Promise<Character>;
  saveCharacter(id: string, body: Partial<Character>): Promise<{ ok: boolean; character: Character }>;
  getConstitution(id: string): Promise<ConstitutionResponse>;
  saveConstitution(id: string, text: string, note?: string): Promise<{ ok: boolean; constitution: unknown }>;
  getRegistryIndex(): Promise<RegistryIndex>;
  getRegistryResources(type: RegistryResourceType, status?: string): Promise<{ items: RegistryResourceSummary[]; total: number }>;
  getRegistryResource(type: RegistryResourceType, id: string): Promise<RegistryResourceDetail>;
  getRegistryRevisions(type: RegistryResourceType, id: string): Promise<RegistryRevisionList>;
  saveRegistryResource(type: RegistryResourceType, id: string, payload: Record<string, unknown>, expectedRevision: number): Promise<RegistryResourceDetail>;
  publishRegistryResource(type: RegistryResourceType, id: string, revision: number, expectedCurrent: number, expectedPublished: number | null): Promise<RegistryResourceDetail>;
  importLegacyRegistry(): Promise<Record<string, unknown>>;
  importRegistryCharacter(card: Record<string, unknown>, id?: string): Promise<Record<string, unknown>>;
  validateRegistryWorkflow(payload: Record<string, unknown>, liveCheck?: boolean): Promise<Record<string, unknown>>;
  importRegistryWorkflow(payload: Record<string, unknown>, id?: string, name?: string): Promise<Record<string, unknown>>;
  testRegistryWorkflow(id: string): Promise<Record<string, unknown>>;
  listVoiceAssets(): Promise<{ items: VoiceAsset[] }>;
  uploadVoiceAsset(file: File, opts: { kind?: string; source?: string; consent?: string; reusePolicy?: string }): Promise<{ ok: boolean; item: VoiceAsset }>;
  deleteVoiceAsset(id: string): Promise<{ ok: boolean; item: VoiceAsset }>;
  getCapabilityPermissions(): Promise<{ items: CapabilityPermission[]; default_allow: boolean }>;
  saveCapabilityPermission(body: Record<string, unknown>): Promise<{ ok: boolean; item: CapabilityPermission }>;
  deleteCapabilityPermission(id: string, expectedVersion?: number): Promise<{ ok: boolean }>;
  getCapabilityProviders(): Promise<{ items: CapabilityProvider[] }>;
  saveCapabilityProvider(body: Record<string, unknown>): Promise<{ ok: boolean; item: CapabilityProvider }>;
  getCapabilityProviderHealth(): Promise<{ items: CapabilityProviderHealth[] }>;
  getCapabilityInvocations(limit?: number): Promise<{ items: CapabilityInvocation[] }>;
  getCapabilityRetention(): Promise<{ retention_days: number; max_records: number }>;
  getTasks(params?: { limit?: number; status?: string; source?: string }): Promise<{ tasks: Task[] }>;
  getTask(id: string): Promise<Task>;
  cancelTask(id: string): Promise<{ ok: boolean; task?: Task; note?: string }>;
  generate(req: GenerateRequest): Promise<GenerateResponse>;
  tts(req: TtsRequest): Promise<TtsResponse>;
  upload(file: File): Promise<{ ok: boolean; upload_id: string; url: string; filename: string; size: number }>;
  getSessions(): Promise<{ sessions: ChatSession[] }>;
  getMessages(sessionId: string): Promise<{ messages: ChatMessage[] }>;
  resetSession(sessionId: string): Promise<{ ok: boolean }>;
  chatStream(req: ChatRequest, onEvent: (ev: ChatEvent) => void, signal?: AbortSignal): Promise<void>;
}

let mockInstance: MockBackend | null = null;
let liveInstance: LiveBackend | null = null;

export function getApi(): Api {
  const mode = useConnection.getState().mode;
  if (mode === "live") {
    if (!liveInstance) liveInstance = new LiveBackend();
    return liveInstance;
  }
  if (!mockInstance) mockInstance = new MockBackend();
  return mockInstance;
}

export function resetMock() {
  if (mockInstance) mockInstance.reset();
  else mockInstance = null;
}

/** mock 后端有实时 tick；订阅其变更以便即时刷新（live 模式无订阅，靠轮询）。 */
export function subscribeBackend(cb: () => void): () => void {
  const api = getApi() as unknown as { subscribe?: (fn: () => void) => () => void };
  if (typeof api.subscribe === "function") return api.subscribe(cb);
  return () => {};
}

/** 启动时确定模式：override > env(mock/live) > auto(探活回退)。 */
export async function initApi(): Promise<void> {
  const conn = useConnection.getState();
  const override = useUI.getState().apiModeOverride;
  const requested = override || conn.requested;
  conn.set({ requested, probing: true });
  if (requested === "mock") {
    conn.set({ mode: "mock", coreOk: false, gatewayOk: false, probing: false, lastError: null });
    return;
  }
  const health = await probeHealth();
  if (requested === "live") {
    conn.set({
      mode: "live", coreOk: health.core, gatewayOk: health.gateway, probing: false,
      lastError: health.core ? null : "Core 不可达（live 模式）",
    });
    return;
  }
  // auto
  const live = health.core;
  conn.set({
    mode: live ? "live" : "mock", coreOk: health.core, gatewayOk: health.gateway,
    probing: false,
    lastError: live ? null : "未检测到本机 Core（127.0.0.1:8900），已回退演示数据。",
  });
}

export async function refreshConnection(): Promise<void> {
  const conn = useConnection.getState();
  const override = useUI.getState().apiModeOverride;

  // 手动固定演示：不改变用户选择。
  if (override === "mock") {
    conn.set({ mode: "mock", coreOk: false, gatewayOk: false, probing: false, lastError: null });
    return;
  }

  const health = await probeHealth();
  if (override === "live") {
    conn.set({
      mode: "live", coreOk: health.core, gatewayOk: health.gateway, probing: false,
      lastError: health.core ? null : "Core 不可达（live 模式）",
    });
    return;
  }

  // 自动模式：Core 恢复后从演示数据自动切回真实服务。
  const live = health.core;
  conn.set({
    mode: live ? "live" : "mock", coreOk: health.core, gatewayOk: health.gateway,
    probing: false,
    lastError: live ? null : "未检测到本机 Core（127.0.0.1:8900），已回退演示数据。",
  });
}

export async function switchMode(mode: "mock" | "live"): Promise<void> {
  useUI.getState().setApiModeOverride(mode);
  await initApi();
}
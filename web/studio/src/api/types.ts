// Möbius Studio — 领域类型（以 core/server.py、media_gateway/server.py 源码为准）

export type TaskStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "submission_unknown";

export type TaskKind = "image" | "speech" | "voice";
export type TaskSource = "console" | "qq" | "web" | string;

export interface TaskResult {
  url?: string;
  audio_url?: string;
  filename?: string;
  seconds?: number;
  width?: number;
  height?: number;
  seed?: number;
  [k: string]: unknown;
}

export interface Task {
  id: string;
  task_id?: string;
  kind: TaskKind;
  status: TaskStatus | string;
  source?: TaskSource;
  user_id?: string | null;
  session_id?: string | null;
  character?: string | null;
  prompt?: string;
  params?: Record<string, unknown>;
  result?: TaskResult | null;
  error?: string | null;
  error_code?: string | null;
  remote_id?: string | null;
  created_at?: string | number | null;
  started_at?: string | number | null;
  finished_at?: string | number | null;
  cancel_requested?: boolean;
  attempts?: number;
  version?: number;
  hint?: string;
}

export interface Counters {
  pending?: number;
  queued?: number;
  running?: number;
  completed?: number;
  succeeded?: number;
  failed?: number;
  cancelled?: number;
  canceled?: number;
  total?: number;
  [k: string]: number | undefined;
}

export interface MediaState {
  state?: "ready" | "busy" | "starting" | "down" | "error" | "off" | "disabled" | string;
  mode?: string;
  active_tasks?: number;
  comfyui?: string;
  [k: string]: unknown;
}

export interface SystemStatus {
  core: { ok: boolean; uptime_sec: number; config?: string };
  media: MediaState;
  counters: Counters;
  langbot: { reachable: boolean };
}

export type ServiceState =
  | "ready" | "starting" | "busy" | "down" | "error" | "disabled" | "unknown" | string;

export interface ServiceInfo {
  name: string;
  label?: string;
  state: ServiceState;
  enabled?: boolean;
  autostart?: boolean;
  port?: number;
  pid?: number | null;
  url?: string;
  detail?: string;
  login?: string | null;
  [k: string]: unknown;
}

export interface ServicesResponse {
  ok: boolean;
  autostart: string[];
  services: ServiceInfo[];
}

export interface MediaConfig {
  mode?: "off" | "on_demand" | "prewarm" | string;
  idle_sleep_sec?: number;
  prewarm_on_start?: boolean;
  [k: string]: unknown;
}

export interface GpuResource {
  name?: string;
  vram_total_mb?: number;
  vram_used_mb?: number;
  vram_free_mb?: number;
  utilization?: number;
  temperature?: number;
  [k: string]: unknown;
}

export interface MediaResources {
  gpu?: GpuResource | GpuResource[];
  queue?: { running?: number; pending?: number };
  leases?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface ModelProfile {
  id: string;
  name?: string;
  workflow?: string;
  checkpoint?: string;
  size?: [number, number];
  default?: boolean;
  available?: boolean;
  notes?: string;
  params?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface LoraInfo {
  id?: string;
  name: string;
  filename?: string;
  strength?: number;
  trigger?: string;
  [k: string]: unknown;
}

export interface VoiceInfo {
  id: string;
  name?: string;
  engine?: string;
  lang?: string;
  gender?: string;
  preview?: string;
  [k: string]: unknown;
}

export interface WorkflowInfo {
  id: string;
  name?: string;
  kind?: string;
  needs_reference?: boolean;
  [k: string]: unknown;
}

export interface ProviderInfo {
  id: string;
  name?: string;
  kind?: string;
  capabilities?: string[];
  available?: boolean;
  [k: string]: unknown;
}

export interface Character {
  id: string;
  name?: string;
  aliases?: string[];
  style_prompt?: string;
  negative_prompt?: string;
  workflow?: string;
  checkpoint?: string;
  size?: [number, number];
  loras?: unknown[];
  voice?: { engine?: string; voice?: string; speed?: number };
  media_policy?: { allow_image?: boolean; allow_voice?: boolean; allow_clone?: boolean };
  avatar?: string;
  constitution?: string;
  constitution_id?: string;
  speech_style?: string;
  relation_stance?: string;
  taboo?: string[];
  visual_anchor?: string;
  notes?: string;
  [k: string]: unknown;
}

export interface ConstitutionVersion {
  id?: string;
  version?: number;
  note?: string;
  active?: boolean | number;
  created_at?: number | string;
}

export interface Constitution {
  text: string;
  version?: number;
  note?: string;
  active?: boolean | number;
}

export interface ConstitutionResponse {
  active: Constitution | null;
  versions: ConstitutionVersion[];
  fallback: string;
}

export interface RegistryIndexCharacter {
  id: string;
  name: string;
  aliases?: string[];
  workflow?: string;
  checkpoint?: string;
  size?: [number, number];
  loras?: unknown[];
  voice?: { engine?: string; voice?: string; speed?: number };
  media_policy?: Record<string, boolean>;
  notes?: string;
  source: string;
  source_kind: string;
  status: string;
  checksum?: string;
  error?: string;
  payload?: Record<string, unknown>;
}

export interface RegistryIndexVoice {
  id: string;
  name: string;
  engine?: string;
  voice?: string;
  speed?: number;
  model?: string;
  weight?: number | null;
  language?: string;
  status: string;
  used_by: string[];
}

export interface RegistryIndexWorkflow {
  id: string;
  name: string;
  source: string;
  source_kind: string;
  status: string;
  format: "api_prompt" | "comfy_workflow" | "ui_workflow" | "unknown";
  node_count: number;
  class_types: string[];
  checksum?: string;
  used_by?: string[];
  error?: string;
  payload?: Record<string, unknown>;
}

export interface RegistryIndexModel {
  id: string;
  name: string;
  workflow?: string;
  checkpoint?: string;
  style?: string;
  available?: boolean;
  default?: boolean;
  vram_gb?: number;
  source: string;
  status: string;
}

export interface RegistryIndex {
  summary: {
    characters: number;
    voices: number;
    workflows: number;
    image_models: number;
    read_only: boolean;
    workflow_dir: string | null;
  };
  characters: RegistryIndexCharacter[];
  voices: RegistryIndexVoice[];
  workflows: RegistryIndexWorkflow[];
  image_models: RegistryIndexModel[];
}

export type RegistryResourceType = "character" | "voice_profile" | "workflow";

export interface RegistryRevisionMeta {
  revision: number;
  checksum: string;
  state: "draft" | "published" | "superseded";
  created_at: number;
  created_by?: string;
  change_note?: string;
  parent_revision?: number | null;
  payload?: Record<string, unknown>;
}

export interface RegistryResourceSummary {
  type: RegistryResourceType;
  id: string;
  status: "active" | "disabled" | "archived" | "deleted";
  current_revision: number;
  published_revision: number | null;
  created_at: number;
  updated_at: number;
}

export interface RegistryResourceDetail extends RegistryResourceSummary {
  current?: RegistryRevisionMeta;
  published?: RegistryRevisionMeta;
}

export interface RegistryRevisionList {
  items: Omit<RegistryRevisionMeta, "payload">[];
}

export interface ChatSession {
  session_id: string;
  title?: string;
  user_id?: string;
  character?: string | null;
  updated_at?: string | number;
  message_count?: number;
  preview?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  created_at?: number;
  task_ids?: string[];
  streaming?: boolean;
  error?: string | null;
}

// ---- 请求体 ----
export interface GenerateRequest {
  prompt: string;
  negative?: string;
  model?: string;
  character?: string;
  workflow?: string;
  checkpoint?: string;
  loras?: { name: string; strength?: number }[];
  width?: number;
  height?: number;
  seed?: number;
  steps?: number;
  cfg?: number;
  reference_image?: string;
  async?: boolean;
  source?: string;
  user_id?: string;
  session_id?: string;
  idempotency_key?: string;
}

export interface GenerateResponse {
  ok: boolean;
  task_id: string;
  status: TaskStatus | string;
  idempotent_hit?: boolean;
  idempotency_key?: string;
  workflow?: string;
  checkpoint?: string | null;
  loras?: unknown[];
  warnings?: string[];
  result?: TaskResult | null;
  error?: string;
  hint?: string;
}

export interface TtsRequest {
  text: string;
  engine?: string;
  voice?: string;
  speed?: number;
  character?: string;
  source?: string;
  user_id?: string;
  session_id?: string;
}

export interface TtsResponse {
  audio_url?: string;
  url?: string;
  filename?: string;
  seconds?: number;
  task_id?: string;
  error?: string;
  hint?: string;
}

export interface ChatRequest {
  source?: string;
  user_id?: string;
  session_id?: string;
  text: string;
  character?: string | null;
}

export type ChatEvent =
  | { type: "meta"; reply_id: string; source: string }
  | { type: "text"; delta: string }
  | { type: "pause" }
  | { type: "done"; reply: string; task_ids?: string[]; [k: string]: unknown }
  | { type: "error"; error: string; message: string };

export type ApiMode = "mock" | "live" | "auto";
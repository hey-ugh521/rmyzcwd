import {
  useQuery, useMutation, useQueryClient,
} from "@tanstack/react-query";
import { getApi } from "./index";
import { useConnection, ApiError } from "./client";
import { useUI } from "../store/ui";
import { toast } from "../store/feedback";
import { friendlyError, isActive } from "./normalize";
import type {
  SystemStatus, ServicesResponse, MediaConfig, MediaResources, Task,
  ModelProfile, LoraInfo, VoiceInfo, WorkflowInfo, ProviderInfo, Character, ConstitutionResponse,
  RegistryIndex, RegistryResourceType, RegistryResourceSummary, RegistryResourceDetail,
  ChatSession, ChatMessage, GenerateRequest, TtsRequest,
} from "./types";
import type { LogsResponse, HistoryItem } from "./index";

/** 当前数据源模式（mock / live）。切换模式时所有查询 key 变化 → 自动重取。 */
export function useApiMode() {
  return useConnection((s) => s.mode);
}

export function errText(e: unknown): string {
  if (e instanceof ApiError) return friendlyError(e.code, e.message);
  if (e instanceof Error) return e.message;
  return "操作失败";
}

const root = (mode: string) => ["mobius", mode] as const;
export const qk = {
  status: (m: string) => [...root(m), "status"],
  services: (m: string) => [...root(m), "services"],
  logs: (m: string, n: string) => [...root(m), "logs", n],
  history: (m: string) => [...root(m), "history"],
  mediaConfig: (m: string) => [...root(m), "mediaConfig"],
  resources: (m: string) => [...root(m), "resources"],
  providers: (m: string) => [...root(m), "providers"],
  models: (m: string) => [...root(m), "models"],
  loras: (m: string, q: string) => [...root(m), "loras", q],
  voices: (m: string) => [...root(m), "voices"],
  workflows: (m: string) => [...root(m), "workflows"],
  characters: (m: string) => [...root(m), "characters"],
  constitution: (m: string, id: string) => [...root(m), "constitution", id],
  registryIndex: (m: string) => [...root(m), "registry-index"],
  registryResources: (m: string, type: string) => [...root(m), "registry-resources", type],
  registryResource: (m: string, type: string, id: string) => [...root(m), "registry-resource", type, id],
  tasks: (m: string) => [...root(m), "tasks"],
  task: (m: string, id: string) => [...root(m), "task", id],
  sessions: (m: string) => [...root(m), "sessions"],
  messages: (m: string, sid: string) => [...root(m), "messages", sid],
};

/* ------------------------------------------------------------ 系统 / 服务 */
export function useStatus() {
  const mode = useApiMode();
  const idle = useUI((s) => s.pollIdleMs);
  const running = useUI((s) => s.pollRunningMs);
  return useQuery<SystemStatus>({
    queryKey: qk.status(mode),
    queryFn: () => getApi().getStatus(),
    refetchInterval: (q) => {
      const c = q.state.data?.counters;
      const active = (c?.running ?? 0) + (c?.pending ?? 0) > 0;
      return active ? running : idle;
    },
    refetchOnWindowFocus: true,
    staleTime: 1500,
  });
}

export function useServices() {
  const mode = useApiMode();
  const idle = useUI((s) => s.pollIdleMs);
  return useQuery<ServicesResponse>({
    queryKey: qk.services(mode),
    queryFn: () => getApi().getServices(),
    refetchInterval: idle,
    staleTime: 1500,
  });
}

export function useServiceLogs(name: string, enabled = true) {
  const mode = useApiMode();
  return useQuery<LogsResponse>({
    queryKey: qk.logs(mode, name),
    queryFn: () => getApi().getServiceLogs(name, 160),
    enabled: enabled && !!name,
    refetchInterval: enabled ? 4000 : false,
    staleTime: 1000,
  });
}

export function useServiceHistory() {
  const mode = useApiMode();
  return useQuery<{ ok: boolean; history: HistoryItem[] }>({
    queryKey: qk.history(mode),
    queryFn: () => getApi().getServiceHistory(40),
    refetchInterval: 8000,
    staleTime: 2000,
  });
}

export function useMediaConfig() {
  const mode = useApiMode();
  return useQuery<MediaConfig>({
    queryKey: qk.mediaConfig(mode),
    queryFn: () => getApi().getMediaConfig(),
    staleTime: 4000,
  });
}

export function useResources() {
  const mode = useApiMode();
  return useQuery<MediaResources>({
    queryKey: qk.resources(mode),
    queryFn: () => getApi().getResources(),
    refetchInterval: 4000,
    staleTime: 1500,
  });
}

/* ---------------------------------------------------------------- 能力目录 */
export function useProviders() {
  const mode = useApiMode();
  return useQuery<{ providers: ProviderInfo[] }>({
    queryKey: qk.providers(mode),
    queryFn: () => getApi().getProviders(),
    staleTime: 60000,
  });
}
export function useModels() {
  const mode = useApiMode();
  return useQuery<{ models: ModelProfile[] }>({
    queryKey: qk.models(mode),
    queryFn: () => getApi().getModels(),
    staleTime: 60000,
  });
}
export function useLoras(q = "") {
  const mode = useApiMode();
  return useQuery<{ loras: LoraInfo[] }>({
    queryKey: qk.loras(mode, q),
    queryFn: () => getApi().getLoras(q),
    staleTime: 60000,
  });
}
export function useVoices() {
  const mode = useApiMode();
  return useQuery<{ voices: VoiceInfo[] }>({
    queryKey: qk.voices(mode),
    queryFn: () => getApi().getVoices(),
    staleTime: 60000,
  });
}
export function useWorkflows() {
  const mode = useApiMode();
  return useQuery<{ workflows: WorkflowInfo[] }>({
    queryKey: qk.workflows(mode),
    queryFn: () => getApi().getWorkflows(),
    staleTime: 60000,
  });
}
export function useCharacters() {
  const mode = useApiMode();
  return useQuery<{ characters: Character[] }>({
    queryKey: qk.characters(mode),
    queryFn: () => getApi().getCharacters(),
    staleTime: 60000,
  });
}

export function useRegistryIndex() {
  const mode = useApiMode();
  return useQuery<RegistryIndex>({
    queryKey: qk.registryIndex(mode),
    queryFn: () => getApi().getRegistryIndex(),
    staleTime: 30000,
  });
}

export function useRegistryResources(type: RegistryResourceType) {
  const mode = useApiMode();
  return useQuery<{ items: RegistryResourceSummary[]; total: number }>({
    queryKey: qk.registryResources(mode, type),
    queryFn: () => getApi().getRegistryResources(type),
    staleTime: 5000,
  });
}

export function useRegistryResource(type: RegistryResourceType, id: string | null) {
  const mode = useApiMode();
  return useQuery<RegistryResourceDetail>({
    queryKey: qk.registryResource(mode, type, id ?? ""),
    queryFn: () => getApi().getRegistryResource(type, id!),
    enabled: !!id,
    staleTime: 3000,
  });
}

export function useRegistryRevisions(type: RegistryResourceType, id: string | null) {
  const mode = useApiMode();
  return useQuery({
    queryKey: [...qk.registryResource(mode, type, id ?? ""), "revisions"],
    queryFn: () => getApi().getRegistryRevisions(type, id!),
    enabled: !!id,
    staleTime: 3000,
  });
}

export function useImportLegacyRegistry() {
  const mode = useApiMode();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => getApi().importLegacyRegistry(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.registryIndex(mode) });
      qc.invalidateQueries({ queryKey: qk.registryResources(mode, "character") });
      qc.invalidateQueries({ queryKey: qk.registryResources(mode, "voice_profile") });
      qc.invalidateQueries({ queryKey: qk.registryResources(mode, "workflow") });
      toast.success("旧资源已导入为 Registry 草稿");
    },
    onError: (e) => toast.error("导入失败", errText(e)),
  });
}

export function useSaveRegistryResource() {
  const qc = useQueryClient();
  const mode = useApiMode();
  return useMutation({
    mutationFn: (vars: { type: RegistryResourceType; id: string; payload: Record<string, unknown>; expectedRevision: number }) =>
      getApi().saveRegistryResource(vars.type, vars.id, vars.payload, vars.expectedRevision),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: qk.registryResource(mode, vars.type, vars.id) });
      qc.invalidateQueries({ queryKey: qk.registryResources(mode, vars.type) });
      qc.invalidateQueries({ queryKey: qk.registryIndex(mode) });
      toast.success("已保存为新的 draft revision");
    },
    onError: (e) => toast.error("保存失败", errText(e)),
  });
}

export function usePublishRegistryResource() {
  const qc = useQueryClient();
  const mode = useApiMode();
  return useMutation({
    mutationFn: (vars: { type: RegistryResourceType; id: string; revision: number; expectedCurrent: number; expectedPublished: number | null }) =>
      getApi().publishRegistryResource(vars.type, vars.id, vars.revision, vars.expectedCurrent, vars.expectedPublished),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: qk.registryResource(mode, vars.type, vars.id) });
      qc.invalidateQueries({ queryKey: qk.registryResources(mode, vars.type) });
      toast.success(`已发布 revision ${vars.revision}`);
    },
    onError: (e) => toast.error("发布失败", errText(e)),
  });
}

export function useConstitution(id: string | null) {
  const mode = useApiMode();
  return useQuery<ConstitutionResponse>({
    queryKey: qk.constitution(mode, id ?? ""),
    queryFn: () => getApi().getConstitution(id!),
    enabled: !!id,
    staleTime: 30000,
  });
}

export function useSaveCharacter() {
  const inv = useInvalidate();
  const mode = useApiMode();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Character> }) => getApi().saveCharacter(id, body),
    onSuccess: (res) => {
      inv([qk.characters(mode), qk.constitution(mode, res.character.id)]);
      toast.success("角色已保存", res.character.name || res.character.id);
    },
    onError: (e) => toast.error("保存角色失败", errText(e)),
  });
}

export function useSaveConstitution() {
  const inv = useInvalidate();
  const mode = useApiMode();
  return useMutation({
    mutationFn: ({ id, text, note }: { id: string; text: string; note?: string }) =>
      getApi().saveConstitution(id, text, note),
    onSuccess: (_res, vars) => {
      inv([qk.constitution(mode, vars.id), qk.characters(mode)]);
      toast.success("系统提示词已保存");
    },
    onError: (e) => toast.error("保存系统提示词失败", errText(e)),
  });
}

/* -------------------------------------------------------------------- 任务 */
export function useTasks(params?: { status?: string; source?: string; limit?: number }) {
  const mode = useApiMode();
  const running = useUI((s) => s.pollRunningMs);
  const idle = useUI((s) => s.pollIdleMs);
  return useQuery<{ tasks: Task[] }>({
    queryKey: [...qk.tasks(mode), params ?? {}],
    queryFn: () => getApi().getTasks({ limit: 200, ...params }),
    refetchInterval: (q) => {
      const list = q.state.data?.tasks ?? [];
      return list.some((t) => isActive(t.status)) ? running : idle;
    },
    refetchOnWindowFocus: true,
    staleTime: 800,
  });
}

export function useTask(id: string | null, enabled = true) {
  const mode = useApiMode();
  const running = useUI((s) => s.pollRunningMs);
  return useQuery<Task>({
    queryKey: qk.task(mode, id ?? ""),
    queryFn: () => getApi().getTask(id!),
    enabled: enabled && !!id,
    refetchInterval: (q) => (q.state.data && isActive(q.state.data.status) ? running : false),
    staleTime: 500,
  });
}

/* -------------------------------------------------------------------- 对话 */
export function useSessions() {
  const mode = useApiMode();
  return useQuery<{ sessions: ChatSession[] }>({
    queryKey: qk.sessions(mode),
    queryFn: () => getApi().getSessions(),
    staleTime: 2000,
  });
}
export function useMessages(sessionId: string | null) {
  const mode = useApiMode();
  return useQuery<{ messages: ChatMessage[] }>({
    queryKey: qk.messages(mode, sessionId ?? ""),
    queryFn: () => getApi().getMessages(sessionId!),
    enabled: !!sessionId,
    staleTime: 1000,
  });
}

/* ------------------------------------------------------------------ 变更 */
function useInvalidate() {
  const qc = useQueryClient();
  const mode = useApiMode();
  return (keys: readonly unknown[][]) => {
    keys.forEach((k) => qc.invalidateQueries({ queryKey: k as unknown[] }));
    // 任务/状态联动刷新
    qc.invalidateQueries({ queryKey: qk.tasks(mode) });
    qc.invalidateQueries({ queryKey: qk.status(mode) });
  };
}

export function useGenerate() {
  const inv = useInvalidate();
  const mode = useApiMode();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: GenerateRequest) => getApi().generate(req),
    onSuccess: (res) => {
      inv([qk.resources(mode)]);
      if (res.idempotent_hit) toast.info("已存在相同任务", "幂等命中，未重复提交。");
      else toast.success("已提交生成任务", "可在「任务」页查看进度。");
      if (res.warnings?.length) toast.warning("提交警告", res.warnings.join("；"));
      qc.invalidateQueries({ queryKey: qk.tasks(mode) });
    },
    onError: (e) => toast.error("提交失败", errText(e)),
  });
}

export function useTts() {
  const inv = useInvalidate();
  const mode = useApiMode();
  return useMutation({
    mutationFn: (req: TtsRequest) => getApi().tts(req),
    onSuccess: () => { inv([qk.resources(mode)]); toast.success("语音合成完成"); },
    onError: (e) => toast.error("合成失败", errText(e)),
  });
}

export function useCancelTask() {
  const inv = useInvalidate();
  const mode = useApiMode();
  return useMutation({
    mutationFn: (id: string) => getApi().cancelTask(id),
    onSuccess: (res) => {
      inv([qk.task(mode, res.task?.id ?? "")]);
      toast.success("已登记取消", res.note ?? "终态由执行端确认。");
    },
    onError: (e) => toast.error("取消失败", errText(e)),
  });
}

export function useServiceAction() {
  const inv = useInvalidate();
  const mode = useApiMode();
  return useMutation({
    mutationFn: (v: { name: string; action: "start" | "stop" | "restart" }) =>
      getApi().serviceAction(v.name, v.action),
    onSuccess: (_res, v) => {
      inv([qk.services(mode), qk.history(mode), qk.resources(mode)]);
      const label = v.action === "stop" ? "停止" : v.action === "restart" ? "重启" : "启动";
      toast.success(`已${label} ${v.name}`, "状态由健康检查确认。");
    },
    onError: (e) => toast.error("服务操作失败", errText(e)),
  });
}

export function useSetMediaConfig() {
  const inv = useInvalidate();
  const mode = useApiMode();
  return useMutation({
    mutationFn: (patch: Partial<MediaConfig>) => getApi().setMediaConfig(patch),
    onSuccess: () => { inv([qk.mediaConfig(mode)]); toast.success("媒体配置已更新"); },
    onError: (e) => toast.error("更新失败", errText(e)),
  });
}

export function useSetMediaMode() {
  const inv = useInvalidate();
  const mode = useApiMode();
  return useMutation({
    mutationFn: (m: string) => getApi().setMediaMode(m),
    onSuccess: (_r, m) => {
      inv([qk.mediaConfig(mode), qk.services(mode), qk.history(mode)]);
      toast.success("媒体模式已切换", m);
    },
    onError: (e) => toast.error("切换失败", errText(e)),
  });
}

export function useMediaWake() {
  const inv = useInvalidate();
  const mode = useApiMode();
  return useMutation({
    mutationFn: () => getApi().mediaWake(),
    onSuccess: () => { inv([qk.services(mode), qk.resources(mode), qk.history(mode)]); toast.success("媒体服务已唤醒"); },
    onError: (e) => toast.error("唤醒失败", errText(e)),
  });
}
export function useMediaSleep() {
  const inv = useInvalidate();
  const mode = useApiMode();
  return useMutation({
    mutationFn: () => getApi().mediaSleep(),
    onSuccess: () => { inv([qk.services(mode), qk.resources(mode), qk.history(mode)]); toast.success("媒体服务已休眠"); },
    onError: (e) => toast.error("休眠失败", errText(e)),
  });
}

export function useUpload() {
  return useMutation({
    mutationFn: (file: File) => getApi().upload(file),
    onError: (e) => toast.error("上传失败", errText(e)),
  });
}

export function useResetSession() {
  const qc = useQueryClient();
  const mode = useApiMode();
  return useMutation({
    mutationFn: (sid: string) => getApi().resetSession(sid),
    onSuccess: (_r, sid) => {
      qc.invalidateQueries({ queryKey: qk.messages(mode, sid) });
      qc.invalidateQueries({ queryKey: qk.sessions(mode) });
      toast.success("会话已清空");
    },
    onError: (e) => toast.error("清空失败", errText(e)),
  });
}
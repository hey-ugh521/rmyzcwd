import { useMemo, useState } from "react";
import type { CapabilityPermission, CapabilityProvider } from "../../api/types";
import {
  useCapabilityInvocations, useCapabilityPermissions, useCapabilityProviderHealth,
  useCapabilityProviders, useCapabilityRetention, useDeleteCapabilityPermission,
  useSaveCapabilityPermission, useSaveCapabilityProvider,
} from "../../api/hooks";
import { toast } from "../../store/feedback";
import { Badge, Button, Card, Field, Input, Select, Tabs, Toggle } from "../../components/ui";
import { IconSliders, IconRefresh, IconTrash, IconCheck } from "../../components/icons";

type Tab = "permissions" | "providers" | "executions";

const TABS: { value: Tab; label: string }[] = [
  { value: "permissions", label: "权限" },
  { value: "providers", label: "Provider" },
  { value: "executions", label: "执行记录" },
];

function split(value: string): string[] {
  return value.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean);
}

function scopeText(scopeJson: string | undefined, key: string): string {
  try {
    const scope = JSON.parse(scopeJson || "{}");
    return Array.isArray(scope[key]) ? scope[key].join("，") : "";
  } catch {
    return "";
  }
}

export function PolicyPage() {
  const [tab, setTab] = useState<Tab>("permissions");
  const [editing, setEditing] = useState<CapabilityPermission | null>(null);
  const [subjectType, setSubjectType] = useState("source");
  const [subject, setSubject] = useState("qq");
  const [capability, setCapability] = useState("image.generate");
  const [effect, setEffect] = useState("allow");
  const [priority, setPriority] = useState("0");
  const [enabled, setEnabled] = useState(true);
  const [scopeCharacters, setScopeCharacters] = useState("");
  const [scopeSources, setScopeSources] = useState("");
  const [scopeUsers, setScopeUsers] = useState("");

  const permissions = useCapabilityPermissions();
  const providers = useCapabilityProviders();
  const health = useCapabilityProviderHealth();
  const invocations = useCapabilityInvocations(40);
  const retention = useCapabilityRetention();
  const savePermission = useSaveCapabilityPermission();
  const deletePermission = useDeleteCapabilityPermission();
  const saveProvider = useSaveCapabilityProvider();

  const healthMap = useMemo(() => {
    const map = new Map<string, { available: boolean; state: string }>();
    for (const h of health.data?.items ?? []) {
      map.set(h.provider, { available: h.available, state: h.state });
    }
    return map;
  }, [health.data]);

  const resetForm = () => {
    setEditing(null);
    setSubjectType("source");
    setSubject("qq");
    setCapability("image.generate");
    setEffect("allow");
    setPriority("0");
    setEnabled(true);
    setScopeCharacters("");
    setScopeSources("");
    setScopeUsers("");
  };

  const loadPermission = (p: CapabilityPermission) => {
    setEditing(p);
    setSubjectType(p.subject_type);
    setSubject(p.subject);
    setCapability(p.capability);
    setEffect(p.effect);
    setPriority(String(p.priority));
    setEnabled(!!p.enabled);
    setScopeCharacters(scopeText(p.scope_json, "characters"));
    setScopeSources(scopeText(p.scope_json, "sources"));
    setScopeUsers(scopeText(p.scope_json, "users"));
  };

  const submitPermission = () => {
    const scope: Record<string, string[]> = {};
    if (scopeCharacters.trim()) scope.characters = split(scopeCharacters);
    if (scopeSources.trim()) scope.sources = split(scopeSources);
    if (scopeUsers.trim()) scope.users = split(scopeUsers);
    savePermission.mutate({
      subject_type: subjectType,
      subject,
      capability,
      effect,
      priority: Number(priority) || 0,
      enabled,
      scope,
    });
    resetForm();
  };

  const updateProvider = (row: CapabilityProvider, patch: Partial<CapabilityProvider>) => {
    saveProvider.mutate({
      capability: row.capability,
      provider: row.provider,
      priority: patch.priority ?? row.priority,
      enabled: patch.enabled ?? !!row.enabled,
      constraints: (() => { try { return JSON.parse(row.constraints_json || "{}"); } catch { return {}; } })(),
    });
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-faint">Capabilities · Policy</p>
          <h1 className="mt-1 font-display text-xl font-semibold text-ink">策略管理</h1>
          <p className="mt-1 text-xs text-muted">能力权限、Provider 优先级与健康、执行记录。默认权限为 allow。</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Badge tone="brand">live 可写</Badge>
          <Button variant="ghost" size="sm" icon={<IconRefresh size={14} />} loading={health.isFetching}
            onClick={() => { health.refetch(); permissions.refetch(); providers.refetch(); invocations.refetch(); }}>
            刷新
          </Button>
        </div>
      </header>

      <Tabs tabs={TABS} value={tab} onChange={setTab} />

      {tab === "permissions" && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_380px]">
          <Card className="p-5">
            <div className="flex items-center gap-2">
              <p className="text-sm font-semibold text-ink">权限表</p>
              <span className="text-[11px] text-faint">default_allow={String(permissions.data?.default_allow ?? true)}</span>
              <Button variant="soft" size="sm" className="ml-auto" onClick={resetForm}>新建规则</Button>
            </div>
            {!permissions.data?.items.length ? (
              <p className="mt-4 text-xs text-faint">当前没有显式权限规则，使用 default_allow。</p>
            ) : (
              <div className="mt-3 divide-y divide-line-soft/60">
                {permissions.data.items.map((p) => (
                  <button key={p.id} onClick={() => loadPermission(p)}
                    className={`flex w-full items-center gap-2 py-2.5 text-left text-xs ${editing?.id === p.id ? "bg-brand/10" : "hover:bg-raised/40"}`}>
                    <Badge tone={p.effect === "deny" ? "danger" : "muted"}>{p.effect}</Badge>
                    <span className="font-mono text-ink">{p.subject_type}:{p.subject}</span>
                    <span className="text-faint">{p.capability}</span>
                    <span className="text-faint">p{p.priority}</span>
                    <span className="ml-auto text-faint">{p.enabled ? "enabled" : "disabled"}</span>
                    <IconTrash size={12} className="text-danger" onClick={(e) => {
                      e.stopPropagation();
                      deletePermission.mutate(p.id);
                    }} />
                  </button>
                ))}
              </div>
            )}
          </Card>

          <Card className="p-5">
            <p className="text-sm font-semibold text-ink">{editing ? `编辑规则 ${editing.id}` : "新建权限规则"}</p>
            <div className="mt-3 space-y-3">
              <Field label="Subject 类型">
                <Select value={subjectType} onChange={(v) => setSubjectType(v)}
                  options={[
                    { value: "source", label: "source" }, { value: "actor", label: "actor" },
                    { value: "user", label: "user" }, { value: "*", label: "*" },
                  ]} />
              </Field>
              <Field label="Subject"><Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="qq / console / user-id / *" /></Field>
              <Field label="Capability">
                <Select value={capability} onChange={(v) => setCapability(v)}
                  options={[
                    { value: "*", label: "*" }, { value: "image.generate", label: "image.generate" },
                    { value: "voice.synthesize", label: "voice.synthesize" },
                  ]} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Effect">
                  <Select value={effect} onChange={(v) => setEffect(v)}
                    options={[{ value: "allow", label: "allow" }, { value: "deny", label: "deny" }]} />
                </Field>
                <Field label="Priority"><Input value={priority} onChange={(e) => setPriority(e.target.value)} /></Field>
              </div>
              <Field label="Scope · characters" hint="逗号分隔；留空表示不限制">
                <Input value={scopeCharacters} onChange={(e) => setScopeCharacters(e.target.value)} placeholder="bronya，vita" />
              </Field>
              <Field label="Scope · sources">
                <Input value={scopeSources} onChange={(e) => setScopeSources(e.target.value)} placeholder="qq，console" />
              </Field>
              <Field label="Scope · users">
                <Input value={scopeUsers} onChange={(e) => setScopeUsers(e.target.value)} placeholder="user-id" />
              </Field>
              <Toggle checked={enabled} onChange={setEnabled} label="启用" />
              <div className="flex gap-2">
                <Button variant="primary" size="sm" onClick={submitPermission} loading={savePermission.isPending}>保存规则</Button>
                {editing && <Button variant="ghost" size="sm" onClick={resetForm}>取消编辑</Button>}
              </div>
            </div>
          </Card>
        </div>
      )}

      {tab === "providers" && (
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-ink">Provider 策略</p>
            <span className="text-[11px] text-faint">priority 越小越优先；不可用 Provider 会被跳过</span>
          </div>
          <div className="mt-3 divide-y divide-line-soft/60">
            {(providers.data?.items ?? []).map((row) => {
              const h = healthMap.get(row.provider);
              return (
                <div key={`${row.capability}:${row.provider}`} className="flex flex-wrap items-center gap-2 py-3 text-xs">
                  <span className="font-mono text-ink">{row.capability}</span>
                  <span className="text-faint">/</span>
                  <span className="text-brand-soft">{row.provider}</span>
                  <Badge tone={h?.available === false ? "danger" : h?.available ? "brand" : "muted"}>
                    {h ? h.state : "unknown"}
                  </Badge>
                  <span className="ml-auto text-faint">priority</span>
                  <Input className="h-7 w-20 text-xs" defaultValue={String(row.priority)}
                    onBlur={(e) => updateProvider(row, { priority: Number(e.target.value) || row.priority })} />
                  <Toggle checked={!!row.enabled} onChange={(v) => updateProvider(row, { enabled: v ? 1 : 0 })} />
                  <IconCheck size={12} className={row.enabled ? "text-success" : "text-faint"} />
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {tab === "executions" && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_300px]">
          <Card className="p-5">
            <p className="text-sm font-semibold text-ink">最近执行</p>
            <div className="mt-3 divide-y divide-line-soft/60">
              {(invocations.data?.items ?? []).map((r) => (
                <div key={r.id} className="flex items-center gap-2 py-2 text-[11px]">
                  <span className="font-mono text-ink">{r.capability}</span>
                  <Badge tone={r.status === "completed" || r.status === "accepted" ? "brand" : r.status === "failed" ? "danger" : "muted"}>{r.status}</Badge>
                  <span className="text-faint">{r.character_id} r{r.character_revision ?? "—"}</span>
                  <span className="text-faint">{r.workflow_id ?? "no-workflow"}</span>
                  <span className="text-faint">{r.provider}</span>
                  <span className="ml-auto text-faint">{new Date(r.created_at * 1000).toLocaleString()}</span>
                </div>
              ))}
              {!invocations.data?.items.length && <p className="py-3 text-xs text-faint">还没有执行记录。</p>}
            </div>
          </Card>
          <Card className="p-5">
            <p className="text-sm font-semibold text-ink">保留策略</p>
            <p className="mt-2 text-xs text-muted">保留 {retention.data?.retention_days ?? 30} 天</p>
            <p className="mt-1 text-xs text-muted">最多 {retention.data?.max_records ?? 10000} 条</p>
            <p className="mt-4 text-[11px] text-faint">prune 在 Core 启动时自动执行；手动触发需调用 API。</p>
            <Button variant="ghost" size="sm" className="mt-4" icon={<IconSliders size={13} />}
              onClick={() => toast.info("使用 POST /api/capabilities/maintenance/prune 手动执行")}>
              维护说明
            </Button>
          </Card>
        </div>
      )}
    </div>
  );
}

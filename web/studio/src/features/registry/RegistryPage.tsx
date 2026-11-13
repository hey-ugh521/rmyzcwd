import { useMemo, useState } from "react";
import type { RegistryIndexCharacter, RegistryIndexModel, RegistryIndexVoice, RegistryIndexWorkflow } from "../../api/types";
import { useRegistryIndex } from "../../api/hooks";
import { Badge, Button, Card, EmptyState, Input, Skeleton } from "../../components/ui";
import { IconCharacters, IconSearch, IconVolume, IconImage, IconExternal, IconRefresh } from "../../components/icons";

type Tab = "characters" | "voices" | "workflows";

const TABS: { id: Tab; label: string; icon: typeof IconCharacters }[] = [
  { id: "characters", label: "角色", icon: IconCharacters },
  { id: "voices", label: "音色", icon: IconVolume },
  { id: "workflows", label: "工作流", icon: IconImage },
];

function Stat({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-[11px] uppercase tracking-[0.16em] text-faint">{label}</p>
      <p className="mt-1 font-display text-2xl font-semibold text-ink">{value}</p>
      {hint && <p className="mt-1 truncate text-[11px] text-muted">{hint}</p>}
    </Card>
  );
}

function DetailRow({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div className="flex gap-3 border-b border-line-soft/60 py-2 last:border-0">
      <span className="w-20 flex-none text-[11px] text-faint">{label}</span>
      <span className="min-w-0 flex-1 break-words text-xs text-ink">{value ?? "—"}</span>
    </div>
  );
}

export function RegistryPage() {
  const q = useRegistryIndex();
  const [tab, setTab] = useState<Tab>("characters");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const data = q.data;
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!data) return [];
    const match = (text: string) => !needle || text.toLowerCase().includes(needle);
    if (tab === "characters") {
      return data.characters.filter((c) => match([c.id, c.name, ...(c.aliases ?? [])].join(" ")));
    }
    if (tab === "voices") {
      return data.voices.filter((v) => match([v.id, v.name, v.engine, v.voice, ...(v.used_by ?? [])].join(" ")));
    }
    return data.workflows.filter((w) => match([w.id, w.name, w.format, ...(w.class_types ?? [])].join(" ")));
  }, [data, tab, search]);

  const selectedCharacter = tab === "characters" ? data?.characters.find((x) => x.id === selected) : undefined;
  const selectedVoice = tab === "voices" ? data?.voices.find((x) => x.id === selected) : undefined;
  const selectedWorkflow = tab === "workflows" ? data?.workflows.find((x) => x.id === selected) : undefined;

  const switchTab = (next: Tab) => {
    setTab(next);
    setSelected(null);
    setSearch("");
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-faint">Registry · Phase 1</p>
          <h1 className="mt-1 font-display text-xl font-semibold text-ink">能力库</h1>
          <p className="mt-1 text-xs text-muted">只读索引现有角色、音色与 ComfyUI 工作流，不改动运行时。</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Badge tone="brand">只读</Badge>
          <Button variant="ghost" size="sm" icon={<IconRefresh size={14} />} loading={q.isFetching} onClick={() => q.refetch()}>
            刷新索引
          </Button>
        </div>
      </header>

      {q.isError ? (
        <Card className="border-danger/30 p-5">
          <p className="text-sm font-medium text-danger">索引读取失败</p>
          <p className="mt-1 text-xs text-muted">{q.error instanceof Error ? q.error.message : "未知错误"}</p>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="角色" value={data?.summary.characters ?? 0} hint="legacy character JSON" />
            <Stat label="音色" value={data?.summary.voices ?? 0} hint="from character cards" />
            <Stat label="工作流" value={data?.summary.workflows ?? 0} hint={data?.summary.workflow_dir ?? "未发现目录"} />
            <Stat label="图像模型" value={data?.summary.image_models ?? 0} hint="models.json" />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-lg border border-line-soft bg-raised/40 p-1">
              {TABS.map((t) => {
                const Icon = t.icon;
                return (
                  <button key={t.id} onClick={() => switchTab(t.id)}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-colors ${tab === t.id ? "bg-brand/15 text-brand-soft" : "text-muted hover:text-ink"}`}>
                    <Icon size={14} />{t.label}
                  </button>
                );
              })}
            </div>
            <div className="relative ml-auto w-full sm:w-72">
              <IconSearch size={14} className="pointer-events-none absolute left-3 top-2.5 text-faint" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索当前索引…" className="pl-8" />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <Card className="overflow-hidden">
              {q.isLoading ? (
                <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}</div>
              ) : filtered.length === 0 ? (
                <EmptyState icon={<IconSearch size={20} />} title="没有匹配项" desc="换个关键词，或确认对应目录存在。" />
              ) : (
                <div className="divide-y divide-line-soft/60">
                  {(tab === "characters" ? filtered as RegistryIndexCharacter[] : []).map((c) => (
                    <button key={c.id} onClick={() => setSelected(c.id)}
                      className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors ${selected === c.id ? "bg-brand/10" : "hover:bg-raised/40"}`}>
                      <span className="grid h-9 w-9 flex-none place-items-center rounded-lg border border-line-soft bg-raised/60 text-brand-soft"><IconCharacters size={16} /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-ink">{c.name}</span>
                        <span className="block truncate text-[11px] text-faint">{c.id} · {c.workflow || "无默认工作流"} · {c.source}</span>
                      </span>
                      <Badge tone="muted">{c.status}</Badge>
                    </button>
                  ))}
                  {(tab === "voices" ? filtered as RegistryIndexVoice[] : []).map((v) => (
                    <button key={v.id} onClick={() => setSelected(v.id)}
                      className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors ${selected === v.id ? "bg-brand/10" : "hover:bg-raised/40"}`}>
                      <span className="grid h-9 w-9 flex-none place-items-center rounded-lg border border-line-soft bg-raised/60 text-brand-soft"><IconVolume size={16} /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-ink">{v.name}</span>
                        <span className="block truncate text-[11px] text-faint">{v.engine} / {v.voice} · {v.used_by.length} 个角色引用</span>
                      </span>
                      <Badge tone="muted">{v.status}</Badge>
                    </button>
                  ))}
                  {(tab === "workflows" ? filtered as RegistryIndexWorkflow[] : []).map((w) => (
                    <button key={w.id} onClick={() => setSelected(w.id)}
                      className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors ${selected === w.id ? "bg-brand/10" : "hover:bg-raised/40"}`}>
                      <span className="grid h-9 w-9 flex-none place-items-center rounded-lg border border-line-soft bg-raised/60 text-brand-soft"><IconImage size={16} /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-ink">{w.name}</span>
                        <span className="block truncate text-[11px] text-faint">{w.format} · {w.node_count} nodes · {w.used_by?.length || 0} 个角色引用</span>
                      </span>
                      <Badge tone={w.format === "ui_workflow" ? "warning" : "muted"}>{w.format === "ui_workflow" ? "UI JSON" : w.format}</Badge>
                    </button>
                  ))}
                </div>
              )}
            </Card>

            <Card className="p-5">
              {!selectedCharacter && !selectedVoice && !selectedWorkflow ? (
                <div className="grid min-h-[300px] place-items-center text-center">
                  <div>
                    <IconSearch size={24} className="mx-auto text-faint" />
                    <p className="mt-3 text-sm font-medium text-ink">选择一项查看详情</p>
                    <p className="mt-1 text-xs text-muted">只读索引不会修改原始文件。</p>
                  </div>
                </div>
              ) : selectedCharacter ? (
                <div>
                  <div className="flex items-start gap-2">
                    <div>
                      <h2 className="font-display text-base font-semibold text-ink">{selectedCharacter.name}</h2>
                      <p className="text-[11px] text-faint">{selectedCharacter.id}</p>
                    </div>
                    <Badge tone="muted" className="ml-auto">{selectedCharacter.status}</Badge>
                  </div>
                  <div className="mt-4">
                    <DetailRow label="来源" value={selectedCharacter.source} />
                    <DetailRow label="类型" value={selectedCharacter.source_kind} />
                    <DetailRow label="工作流" value={selectedCharacter.workflow} />
                    <DetailRow label="底模" value={selectedCharacter.checkpoint} />
                    <DetailRow label="尺寸" value={selectedCharacter.size?.join(" × ")} />
                    <DetailRow label="音色" value={`${selectedCharacter.voice?.engine || "—"} / ${selectedCharacter.voice?.voice || "—"}`} />
                    <DetailRow label="LoRA" value={selectedCharacter.loras?.length ? `${selectedCharacter.loras.length} 个` : "—"} />
                    <DetailRow label="校验和" value={selectedCharacter.checksum?.slice(0, 16)} />
                  </div>
                  {selectedCharacter.notes && <p className="mt-3 rounded-lg bg-raised/50 p-3 text-xs leading-relaxed text-muted">{selectedCharacter.notes}</p>}
                  <Button variant="ghost" size="sm" className="mt-4" icon={<IconExternal size={13} />} onClick={() => window.location.hash = "/characters"}>
                    去角色编辑
                  </Button>
                </div>
              ) : selectedVoice ? (
                <div>
                  <h2 className="font-display text-base font-semibold text-ink">{selectedVoice.name}</h2>
                  <div className="mt-4">
                    <DetailRow label="引擎" value={selectedVoice.engine} />
                    <DetailRow label="Voice" value={selectedVoice.voice} />
                    <DetailRow label="语速" value={selectedVoice.speed} />
                    <DetailRow label="模型" value={selectedVoice.model} />
                    <DetailRow label="引用角色" value={selectedVoice.used_by.join("、") || "未被引用"} />
                  </div>
                </div>
              ) : selectedWorkflow ? (
                <div>
                  <h2 className="font-display text-base font-semibold text-ink">{selectedWorkflow.name}</h2>
                  <div className="mt-4">
                    <DetailRow label="来源" value={selectedWorkflow.source} />
                    <DetailRow label="格式" value={selectedWorkflow.format} />
                    <DetailRow label="节点数" value={selectedWorkflow.node_count} />
                    <DetailRow label="节点类型" value={selectedWorkflow.class_types.join("、") || "—"} />
                    <DetailRow label="引用角色" value={selectedWorkflow.used_by?.join("、") || "未被角色引用"} />
                    <DetailRow label="校验和" value={selectedWorkflow.checksum?.slice(0, 16)} />
                  </div>
                  <p className="mt-3 rounded-lg bg-raised/50 p-3 text-[11px] leading-relaxed text-muted">
                    Phase 1 只展示索引；工作流导入/验证/试跑属于 Phase 2。
                  </p>
                </div>
              ) : null}
            </Card>
          </div>

          {tab === "characters" && data && (
            <Card className="p-4">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-xs font-medium text-ink">图像模型注册表</p>
                <span className="text-[11px] text-faint">来自 models.json</span>
                <div className="ml-auto flex flex-wrap gap-1.5">
                  {data.image_models.slice(0, 8).map((m: RegistryIndexModel) => (
                    <Badge key={m.id} tone={m.available === false ? "warning" : "muted"}>{m.name}</Badge>
                  ))}
                </div>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

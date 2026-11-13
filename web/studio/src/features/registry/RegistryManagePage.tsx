import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { RegistryResourceType } from "../../api/types";
import {
  useImportLegacyRegistry, usePublishRegistryResource, useRegistryResource,
  useRegistryResources, useRegistryRevisions, useSaveRegistryResource, useTestRegistryWorkflow,
  useVoiceAssets, useUploadVoiceAsset, useDeleteVoiceAsset,
} from "../../api/hooks";
import { getApi } from "../../api";
import { useApiMode } from "../../api/hooks";
import { toast } from "../../store/feedback";
import { Badge, Button, Card, EmptyState, Field, Textarea } from "../../components/ui";
import { CharacterForm } from "./CharacterForm";
import { IconCharacters, IconImage, IconPlus, IconRefresh, IconUpload, IconVolume, IconWarning, IconTrash } from "../../components/icons";

const TYPES: { type: RegistryResourceType; label: string; icon: typeof IconCharacters }[] = [
  { type: "character", label: "角色", icon: IconCharacters },
  { type: "voice_profile", label: "音色", icon: IconVolume },
  { type: "workflow", label: "工作流", icon: IconImage },
];

export function RegistryManagePage() {
  const qc = useQueryClient();
  const mode = useApiMode();
  const [type, setType] = useState<RegistryResourceType>("character");
  const [selected, setSelected] = useState<string | null>(null);
  const [jsonText, setJsonText] = useState("");
  const [testResult, setTestResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const charFile = useRef<HTMLInputElement>(null);
  const workflowFile = useRef<HTMLInputElement>(null);
  const voiceFile = useRef<HTMLInputElement>(null);

  const list = useRegistryResources(type);
  const detail = useRegistryResource(type, selected);
  const revisions = useRegistryRevisions(type, selected);
  const importLegacy = useImportLegacyRegistry();
  const save = useSaveRegistryResource();
  const publish = usePublishRegistryResource();
  const test = useTestRegistryWorkflow();
  const assets = useVoiceAssets();
  const uploadAsset = useUploadVoiceAsset();
  const deleteAsset = useDeleteVoiceAsset();

  useEffect(() => {
    const payload = detail.data?.current?.payload;
    if (payload) setJsonText(JSON.stringify(payload, null, 2));
    else setJsonText("");
  }, [detail.data?.current?.revision, selected]);

  const switchType = (next: RegistryResourceType) => {
    setType(next);
    setSelected(null);
    setJsonText("");
    setTestResult(null);
  };

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["mobius", mode, "registry-resources"] });
    qc.invalidateQueries({ queryKey: ["mobius", mode, "registry-resource"] });
    qc.invalidateQueries({ queryKey: ["mobius", mode, "registry-index"] });
  };

  const importChar = async (file: File) => {
    setBusy(true);
    try {
      const card = JSON.parse(await file.text());
      await getApi().importRegistryCharacter(card);
      toast.success("角色卡已导入为 draft");
      invalidate();
    } catch (e) {
      toast.error("角色卡导入失败", e instanceof Error ? e.message : "无法解析 JSON");
    } finally {
      setBusy(false);
    }
  };

  const importWorkflow = async (file: File) => {
    setBusy(true);
    try {
      const payload = JSON.parse(await file.text());
      const report = await getApi().validateRegistryWorkflow(payload, true) as any;
      const validation = report?.validation ?? report;
      if (validation?.status !== "valid") {
        const errors = validation?.errors?.join("；") || "验证失败";
        toast.error("工作流验证未通过", errors);
        return;
      }
      await getApi().importRegistryWorkflow(payload, file.name.replace(/\.json$/i, ""));
      toast.success("工作流已导入为 draft");
      invalidate();
    } catch (e) {
      toast.error("工作流导入失败", e instanceof Error ? e.message : "无法解析 JSON");
    } finally {
      setBusy(false);
    }
  };

  const saveDraft = () => {
    if (!selected || !detail.data) return;
    try {
      const payload = JSON.parse(jsonText);
      save.mutate({ type, id: selected, payload, expectedRevision: detail.data.current_revision });
    } catch (e) {
      toast.error("JSON 不合法", e instanceof Error ? e.message : "无法解析");
    }
  };

  const publishCurrent = () => {
    if (!detail.data) return;
    publish.mutate({
      type, id: detail.data.id, revision: detail.data.current_revision,
      expectedCurrent: detail.data.current_revision,
      expectedPublished: detail.data.published_revision,
    });
  };

  const publishRevision = (revision: number) => {
    if (!detail.data) return;
    publish.mutate({
      type, id: detail.data.id, revision,
      expectedCurrent: detail.data.current_revision,
      expectedPublished: detail.data.published_revision,
    });
  };

  const runTest = async () => {
    if (!selected) return;
    try {
      const res = await test.mutateAsync(selected);
      setTestResult(res);
    } catch {
      setTestResult(null);
    }
  };

  const uploadVoice = (file: File) => {
    uploadAsset.mutate({ file, kind: "reference_audio", source: "studio-upload" });
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-faint">Registry · Phase 2A</p>
          <h1 className="mt-1 font-display text-xl font-semibold text-ink">资源管理</h1>
          <p className="mt-1 text-xs text-muted">导入现有可能资源为 draft；编辑产生 revision；发布后运行时才可见（Runtime 尚未接入）。</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <input ref={charFile} type="file" accept=".json,application/json" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importChar(f); e.currentTarget.value = ""; }} />
          <input ref={workflowFile} type="file" accept=".json,application/json" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importWorkflow(f); e.currentTarget.value = ""; }} />
          <Button variant="ghost" size="sm" icon={<IconUpload size={14} />} onClick={() => charFile.current?.click()} loading={busy}>导入角色卡</Button>
          <Button variant="ghost" size="sm" icon={<IconUpload size={14} />} onClick={() => workflowFile.current?.click()} loading={busy}>导入工作流</Button>
          <Button variant="primary" size="sm" icon={<IconPlus size={14} />} onClick={() => importLegacy.mutate()} loading={importLegacy.isPending}>
            导入旧资源
          </Button>
        </div>
      </header>

      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          {TYPES.map((t) => {
            const Icon = t.icon;
            return (
              <button key={t.type} onClick={() => switchType(t.type)}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs ${type === t.type ? "bg-brand/15 text-brand-soft" : "text-muted hover:text-ink"}`}>
                <Icon size={14} />{t.label}
              </button>
            );
          })}
          <span className="ml-auto text-[11px] text-faint">
            {list.data ? `${list.data.total} 个 Registry 资源` : "读取中…"}
          </span>
          <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="刷新" onClick={() => list.refetch()}><IconRefresh size={14} /></Button>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <Card className="overflow-hidden">
          {list.isLoading ? (
            <div className="p-4 text-xs text-muted">读取中…</div>
          ) : !list.data?.items.length ? (
            <EmptyState icon={<IconPlus size={20} />} title="还没有 Registry 资源" desc="先点「导入旧资源」，或导入角色卡/API workflow。" />
          ) : (
            <div className="divide-y divide-line-soft/60">
              {list.data.items.map((r) => (
                <button key={r.id} onClick={() => { setSelected(r.id); setTestResult(null); }}
                  className={`flex w-full items-center gap-2 px-4 py-3 text-left ${selected === r.id ? "bg-brand/10" : "hover:bg-raised/40"}`}>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-ink">{r.id}</span>
                    <span className="block text-[11px] text-faint">current r{r.current_revision} · published {r.published_revision ?? "—"}</span>
                  </span>
                  <Badge tone={r.status === "active" ? "brand" : "muted"}>{r.status}</Badge>
                </button>
              ))}
            </div>
          )}
        </Card>

        <Card className="p-5">
          {!selected || !detail.data ? (
            <div className="grid min-h-[360px] place-items-center text-center">
              <div>
                <IconCharacters size={26} className="mx-auto text-faint" />
                <p className="mt-3 text-sm font-medium text-ink">选择一个资源管理</p>
                <p className="mt-1 text-xs text-muted">编辑 JSON 会产生新 revision；发布不会自动接入 Runtime。</p>
              </div>
            </div>
          ) : (
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <div>
                  <h2 className="font-display text-base font-semibold text-ink">{detail.data.id}</h2>
                  <p className="text-[11px] text-faint">
                    current r{detail.data.current_revision} · published r{detail.data.published_revision ?? "—"}
                  </p>
                </div>
                <Badge tone={detail.data.published_revision ? "brand" : "warning"} className="ml-auto">
                  {detail.data.published_revision ? "已发布" : "仅 draft"}
                </Badge>
                <Button variant="primary" size="sm" onClick={publishCurrent}
                  loading={publish.isPending} disabled={!detail.data.current}>
                  发布当前
                </Button>
                {type === "workflow" && (
                  <Button variant="soft" size="sm" onClick={runTest} loading={test.isPending}>
                    试跑 workflow
                  </Button>
                )}
              </div>

              {type === "character" && detail.data.current?.payload && (
                <CharacterForm
                  payload={detail.data.current.payload as Record<string, any>}
                  saving={save.isPending}
                  onSave={(payload) => save.mutate({
                    type, id: detail.data.id, payload, expectedRevision: detail.data.current_revision,
                  })}
                />
              )}

              <Field label="当前 revision payload（JSON）" hint="保存会创建新 draft；不会改变 published。">
                <Textarea rows={14} value={jsonText} onChange={(e) => setJsonText(e.target.value)} className="font-mono text-xs" />
              </Field>              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="soft" size="sm" onClick={saveDraft} loading={save.isPending}>保存为 draft</Button>
                <Button variant="ghost" size="sm" onClick={() => setJsonText(JSON.stringify(detail.data?.current?.payload ?? {}, null, 2))}>还原</Button>
              </div>

              {revisions.data?.items?.length ? (
                <div className="mt-5 border-t border-line-soft pt-4">
                  <p className="text-xs font-medium text-ink">版本历史</p>
                  <div className="mt-2 space-y-1">
                    {revisions.data.items.map((rev) => (
                      <div key={rev.revision} className="flex items-center gap-2 rounded-md bg-raised/40 px-2 py-1.5 text-[11px]">
                        <span className="font-mono text-ink">r{rev.revision}</span>
                        <span className="text-faint">{rev.state}</span>
                        <span className="truncate text-faint">{rev.change_note}</span>
                        {rev.revision !== detail.data?.current_revision && (
                          <button className="ml-auto text-brand-soft hover:underline" onClick={() => publishRevision(rev.revision)}>
                            发布此版本
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {testResult?.result?.result?.url && (
                <div className="mt-5 border-t border-line-soft pt-4">
                  <p className="text-xs font-medium text-ink">试跑结果</p>
                  <img src={testResult.result.result.url} alt="workflow test result"
                    className="mt-2 max-h-72 rounded-lg border border-line-soft object-contain" />
                  <p className="mt-1 text-[11px] text-faint">
                    {testResult.result.result.filename} · {testResult.result.seconds}s
                  </p>
                </div>
              )}

              {detail.data.published_revision === null && (
                <p className="mt-4 flex items-start gap-2 rounded-lg border border-warning/25 bg-warning/10 p-3 text-[11px] text-warning">
                  <IconWarning size={14} className="mt-0.5 flex-none" />
                  该资源尚未发布；Agent/Gateway 仍不会读取 Registry。
                </p>
              )}
            </div>
          )}
        </Card>
      </div>

      {type === "voice_profile" && (
        <Card className="p-5">
          <div className="flex flex-wrap items-center gap-2">
            <div>
              <p className="text-sm font-semibold text-ink">VoiceAsset 素材库</p>
              <p className="mt-1 text-[11px] text-muted">参考音频、模型权重、预览缓存；软删除，不直接删除文件。</p>
            </div>
            <input ref={voiceFile} type="file" accept=".wav,.mp3,.flac,.ogg,.safetensors,.pt,.pth,.bin,.json" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadVoice(f); e.currentTarget.value = ""; }} />
            <Button variant="soft" size="sm" className="ml-auto" icon={<IconUpload size={14} />}
              loading={uploadAsset.isPending} onClick={() => voiceFile.current?.click()}>
              上传素材
            </Button>
          </div>
          {!assets.data?.items.length ? (
            <p className="mt-4 text-xs text-faint">还没有 VoiceAsset。</p>
          ) : (
            <div className="mt-3 divide-y divide-line-soft/60">
              {assets.data.items.map((a) => (
                <div key={a.id} className="flex items-center gap-2 py-2 text-xs">
                  <span className="font-mono text-faint">{a.id}</span>
                  <span className="truncate text-ink">{a.filename}</span>
                  <Badge tone="muted">{a.kind}</Badge>
                  <span className="text-faint">{(a.size / 1024).toFixed(1)} KB</span>
                  <button className="ml-auto text-danger hover:underline" onClick={() => deleteAsset.mutate(a.id)}>
                    <IconTrash size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

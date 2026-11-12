import { useEffect, useMemo, useRef, useState } from "react";
import type { Character } from "../../api/types";
import {
  useCharacters, useConstitution, useSaveCharacter, useSaveConstitution,
  useVoices, useWorkflows,
} from "../../api/hooks";
import { toast } from "../../store/feedback";
import {
  Card, Button, Badge, Field, Input, Textarea, Select, Toggle, Skeleton, EmptyState, Tooltip,
} from "../../components/ui";
import {
  IconCharacters, IconPlus, IconSearch, IconDownload, IconClose, IconCheck,
} from "../../components/icons";

const splitList = (s: string) => s.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean);
const listText = (v: unknown) => Array.isArray(v) ? v.join("，") : "";

function emptyCharacter(): Character {
  return {
    id: `char_${Date.now().toString(36)}`,
    name: "新角色",
    aliases: [],
    style_prompt: "",
    negative_prompt: "",
    workflow: "portrait",
    checkpoint: "",
    size: [832, 1216],
    loras: [],
    voice: { engine: "kokoro", voice: "zf_xiaobei", speed: 1 },
    media_policy: { allow_image: true, allow_voice: true, allow_clone: false },
    speech_style: "",
    relation_stance: "",
    taboo: [],
    visual_anchor: "",
    notes: "",
  };
}

export function CharactersPage() {
  const chars = useCharacters();
  const workflows = useWorkflows();
  const voices = useVoices();
  const saveChar = useSaveCharacter();
  const saveCons = useSaveConstitution();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Character | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [search, setSearch] = useState("");
  const [promptText, setPromptText] = useState("");
  const [promptNote, setPromptNote] = useState("");
  const [promptLoadedFor, setPromptLoadedFor] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const list = chars.data?.characters ?? [];
  const selected = list.find((c) => c.id === selectedId) ?? null;
  const constitution = useConstitution(selectedId && !isNew ? selectedId : null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return list;
    return list.filter((c) =>
      [c.id, c.name, ...(c.aliases ?? [])].join(" ").toLowerCase().includes(q));
  }, [list, search]);

  // 选中已有角色：载入角色卡字段；系统提示词等 API 返回后再填充，避免覆盖正在输入。
  useEffect(() => {
    if (!selected) return;
    setDraft({ ...selected });
    setIsNew(false);
    setPromptLoadedFor(null);
    setPromptText(typeof selected.constitution === "string" ? selected.constitution : "");
    setPromptNote("");
  }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!constitution.data || !selectedId || isNew) return;
    const active = constitution.data.active?.text
      ?? constitution.data.fallback
      ?? selected?.constitution
      ?? "";
    if (promptLoadedFor !== selectedId) {
      setPromptText(active);
      setPromptLoadedFor(selectedId);
    }
  }, [constitution.data, selectedId, isNew, selected?.constitution, promptLoadedFor]);

  const select = (c: Character) => {
    setSelectedId(c.id);
    setDraft({ ...c });
    setIsNew(false);
    setPromptLoadedFor(null);
  };

  const createNew = () => {
    const c = emptyCharacter();
    setSelectedId(c.id);
    setDraft(c);
    setIsNew(true);
    setPromptText("");
    setPromptNote("");
    setPromptLoadedFor(c.id);
  };

  const patch = (p: Partial<Character>) => setDraft((d) => d ? { ...d, ...p } : d);

  const saveAll = async () => {
    if (!draft) return;
    const id = draft.id.trim();
    if (!/^[a-zA-Z0-9_-]+$/.test(id)) {
      toast.warning("角色 ID 非法", "只能包含字母、数字、下划线、连字符。");
      return;
    }
    if (!draft.name?.trim()) {
      toast.warning("请填写角色名称");
      return;
    }
    setSaving(true);
    try {
      const body: Partial<Character> = {
        ...draft,
        id,
        name: draft.name.trim(),
        aliases: splitList(listText(draft.aliases)),
        taboo: splitList(listText(draft.taboo)),
        size: [
          Math.max(64, Number(draft.size?.[0] ?? 832)),
          Math.max(64, Number(draft.size?.[1] ?? 1216)),
        ],
      };
      await saveChar.mutateAsync({ id, body });
      if (promptText.trim().length >= 10) {
        await saveCons.mutateAsync({ id, text: promptText.trim(), note: promptNote.trim() });
      }
      setSelectedId(id);
      setIsNew(false);
      setPromptLoadedFor(id);
    } catch {
      // toast 已在 mutation 中处理
    } finally {
      setSaving(false);
    }
  };

  const onImport = async (file: File) => {
    try {
      const raw = JSON.parse(await file.text());
      const card = raw?.character ?? raw;
      if (!card || typeof card !== "object" || !card.id || !card.name) {
        toast.error("角色卡格式不对", "需要 JSON，且至少包含 id 和 name。");
        return;
      }
      const id = String(card.id).replace(/[^a-zA-Z0-9_-]/g, "_");
      await saveChar.mutateAsync({ id, body: { ...card, id } });
      setSelectedId(id);
      toast.success("角色卡已导入", String(card.name));
    } catch (e) {
      toast.error("导入失败", e instanceof Error ? e.message : "无法解析 JSON");
    }
  };

  const doExport = () => {
    if (!draft) return;
    const data = { ...draft, aliases: splitList(listText(draft.aliases)), taboo: splitList(listText(draft.taboo)) };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${draft.id || "character"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-faint">Characters</p>
          <h1 className="mt-1 font-display text-xl font-semibold text-ink">角色与系统提示词</h1>
          <p className="mt-1 text-xs text-muted">角色卡、人格宪法、生图/语音默认参数。保存后用于 Studio 对话与创作。</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <input ref={fileRef} type="file" accept=".json,application/json" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onImport(f); e.currentTarget.value = ""; }} />
          <Button variant="ghost" size="sm" onClick={() => fileRef.current?.click()}>导入角色卡</Button>
          <Button variant="primary" size="sm" icon={<IconPlus size={15} />} onClick={createNew}>新建角色</Button>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
        <Card className="overflow-hidden">
          <div className="border-b border-line-soft p-3">
            <div className="relative">
              <IconSearch size={14} className="pointer-events-none absolute left-3 top-2.5 text-faint" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索角色 / 别名…" className="pl-8" />
            </div>
          </div>
          <div className="max-h-[calc(100vh-260px)] overflow-y-auto p-2">
            {chars.isLoading ? (
              <div className="space-y-2 p-1">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}</div>
            ) : filtered.length === 0 ? (
              <EmptyState icon={<IconCharacters size={20} />} title="没有角色" desc="新建一个角色，或导入角色卡 JSON。" />
            ) : filtered.map((c) => (
              <button key={c.id} onClick={() => select(c)}
                className={`flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition-colors ${c.id === selectedId ? "bg-brand/12 ring-1 ring-brand/25" : "hover:bg-raised/60"}`}>
                <span className="grid h-9 w-9 flex-none place-items-center rounded-lg border border-line-soft bg-raised/60 text-brand-soft">
                  <IconCharacters size={17} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink">{c.name || c.id}</span>
                  <span className="block truncate text-[11px] text-faint">{c.id}{c.workflow ? ` · ${c.workflow}` : ""}</span>
                </span>
                {c.id === selectedId && <IconCheck size={14} className="flex-none text-brand-soft" />}
              </button>
            ))}
          </div>
        </Card>

        {!draft ? (
          <Card className="grid min-h-[420px] place-items-center p-8 text-center">
            <div>
              <IconCharacters size={30} className="mx-auto text-faint" />
              <p className="mt-3 text-sm font-medium text-ink">选择一个角色开始编辑</p>
              <p className="mt-1 text-xs text-muted">系统提示词、角色卡字段、生图与语音默认值都从这里改。</p>
            </div>
          </Card>
        ) : (
          <div className="space-y-4">
            <Card className="p-5">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-display text-base font-semibold text-ink">{isNew ? "新建角色" : draft.name || draft.id}</h2>
                <Badge tone={isNew ? "warning" : "brand"}>{isNew ? "未保存" : "已保存"}</Badge>
                <div className="ml-auto flex gap-2">
                  <Button variant="ghost" size="sm" icon={<IconDownload size={14} />} onClick={doExport}>导出 JSON</Button>
                  <Button variant="primary" size="sm" icon={<IconCheck size={14} />} loading={saving || saveChar.isPending || saveCons.isPending} onClick={saveAll}>保存全部</Button>
                </div>
              </div>

              <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
                <Field label="角色 ID" hint="只能用字母、数字、下划线、连字符；保存后不建议修改。">
                  <Input value={draft.id} disabled={!isNew} onChange={(e) => patch({ id: e.target.value })} />
                </Field>
                <Field label="显示名称">
                  <Input value={draft.name ?? ""} onChange={(e) => patch({ name: e.target.value })} />
                </Field>
                <Field label="别名" hint="用逗号或换行分隔，用于对话/搜索匹配。">
                  <Input value={listText(draft.aliases)} onChange={(e) => patch({ aliases: splitList(e.target.value) })} />
                </Field>
                <Field label="视觉锚点">
                  <Input value={draft.visual_anchor ?? ""} onChange={(e) => patch({ visual_anchor: e.target.value })}
                    placeholder="例如：银发、红瞳、黑色制服" />
                </Field>
              </div>
            </Card>

            <Card className="p-5">
              <div className="flex flex-wrap items-start gap-2">
                <div>
                  <h3 className="font-display text-sm font-semibold text-ink">系统提示词 / 人格宪法</h3>
                  <p className="mt-1 text-xs text-muted">
                    Core 对话实际使用的系统提示词。保存后会生成新版本，旧版本保留可追溯。
                  </p>
                </div>
                <Badge tone="brand" className="ml-auto">
                  {constitution.data?.active?.version ? `v${constitution.data.active.version}` : "未保存"}
                </Badge>
              </div>
              <Field label="提示词正文" hint="至少 10 个字符。不要写密钥/隐私信息。">
                <Textarea rows={8} value={promptText} onChange={(e) => setPromptText(e.target.value)}
                  placeholder="你是……。你的核心立场是……；不可越过的边界是……" />
              </Field>
              <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
                <Field label="版本备注（可选）">
                  <Input value={promptNote} onChange={(e) => setPromptNote(e.target.value)} placeholder="例如：收紧边界 / 调整语气" />
                </Field>
                <div className="flex items-end">
                  <Button variant="primary" size="sm" icon={<IconCheck size={14} />}
                    loading={saveCons.isPending}
                    onClick={() => draft && promptText.trim().length >= 10
                      ? saveCons.mutate({ id: draft.id, text: promptText.trim(), note: promptNote.trim() })
                      : toast.warning("系统提示词至少需要 10 个字符")}>
                    只保存提示词
                  </Button>
                </div>
              </div>
              {!!constitution.data?.versions?.length && (
                <p className="mt-2 text-[11px] text-faint">历史版本：{constitution.data.versions.length} 个（最近：v{constitution.data.versions[0]?.version}）</p>
              )}
            </Card>

            <Card className="p-5">
              <h3 className="font-display text-sm font-semibold text-ink">人格与表达</h3>
              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                <Field label="说话风格">
                  <Textarea rows={3} value={draft.speech_style ?? ""} onChange={(e) => patch({ speech_style: e.target.value })}
                    placeholder="语速、句长、称呼、停顿习惯…" />
                </Field>
                <Field label="关系立场">
                  <Textarea rows={3} value={draft.relation_stance ?? ""} onChange={(e) => patch({ relation_stance: e.target.value })}
                    placeholder="角色如何看用户、如何做选择…" />
                </Field>
                <Field label="禁忌" hint="用逗号或换行分隔。">
                  <Textarea rows={3} value={listText(draft.taboo)} onChange={(e) => patch({ taboo: splitList(e.target.value) })} />
                </Field>
                <Field label="备注">
                  <Textarea rows={3} value={draft.notes ?? ""} onChange={(e) => patch({ notes: e.target.value })} />
                </Field>
              </div>
            </Card>

            <Card className="p-5">
              <h3 className="font-display text-sm font-semibold text-ink">创作默认值</h3>
              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                <Field label="工作流">
                  <Select value={draft.workflow ?? ""} onChange={(v) => patch({ workflow: v })} ariaLabel="工作流"
                    options={(workflows.data?.workflows ?? [])
                      .map((w) => ({ value: w.name || "", label: w.name || "" }))
                      .filter((w) => w.value)} />
                </Field>
                <Field label="底模 / checkpoint">
                  <Input value={draft.checkpoint ?? ""} onChange={(e) => patch({ checkpoint: e.target.value })}
                    placeholder="Illustrious-XL-v1.0.safetensors" />
                </Field>
                <Field label="正向风格词">
                  <Textarea rows={3} value={draft.style_prompt ?? ""} onChange={(e) => patch({ style_prompt: e.target.value })} />
                </Field>
                <Field label="负向提示词">
                  <Textarea rows={3} value={draft.negative_prompt ?? ""} onChange={(e) => patch({ negative_prompt: e.target.value })} />
                </Field>
                <Field label="默认尺寸">
                  <div className="flex items-center gap-2">
                    <Input type="number" value={draft.size?.[0] ?? 832}
                      onChange={(e) => patch({ size: [Number(e.target.value) || 832, draft.size?.[1] ?? 1216] })} />
                    <span className="text-xs text-faint">×</span>
                    <Input type="number" value={draft.size?.[1] ?? 1216}
                      onChange={(e) => patch({ size: [draft.size?.[0] ?? 832, Number(e.target.value) || 1216] })} />
                  </div>
                </Field>
                <Field label="语音音色">
                  <Select value={draft.voice?.voice ?? ""} onChange={(v) => patch({ voice: { ...(draft.voice ?? {}), voice: v } })} ariaLabel="语音音色"
                    options={(voices.data?.voices ?? [])
                      .map((v) => ({ value: v.id || v.name || "", label: v.name || v.id || "" }))
                      .filter((v) => v.value)} />
                </Field>
              </div>
              <div className="mt-4 flex flex-wrap gap-6 border-t border-line-soft pt-4">
                <Toggle checked={!!draft.media_policy?.allow_image} onChange={(v) => patch({ media_policy: { ...(draft.media_policy ?? {}), allow_image: v } })} label="允许生成图片" />
                <Toggle checked={!!draft.media_policy?.allow_voice} onChange={(v) => patch({ media_policy: { ...(draft.media_policy ?? {}), allow_voice: v } })} label="允许语音" />
                <Toggle checked={!!draft.media_policy?.allow_clone} onChange={(v) => patch({ media_policy: { ...(draft.media_policy ?? {}), allow_clone: v } })} label="允许克隆音色" />
              </div>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { Button, Field, Input, Textarea, Toggle } from "../../components/ui";

type AnyPayload = Record<string, any>;

function splitList(value: string) {
  return value.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean);
}

function blocksOf(payload: AnyPayload): Record<string, string> {
  const out: Record<string, string> = {};
  for (const b of payload?.prompt?.blocks ?? []) {
    if (b?.kind) out[b.kind] = b.content ?? "";
  }
  return out;
}

function mergeBlocks(payload: AnyPayload, values: Record<string, string>): AnyPayload {
  const existing = Array.isArray(payload?.prompt?.blocks)
    ? payload.prompt.blocks.map((b: any) => ({ ...b }))
    : [];
  const byKind = new Map<string, AnyPayload>(
    existing.map((b: any) => [String(b.kind), b] as [string, AnyPayload]),
  );
  let order = existing.length;
  for (const [kind, content] of Object.entries(values)) {
    if (!content.trim()) {
      byKind.delete(kind);
      continue;
    }
    const prior = byKind.get(kind);
    byKind.set(kind, {
      id: prior?.id ?? kind,
      kind,
      title: prior?.title ?? kind,
      content,
      enabled: prior?.enabled ?? true,
      order: prior?.order ?? order++,
      placement: prior?.placement ?? "before_history",
      ...(prior?.depth !== undefined ? { depth: prior.depth } : {}),
    });
  }
  return { ...payload, prompt: { ...(payload.prompt ?? {}), blocks: [...byKind.values()] } };
}

export function CharacterForm({ payload, onSave, saving }: {
  payload?: AnyPayload;
  onSave: (next: AnyPayload) => void;
  saving?: boolean;
}) {
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState("");
  const [system, setSystem] = useState("");
  const [description, setDescription] = useState("");
  const [personality, setPersonality] = useState("");
  const [scenario, setScenario] = useState("");
  const [speechStyle, setSpeechStyle] = useState("");
  const [relationStance, setRelationStance] = useState("");
  const [visualAnchor, setVisualAnchor] = useState("");
  const [notes, setNotes] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [checkpoint, setCheckpoint] = useState("");
  const [allowImage, setAllowImage] = useState(true);
  const [allowVoice, setAllowVoice] = useState(true);

  useEffect(() => {
    const blocks = blocksOf(payload ?? {});
    setName(String(payload?.identity?.name ?? ""));
    setAliases((payload?.identity?.aliases ?? []).join("，"));
    setSystem(blocks.system ?? "");
    setDescription(blocks.description ?? "");
    setPersonality(blocks.personality ?? "");
    setScenario(blocks.scenario ?? "");
    setSpeechStyle(String(payload?.expression?.speechStyle ?? ""));
    setRelationStance(String(payload?.expression?.relationStance ?? ""));
    setVisualAnchor(String(payload?.expression?.visualAnchor ?? ""));
    setNotes(String(payload?.identity?.notes ?? ""));
    setWorkflow(String(payload?.image_defaults?.workflow ?? ""));
    setCheckpoint(String(payload?.image_defaults?.checkpoint ?? ""));
    setAllowImage(payload?.media_policy?.allow_image !== false);
    setAllowVoice(payload?.media_policy?.allow_voice !== false);
  }, [payload]);

  const nextPayload = useMemo(() => {
    const base: AnyPayload = structuredClone(payload ?? {});
    base.identity = { ...(base.identity ?? {}), name, aliases: splitList(aliases), notes };
    base.expression = {
      ...(base.expression ?? {}),
      speechStyle, relationStance, visualAnchor,
    };
    base.image_defaults = {
      ...(base.image_defaults ?? {}), workflow, checkpoint,
    };
    base.media_policy = {
      ...(base.media_policy ?? {}), allow_image: allowImage, allow_voice: allowVoice,
    };
    return mergeBlocks(base, { system, description, personality, scenario });
  }, [payload, name, aliases, system, description, personality, scenario,
      speechStyle, relationStance, visualAnchor, notes, workflow, checkpoint,
      allowImage, allowVoice]);

  return (
    <div className="space-y-4 border-t border-line-soft pt-4">
      <div className="flex items-center gap-2">
        <p className="text-xs font-medium text-ink">角色表单</p>
        <span className="text-[11px] text-faint">保存会创建新 draft revision</span>
        <Button variant="primary" size="sm" className="ml-auto" loading={saving}
          onClick={() => onSave(nextPayload)}>
          保存表单为 draft
        </Button>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Field label="名称"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="别名"><Input value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="逗号分隔" /></Field>
        <Field label="默认工作流"><Input value={workflow} onChange={(e) => setWorkflow(e.target.value)} /></Field>
        <Field label="默认底模"><Input value={checkpoint} onChange={(e) => setCheckpoint(e.target.value)} /></Field>
      </div>
      <Field label="系统提示词 / 核心宪法">
        <Textarea rows={4} value={system} onChange={(e) => setSystem(e.target.value)} />
      </Field>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Field label="角色描述"><Textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="性格"><Textarea rows={3} value={personality} onChange={(e) => setPersonality(e.target.value)} /></Field>
        <Field label="场景"><Textarea rows={3} value={scenario} onChange={(e) => setScenario(e.target.value)} /></Field>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Field label="说话风格"><Textarea rows={2} value={speechStyle} onChange={(e) => setSpeechStyle(e.target.value)} /></Field>
        <Field label="关系立场"><Textarea rows={2} value={relationStance} onChange={(e) => setRelationStance(e.target.value)} /></Field>
        <Field label="视觉锚点"><Input value={visualAnchor} onChange={(e) => setVisualAnchor(e.target.value)} /></Field>
        <Field label="备注"><Input value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      </div>
      <div className="flex flex-wrap gap-6 border-t border-line-soft pt-3">
        <Toggle checked={allowImage} onChange={setAllowImage} label="允许生图" />
        <Toggle checked={allowVoice} onChange={setAllowVoice} label="允许语音" />
      </div>
    </div>
  );
}

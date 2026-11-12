import { useEffect, useMemo, useRef, useState } from "react";
import type { Task, GenerateRequest, ModelProfile } from "../../api/types";
import {
  useModels, useCharacters, useWorkflows, useLoras, useGenerate, useUpload, useTask, useCancelTask,
} from "../../api/hooks";
import { normalizeStatus, friendlyError } from "../../api/normalize";
import { confirmAction, toast } from "../../store/feedback";
import { useLibrary } from "../../store/library";
import { playSound } from "../../lib/sound";
import { cn, clamp, downloadUrl, fmtDuration } from "../../lib/utils";
import {
  Button, Card, Field, Select, Slider, Stepper, Toggle, Textarea, Badge,
  StatusBadge, ProgressBar, Skeleton, Tooltip, Chip,
} from "../../components/ui";
import { ImageTile, Lightbox, mediaSrc } from "../../components/media";
import {
  IconImage, IconDice, IconLock, IconUnlock, IconWand, IconRefresh, IconDownload,
  IconStar, IconStop, IconUpload, IconClose, IconClock, IconLayers, IconExpand, IconWarning,
} from "../../components/icons";

const SAMPLE_PROMPTS = [
  "银发红瞳少女，赛博朋克都市夜景，霓虹反光，电影感",
  "黄昏的海边灯塔，暖色天空，海浪，写实摄影",
  "水墨风格的山水，留白，远山，孤舟",
  "深海发光水母，幽蓝，体积光，微距",
  "森林晨雾，阳光穿过树梢，一只鹿，写实",
  "机械心脏，蒸汽朋克，黄铜齿轮，微距摄影",
  "春日樱花树下，花瓣飘落，少女背影，动漫风格",
  "抽象几何海报，品红与青，包豪斯风格",
];

const SIZE_PRESETS = [
  { label: "1:1", w: 1024, h: 1024 },
  { label: "3:4", w: 832, h: 1216 },
  { label: "4:3", w: 1216, h: 832 },
  { label: "16:9", w: 1328, h: 768 },
  { label: "9:16", w: 768, h: 1328 },
];

function estSeconds(model: ModelProfile | undefined, steps: number) {
  const base = model?.id === "flux3" ? 70 : model?.id === "qwen_image_2_1" ? 30 : 22;
  return Math.round(base + steps * 0.4);
}

interface LoraSel { name: string; strength: number }

export function ImageCreate({ prefill }: { prefill?: Task | null }) {
  const models = useModels();
  const characters = useCharacters();
  const workflows = useWorkflows();
  const lorasQ = useLoras();
  const generate = useGenerate();
  const upload = useUpload();
  const cancel = useCancelTask();
  const favorites = useLibrary((s) => s.favorites);
  const toggleFav = useLibrary((s) => s.toggleFav);

  const modelList = models.data?.models ?? [];
  const defaultModel = modelList.find((m) => m.default) ?? modelList[0];

  const pp = (prefill?.params ?? {}) as Record<string, any>;
  const [prompt, setPrompt] = useState(prefill?.prompt ?? "");
  const [negative, setNegative] = useState<string>(pp.negative ?? "lowres, bad anatomy, watermark, text");
  const [modelId, setModelId] = useState<string>(pp.model ?? defaultModel?.id ?? "");
  const [character, setCharacter] = useState(prefill?.character ?? "");
  const [workflow, setWorkflow] = useState<string>(pp.workflow ?? "");
  const [width, setWidth] = useState<number>(pp.width ?? 1024);
  const [height, setHeight] = useState<number>(pp.height ?? 1024);
  const [steps, setSteps] = useState<number>(pp.steps ?? 28);
  const [cfg, setCfg] = useState<number>(pp.cfg ?? 5.5);
  const [seed, setSeed] = useState<number>(pp.seed ?? Math.floor(Math.random() * 2 ** 31));
  const [lockSeed, setLockSeed] = useState(false);
  const [selLoras, setSelLoras] = useState<LoraSel[]>(pp.loras ?? []);
  const [refImage, setRefImage] = useState<string | null>(null);
  const [refName, setRefName] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [lightbox, setLightbox] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const model = modelList.find((m) => m.id === modelId) ?? defaultModel;
  const task = useTask(taskId);
  const t = task.data;
  const st = t ? normalizeStatus(t.status) : null;

  // 模型变化 → 同步工作流 / 尺寸 / 步数 / CFG 默认值
  useEffect(() => {
    if (!model) return;
    setWorkflow((w) => w || model.workflow || "");
    if (!prefill) {
      if (model.size) { setWidth(model.size[0]); setHeight(model.size[1]); }
      const mp = (model.params ?? {}) as Record<string, any>;
      if (mp.steps != null) setSteps(mp.steps);
      if (mp.cfg != null) setCfg(mp.cfg);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId, model?.id]);

  const supportsLora = !!model && !["qwen_image", "flux"].includes(model.workflow || "");
  const availableLoras = lorasQ.data?.loras ?? [];
  const charList = characters.data?.characters ?? [];
  const wfList = workflows.data?.workflows ?? [];
  const activeChar = charList.find((c) => c.id === character);

  const aspect = width / height;
  const previewAngle = seed % 360;
  const resultUrl = t?.result?.url;
  const busy = st === "pending" || st === "running";

  const randomizePrompt = () => {
    const next = SAMPLE_PROMPTS[Math.floor(Math.random() * SAMPLE_PROMPTS.length)];
    setPrompt(next);
    if (!lockSeed) setSeed(Math.floor(Math.random() * 2 ** 31));
    playSound("click");
  };
  const rollSeed = () => { setSeed(Math.floor(Math.random() * 2 ** 31)); playSound("click"); };

  const reset = () => {
    setPrompt(""); setNegative("lowres, bad anatomy, watermark, text");
    setCharacter(""); setSelLoras([]); setRefImage(null); setRefName("");
    if (defaultModel) {
      setModelId(defaultModel.id);
      if (defaultModel.size) { setWidth(defaultModel.size[0]); setHeight(defaultModel.size[1]); }
    }
    setLockSeed(false); setSeed(Math.floor(Math.random() * 2 ** 31));
    toast.info("已重置参数");
  };

  const onUpload = async (file: File) => {
    if (!file.type.startsWith("image/")) { toast.error("请选择图片文件"); return; }
    if (file.size > 12 * 1024 * 1024) { toast.error("图片过大（>12MB）"); return; }
    const res = await upload.mutateAsync(file).catch(() => null);
    if (res) { setRefImage(res.url); setRefName(res.filename); toast.success("参考图已上传", res.filename); }
  };

  const submit = async () => {
    if (!prompt.trim()) { toast.warning("请先填写提示词"); return; }
    if (model?.available === false) { toast.error("底模不可用", model.notes); return; }
    if (!lockSeed) setSeed(Math.floor(Math.random() * 2 ** 31));
    const req: GenerateRequest = {
      prompt: prompt.trim(), negative: negative.trim() || undefined,
      model: modelId, character: character || undefined, workflow: workflow || undefined,
      width, height, steps, cfg, seed: lockSeed ? seed : undefined,
      loras: supportsLora && selLoras.length ? selLoras : undefined,
      reference_image: refImage || undefined,
      source: "web", async: true,
    };
    playSound("submit");
    const res = await generate.mutateAsync(req).catch(() => null);
    if (res?.task_id) {
      setTaskId(res.task_id);
      setHistory((h) => [res.task_id, ...h.filter((x) => x !== res.task_id)].slice(0, 12));
    }
  };

  const doCancel = async () => {
    if (!taskId) return;
    const ok = await confirmAction({ title: "取消生成", tone: "danger", confirmLabel: "取消任务", message: "确定取消当前图像生成任务？" });
    if (ok) { playSound("cancel"); cancel.mutate(taskId); }
  };

  const download = async () => {
    if (!resultUrl) return;
    await downloadUrl(mediaSrc(resultUrl), t?.result?.filename || `mobius_${seed}.png`);
    toast.success("已开始下载");
  };

  const isFav = taskId ? favorites.includes(taskId) : false;
  const summary = useMemo(() => [
    { k: "底模", v: model?.name || modelId },
    { k: "工作流", v: workflow || "自动" },
    { k: "尺寸", v: `${width}×${height}` },
    { k: "步数", v: String(steps) },
    { k: "CFG", v: cfg.toFixed(1) },
    { k: "种子", v: String(seed) },
  ], [model, modelId, workflow, width, height, steps, cfg, seed]);

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      {/* 画布 / 结果区 */}
      <div className="space-y-4">
        <Card className="p-4">
          <div className="mb-2 flex items-center gap-2">
            <label className="label mb-0 flex items-center gap-1.5" htmlFor="prompt"><IconWand size={14} className="text-magenta" /> 提示词</label>
            <div className="ml-auto flex items-center gap-1.5">
              <Tooltip label="随机灵感"><Button variant="ghost" size="sm" icon={<IconDice size={14} />} onClick={randomizePrompt}>灵感</Button></Tooltip>
              <span className="tnum text-[11px] text-faint">{prompt.length} 字</span>
            </div>
          </div>
          <Textarea id="prompt" rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)}
            placeholder="描述你想生成的画面，例如：银发红瞳少女，赛博朋克都市夜景，霓虹反光，电影感…" />
          {activeChar?.style_prompt && (
            <p className="mt-2 flex items-center gap-1.5 text-[11px] text-faint">
              <IconLayers size={12} /> 角色「{activeChar.name}」风格将叠加：{activeChar.style_prompt.slice(0, 40)}…
            </p>
          )}
        </Card>

        {/* 预览框 / 结果 */}
        <Card className="relative overflow-hidden p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h3 className="font-display text-sm font-semibold text-ink">画布</h3>
            {t && <StatusBadge status={t.status} />}
            <div className="ml-auto flex items-center gap-1.5">
              {st === "completed" && resultUrl && (
                <>
                  <Tooltip label={isFav ? "取消收藏" : "收藏"}>
                    <Button variant="ghost" size="icon" onClick={() => { if (taskId) { toggleFav(taskId); toast.success(isFav ? "已取消收藏" : "已收藏"); } }} aria-label="收藏">
                      <IconStar size={16} className={cn(isFav && "fill-warning text-warning")} />
                    </Button>
                  </Tooltip>
                  <Button variant="ghost" size="sm" icon={<IconExpand size={14} />} onClick={() => setLightbox(true)}>大图</Button>
                  <Button variant="ghost" size="sm" icon={<IconDownload size={14} />} onClick={download}>下载</Button>
                </>
              )}
              {busy && <Button variant="danger" size="sm" icon={<IconStop size={14} />} loading={cancel.isPending} onClick={doCancel}>取消</Button>}
            </div>
          </div>

          <div className="relative mx-auto w-full" style={{ maxWidth: 560 }}>
            <div className="relative w-full overflow-hidden rounded-xl ring-1 ring-line-soft"
              style={{ aspectRatio: `${aspect}` }}>
              {/* 占位 / 运行背景（随种子live变化的装饰渐变，非真实结果） */}
              {st !== "completed" && (
                <div className="absolute inset-0"
                  style={{ background: `linear-gradient(${previewAngle}deg, rgb(var(--c-brand)/0.18), rgb(var(--c-magenta)/0.12) 45%, rgb(var(--c-cyan)/0.14))` }}>
                  <div className="absolute inset-0 app-grid opacity-30" />
                </div>
              )}
              {st === "completed" && resultUrl ? (
                <ImageTile src={resultUrl} alt={prompt || "生成结果"} eager rounded="rounded-none" className="absolute inset-0 h-full w-full" onClick={() => setLightbox(true)} />
              ) : (
                <div className="absolute inset-0 grid place-items-center p-6 text-center">
                  {busy ? (
                    <div className="w-full max-w-xs">
                      <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-brand/15">
                        <span className="led led-live bg-brand h-3 w-3" />
                      </div>
                      <p className="text-sm font-medium text-ink">{st === "pending" ? "排队等待执行端…" : "正在生成图像…"}</p>
                      <ProgressBar indeterminate className="mt-3" />
                      <p className="mt-2 text-[11px] text-faint">预计 ~{fmtDuration(estSeconds(model, steps))} · 真实状态，无假进度</p>
                    </div>
                  ) : st === "failed" || st === "submission_unknown" ? (
                    <div className="max-w-xs">
                      <IconWarning size={26} className="mx-auto text-danger" />
                      <p className="mt-2 text-sm font-medium text-danger">{friendlyError(t?.error_code || t?.error, t?.error || undefined)}</p>
                      <Button variant="soft" size="sm" className="mt-3" icon={<IconRefresh size={14} />} onClick={submit}>重试</Button>
                    </div>
                  ) : (
                    <div className="max-w-xs">
                      <div className="mx-auto mb-3 grid h-14 w-14 place-items-center rounded-2xl bg-raised/70 ring-1 ring-line-soft">
                        <IconImage size={26} className="text-faint" />
                      </div>
                      <p className="text-sm text-muted">图像将在此生成</p>
                      <p className="mt-1 text-[11px] leading-relaxed text-faint">调整右侧参数，预览框比例与设置实时同步。提交后显示真实结果。</p>
                    </div>
                  )}
                </div>
              )}
              {/* 角标 */}
              <div className="pointer-events-none absolute left-2 top-2 flex flex-wrap gap-1.5">
                <span className="rounded-md bg-base/70 px-2 py-0.5 text-[10px] text-muted backdrop-blur">{model?.name || modelId}</span>
                <span className="tnum rounded-md bg-base/70 px-2 py-0.5 text-[10px] text-muted backdrop-blur">{width}×{height}</span>
              </div>
            </div>
          </div>

          {/* 本次会话历史 */}
          {history.length > 0 && (
            <div className="mt-4 border-t border-line-soft pt-3">
              <p className="section-title mb-2">本次会话</p>
              <div className="scroll-x flex gap-2 pb-1">
                {history.map((id) => (
                  <HistoryThumb key={id} id={id} active={id === taskId} onClick={() => setTaskId(id)} />
                ))}
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* 参数控制区（常驻可见，≥7 实时控件） */}
      <div className="space-y-4">
        <Card className="p-4">
          <div className="mb-3 flex items-center gap-2">
            <IconLayers size={15} className="text-brand-soft" />
            <h3 className="font-display text-sm font-semibold text-ink">参数</h3>
            <Badge tone="magenta" className="ml-auto">实时</Badge>
          </div>

          <div className="space-y-3.5">
            <Field label="底模">
              <Select value={modelId} onChange={setModelId} ariaLabel="底模"
                options={modelList.map((m) => ({ value: m.id, label: m.name || m.id, hint: m.available === false ? "不可用" : m.notes, disabled: m.available === false }))} />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="角色">
                <Select value={character} onChange={setCharacter} ariaLabel="角色" placeholder="无"
                  options={[{ value: "", label: "无角色" }, ...charList.map((c) => ({ value: c.id, label: c.name || c.id }))]} />
              </Field>
              <Field label="工作流">
                <Select value={workflow} onChange={setWorkflow} ariaLabel="工作流" placeholder="自动"
                  options={wfList.map((w) => ({ value: w.id, label: w.name || w.id }))} />
              </Field>
            </div>

            <Field label="尺寸" hint={`比例 ${(aspect).toFixed(2)} · 范围 64–4096`}>
              <div className="mb-2 flex flex-wrap gap-1.5">
                {SIZE_PRESETS.map((s) => (
                  <Chip key={s.label} active={width === s.w && height === s.h} onClick={() => { setWidth(s.w); setHeight(s.h); }}>{s.label}</Chip>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Stepper value={width} onChange={setWidth} min={64} max={4096} step={64} ariaLabel="宽度" suffix="W" />
                <Stepper value={height} onChange={setHeight} min={64} max={4096} step={64} ariaLabel="高度" suffix="H" />
              </div>
            </Field>

            <Field label={`采样步数 · ${steps}`}>
              <Slider value={steps} onChange={setSteps} min={8} max={60} step={1} ariaLabel="采样步数" />
            </Field>
            <Field label={`CFG 引导 · ${cfg.toFixed(1)}`}>
              <Slider value={cfg} onChange={setCfg} min={1} max={15} step={0.5} ariaLabel="CFG" format={(v) => v.toFixed(1)} />
            </Field>

            <Field label="种子">
              <div className="flex items-center gap-2">
                <Stepper value={seed} onChange={setSeed} min={0} max={2 ** 31 - 1} step={1} ariaLabel="种子" className="flex-1" />
                <Tooltip label="随机种子"><Button variant="ghost" size="icon" onClick={rollSeed} aria-label="随机种子"><IconDice size={16} /></Button></Tooltip>
                <Tooltip label={lockSeed ? "已锁定（提交时复用）" : "未锁定（每次随机）"}>
                  <Button variant={lockSeed ? "soft" : "ghost"} size="icon" onClick={() => setLockSeed((v) => !v)} aria-label="锁定种子" aria-pressed={lockSeed}>
                    {lockSeed ? <IconLock size={16} /> : <IconUnlock size={16} />}
                  </Button>
                </Tooltip>
              </div>
            </Field>

            <Field label="负向提示词">
              <Textarea rows={2} value={negative} onChange={(e) => setNegative(e.target.value)} placeholder="不希望出现的元素…" />
            </Field>
          </div>
        </Card>

        {/* LoRA */}
        <Card className="p-4">
          <div className="mb-2 flex items-center gap-2">
            <h3 className="font-display text-sm font-semibold text-ink">LoRA</h3>
            <span className="tnum ml-auto text-[11px] text-faint">{selLoras.length} 启用</span>
          </div>
          {!supportsLora ? (
            <p className="rounded-md border border-line-soft bg-base/40 px-3 py-2 text-[11px] leading-snug text-faint">
              当前底模（{model?.name}）的工作流不支持 LoRA / 角色一致性，已自动禁用。
            </p>
          ) : (
            <div className="space-y-2">
              {availableLoras.map((l) => {
                const sel = selLoras.find((x) => x.name === l.name);
                return (
                  <div key={l.name} className={cn("rounded-lg border p-2.5 transition-colors", sel ? "border-brand/35 bg-brand/8" : "border-line-soft bg-base/30")}>
                    <div className="flex items-center gap-2">
                      <Toggle size="sm" checked={!!sel} label={`启用 ${l.name}`}
                        onChange={(v) => setSelLoras((arr) => v ? [...arr, { name: l.name, strength: l.strength ?? 0.7 }] : arr.filter((x) => x.name !== l.name))} />
                      <span className="min-w-0 flex-1 truncate text-xs text-ink">{l.name}</span>
                      {sel && <span className="tnum text-[11px] text-brand-soft">{sel.strength.toFixed(2)}</span>}
                    </div>
                    {sel && (
                      <Slider className="mt-2" value={sel.strength} min={0} max={1.5} step={0.05} ariaLabel={`${l.name} 强度`}
                        format={(v) => v.toFixed(2)}
                        onChange={(v) => setSelLoras((arr) => arr.map((x) => x.name === l.name ? { ...x, strength: v } : x))} />
                    )}
                  </div>
                );
              })}
              {availableLoras.length === 0 && <Skeleton className="h-16 w-full" />}
            </div>
          )}
        </Card>

        {/* 参考图 */}
        <Card className="p-4">
          <div className="mb-2 flex items-center gap-2">
            <h3 className="font-display text-sm font-semibold text-ink">参考图</h3>
            <span className="ml-auto text-[11px] text-faint">图生图可选</span>
          </div>
          <input ref={fileRef} type="file" accept="image/*" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); e.target.value = ""; }} />
          {refImage ? (
            <div className="flex items-center gap-3">
              <ImageTile src={refImage} alt="参考图" className="h-16 w-16 flex-none ring-1 ring-line-soft" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs text-ink">{refName}</p>
                <button className="mt-1 text-[11px] text-danger hover:underline" onClick={() => { setRefImage(null); setRefName(""); }}>移除</button>
              </div>
            </div>
          ) : (
            <Button variant="ghost" className="w-full" icon={<IconUpload size={15} />} loading={upload.isPending} onClick={() => fileRef.current?.click()}>
              上传参考图
            </Button>
          )}
        </Card>

        {/* 摘要 + 提交 */}
        <Card className="sticky bottom-4 p-4">
          <div className="mb-3 grid grid-cols-3 gap-1.5">
            {summary.map((s) => (
              <div key={s.k} className="rounded-md border border-line-soft bg-base/40 px-2 py-1.5">
                <p className="text-[10px] text-faint">{s.k}</p>
                <p className="tnum truncate text-[11px] text-ink" title={s.v}>{s.v}</p>
              </div>
            ))}
          </div>
          <div className="mb-3 flex items-center gap-1.5 text-[11px] text-faint">
            <IconClock size={12} /> 预计 ~{fmtDuration(estSeconds(model, steps))}
            {model?.available === false && <span className="ml-auto text-danger">底模不可用</span>}
          </div>
          <div className="flex gap-2">
            <Button variant="primary" className="flex-1" size="lg" icon={<IconImage size={17} />} loading={generate.isPending || busy} onClick={submit}>
              {busy ? "生成中…" : "生成图像"}
            </Button>
            <Tooltip label="重置参数"><Button variant="ghost" size="icon" className="h-11 w-11" onClick={reset} aria-label="重置"><IconRefresh size={17} /></Button></Tooltip>
          </div>
        </Card>
      </div>

      <Lightbox open={lightbox} onClose={() => setLightbox(false)} src={resultUrl}
        title={prompt.slice(0, 40) || "作品"} subtitle={`${width}×${height} · 种子 ${seed}`} onDownload={download} />
    </div>
  );
}

function HistoryThumb({ id, active, onClick }: { id: string; active: boolean; onClick: () => void }) {
  const { data } = useTask(id);
  const st = data ? normalizeStatus(data.status) : "pending";
  return (
    <button onClick={onClick} className={cn("relative h-14 w-14 flex-none overflow-hidden rounded-md ring-1 transition-all",
      active ? "ring-2 ring-brand" : "ring-line-soft hover:ring-brand/50")}>
      {st === "completed" && data?.result?.url ? (
        <ImageTile src={data.result.url} alt="历史" rounded="rounded-none" className="h-full w-full" />
      ) : (
        <span className="grid h-full w-full place-items-center bg-raised">
          {st === "failed" ? <IconClose size={16} className="text-danger" /> : <span className={cn("led", st === "running" ? "led-live bg-brand" : "bg-warning")} />}
        </span>
      )}
    </button>
  );
}
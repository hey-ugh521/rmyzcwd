import { useEffect, useMemo, useState } from "react";
import type { Task, TtsRequest } from "../../api/types";
import { useVoices, useCharacters, useTts } from "../../api/hooks";
import { toast } from "../../store/feedback";
import { playSound } from "../../lib/sound";
import { cn, downloadUrl, fmtDuration } from "../../lib/utils";
import { Button, Card, Field, Select, Slider, Textarea, Badge, Skeleton } from "../../components/ui";
import { AudioPlayer, mediaSrc } from "../../components/media";
import { IconMic, IconRefresh, IconDownload, IconPlay, IconClock, IconLayers, IconWand, IconVolume } from "../../components/icons";

const SAMPLE_TEXTS = [
  "欢迎使用 Möbius Studio，这是本机语音合成演示。所有音频都在你的设备上生成，不会上传到任何外部服务。",
  "今天天气不错，要不要出去走走？顺便带上相机，记录一下黄昏的光。",
  "系统已完成自检，所有服务运行正常，可以开始创作了。",
];

export function VoiceCreate({ prefill }: { prefill?: Task | null }) {
  const voicesQ = useVoices();
  const charsQ = useCharacters();
  const tts = useTts();

  const pp = (prefill?.params ?? {}) as Record<string, any>;
  const voices = voicesQ.data?.voices ?? [];
  const chars = charsQ.data?.characters ?? [];

  const [text, setText] = useState(prefill?.prompt ?? "");
  const [engine, setEngine] = useState(pp.engine ?? "kokoro");
  const [voice, setVoice] = useState(pp.voice ?? "zf_xiaobei");
  const [speed, setSpeed] = useState(pp.speed ?? 1.0);
  const [character, setCharacter] = useState(prefill?.character ?? "");
  const [result, setResult] = useState<{ url: string; filename?: string; seconds?: number; task_id?: string } | null>(null);

  const engineVoices = useMemo(() => voices.filter((v) => (v.engine || "kokoro") === engine), [voices, engine]);

  // 引擎切换 → 选第一个可用音色
  useEffect(() => {
    if (engineVoices.length && !engineVoices.find((v) => v.id === voice)) setVoice(engineVoices[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine]);

  // 角色 → 套用其音色档案
  useEffect(() => {
    const c = chars.find((x) => x.id === character);
    if (c?.voice) {
      if (c.voice.engine) setEngine(c.voice.engine);
      if (c.voice.voice) setVoice(c.voice.voice);
      if (c.voice.speed != null) setSpeed(c.voice.speed);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [character]);

  const estSec = Math.max(1, text.length / 7 / Math.max(0.5, speed));
  const activeVoice = voices.find((v) => v.id === voice);

  const submit = async () => {
    if (!text.trim()) { toast.warning("请先输入要合成的文本"); return; }
    const req: TtsRequest = { text: text.trim(), engine, voice, speed, character: character || undefined, source: "web" };
    playSound("submit");
    const res = await tts.mutateAsync(req).catch(() => null);
    if (res?.audio_url || res?.url) {
      setResult({ url: (res.audio_url || res.url)!, filename: res.filename, seconds: res.seconds, task_id: res.task_id });
      playSound("success");
    }
  };

  const reset = () => {
    setText(""); setEngine("kokoro"); setVoice("zf_xiaobei"); setSpeed(1); setCharacter(""); setResult(null);
    toast.info("已重置");
  };

  const download = async () => {
    if (!result) return;
    await downloadUrl(mediaSrc(result.url), result.filename || "mobius_tts.wav");
    toast.success("已开始下载");
  };

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-4">
        <Card className="p-4">
          <div className="mb-2 flex items-center gap-2">
            <label className="label mb-0 flex items-center gap-1.5" htmlFor="tts-text"><IconWand size={14} className="text-cyan" /> 合成文本</label>
            <div className="ml-auto flex items-center gap-1.5">
              <Button variant="ghost" size="sm" icon={<IconRefresh size={13} />} onClick={() => setText(SAMPLE_TEXTS[Math.floor(Math.random() * SAMPLE_TEXTS.length)])}>示例</Button>
              <span className="tnum text-[11px] text-faint">{text.length} 字</span>
            </div>
          </div>
          <Textarea id="tts-text" rows={6} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="输入要朗读的文本，支持中文与英文。例如：欢迎使用 Möbius Studio…" />
        </Card>

        <Card className="p-4">
          <div className="mb-3 flex items-center gap-2">
            <h3 className="font-display text-sm font-semibold text-ink">试听</h3>
            {result && <Badge tone="cyan" dot>已合成</Badge>}
            {result && (
              <Button variant="ghost" size="sm" className="ml-auto" icon={<IconDownload size={14} />} onClick={download}>下载 WAV</Button>
            )}
          </div>

          {/* 波形装饰 */}
          <div className="relative mb-4 grid h-28 place-items-center overflow-hidden rounded-xl bg-gradient-to-br from-cyan/10 to-brand/10 ring-1 ring-line-soft">
            <div className="flex h-16 items-center gap-[3px]">
              {Array.from({ length: 56 }).map((_, i) => {
                const h = 12 + Math.abs(Math.sin(i * 0.5 + text.length * 0.1)) * 40 * (result ? 1 : 0.4);
                return <span key={i} className={cn("w-[3px] rounded-full transition-all duration-300", result ? "bg-cyan/70" : "bg-faint/40")} style={{ height: `${h}%` }} />;
              })}
            </div>
            {!result && (
              <span className="absolute inset-0 grid place-items-center">
                <span className="grid h-12 w-12 place-items-center rounded-full bg-base/70 text-cyan ring-1 ring-cyan/30 backdrop-blur"><IconVolume size={22} /></span>
              </span>
            )}
          </div>

          {tts.isPending ? (
            <div className="flex items-center gap-3 rounded-lg border border-line-soft bg-raised/50 px-3 py-3">
              <span className="led led-live bg-cyan" />
              <span className="text-sm text-muted">正在合成语音…</span>
            </div>
          ) : result ? (
            <AudioPlayer src={result.url} autoPlay />
          ) : (
            <div className="rounded-lg border border-dashed border-line py-6 text-center">
              <IconMic size={22} className="mx-auto text-faint" />
              <p className="mt-2 text-xs text-muted">合成后可在此试听与下载</p>
            </div>
          )}
          {result?.seconds != null && (
            <p className="mt-2 text-[11px] text-faint">时长 {fmtDuration(result.seconds)} · {activeVoice?.name || voice} · {engine}</p>
          )}
        </Card>
      </div>

      {/* 参数控制区 */}
      <div className="space-y-4">
        <Card className="p-4">
          <div className="mb-3 flex items-center gap-2">
            <IconLayers size={15} className="text-cyan" />
            <h3 className="font-display text-sm font-semibold text-ink">语音参数</h3>
            <Badge tone="cyan" className="ml-auto">实时</Badge>
          </div>
          <div className="space-y-3.5">
            <Field label="角色档案" hint="选择角色将套用其音色与语速">
              <Select value={character} onChange={setCharacter} ariaLabel="角色" placeholder="无"
                options={[{ value: "", label: "无角色" }, ...chars.filter((c) => c.media_policy?.allow_voice !== false).map((c) => ({ value: c.id, label: c.name || c.id }))]} />
            </Field>
            <Field label="引擎">
              <Select value={engine} onChange={setEngine} ariaLabel="引擎"
                options={[
                  { value: "kokoro", label: "Kokoro TTS", hint: "CPU · 中英 · 快速" },
                  { value: "gpt-sovits", label: "GPT-SoVITS", hint: "GPU · 克隆音色" },
                ]} />
            </Field>
            <Field label="音色">
              {voicesQ.isLoading ? <Skeleton className="h-9 w-full" /> : (
                <Select value={voice} onChange={setVoice} ariaLabel="音色"
                  options={engineVoices.map((v) => ({ value: v.id, label: v.name || v.id, hint: `${v.lang || ""} ${v.gender === "female" ? "女" : v.gender === "male" ? "男" : ""}`.trim() }))} />
              )}
            </Field>
            <Field label={`语速 · ${speed.toFixed(2)}×`}>
              <Slider value={speed} onChange={setSpeed} min={0.5} max={2} step={0.05} ariaLabel="语速" format={(v) => `${v.toFixed(2)}×`} />
            </Field>
            <div className="rounded-lg border border-line-soft bg-base/40 px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-[11px] text-faint"><IconClock size={12} /> 预计时长</div>
              <p className="tnum mt-1 text-sm text-ink">~{fmtDuration(estSec)}</p>
            </div>
          </div>
        </Card>

        <Card className="sticky bottom-4 p-4">
          <div className="flex gap-2">
            <Button variant="primary" className="flex-1" size="lg" icon={<IconPlay size={16} />} loading={tts.isPending} onClick={submit}>
              {tts.isPending ? "合成中…" : "合成并试听"}
            </Button>
            <Button variant="ghost" size="icon" className="h-11 w-11" onClick={reset} aria-label="重置"><IconRefresh size={17} /></Button>
          </div>
          <p className="mt-2.5 text-[11px] leading-snug text-faint">
            克隆音色（GPT-SoVITS）首次使用会自动唤醒 GPU 服务。音频仅在本机播放与保存。
          </p>
        </Card>
      </div>
    </div>
  );
}
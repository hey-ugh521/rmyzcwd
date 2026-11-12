import { useEffect, useRef, useState } from "react";
import { GW_BASE } from "../api/client";
import { cn, resolveMediaUrl, fmtDuration, downloadUrl, copyText } from "../lib/utils";
import { toast } from "../store/feedback";
import { playSound } from "../lib/sound";
import { Button, Spinner } from "./ui";
import { Modal } from "./overlays";
import {
  IconImage, IconMic, IconPlay, IconPause, IconDownload,
  IconCopy, IconWarning, IconExpand,
} from "./icons";

export function mediaSrc(url?: string | null) {
  return resolveMediaUrl(url, GW_BASE);
}

/* ------------------------------------------------------------- 图片缩略 */
export function ImageTile({
  src, alt, className, imgClassName, rounded = "rounded-lg", onClick, eager,
}: {
  src?: string | null; alt: string; className?: string; imgClassName?: string;
  rounded?: string; onClick?: () => void; eager?: boolean;
}) {
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const url = mediaSrc(src);
  const Wrapper = onClick ? "button" : "div";
  return (
    <Wrapper
      onClick={onClick}
      className={cn("group relative block overflow-hidden bg-raised", rounded, onClick && "cursor-pointer", className)}
      aria-label={onClick ? `查看 ${alt}` : undefined}
    >
      {!loaded && !error && (
        <div className="absolute inset-0 skeleton grid place-items-center">
          <IconImage size={22} className="text-faint" />
        </div>
      )}
      {error || !url ? (
        <div className="absolute inset-0 grid place-items-center bg-raised text-faint">
          <div className="flex flex-col items-center gap-1.5">
            <IconWarning size={20} />
            <span className="text-[10px]">无法加载</span>
          </div>
        </div>
      ) : (
        <img
          src={url} alt={alt} loading={eager ? "eager" : "lazy"} decoding="async"
          onLoad={() => setLoaded(true)} onError={() => setError(true)}
          className={cn("h-full w-full object-cover transition-all duration-500",
            loaded ? "opacity-100 scale-100" : "opacity-0 scale-[1.03]",
            onClick && "group-hover:scale-[1.04]", imgClassName)}
        />
      )}
      {onClick && loaded && !error && (
        <span className="pointer-events-none absolute inset-0 bg-gradient-to-t from-base/70 via-transparent to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
      )}
      {onClick && loaded && !error && (
        <span className="pointer-events-none absolute bottom-2 right-2 grid h-7 w-7 place-items-center rounded-md bg-base/70 text-ink opacity-0 backdrop-blur transition-opacity group-hover:opacity-100">
          <IconExpand size={15} />
        </span>
      )}
    </Wrapper>
  );
}

/* ------------------------------------------------------------- 音频播放 */
export function AudioPlayer({ src, compact, autoPlay, className }:
  { src?: string | null; compact?: boolean; autoPlay?: boolean; className?: string }) {
  const ref = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [dur, setDur] = useState(0);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(false);
  const url = mediaSrc(src);

  useEffect(() => {
    const a = ref.current;
    if (!a) return;
    const onTime = () => setTime(a.currentTime);
    const onMeta = () => { setDur(a.duration || 0); setReady(true); };
    const onEnd = () => { setPlaying(false); setTime(0); };
    const onErr = () => setError(true);
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("ended", onEnd);
    a.addEventListener("error", onErr);
    return () => {
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("loadedmetadata", onMeta);
      a.removeEventListener("ended", onEnd);
      a.removeEventListener("error", onErr);
    };
  }, [url]);

  const toggle = async () => {
    const a = ref.current;
    if (!a || error) return;
    if (playing) { a.pause(); setPlaying(false); }
    else { try { await a.play(); setPlaying(true); playSound("click"); } catch { setError(true); } }
  };

  const seek = (v: number) => { const a = ref.current; if (a) { a.currentTime = v; setTime(v); } };
  const pct = dur > 0 ? (time / dur) * 100 : 0;

  if (!url || error) {
    return (
      <div className={cn("flex items-center gap-2 rounded-md border border-line-soft bg-raised/50 px-3 py-2 text-xs text-faint", className)}>
        <IconWarning size={15} /> 音频不可用
      </div>
    );
  }

  return (
    <div className={cn("flex items-center gap-3 rounded-lg border border-line-soft bg-raised/60 px-3", compact ? "py-2" : "py-2.5", className)}>
      <audio ref={ref} src={url} preload="metadata" autoPlay={autoPlay} />
      <button
        onClick={toggle} disabled={!ready}
        aria-label={playing ? "暂停" : "播放"}
        className="grid h-9 w-9 flex-none place-items-center rounded-full bg-gradient-to-br from-brand to-magenta text-white shadow-glow transition-transform hover:scale-105 disabled:opacity-50"
      >
        {!ready ? <Spinner size={15} className="text-white" /> : playing ? <IconPause size={16} /> : <IconPlay size={16} className="ml-0.5" />}
      </button>
      <div className="min-w-0 flex-1">
        <div className="relative flex h-5 items-center">
          <div className="absolute h-1.5 w-full overflow-hidden rounded-full bg-base">
            <div className="h-full rounded-full bg-gradient-to-r from-cyan to-brand transition-[width] duration-150" style={{ width: `${pct}%` }} />
          </div>
          <input
            type="range" min={0} max={dur || 0} step={0.01} value={time}
            onChange={(e) => seek(Number(e.target.value))} aria-label="播放进度"
            className="relative h-5 w-full cursor-pointer appearance-none bg-transparent
              [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3
              [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-ink [&::-webkit-slider-thumb]:shadow"
          />
        </div>
      </div>
      <span className="tnum flex-none text-[11px] text-muted">{fmtDuration(time)} / {fmtDuration(dur)}</span>
    </div>
  );
}

/* --------------------------------------------------------------- 灯箱 */
export function Lightbox({
  open, onClose, src, title, subtitle, onDownload,
}: {
  open: boolean; onClose: () => void; src?: string | null; title?: string;
  subtitle?: string; onDownload?: () => void;
}) {
  const url = mediaSrc(src);
  const dl = async () => {
    if (onDownload) return onDownload();
    if (!url) return;
    await downloadUrl(url, `${title || "mobius"}.png`);
    toast.success("已开始下载");
  };
  return (
    <Modal open={open} onClose={onClose} size="xl" title={title} subtitle={subtitle}
      footer={
        <>
          <Button variant="ghost" icon={<IconCopy size={15} />} onClick={async () => {
            if (await copyText(url)) toast.success("已复制图片地址"); else toast.error("复制失败");
          }}>复制地址</Button>
          <Button variant="primary" icon={<IconDownload size={15} />} onClick={dl}>下载</Button>
        </>
      }>
      <div className="grid place-items-center rounded-lg bg-base/60 p-2">
        {url ? (
          <img src={url} alt={title || "作品大图"} className="max-h-[62vh] w-auto rounded-md object-contain" />
        ) : (
          <div className="grid h-64 place-items-center text-faint"><IconImage size={32} /></div>
        )}
      </div>
    </Modal>
  );
}

/* 语音结果小徽章（用于列表） */
export function VoiceGlyph({ className }: { className?: string }) {
  return (
    <span className={cn("grid place-items-center rounded-lg bg-cyan/12 text-cyan ring-1 ring-cyan/25", className)}>
      <IconMic size={20} />
    </span>
  );
}
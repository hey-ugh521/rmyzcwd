import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { NAV } from "./nav";
import { useUI } from "../store/ui";
import { refreshConnection, switchMode } from "../api";
import { useConnection } from "../api/client";
import { cn, fuzzyScore } from "../lib/utils";
import { toast } from "../store/feedback";
import {
  IconSearch, IconCreate, IconImage, IconMic, IconSun, IconMoon,
  IconVolume, IconVolumeX, IconRefresh, IconDatabase, IconArrowRight,
} from "../components/icons";

interface Cmd {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: React.ReactNode;
  keywords: string;
  run: () => void;
}

export function CommandPalette() {
  const open = useUI((s) => s.commandOpen);
  const setOpen = useUI((s) => s.setCommandOpen);
  const theme = useUI((s) => s.theme);
  const toggleTheme = useUI((s) => s.toggleTheme);
  const muted = useUI((s) => s.muted);
  const toggleMuted = useUI((s) => s.toggleMuted);
  const mode = useConnection((s) => s.mode);
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const close = () => { setOpen(false); setQ(""); setActive(0); };
  const go = (to: string) => { navigate(to); close(); };

  const commands = useMemo<Cmd[]>(() => {
    const nav: Cmd[] = NAV.map((n) => ({
      id: `nav:${n.to}`, label: n.label, hint: n.desc, group: "跳转",
      icon: <n.icon size={17} />, keywords: n.keywords, run: () => go(n.to),
    }));
    const actions: Cmd[] = [
      { id: "act:image", label: "新建图像创作", hint: "打开创作台 · 图像", group: "操作", icon: <IconImage size={17} />, keywords: "new image create 画 生成 图片", run: () => go("/create?tab=image") },
      { id: "act:voice", label: "新建语音合成", hint: "打开创作台 · 语音", group: "操作", icon: <IconMic size={17} />, keywords: "new voice tts 语音 合成 朗读", run: () => go("/create?tab=voice") },
      { id: "act:theme", label: theme === "dark" ? "切换到亮色主题" : "切换到暗色主题", group: "操作", icon: theme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />, keywords: "theme dark light 主题 外观 暗 亮", run: () => { toggleTheme(); close(); } },
      { id: "act:mute", label: muted ? "开启提示音" : "静音提示音", group: "操作", icon: muted ? <IconVolume size={17} /> : <IconVolumeX size={17} />, keywords: "mute sound 静音 声音 提示音", run: () => { toggleMuted(); close(); } },
      { id: "act:refresh", label: "重新探活本机服务", group: "操作", icon: <IconRefresh size={17} />, keywords: "refresh reconnect 探活 重连 刷新", run: async () => { close(); await refreshConnection(); const c = useConnection.getState(); toast[c.coreOk ? "success" : "error"](c.coreOk ? "本机服务正常" : "Core 不可达", c.lastError ?? undefined); } },
      { id: "act:mode", label: mode === "mock" ? "切换到本机服务" : "切换到演示数据", group: "操作", icon: <IconDatabase size={17} />, keywords: "mode mock live 数据源 演示 本机 切换", run: async () => { const target = mode === "mock" ? "live" : "mock"; close(); await switchMode(target); toast.info(target === "mock" ? "已切换到演示数据" : "已切换到本机服务"); } },
    ];
    return [...nav, ...actions];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme, muted, mode]);

  const results = useMemo(() => {
    if (!q.trim()) return commands;
    return commands
      .map((c) => ({ c, s: Math.max(fuzzyScore(q, c.label), fuzzyScore(q, c.keywords) * 0.8) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s)
      .map((x) => x.c);
  }, [q, commands]);

  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 30); }, [open]);
  useEffect(() => { setActive(0); }, [q]);
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!open) return null;

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(results.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); results[active]?.run(); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
  };

  let lastGroup = "";

  return createPortal(
    <div className="fixed inset-0 z-[150] flex items-start justify-center p-4 pt-[12vh]" onKeyDown={onKey}>
      <div className="absolute inset-0 bg-base/70 backdrop-blur-sm animate-fade-in" onClick={close} />
      <div role="dialog" aria-modal="true" aria-label="命令面板"
        className="relative w-full max-w-xl overflow-hidden rounded-xl border border-line bg-overlay/95 glass shadow-pop animate-scale-in">
        <div className="flex items-center gap-3 border-b border-line-soft px-4">
          <IconSearch size={18} className="flex-none text-faint" />
          <input
            ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="搜索页面或操作…" aria-label="搜索命令"
            className="h-12 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-faint"
          />
          <span className="kbd flex-none">ESC</span>
        </div>
        <div ref={listRef} className="scroll-y max-h-[52vh] p-2">
          {results.length === 0 && (
            <p className="px-3 py-8 text-center text-sm text-faint">没有匹配的命令</p>
          )}
          {results.map((c, i) => {
            const header = c.group !== lastGroup ? c.group : null;
            lastGroup = c.group;
            return (
              <div key={c.id}>
                {header && <p className="section-title px-3 pb-1 pt-2">{header}</p>}
                <button
                  data-idx={i}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => c.run()}
                  className={cn("flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors",
                    i === active ? "bg-brand/15 text-ink" : "text-muted")}
                >
                  <span className={cn("flex-none", i === active ? "text-brand-soft" : "text-faint")}>{c.icon}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{c.label}</span>
                    {c.hint && <span className="block truncate text-[11px] text-faint">{c.hint}</span>}
                  </span>
                  {i === active && <IconArrowRight size={15} className="flex-none text-brand-soft" />}
                </button>
              </div>
            );
          })}
        </div>
        <div className="flex items-center gap-3 border-t border-line-soft px-4 py-2 text-[10px] text-faint">
          <span className="flex items-center gap-1"><span className="kbd">↑</span><span className="kbd">↓</span> 选择</span>
          <span className="flex items-center gap-1"><span className="kbd">↵</span> 执行</span>
          <span className="ml-auto flex items-center gap-1"><IconCreate size={12} /> Möbius Studio</span>
        </div>
      </div>
    </div>,
    document.body
  );
}
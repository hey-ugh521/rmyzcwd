import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useConnection } from "../api/client";
import { refreshConnection, switchMode } from "../api";
import { useUI } from "../store/ui";
import { cn } from "../lib/utils";
import { Led, Button, Badge } from "../components/ui";
import { IconWifi, IconRefresh, IconChevronDown, IconDatabase, IconCpu } from "../components/icons";
import { toast } from "../store/feedback";

export function ConnectionStatus() {
  const conn = useConnection();
  const override = useUI((s) => s.apiModeOverride);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ top: 0, right: 0 });

  const mock = conn.mode === "mock";
  const ok = !mock && conn.coreOk;
  const tone = mock ? "warning" : ok ? "success" : "danger";
  const label = conn.probing ? "连接中" : mock ? "演示数据" : ok ? "本机已连接" : "Core 不可达";

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = ref.current?.getBoundingClientRect();
      if (r) setPos({ top: r.bottom + 8, right: window.innerWidth - r.right });
    };
    place();
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node) && !popRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("resize", place);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("resize", place); document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const doRefresh = async () => {
    setBusy(true);
    await refreshConnection();
    setBusy(false);
    const c = useConnection.getState();
    if (c.mode === "mock") toast.info("当前为演示数据", "切换到「本机」以连接真实服务。");
    else toast[c.coreOk ? "success" : "error"](c.coreOk ? "本机服务正常" : "Core 仍不可达", c.lastError ?? undefined);
  };

  const doSwitch = async (m: "mock" | "live") => {
    setBusy(true);
    await switchMode(m);
    setBusy(false);
    setOpen(false);
    toast.info(m === "mock" ? "已切换到演示数据" : "已切换到本机服务", m === "live" ? "正在探活 Core / Gateway…" : undefined);
  };

  return (
    <>
      <button
        ref={ref}
        onClick={() => setOpen((v) => !v)}
        className={cn("inline-flex h-8 items-center gap-2 rounded-full border px-3 text-xs font-medium transition-colors",
          tone === "success" && "border-success/30 bg-success/10 text-success",
          tone === "warning" && "border-warning/30 bg-warning/10 text-warning",
          tone === "danger" && "border-danger/30 bg-danger/10 text-danger")}
        aria-haspopup="dialog" aria-expanded={open}
        title="数据源与连接状态"
      >
        <Led tone={tone} live={ok || conn.probing} />
        <span className="hidden sm:inline">{label}</span>
        <IconChevronDown size={13} className={cn("transition-transform", open && "rotate-180")} />
      </button>

      {open && createPortal(
        <div ref={popRef} role="dialog" aria-label="连接状态"
          className="fixed z-[120] w-[300px] rounded-xl border border-line bg-overlay/95 glass p-4 shadow-pop animate-scale-in"
          style={{ top: pos.top, right: pos.right }}>
          <div className="flex items-center gap-2">
            <IconWifi size={16} className="text-brand-soft" />
            <p className="text-sm font-semibold text-ink">数据源</p>
            <Badge tone={tone} className="ml-auto">{mock ? "MOCK" : "LIVE"}</Badge>
          </div>

          <div className="mt-3 space-y-2 text-xs">
            <Row icon={<IconCpu size={14} />} label="Core" ok={conn.coreOk} mock={mock} />
            <Row icon={<IconDatabase size={14} />} label="Media Gateway" ok={conn.gatewayOk} mock={mock} />
          </div>

          {conn.lastError && !mock && (
            <p className="mt-3 rounded-md border border-danger/25 bg-danger/10 px-2.5 py-2 text-[11px] leading-snug text-danger">{conn.lastError}</p>
          )}
          {mock && (
            <p className="mt-3 rounded-md border border-line-soft bg-raised/60 px-2.5 py-2 text-[11px] leading-snug text-muted">
              演示数据由本机 mock 引擎生成（含程序化预览图与合成音频），不连接真实服务、不读取任何用户数据。
            </p>
          )}

          <div className="mt-3 grid grid-cols-2 gap-1.5 rounded-lg border border-line-soft bg-base/50 p-1">
            <button onClick={() => doSwitch("live")} disabled={busy}
              className={cn("h-8 rounded-md text-xs font-medium transition-colors", !mock ? "bg-raised text-ink shadow-sm" : "text-muted hover:text-ink")}>
              本机服务
            </button>
            <button onClick={() => doSwitch("mock")} disabled={busy}
              className={cn("h-8 rounded-md text-xs font-medium transition-colors", mock ? "bg-raised text-ink shadow-sm" : "text-muted hover:text-ink")}>
              演示数据
            </button>
          </div>
          {override && (
            <p className="mt-1.5 text-center text-[10px] text-faint">已手动固定为 {override === "mock" ? "演示" : "本机"}（设置中可恢复自动）</p>
          )}

          <Button variant="ghost" size="sm" className="mt-3 w-full" icon={<IconRefresh size={14} />} loading={busy} onClick={doRefresh}>
            重新探活
          </Button>
        </div>,
        document.body
      )}
    </>
  );
}

function Row({ icon, label, ok, mock }: { icon: React.ReactNode; label: string; ok: boolean; mock: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-line-soft bg-raised/50 px-2.5 py-2">
      <span className="text-faint">{icon}</span>
      <span className="flex-1 text-muted">{label}</span>
      {mock ? <span className="text-faint">模拟</span> : <Led tone={ok ? "success" : "danger"} live={ok} />}
      <span className={cn("tnum text-[11px]", mock ? "text-faint" : ok ? "text-success" : "text-danger")}>
        {mock ? "ready" : ok ? "online" : "offline"}
      </span>
    </div>
  );
}
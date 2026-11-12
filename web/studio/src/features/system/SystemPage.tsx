import { useEffect, useRef, useState } from "react";
import type { ServiceInfo } from "../../api/types";
import {
  useStatus, useServices, useResources, useMediaConfig, useServiceHistory,
  useServiceAction, useSetMediaMode, useMediaWake, useMediaSleep, useSetMediaConfig, useServiceLogs,
} from "../../api/hooks";
import { serviceTone, serviceText, normalizeStatus } from "../../api/normalize";
import { confirmAction, toast } from "../../store/feedback";
import { playSound } from "../../lib/sound";
import { cn, fmtUptime, fmtTime, fmtNumber } from "../../lib/utils";
import { Card, Button, Led, Badge, Skeleton, ProgressBar, Select, Stepper, Toggle, Field, Tooltip, toneText } from "../../components/ui";
import { Drawer } from "../../components/overlays";
import {
  IconSystem, IconCpu, IconZap, IconPower, IconRefresh, IconStop, IconTerminal,
  IconClock, IconDatabase, IconLayers, IconWarning, IconPlay, IconMoon, IconSun,
} from "../../components/icons";

const MODES = [
  { value: "off", label: "关闭", desc: "停用媒体能力" },
  { value: "on_demand", label: "按需", desc: "用时唤醒，空闲休眠" },
  { value: "prewarm", label: "预热", desc: "常驻就绪，响应最快" },
];

function Gauge({ value, label, tone = "brand", unit = "%" }: { value: number; label: string; tone?: "brand" | "magenta" | "cyan" | "warning"; unit?: string }) {
  const r = 34, c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value));
  const stroke = { brand: "rgb(var(--c-brand))", magenta: "rgb(var(--c-magenta))", cyan: "rgb(var(--c-cyan))", warning: "rgb(var(--c-warning))" }[tone];
  return (
    <div className="flex flex-col items-center">
      <div className="relative h-24 w-24">
        <svg viewBox="0 0 80 80" className="h-full w-full -rotate-90">
          <circle cx="40" cy="40" r={r} fill="none" stroke="rgb(var(--c-raised))" strokeWidth="7" />
          <circle cx="40" cy="40" r={r} fill="none" stroke={stroke} strokeWidth="7" strokeLinecap="round"
            strokeDasharray={c} strokeDashoffset={c - (pct / 100) * c} style={{ transition: "stroke-dashoffset 0.6s var(--ease-out)" }} />
        </svg>
        <div className="absolute inset-0 grid place-items-center">
          <span className="tnum font-display text-lg font-semibold text-ink">{Math.round(pct)}<span className="text-xs text-faint">{unit}</span></span>
        </div>
      </div>
      <p className="mt-1 text-xs text-muted">{label}</p>
    </div>
  );
}

function ServiceCard({ s, onLogs }: { s: ServiceInfo; onLogs: (name: string) => void }) {
  const action = useServiceAction();
  const tone = serviceTone(s.state);
  const ready = s.state === "ready";
  const disabled = s.enabled === false;

  const act = async (a: "start" | "stop" | "restart") => {
    const labels = { start: "启动", stop: "停止", restart: "重启" };
    const ok = await confirmAction({
      title: `${labels[a]}服务`, tone: a === "stop" ? "danger" : "brand", confirmLabel: labels[a],
      message: `确定${labels[a]}「${s.label || s.name}」？${a === "stop" ? "停止后相关创作能力将不可用，直到再次启动。" : "操作会作用于本机真实服务进程。"}`,
    });
    if (!ok) return;
    playSound(a === "stop" ? "cancel" : "submit");
    action.mutate({ name: s.name, action: a });
  };

  return (
    <Card className={cn("p-4", disabled && "opacity-70")}>
      <div className="flex items-start gap-3">
        <span className="mt-1"><Led tone={tone} live={ready} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-ink">{s.label || s.name}</p>
            {s.autostart && <Tooltip label="开机自启"><Badge tone="muted">自启</Badge></Tooltip>}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-muted">{s.detail || s.name}</p>
        </div>
        <span className={cn("flex-none text-xs font-medium", toneText[tone])}>{serviceText(s.state)}</span>
      </div>

      <div className="mt-3 flex items-center gap-2 text-[11px] text-faint">
        {s.port && <span className="tnum rounded border border-line-soft bg-base/50 px-1.5 py-0.5">:{s.port}</span>}
        {s.pid != null && <span className="tnum">PID {s.pid}</span>}
        {disabled && <span className="text-faint">已禁用</span>}
        <button onClick={() => onLogs(s.name)} className="ml-auto inline-flex items-center gap-1 text-brand-soft hover:underline">
          <IconTerminal size={12} /> 日志
        </button>
      </div>

      <div className="mt-3 flex gap-1.5">
        {ready ? (
          <>
            <Button variant="ghost" size="sm" className="flex-1" icon={<IconRefresh size={13} />} loading={action.isPending} onClick={() => act("restart")}>重启</Button>
            <Button variant="danger" size="sm" className="flex-1" icon={<IconStop size={13} />} loading={action.isPending} onClick={() => act("stop")}>停止</Button>
          </>
        ) : (
          <Button variant="soft" size="sm" className="flex-1" icon={<IconPlay size={13} />} loading={action.isPending} disabled={disabled} onClick={() => act("start")}>
            {s.state === "starting" ? "启动中…" : "启动"}
          </Button>
        )}
      </div>
    </Card>
  );
}

function LogsDrawer({ name, onClose }: { name: string | null; onClose: () => void }) {
  const { data, isLoading } = useServiceLogs(name ?? "", !!name);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [data?.logs]);
  return (
    <Drawer open={!!name} onClose={onClose} width={620} title={`${name || ""} · 实时日志`}
      subtitle={<span className="text-[11px] text-faint">每 4 秒刷新 · 仅本机日志，不含密钥</span>}>
      <div ref={ref} className="scroll-y h-[60vh] rounded-lg border border-line-soft bg-base/80 p-3 font-mono text-[11px] leading-relaxed">
        {isLoading ? <Skeleton className="h-full w-full" /> : (
          (data?.logs ?? []).map((line, i) => (
            <div key={i} className={cn("whitespace-pre-wrap break-all py-0.5",
              /ERROR|WARN|失败/i.test(line) ? "text-warning" : /ready|ok|200|完成/i.test(line) ? "text-success/90" : "text-muted")}>
              {line}
            </div>
          ))
        )}
        {!isLoading && (data?.logs?.length ?? 0) === 0 && <p className="text-faint">暂无日志</p>}
      </div>
    </Drawer>
  );
}

export function SystemPage() {
  const status = useStatus();
  const services = useServices();
  const resources = useResources();
  const config = useMediaConfig();
  const history = useServiceHistory();
  const setMode = useSetMediaMode();
  const wake = useMediaWake();
  const sleep = useMediaSleep();
  const setConfig = useSetMediaConfig();
  const [logs, setLogs] = useState<string | null>(null);
  const [idle, setIdle] = useState<number | null>(null);

  const svcList = services.data?.services ?? [];
  const gpu = Array.isArray(resources.data?.gpu) ? resources.data?.gpu[0] : resources.data?.gpu;
  const vramPct = gpu?.vram_total_mb ? ((gpu.vram_used_mb ?? 0) / gpu.vram_total_mb) * 100 : 0;
  const mediaState = status.data?.media?.state ?? "unknown";
  const mode = config.data?.mode ?? "on_demand";
  const idleSec = idle ?? config.data?.idle_sleep_sec ?? 600;
  const queue = resources.data?.queue;

  const changeMode = async (m: string) => {
    if (m === mode) return;
    const ok = await confirmAction({
      title: "切换媒体模式", tone: m === "off" ? "danger" : "brand", confirmLabel: "切换",
      message: `确定将媒体模式切换为「${MODES.find((x) => x.value === m)?.label}」？${m === "off" ? "关闭后图像与语音生成将不可用。" : ""}`,
    });
    if (ok) setMode.mutate(m);
  };

  const doWake = async () => {
    const ok = await confirmAction({ title: "唤醒媒体服务", confirmLabel: "唤醒", message: "确定唤醒 ComfyUI 等媒体引擎？将占用本机 GPU 资源。" });
    if (ok) { playSound("submit"); wake.mutate(); }
  };
  const doSleep = async () => {
    const ok = await confirmAction({ title: "休眠媒体服务", tone: "danger", confirmLabel: "休眠", message: "确定让媒体服务进入空闲休眠？正在执行的任务可能受影响。" });
    if (ok) { playSound("cancel"); sleep.mutate(); }
  };

  return (
    <div className="space-y-6">
      {/* 概览 */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card className="p-4">
          <div className="flex items-center gap-2 text-xs text-muted"><IconClock size={14} /> Core 运行时长</div>
          <p className="tnum mt-2 font-display text-xl font-semibold text-ink">{fmtUptime(status.data?.core?.uptime_sec)}</p>
          <p className="mt-1 text-[11px] text-faint">{status.data?.core?.ok ? "核心编排正常" : "核心异常"}</p>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-2 text-xs text-muted"><IconLayers size={14} /> 媒体引擎</div>
          <div className="mt-2 flex items-center gap-2">
            <Led tone={serviceTone(mediaState)} live={mediaState === "ready"} />
            <p className="font-display text-xl font-semibold text-ink">{serviceText(mediaState)}</p>
          </div>
          <p className="mt-1 text-[11px] text-faint">模式：{MODES.find((m) => m.value === mode)?.label ?? mode}</p>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-2 text-xs text-muted"><IconZap size={14} /> 执行队列</div>
          <p className="tnum mt-2 font-display text-xl font-semibold text-ink">{fmtNumber((queue?.running ?? 0) + (queue?.pending ?? 0))}</p>
          <p className="mt-1 text-[11px] text-faint">{queue?.running ?? 0} 运行 · {queue?.pending ?? 0} 排队</p>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-2 text-xs text-muted"><IconDatabase size={14} /> LangBot</div>
          <div className="mt-2 flex items-center gap-2">
            <Led tone={status.data?.langbot?.reachable ? "success" : "muted"} live={!!status.data?.langbot?.reachable} />
            <p className="font-display text-xl font-semibold text-ink">{status.data?.langbot?.reachable ? "在线" : "未启用"}</p>
          </div>
          <p className="mt-1 text-[11px] text-faint">QQ 入口（可选）</p>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        {/* 服务列表 */}
        <div className="xl:col-span-2">
          <div className="mb-3 flex items-center gap-2">
            <IconSystem size={16} className="text-brand-soft" />
            <h3 className="font-display text-base font-semibold text-ink">服务</h3>
            <span className="text-xs text-faint">{svcList.filter((s) => s.state === "ready").length}/{svcList.length} 就绪</span>
            <Button variant="ghost" size="sm" className="ml-auto" icon={<IconRefresh size={13} />} onClick={() => services.refetch()}>刷新</Button>
          </div>
          {services.isLoading ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-36 w-full" />)}</div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {svcList.map((s) => <ServiceCard key={s.name} s={s} onLogs={setLogs} />)}
            </div>
          )}
        </div>

        {/* 资源 + 媒体控制 */}
        <div className="space-y-6">
          <Card className="p-4">
            <div className="mb-3 flex items-center gap-2">
              <IconCpu size={16} className="text-magenta" />
              <h3 className="font-display text-base font-semibold text-ink">GPU 资源</h3>
            </div>
            {gpu ? (
              <>
                <p className="mb-3 truncate text-xs text-muted">{gpu.name || "GPU"}</p>
                <div className="flex items-center justify-around">
                  <Gauge value={gpu.utilization ?? 0} label="利用率" tone="brand" />
                  <Gauge value={vramPct} label="显存" tone="magenta" />
                </div>
                <div className="mt-4 space-y-2">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-faint">显存占用</span>
                    <span className="tnum text-muted">{Math.round((gpu.vram_used_mb ?? 0) / 1024)} / {Math.round((gpu.vram_total_mb ?? 0) / 1024)} GB</span>
                  </div>
                  <ProgressBar value={vramPct} tone={vramPct > 85 ? "danger" : "magenta"} />
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-faint">温度</span>
                    <span className={cn("tnum", (gpu.temperature ?? 0) > 80 ? "text-danger" : "text-muted")}>{gpu.temperature ?? "—"} °C</span>
                  </div>
                </div>
              </>
            ) : <Skeleton className="h-40 w-full" />}
          </Card>

          <Card className="p-4">
            <div className="mb-3 flex items-center gap-2">
              <IconPower size={16} className="text-cyan" />
              <h3 className="font-display text-base font-semibold text-ink">媒体控制</h3>
            </div>
            <Field label="运行模式">
              <div className="grid grid-cols-3 gap-1.5 rounded-lg border border-line-soft bg-base/50 p-1">
                {MODES.map((m) => (
                  <Tooltip key={m.value} label={m.desc}>
                    <button onClick={() => changeMode(m.value)} disabled={setMode.isPending}
                      className={cn("h-8 w-full rounded-md text-xs font-medium transition-colors",
                        mode === m.value ? "bg-raised text-ink shadow-sm" : "text-muted hover:text-ink")}>
                      {m.label}
                    </button>
                  </Tooltip>
                ))}
              </div>
            </Field>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <Button variant="soft" size="sm" icon={<IconSun size={14} />} loading={wake.isPending} onClick={doWake} disabled={mode === "off"}>唤醒</Button>
              <Button variant="ghost" size="sm" icon={<IconMoon size={14} />} loading={sleep.isPending} onClick={doSleep}>休眠</Button>
            </div>
            <div className="mt-4 space-y-3 border-t border-line-soft pt-3">
              <Field label="空闲休眠（秒）" hint="按需模式下，空闲超过此时长自动休眠">
                <div className="flex items-center gap-2">
                  <Stepper value={idleSec} onChange={setIdle} min={60} max={7200} step={60} ariaLabel="空闲休眠秒数" className="flex-1" />
                  <Button variant="ghost" size="sm" loading={setConfig.isPending}
                    onClick={async () => {
                      const ok = await confirmAction({ title: "更新媒体配置", confirmLabel: "保存", message: `将空闲休眠时长设为 ${idleSec} 秒？` });
                      if (ok) setConfig.mutate({ idle_sleep_sec: idleSec });
                    }}>保存</Button>
                </div>
              </Field>
            </div>
            {mode === "off" && (
              <p className="mt-3 flex items-center gap-1.5 rounded-md border border-warning/25 bg-warning/10 px-2.5 py-2 text-[11px] text-warning">
                <IconWarning size={13} /> 媒体已关闭，图像与语音生成不可用。
              </p>
            )}
          </Card>
        </div>
      </div>

      {/* 状态历史 */}
      <Card className="p-4 sm:p-5">
        <div className="mb-3 flex items-center gap-2">
          <IconClock size={16} className="text-brand-soft" />
          <h3 className="font-display text-base font-semibold text-ink">状态变更历史</h3>
        </div>
        {history.isLoading ? <Skeleton className="h-24 w-full" /> : (
          <ol className="relative space-y-3 border-l border-line-soft pl-5">
            {(history.data?.history ?? []).slice(0, 12).map((h, i) => (
              <li key={i} className="relative">
                <span className="absolute -left-[26px] top-1"><Led tone={serviceTone(h.to)} /></span>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium text-ink">{h.service}</span>
                  <span className="text-faint">{serviceText(h.from)}</span>
                  <span className="text-faint">→</span>
                  <span className={toneText[serviceTone(h.to)]}>{serviceText(h.to)}</span>
                  <span className="ml-auto tnum text-[11px] text-faint">{fmtTime(h.ts)}</span>
                </div>
                {h.detail && <p className="mt-0.5 text-[11px] text-muted">{h.detail}</p>}
              </li>
            ))}
            {(history.data?.history?.length ?? 0) === 0 && <p className="text-xs text-faint">暂无记录</p>}
          </ol>
        )}
      </Card>

      <LogsDrawer name={logs} onClose={() => setLogs(null)} />
    </div>
  );
}
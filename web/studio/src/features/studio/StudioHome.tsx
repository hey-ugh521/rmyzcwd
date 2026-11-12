import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useStatus, useTasks, useResources, useServices } from "../../api/hooks";
import { normalizeStatus, isActive, serviceTone, serviceText, kindText } from "../../api/normalize";
import { useConnection } from "../../api/client";
import { cn, fmtUptime, fmtRelative, fmtNumber } from "../../lib/utils";
import { Card, Button, StatusBadge, Led, Skeleton, EmptyState, Badge, ProgressBar, toneText } from "../../components/ui";
import { ImageTile, VoiceGlyph } from "../../components/media";
import { TaskDetail } from "../../components/TaskDetail";
import {
  IconImage, IconMic, IconChat, IconArrowRight, IconCpu, IconZap,
  IconSuccess, IconClock, IconLayers, IconStudio, IconPlus, IconStop,
} from "../../components/icons";
import { useCancelTask } from "../../api/hooks";
import { confirmAction } from "../../store/feedback";
import { playSound } from "../../lib/sound";

function greeting() {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 12) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

function Stat({ icon, label, value, sub, blob, iconClass, loading }:
  { icon: React.ReactNode; label: string; value: string; sub?: string; blob: string; iconClass: string; loading?: boolean }) {
  return (
    <Card className="relative overflow-hidden p-4">
      <div className={cn("absolute -right-6 -top-6 h-20 w-20 rounded-full opacity-[0.12] blur-2xl", blob)} />
      <div className="relative flex items-start justify-between">
        <div>
          <p className="text-xs text-muted">{label}</p>
          {loading ? <Skeleton className="mt-2 h-7 w-16" /> : (
            <p className="tnum mt-1.5 font-display text-2xl font-semibold tracking-tight text-ink">{value}</p>
          )}
          {sub && <p className="mt-1 text-[11px] text-faint">{sub}</p>}
        </div>
        <span className={cn("grid h-9 w-9 flex-none place-items-center rounded-lg bg-raised", iconClass)}>{icon}</span>
      </div>
    </Card>
  );
}

export function StudioHome() {
  const navigate = useNavigate();
  const mode = useConnection((s) => s.mode);
  const status = useStatus();
  const tasks = useTasks();
  const resources = useResources();
  const services = useServices();
  const cancel = useCancelTask();
  const [detail, setDetail] = useState<string | null>(null);

  const list = tasks.data?.tasks ?? [];
  const active = useMemo(() => list.filter((t) => isActive(t.status)), [list]);
  const recentWorks = useMemo(() => list.filter((t) => normalizeStatus(t.status) === "completed" && t.result?.url).slice(0, 8), [list]);
  const counters = status.data?.counters;
  const completed = counters?.completed ?? 0;
  const failed = counters?.failed ?? 0;
  const total = counters?.total ?? 0;
  const rate = completed + failed > 0 ? Math.round((completed / (completed + failed)) * 100) : 100;
  const gpu = Array.isArray(resources.data?.gpu) ? resources.data?.gpu[0] : resources.data?.gpu;
  const vramPct = gpu && gpu.vram_total_mb ? Math.round(((gpu.vram_used_mb ?? 0) / gpu.vram_total_mb) * 100) : 0;
  const svcList = services.data?.services ?? [];
  const coreSvc = svcList.filter((s) => ["core", "gateway", "comfyui", "kokoro", "gsv"].includes(s.name));

  const quickCancel = async (id: string, prompt?: string) => {
    const ok = await confirmAction({ title: "取消任务", tone: "danger", confirmLabel: "取消任务", message: `确定取消「${(prompt || id).slice(0, 24)}」？` });
    if (ok) { playSound("cancel"); cancel.mutate(id); }
  };

  return (
    <div className="space-y-6">
      {/* 问候 + 主操作 */}
      <div className="relative overflow-hidden rounded-xl border border-line-soft bg-gradient-to-br from-brand/12 via-surface/40 to-magenta/10 p-5 sm:p-6">
        <div className="pointer-events-none absolute -right-10 -top-16 h-56 w-56 rounded-full bg-brand/15 blur-3xl" />
        <div className="relative flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Badge tone={mode === "mock" ? "warning" : "success"} dot>{mode === "mock" ? "演示数据" : "本机运行"}</Badge>
              <span className="text-xs text-faint">{new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })}</span>
            </div>
            <h2 className="mt-2.5 font-display text-2xl font-semibold tracking-tight text-ink sm:text-[28px]">
              {greeting()}，开始今天的<span className="text-gradient">创作</span>
            </h2>
            <p className="mt-1.5 max-w-xl text-sm text-muted">
              在本机生成图像与语音，全程数据不出设备。{active.length > 0 ? `当前有 ${active.length} 个任务正在执行。` : "队列空闲，随时可以开始。"}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" size="lg" icon={<IconImage size={17} />} onClick={() => navigate("/create?tab=image")}>生成图像</Button>
            <Button variant="ghost" size="lg" icon={<IconMic size={17} />} onClick={() => navigate("/create?tab=voice")}>合成语音</Button>
          </div>
        </div>
      </div>

      {/* 统计 */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat icon={<IconSuccess size={18} />} label="今日完成" value={fmtNumber(completed)} sub={`共 ${total} 个任务`} blob="bg-success" iconClass="text-success" loading={status.isLoading} />
        <Stat icon={<IconZap size={18} />} label="进行中" value={fmtNumber(active.length)} sub={active.length ? "实时刷新" : "队列空闲"} blob="bg-brand" iconClass="text-brand-soft" loading={tasks.isLoading} />
        <Stat icon={<IconLayers size={18} />} label="成功率" value={`${rate}%`} sub={`${failed} 个失败`} blob="bg-cyan" iconClass="text-cyan" loading={status.isLoading} />
        <Stat icon={<IconCpu size={18} />} label="显存占用" value={`${vramPct}%`} sub={gpu ? `${Math.round((gpu.vram_used_mb ?? 0) / 1024)} / ${Math.round((gpu.vram_total_mb ?? 0) / 1024)} GB` : "—"} blob="bg-magenta" iconClass="text-magenta" loading={resources.isLoading} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        {/* 左：快捷入口 + 最近作品 */}
        <div className="space-y-6 xl:col-span-2">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[
              { to: "/create?tab=image", icon: <IconImage size={20} />, title: "图像创作", desc: "多底模 · LoRA · 实时参数", tone: "text-magenta" },
              { to: "/create?tab=voice", icon: <IconMic size={20} />, title: "语音合成", desc: "Kokoro · 克隆音色", tone: "text-cyan" },
              { to: "/chat", icon: <IconChat size={20} />, title: "对话助手", desc: "说「画…」即创作", tone: "text-brand-soft" },
            ].map((q) => (
              <Link key={q.to} to={q.to} className="card card-hover group flex items-center gap-3 p-4">
                <span className={cn("grid h-11 w-11 flex-none place-items-center rounded-xl bg-raised ring-1 ring-line-soft", q.tone)}>{q.icon}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-ink">{q.title}</span>
                  <span className="block truncate text-[11px] text-muted">{q.desc}</span>
                </span>
                <IconArrowRight size={16} className="flex-none text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-brand-soft" />
              </Link>
            ))}
          </div>

          <Card className="p-4 sm:p-5">
            <div className="mb-4 flex items-center gap-2">
              <h3 className="font-display text-base font-semibold text-ink">最近作品</h3>
              <Link to="/gallery" className="ml-auto inline-flex items-center gap-1 text-xs text-brand-soft hover:underline">
                查看全部 <IconArrowRight size={13} />
              </Link>
            </div>
            {tasks.isLoading ? (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="aspect-square w-full" />)}
              </div>
            ) : recentWorks.length === 0 ? (
              <EmptyState icon={<IconStudio size={22} />} title="还没有作品" desc="生成第一张图像后，会自动收录到这里。"
                action={<Button variant="primary" size="sm" icon={<IconPlus size={15} />} onClick={() => navigate("/create?tab=image")}>开始创作</Button>} />
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {recentWorks.map((t) => (
                  <div key={t.id} className="space-y-1.5">
                    {t.kind === "image" ? (
                      <ImageTile src={t.result?.url} alt={t.prompt || "作品"} className="aspect-square w-full ring-1 ring-line-soft" onClick={() => setDetail(t.id)} />
                    ) : (
                      <button onClick={() => setDetail(t.id)} className="grid aspect-square w-full place-items-center rounded-lg bg-cyan/8 ring-1 ring-cyan/20 transition-colors hover:bg-cyan/14">
                        <VoiceGlyph className="h-12 w-12" />
                      </button>
                    )}
                    <p className="truncate text-[11px] text-muted" title={t.prompt}>{t.prompt || kindText(t.kind)}</p>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* 右：实时队列 + 服务健康 */}
        <div className="space-y-6">
          <Card className="p-4 sm:p-5">
            <div className="mb-3 flex items-center gap-2">
              <h3 className="font-display text-base font-semibold text-ink">实时队列</h3>
              {active.length > 0 && <span className="led led-live bg-brand" />}
              <Link to="/tasks" className="ml-auto text-xs text-brand-soft hover:underline">全部任务</Link>
            </div>
            {active.length === 0 ? (
              <div className="rounded-lg border border-dashed border-line py-8 text-center">
                <IconClock size={22} className="mx-auto text-faint" />
                <p className="mt-2 text-xs text-muted">队列空闲</p>
              </div>
            ) : (
              <ul className="space-y-2.5">
                {active.slice(0, 5).map((t) => (
                  <li key={t.id} className="rounded-lg border border-line-soft bg-base/40 p-3">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={t.status} />
                      <button onClick={() => setDetail(t.id)} className="ml-auto text-faint hover:text-brand-soft" aria-label="查看详情"><IconArrowRight size={14} /></button>
                      <button onClick={() => quickCancel(t.id, t.prompt)} className="text-faint hover:text-danger" aria-label="取消任务"><IconStop size={14} /></button>
                    </div>
                    <p className="mt-2 truncate text-xs text-ink" title={t.prompt}>{t.prompt || t.id}</p>
                    <ProgressBar indeterminate className="mt-2" tone={normalizeStatus(t.status) === "pending" ? "warning" : "brand"} />
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card className="p-4 sm:p-5">
            <div className="mb-3 flex items-center gap-2">
              <h3 className="font-display text-base font-semibold text-ink">服务健康</h3>
              <Link to="/system" className="ml-auto text-xs text-brand-soft hover:underline">系统详情</Link>
            </div>
            <ul className="space-y-1.5">
              {coreSvc.map((s) => (
                <li key={s.name} className="flex items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-raised/50">
                  <Led tone={serviceTone(s.state)} live={s.state === "ready"} />
                  <span className="flex-1 truncate text-xs text-ink">{s.label || s.name}</span>
                  <span className={cn("text-[11px]", toneText[serviceTone(s.state)])}>{serviceText(s.state)}</span>
                </li>
              ))}
              {coreSvc.length === 0 && <Skeleton className="h-24 w-full" />}
            </ul>
            <div className="mt-3 flex items-center justify-between border-t border-line-soft pt-3 text-[11px] text-faint">
              <span>Core 运行时长</span>
              <span className="tnum text-muted">{fmtUptime(status.data?.core?.uptime_sec)}</span>
            </div>
          </Card>
        </div>
      </div>

      <TaskDetail taskId={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
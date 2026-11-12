import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useUI } from "../../store/ui";
import { useLibrary } from "../../store/library";
import { useConnection } from "../../api/client";
import { initApi, refreshConnection, switchMode, resetMock } from "../../api";
import { useMediaConfig } from "../../api/hooks";
import { confirmAction, toast } from "../../store/feedback";
import { playSound } from "../../lib/sound";
import { cn } from "../../lib/utils";
import {
  Card, Button, Toggle, Stepper, Led, Badge, Skeleton, Tooltip,
} from "../../components/ui";
import {
  IconSun, IconMoon, IconVolume, IconWifi, IconDatabase,
  IconCpu, IconRefresh, IconLock, IconInfo, IconSliders, IconClock,
  IconCheck, IconLogo, IconStudio, IconGallery, IconWarning, IconPower,
} from "../../components/icons";

const APP_VERSION = "1.0.0";

const SECTIONS = [
  { id: "appearance", label: "外观与声音", icon: IconSun },
  { id: "connection", label: "数据源与连接", icon: IconWifi },
  { id: "backend", label: "本机后端（只读）", icon: IconDatabase },
  { id: "data", label: "数据与隐私", icon: IconLock },
  { id: "about", label: "关于", icon: IconInfo },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

/* ------------------------------------------------------------------ 布局件 */
function Section({ id, icon, title, desc, action, children }: {
  id: SectionId; icon: React.ReactNode; title: string; desc?: string;
  action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <Card id={`set-${id}`} className="scroll-mt-4 overflow-hidden">
      <div className="flex items-center gap-3 border-b border-line-soft px-5 py-4">
        <span className="grid h-9 w-9 flex-none place-items-center rounded-lg border border-line-soft bg-raised/60 text-brand-soft">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-sm font-semibold text-ink">{title}</h3>
          {desc && <p className="mt-0.5 text-xs leading-snug text-muted">{desc}</p>}
        </div>
        {action}
      </div>
      <div className="divide-y divide-line-soft px-5">{children}</div>
    </Card>
  );
}

function Row({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 sm:pr-6">
        <p className="text-sm font-medium text-ink">{title}</p>
        {desc && <p className="mt-1 max-w-md text-xs leading-relaxed text-muted">{desc}</p>}
      </div>
      <div className="flex flex-none items-center gap-2">{children}</div>
    </div>
  );
}

function Segmented<T extends string>({ value, onChange, options, ariaLabel }: {
  value: T; onChange: (v: T) => void; ariaLabel: string;
  options: { value: T; label: React.ReactNode }[];
}) {
  return (
    <div role="radiogroup" aria-label={ariaLabel}
      className="inline-flex items-center gap-1 rounded-lg border border-line-soft bg-base/50 p-1">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-all duration-150",
            value === o.value ? "bg-raised text-ink shadow-sm" : "text-muted hover:text-ink"
          )}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function ConnRow({ icon, label, ok, mock }: { icon: React.ReactNode; label: string; ok: boolean; mock: boolean }) {
  return (
    <div className="flex items-center gap-2.5 rounded-md border border-line-soft bg-raised/50 px-3 py-2.5">
      <span className="text-faint">{icon}</span>
      <span className="flex-1 text-xs text-muted">{label}</span>
      {mock ? <span className="text-[11px] text-faint">模拟</span> : <Led tone={ok ? "success" : "danger"} live={ok} />}
      <span className={cn("tnum text-[11px]", mock ? "text-faint" : ok ? "text-success" : "text-danger")}>
        {mock ? "ready" : ok ? "online" : "offline"}
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------- 页面 */
export function SettingsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const ui = useUI();
  const conn = useConnection();
  const config = useMediaConfig();
  const resetLibrary = useLibrary((s) => s.resetLibrary);
  const [active, setActive] = useState<SectionId>("appearance");
  const [busy, setBusy] = useState(false);

  const mock = conn.mode === "mock";
  const modeValue = ui.apiModeOverride ?? "auto";

  // 滚动监听：高亮当前分区（滚动容器为 Layout 的 #main）
  useEffect(() => {
    const root = document.getElementById("main");
    const els = SECTIONS.map((s) => document.getElementById(`set-${s.id}`)).filter(Boolean) as HTMLElement[];
    if (!els.length) return;
    const obs = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (vis[0]) setActive(vis[0].target.id.replace("set-", "") as SectionId);
      },
      { root, rootMargin: "-8% 0px -78% 0px", threshold: 0 }
    );
    els.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, []);

  const jump = useCallback((id: SectionId) => {
    document.getElementById(`set-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActive(id);
  }, []);

  const changeMode = async (m: "auto" | "mock" | "live") => {
    if (m === modeValue) return;
    setBusy(true);
    if (m === "auto") { ui.setApiModeOverride(null); await initApi(); }
    else await switchMode(m);
    setBusy(false);
    const c = useConnection.getState();
    toast.info(
      m === "auto" ? "已恢复自动选择" : m === "mock" ? "已切换到演示数据" : "已切换到本机服务",
      m === "auto" ? `当前解析为「${c.mode === "mock" ? "演示" : "本机"}」` : m === "live" ? "正在探活 Core / Gateway…" : undefined
    );
  };

  const reprobe = async () => {
    setBusy(true);
    if (mock) { await initApi(); } else { await refreshConnection(); }
    setBusy(false);
    const c = useConnection.getState();
    if (c.mode === "mock") toast.info("当前为演示数据", "切换到「本机」以连接真实服务。");
    else toast[c.coreOk ? "success" : "error"](c.coreOk ? "本机服务正常" : "Core 仍不可达", c.lastError ?? undefined);
  };

  const setRunning = (sec: number) => ui.setPoll(Math.round(sec * 1000), ui.pollIdleMs);
  const setIdle = (sec: number) => ui.setPoll(ui.pollRunningMs, Math.round(sec * 1000));

  const doResetLibrary = async () => {
    const ok = await confirmAction({
      title: "重置作品库视图", tone: "danger", confirmLabel: "重置",
      message: "将清空本机的收藏、隐藏、自定义排序与保存的视图。此操作只影响前端展示，不会删除后端任务或媒体文件。",
    });
    if (!ok) return;
    resetLibrary();
    toast.success("作品库视图已重置");
  };

  const doResetDemo = async () => {
    const ok = await confirmAction({
      title: "重置演示数据", tone: "brand", confirmLabel: "重新生成",
      message: "将重新生成 mock 任务、会话与程序化预览。仅影响演示模式，不涉及任何真实用户数据。",
    });
    if (!ok) return;
    resetMock();
    qc.invalidateQueries({ queryKey: ["mobius"] });
    toast.success("演示数据已重新生成");
  };

  const doResetPrefs = async () => {
    const ok = await confirmAction({
      title: "恢复默认偏好", tone: "danger", confirmLabel: "恢复默认",
      message: "将主题、侧边栏、声音、轮询间隔与数据源选择恢复为默认值（数据源回到自动）。作品库视图不受影响。",
    });
    if (!ok) return;
    ui.resetPreferences();
    await initApi();
    toast.success("偏好已恢复默认");
  };

  const replayOnboarding = () => {
    ui.resetOnboarding();
    toast.info("新手引导已重置", "回到首页即可再次查看。");
    navigate("/");
  };

  const mediaModeLabel = ({ off: "关闭", on_demand: "按需", prewarm: "预热" } as Record<string, string>)[config.data?.mode ?? ""] ?? (config.data?.mode ?? "—");

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-xl font-semibold tracking-tight text-ink">设置</h2>
          <p className="mt-1 text-sm text-muted">外观、数据源与本机偏好。所有偏好仅保存在本机浏览器，不含任何密钥。</p>
        </div>
        <Badge tone={mock ? "warning" : conn.coreOk ? "success" : "danger"} dot>
          {mock ? "演示数据" : conn.coreOk ? "本机已连接" : "Core 不可达"}
        </Badge>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[190px_minmax(0,1fr)]">
        {/* 分区导航（sticky） */}
        <nav aria-label="设置分区" className="lg:sticky lg:top-2 lg:self-start">
          <div className="flex gap-1 overflow-x-auto no-scrollbar lg:flex-col lg:overflow-visible">
            {SECTIONS.map((s) => {
              const Icon = s.icon;
              const on = active === s.id;
              return (
                <button key={s.id} type="button" onClick={() => jump(s.id)}
                  aria-current={on ? "true" : undefined}
                  className={cn(
                    "inline-flex flex-none items-center gap-2.5 rounded-md px-3 h-9 text-sm transition-all duration-150 lg:w-full",
                    on ? "bg-brand/12 text-ink" : "text-muted hover:bg-raised/70 hover:text-ink"
                  )}>
                  <Icon size={15} className={cn(on ? "text-brand-soft" : "text-faint")} />
                  <span className="whitespace-nowrap">{s.label}</span>
                </button>
              );
            })}
          </div>
        </nav>

        {/* 内容 */}
        <div className="min-w-0 space-y-6">
          {/* 外观与声音 */}
          <Section id="appearance" icon={<IconSun size={17} />} title="外观与声音"
            desc="主题、侧边栏与提示音。暗色优先，随时切换。">
            <Row title="主题" desc="暗色适合长时间创作；亮色适合明亮环境。">
              <Segmented ariaLabel="主题" value={ui.theme} onChange={(v) => { ui.setTheme(v); playSound("click"); }}
                options={[
                  { value: "dark", label: <><IconMoon size={14} /> 暗色</> },
                  { value: "light", label: <><IconSun size={14} /> 亮色</> },
                ]} />
            </Row>
            <Row title="声音反馈" desc="提交、完成、取消时的轻提示音，全部由本机 WebAudio 合成，无外部音频文件。">
              <Button variant="ghost" size="sm" icon={<IconVolume size={14} />}
                onClick={() => { if (ui.muted) ui.toggleMuted(); playSound("success"); }} disabled={ui.muted}>
                试听
              </Button>
              <Toggle checked={!ui.muted} onChange={() => { ui.toggleMuted(); if (ui.muted) playSound("click"); }}
                label="声音反馈" />
            </Row>
            <Row title="折叠侧边栏" desc="收起导航以扩大画布区域。快捷键 Ctrl/⌘ + B。">
              <Toggle checked={ui.sidebarCollapsed} onChange={(v) => ui.setSidebar(v)} label="折叠侧边栏" />
            </Row>
          </Section>

          {/* 数据源与连接 */}
          <Section id="connection" icon={<IconWifi size={17} />} title="数据源与连接"
            desc="选择数据来源并查看本机服务连通性。"
            action={<Button variant="ghost" size="sm" icon={<IconRefresh size={13} />} loading={busy} onClick={reprobe}>重新检测</Button>}>
            <Row title="数据源" desc="自动：探活本机 Core，可达则用真实服务，否则回退演示。也可手动固定。">
              <Segmented ariaLabel="数据源" value={modeValue} onChange={changeMode}
                options={[
                  { value: "auto", label: "自动" },
                  { value: "live", label: "本机" },
                  { value: "mock", label: "演示" },
                ]} />
            </Row>
            <div className="py-4">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <ConnRow icon={<IconCpu size={14} />} label="Core · 8900" ok={conn.coreOk} mock={mock} />
                <ConnRow icon={<IconDatabase size={14} />} label="Media Gateway · 8899" ok={conn.gatewayOk} mock={mock} />
              </div>
              {conn.lastError && !mock && (
                <p className="mt-2 rounded-md border border-danger/25 bg-danger/10 px-3 py-2 text-[11px] leading-snug text-danger">{conn.lastError}</p>
              )}
              {mock && (
                <p className="mt-2 rounded-md border border-line-soft bg-raised/60 px-3 py-2 text-[11px] leading-snug text-muted">
                  演示数据由本机 mock 引擎生成（程序化预览图与合成音频），不连接真实服务、不读取任何用户数据。
                </p>
              )}
              {ui.apiModeOverride && (
                <p className="mt-2 text-[11px] text-faint">已手动固定为「{ui.apiModeOverride === "mock" ? "演示" : "本机"}」，选择「自动」可恢复探活。</p>
              )}
            </div>
            <Row title="进行中轮询间隔" desc="有任务运行时刷新任务/状态的频率。后端暂无统一任务 SSE，前端按状态轮询。">
              <Stepper ariaLabel="进行中轮询间隔（秒）" value={Math.round(ui.pollRunningMs / 1000)}
                onChange={setRunning} min={1} max={30} step={1} suffix="秒" className="w-36" />
            </Row>
            <Row title="空闲轮询间隔" desc="无运行任务时的刷新频率，降低本机开销。">
              <Stepper ariaLabel="空闲轮询间隔（秒）" value={Math.round(ui.pollIdleMs / 1000)}
                onChange={setIdle} min={5} max={120} step={1} suffix="秒" className="w-36" />
            </Row>
            <div className="flex items-start gap-2 py-3 text-[11px] leading-relaxed text-faint">
              <IconInfo size={13} className="mt-0.5 flex-none" />
              <span>进行中任务只显示「进行中」与不确定进度条，不伪造百分比；终态以执行端返回为准。</span>
            </div>
          </Section>

          {/* 本机后端（只读） */}
          <Section id="backend" icon={<IconDatabase size={17} />} title="本机后端（只读）"
            desc="Core / 网关的运行时配置，前端仅展示。"
            action={<Button variant="soft" size="sm" icon={<IconPower size={13} />} onClick={() => navigate("/system")}>前往服务页</Button>}>
            <div className="flex items-start gap-2.5 border-b border-line-soft py-3.5">
              <IconLock size={14} className="mt-0.5 flex-none text-warning" />
              <p className="text-[11px] leading-relaxed text-muted">
                永久默认值需在 <span className="font-medium text-ink">Launcher / 安装器</span> 中设置；本阶段前端不写后端配置。
                临时媒体控制（唤醒 / 休眠 / 模式切换）请前往「服务」页，那里有二次确认与实时反馈。
              </p>
            </div>
            {config.isLoading ? (
              <div className="space-y-2 py-4"><Skeleton className="h-9 w-full" /><Skeleton className="h-9 w-full" /></div>
            ) : config.isError ? (
              <div className="flex items-center gap-2 py-4 text-xs text-danger">
                <IconWarning size={14} /> 无法读取媒体配置（{mock ? "演示" : "本机"}）。
                <Button variant="ghost" size="sm" className="ml-auto" onClick={() => config.refetch()}>重试</Button>
              </div>
            ) : (
              <>
                <Row title="媒体模式" desc="off 关闭 · on_demand 按需 · prewarm 预热。">
                  <Badge tone={config.data?.mode === "off" ? "muted" : config.data?.mode === "prewarm" ? "brand" : "cyan"}>
                    <IconLock size={11} /> {mediaModeLabel}
                  </Badge>
                </Row>
                <Row title="空闲休眠阈值" desc="媒体引擎空闲多久后自动休眠以释放显存。">
                  <span className="tnum inline-flex h-9 items-center rounded-md border border-line-soft bg-base/50 px-3 text-sm text-muted">
                    {config.data?.idle_sleep_sec ?? "—"} <span className="ml-1 text-[11px] text-faint">秒</span>
                  </span>
                </Row>
                <Row title="开机预热" desc="启动时是否预先加载媒体引擎。">
                  <Badge tone={config.data?.prewarm_on_start ? "success" : "muted"}>
                    {config.data?.prewarm_on_start ? "开启" : "关闭"}
                  </Badge>
                </Row>
              </>
            )}
          </Section>

          {/* 数据与隐私 */}
          <Section id="data" icon={<IconLock size={17} />} title="数据与隐私"
            desc="管理本机前端数据，破坏性操作均有二次确认。">
            <Row title="新手引导" desc="重新查看首次启动的功能与隐私说明。">
              <Button variant="ghost" size="sm" icon={<IconStudio size={14} />} onClick={replayOnboarding}>重新查看</Button>
            </Row>
            <Row title="作品库视图" desc="清空本机收藏、隐藏、自定义排序与保存的视图（不影响后端任务）。">
              <Button variant="danger" size="sm" icon={<IconGallery size={14} />} onClick={doResetLibrary}>重置</Button>
            </Row>
            <Row title="演示数据" desc={mock ? "重新生成 mock 任务、会话与程序化预览。" : "仅演示模式可用；当前为本机服务。"}>
              <Tooltip label={mock ? "重新生成演示数据" : "切换到演示模式后可用"}>
                <Button variant="ghost" size="sm" icon={<IconRefresh size={14} />} onClick={doResetDemo} disabled={!mock}>重置</Button>
              </Tooltip>
            </Row>
            <Row title="偏好设置" desc="主题、侧边栏、声音、轮询与数据源恢复默认（数据源回到自动）。">
              <Button variant="danger" size="sm" icon={<IconSliders size={14} />} onClick={doResetPrefs}>恢复默认</Button>
            </Row>
            <div className="py-4">
              <div className="rounded-lg border border-line-soft bg-raised/40 p-4">
                <p className="flex items-center gap-2 text-xs font-semibold text-ink"><IconLock size={14} className="text-success" /> 隐私承诺</p>
                <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {[
                    "无遥测 / 统计 / Sentry，不上报任何使用数据",
                    "无外部 CDN、在线字体或远程图标，离线可用",
                    "图片 / 音频只在本机显示，不上传任何外部服务",
                    "不读取 secrets.json、API key 或数据库密码",
                    "偏好仅存于浏览器 localStorage，不含密钥",
                    "不接数据库、不读取用户文件系统",
                  ].map((t) => (
                    <li key={t} className="flex items-start gap-2 text-[11px] leading-relaxed text-muted">
                      <IconCheck size={13} className="mt-0.5 flex-none text-success" /> {t}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Section>

          {/* 关于 */}
          <Section id="about" icon={<IconInfo size={17} />} title="关于" desc="Möbius Studio — 本机 AI 创作与对话主界面。">
            <div className="flex flex-col gap-4 py-5 sm:flex-row sm:items-center">
              <span className="grid h-14 w-14 flex-none place-items-center rounded-2xl border border-brand/25 bg-brand/10 text-brand-soft">
                <IconLogo size={30} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-display text-base font-semibold text-ink">Möbius Studio <span className="tnum ml-1 text-xs font-normal text-faint">v{APP_VERSION}</span></p>
                <p className="mt-1 text-xs leading-relaxed text-muted">
                  本机优先的创作工作台：图像生成、语音合成、任务队列、作品库与流式对话。
                  运维操作仍由既有 Console 承担，本界面不改动 Core / 网关 / Launcher。
                </p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {["React 18", "Vite", "TypeScript", "Tailwind", "TanStack Query", "Zustand"].map((t) => (
                    <span key={t} className="rounded-full border border-line-soft bg-raised/60 px-2.5 py-0.5 text-[10px] text-muted">{t}</span>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 py-4">
              <Badge tone="brand" dot>本机优先</Badge>
              <Badge tone="cyan" dot>离线可用</Badge>
              <Badge tone="success" dot>无遥测</Badge>
              <Badge tone="muted"><IconClock size={11} /> 桌面优先 · 1280×800+</Badge>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}
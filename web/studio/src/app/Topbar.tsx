import { useLocation } from "react-router-dom";
import { NAV } from "./nav";
import { useUI } from "../store/ui";
import { ConnectionStatus } from "./ConnectionStatus";
import { Button, Tooltip } from "../components/ui";
import { IconSearch, IconSun, IconMoon, IconVolume, IconVolumeX, IconCommand } from "../components/icons";

function usePageMeta() {
  const { pathname } = useLocation();
  if (pathname.startsWith("/chat")) return NAV.find((n) => n.to === "/chat")!;
  const hit = NAV.find((n) => (n.end ? pathname === n.to : pathname.startsWith(n.to)));
  return hit ?? NAV[0];
}

export function Topbar() {
  const meta = usePageMeta();
  const theme = useUI((s) => s.theme);
  const toggleTheme = useUI((s) => s.toggleTheme);
  const muted = useUI((s) => s.muted);
  const toggleMuted = useUI((s) => s.toggleMuted);
  const setCommandOpen = useUI((s) => s.setCommandOpen);

  return (
    <header className="relative z-20 flex h-[var(--topbar-h)] flex-none items-center gap-3 border-b border-line-soft bg-surface/60 glass px-4 sm:px-5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate font-display text-[15px] font-semibold tracking-tight text-ink">{meta.label}</h1>
          <span className="hidden text-xs text-faint md:inline">/ {meta.desc}</span>
        </div>
      </div>

      <button
        onClick={() => setCommandOpen(true)}
        className="hidden h-8 items-center gap-2 rounded-md border border-line bg-base/50 px-2.5 text-xs text-faint transition-colors hover:border-brand/40 hover:text-muted sm:flex"
        aria-label="打开命令面板"
      >
        <IconSearch size={14} />
        <span className="hidden lg:inline">搜索 / 跳转</span>
        <span className="kbd ml-1 gap-0.5"><IconCommand size={10} />K</span>
      </button>

      <ConnectionStatus />

      <div className="flex items-center gap-1">
        <Tooltip label={muted ? "已静音 · 点击开启提示音" : "提示音开启 · 点击静音"}>
          <Button variant="ghost" size="icon" onClick={toggleMuted} aria-label={muted ? "开启提示音" : "静音"} aria-pressed={muted}>
            {muted ? <IconVolumeX size={17} /> : <IconVolume size={17} />}
          </Button>
        </Tooltip>
        <Tooltip label={theme === "dark" ? "切换到亮色" : "切换到暗色"}>
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="切换主题">
            {theme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />}
          </Button>
        </Tooltip>
        <Tooltip label="命令面板 (Ctrl/⌘ K)">
          <Button variant="ghost" size="icon" className="sm:hidden" onClick={() => setCommandOpen(true)} aria-label="命令面板">
            <IconSearch size={17} />
          </Button>
        </Tooltip>
      </div>
    </header>
  );
}
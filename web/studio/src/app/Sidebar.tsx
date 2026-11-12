import { NavLink } from "react-router-dom";
import { NAV } from "./nav";
import { useUI } from "../store/ui";
import { useTasks } from "../api/hooks";
import { isActive } from "../api/normalize";
import { cn } from "../lib/utils";
import { IconLogo, IconChevronLeft, IconPlus } from "../components/icons";
import { Tooltip } from "../components/ui";

export function Sidebar() {
  const collapsed = useUI((s) => s.sidebarCollapsed);
  const toggle = useUI((s) => s.toggleSidebar);
  const { data } = useTasks();
  const activeCount = (data?.tasks ?? []).filter((t) => isActive(t.status)).length;

  return (
    <aside
      className="relative z-20 flex h-full flex-none flex-col border-r border-line-soft bg-surface/70 glass"
      style={{ width: collapsed ? "var(--sidebar-w-collapsed)" : "var(--sidebar-w)", transition: "width 0.24s var(--ease-out)" }}
    >
      {/* 品牌 */}
      <div className={cn("flex h-[var(--topbar-h)] flex-none items-center gap-2.5 px-4", collapsed && "justify-center px-0")}>
        <span className="relative grid h-9 w-9 flex-none place-items-center rounded-xl bg-gradient-to-br from-brand/25 to-magenta/20 ring-1 ring-brand/30">
          <IconLogo size={22} className="text-brand-soft" />
        </span>
        {!collapsed && (
          <div className="min-w-0 leading-tight">
            <p className="truncate font-display text-[15px] font-semibold tracking-tight text-ink">Möbius</p>
            <p className="truncate text-[10px] uppercase tracking-[0.2em] text-faint">Studio</p>
          </div>
        )}
      </div>

      {/* 新建创作 */}
      <div className={cn("px-3 pb-2", collapsed && "px-2")}>
        <NavLink to="/create" className="block">
          {collapsed ? (
            <Tooltip label="新建创作" side="right">
              <span className="btn btn-primary btn-icon w-full"><IconPlus size={18} /></span>
            </Tooltip>
          ) : (
            <span className="btn btn-primary w-full"><IconPlus size={16} />新建创作</span>
          )}
        </NavLink>
      </div>

      {/* 导航 */}
      <nav className="scroll-y flex-1 px-3 py-2" aria-label="主导航">
        {!collapsed && <p className="section-title px-3 pb-2 pt-1">导航</p>}
        <ul className="space-y-1">
          {NAV.map((item) => {
            const Icon = item.icon;
            const badge = item.to === "/tasks" && activeCount > 0 ? activeCount : null;
            const link = (
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive: a }) => cn("nav-item", a && "nav-item-active", collapsed && "justify-center px-0")}
              >
                <span className="relative flex-none">
                  <Icon size={19} />
                  {collapsed && badge != null && (
                    <span className="absolute -right-1.5 -top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-magenta px-1 text-[9px] font-bold text-white">{badge}</span>
                  )}
                </span>
                {!collapsed && (
                  <>
                    <span className="flex-1 truncate">{item.label}</span>
                    {badge != null && (
                      <span className="tnum grid h-5 min-w-5 place-items-center rounded-full bg-brand/20 px-1.5 text-[10px] font-semibold text-brand-soft">{badge}</span>
                    )}
                  </>
                )}
              </NavLink>
            );
            return (
              <li key={item.to}>
                {collapsed ? <Tooltip label={item.label} side="right"><div className="w-full">{link}</div></Tooltip> : link}
              </li>
            );
          })}
        </ul>
      </nav>

      {/* 底部 */}
      <div className="flex-none border-t border-line-soft p-3">
        <button
          onClick={toggle}
          className={cn("nav-item w-full", collapsed && "justify-center px-0")}
          aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
        >
          <IconChevronLeft size={18} className={cn("flex-none transition-transform duration-200", collapsed && "rotate-180")} />
          {!collapsed && <span className="text-xs">收起侧栏</span>}
        </button>
        {!collapsed && (
          <p className="mt-2 px-3 text-[10px] leading-relaxed text-faint">
            本机运行 · 数据不出设备
          </p>
        )}
      </div>
    </aside>
  );
}
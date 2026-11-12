import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { useUI } from "../store/ui";
import { useTasks } from "../api/hooks";
import { useTaskChime } from "../lib/sound";

export function Layout() {
  const setCommandOpen = useUI((s) => s.setCommandOpen);
  const toggleSidebar = useUI((s) => s.toggleSidebar);
  const setSidebar = useUI((s) => s.setSidebar);
  const { data } = useTasks();
  useTaskChime(data?.tasks);

  // 全局快捷键
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); setCommandOpen(!useUI.getState().commandOpen); }
      else if (mod && e.key.toLowerCase() === "b") { e.preventDefault(); toggleSidebar(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setCommandOpen, toggleSidebar]);

  // 窄屏自动收起侧栏（桌面优先，窄屏不破坏）
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const apply = () => setSidebar(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [setSidebar]);

  return (
    <div className="relative flex h-full w-full overflow-hidden">
      <div className="app-bg" aria-hidden="true" />
      <div className="app-grid" aria-hidden="true" />
      <Sidebar />
      <div className="relative z-10 flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="scroll-y relative flex-1" id="main">
          <div className="mx-auto w-full max-w-[1600px] px-4 py-5 sm:px-6 sm:py-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
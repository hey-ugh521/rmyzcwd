import { create } from "zustand";
import type { ApiMode } from "../api/types";

export type Theme = "dark" | "light";

interface UIState {
  theme: Theme;
  sidebarCollapsed: boolean;
  muted: boolean;
  commandOpen: boolean;
  onboardingDone: boolean;
  apiModeOverride: ApiMode | null; // null = 用 env/auto
  pollRunningMs: number;
  pollIdleMs: number;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  toggleSidebar: () => void;
  setSidebar: (v: boolean) => void;
  toggleMuted: () => void;
  setCommandOpen: (v: boolean) => void;
  dismissOnboarding: () => void;
  resetOnboarding: () => void;
  resetPreferences: () => void;
  setApiModeOverride: (m: ApiMode | null) => void;
  setPoll: (running: number, idle: number) => void;
}

const LS_KEY = "mobius-studio-ui";

function load(): Partial<UIState> {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}
function persist(s: UIState) {
  try {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify({
        theme: s.theme, sidebarCollapsed: s.sidebarCollapsed, muted: s.muted,
        onboardingDone: s.onboardingDone, apiModeOverride: s.apiModeOverride,
        pollRunningMs: s.pollRunningMs, pollIdleMs: s.pollIdleMs,
      })
    );
  } catch {
    /* 隐私模式等场景忽略 */
  }
}

const saved = load();
const initialTheme: Theme = saved.theme === "light" ? "light" : "dark";
const DEFAULT_POLL_RUNNING = Number(import.meta.env.VITE_POLL_RUNNING_MS || 3500);
const DEFAULT_POLL_IDLE = Number(import.meta.env.VITE_POLL_IDLE_MS || 18000);

export const useUI = create<UIState>((set, get) => ({
  theme: initialTheme,
  sidebarCollapsed: saved.sidebarCollapsed ?? false,
  muted: saved.muted ?? false,
  commandOpen: false,
  onboardingDone: saved.onboardingDone ?? false,
  apiModeOverride: (saved.apiModeOverride as ApiMode) ?? null,
  pollRunningMs: saved.pollRunningMs ?? DEFAULT_POLL_RUNNING,
  pollIdleMs: saved.pollIdleMs ?? DEFAULT_POLL_IDLE,
  setTheme: (t) => { set({ theme: t }); applyTheme(t); persist(get()); },
  toggleTheme: () => { const t = get().theme === "dark" ? "light" : "dark"; get().setTheme(t); },
  toggleSidebar: () => { set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })); persist(get()); },
  setSidebar: (v) => { set({ sidebarCollapsed: v }); persist(get()); },
  toggleMuted: () => { set((s) => ({ muted: !s.muted })); persist(get()); },
  setCommandOpen: (v) => set({ commandOpen: v }),
  dismissOnboarding: () => { set({ onboardingDone: true }); persist(get()); },
  resetOnboarding: () => { set({ onboardingDone: false }); persist(get()); },
  resetPreferences: () => {
    set({
      theme: "dark", sidebarCollapsed: false, muted: false,
      apiModeOverride: null, pollRunningMs: DEFAULT_POLL_RUNNING, pollIdleMs: DEFAULT_POLL_IDLE,
    });
    applyTheme("dark");
    persist(get());
  },
  setApiModeOverride: (m) => { set({ apiModeOverride: m }); persist(get()); },
  setPoll: (running, idle) => { set({ pollRunningMs: running, pollIdleMs: idle }); persist(get()); },
}));

export function applyTheme(t: Theme) {
  document.documentElement.setAttribute("data-theme", t);
}
applyTheme(initialTheme);
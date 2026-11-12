import { useEffect, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { initApi, subscribeBackend } from "../api";
import { useConnection } from "../api/client";
import { IconLogo } from "../components/icons";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      retryDelay: 600,
      refetchOnWindowFocus: false,
      staleTime: 2000,
      gcTime: 5 * 60 * 1000,
    },
    mutations: { retry: 0 },
  },
});

function BootScreen() {
  return (
    <div className="fixed inset-0 z-[200] grid place-items-center bg-base">
      <div className="app-bg" />
      <div className="relative flex flex-col items-center gap-5 animate-fade-in">
        <div className="relative grid h-16 w-16 place-items-center">
          <span className="absolute inset-0 rounded-2xl bg-brand/20 blur-xl animate-pulse-ring" />
          <IconLogo size={44} className="relative text-brand-soft" />
        </div>
        <div className="text-center">
          <p className="font-display text-lg font-semibold tracking-tight text-ink">Möbius Studio</p>
          <p className="mt-1 text-xs text-muted">正在连接本机服务…</p>
        </div>
        <div className="h-1 w-40 overflow-hidden rounded-full bg-raised">
          <div className="h-full w-1/3 rounded-full bg-gradient-to-r from-brand to-magenta animate-[shimmer_1.2s_infinite]" />
        </div>
      </div>
    </div>
  );
}

export function Providers({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const mode = useConnection((s) => s.mode);

  useEffect(() => {
    let alive = true;
    initApi().finally(() => { if (alive) setReady(true); });
    return () => { alive = false; };
  }, []);

  // mock 后端实时 tick → 去抖刷新所有查询（live 模式无订阅，靠轮询）
  useEffect(() => {
    if (!ready) return;
    let t: number | undefined;
    const unsub = subscribeBackend(() => {
      if (t != null) return;
      t = window.setTimeout(() => {
        t = undefined;
        queryClient.invalidateQueries({ queryKey: ["mobius"] });
      }, 140);
    });
    return () => { unsub(); if (t != null) clearTimeout(t); };
  }, [ready, mode]);

  if (!ready) return <BootScreen />;

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

export { queryClient };
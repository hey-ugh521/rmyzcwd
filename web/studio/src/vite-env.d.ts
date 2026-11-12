/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_MODE?: "mock" | "live" | "auto";
  readonly VITE_CORE_BASE?: string;
  readonly VITE_GATEWAY_BASE?: string;
  readonly VITE_CORE_TOKEN?: string;
  readonly VITE_POLL_RUNNING_MS?: string;
  readonly VITE_POLL_IDLE_MS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
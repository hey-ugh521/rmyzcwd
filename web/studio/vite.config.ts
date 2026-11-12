import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发期用代理把 Core(8900) / Gateway(8899) 收敛到同源前缀，避免 CORS。
// 生产由 Core 静态托管，base 通过 VITE_CORE_BASE / VITE_GATEWAY_BASE 注入。
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    host: "127.0.0.1",
    proxy: {
      "/__core": {
        target: "http://127.0.0.1:8900",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/__core/, ""),
      },
      "/__gw": {
        target: "http://127.0.0.1:8899",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/__gw/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2020",
    chunkSizeWarningLimit: 900,
  },
});
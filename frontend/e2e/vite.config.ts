/**
 * 端到端测试专用的 Vite 配置：dev server 把 /api 代理到 e2e 后端。
 *
 * 单独一份，不改开发用的 vite.config.ts（它固定代理到 8000）。
 * 用 `pnpm exec vite --config e2e/vite.config.ts` 启动。
 */
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { BACKEND_PORT, FRONTEND_PORT } from "./config";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: FRONTEND_PORT,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${BACKEND_PORT}`,
        changeOrigin: true,
      },
    },
  },
});

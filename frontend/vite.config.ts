import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // 本机 jsdom + AntD 渲染慢（P4-18 实测：SettingsPage 每个用例约 5.7 秒），
    // 负载高时整用例可能超过 30 秒，统一放宽；异步查询超时在 vitest.setup.ts 里调。
    testTimeout: 60_000,
    setupFiles: ["./vitest.setup.ts"],
  },
});

/**
 * Playwright 配置（P1-19）。
 *
 * `make e2e` 先跑 scripts/e2e-prepare.sh 重置 adp_e2e，再执行本配置：
 * webServer 同时启动后端（uvicorn，连 adp_e2e、Redis 12 号库）和前端（Vite dev server，/api 代理到后端）。
 * 只跑 Chromium，宽度 1440（平台只在 PC 端使用）。
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

import {
  BACKEND_PORT,
  BASE_URL,
  DATABASE_URL,
  FRONTEND_PORT,
  JWT_SECRET,
  MIGRATION_DATABASE_URL,
  REDIS_URL,
} from "./config";

const e2eDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(e2eDir, "..");
const backendDir = path.resolve(frontendDir, "../backend");

export default defineConfig({
  testDir: "./tests",
  // 用 .e2e.ts 后缀，避免被 Vitest 的默认 include（*.spec.ts / *.test.ts）扫到
  testMatch: "**/*.e2e.ts",
  // 用例之间有先后关系（新建的组织和用户后面要用），单 worker 顺序跑
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  // 默认 test-results/，已被 .gitignore 忽略
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: [
    {
      command: `uv run uvicorn app.main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: backendDir,
      url: `http://127.0.0.1:${BACKEND_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        APP_ENV: "test",
        DATABASE_URL,
        MIGRATION_DATABASE_URL,
        REDIS_URL,
        JWT_SECRET,
        COOKIE_SECURE: "false",
        LOG_LEVEL: "WARNING",
      },
    },
    {
      command: `pnpm exec vite --config e2e/vite.config.ts --port ${FRONTEND_PORT}`,
      cwd: frontendDir,
      url: BASE_URL,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});

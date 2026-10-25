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
  CELERY_BROKER_URL,
  DATABASE_URL,
  FAKE_LLM_PORT,
  FAKE_PARSER_PORT,
  FRONTEND_PORT,
  JWT_SECRET,
  MIGRATION_DATABASE_URL,
  PARSER_TOKEN,
  PARSER_URL,
  REDIS_URL,
  S3_ACCESS_KEY,
  S3_BUCKET,
  S3_ENDPOINT,
  S3_SECRET_KEY,
} from "./config";

// 后端 / Celery worker / 假解析服务共用的第三期环境变量：同一套对象存储桶和解析服务地址。
const storageEnv = {
  S3_ENDPOINT,
  S3_BUCKET,
  S3_ACCESS_KEY,
  S3_SECRET_KEY,
};
const parserEnv = {
  PARSER_URL,
  PARSER_TOKEN,
};

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
        CELERY_BROKER_URL,
        ...storageEnv,
        ...parserEnv,
      },
    },
    {
      command: `pnpm exec vite --config e2e/vite.config.ts --port ${FRONTEND_PORT}`,
      cwd: frontendDir,
      url: BASE_URL,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      // 本地假模型服务（P2-12）：端到端用例的模型调用都打到它
      command: `uv run uvicorn tests.fake_llm.server:app --host 127.0.0.1 --port ${FAKE_LLM_PORT}`,
      cwd: backendDir,
      url: `http://127.0.0.1:${FAKE_LLM_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        FAKE_LLM_PORT: String(FAKE_LLM_PORT),
        // 逐字输出的间隔，给“停止生成”用例留出足够窗口
        FAKE_LLM_CHAR_DELAY: "0.1",
      },
    },
    {
      // 本地假解析服务（P3-09）：上传的文档由它返回固定的 PDF / Word 解析结果
      command: `uv run uvicorn --factory tests.fake_parser.server:create_app --host 127.0.0.1 --port ${FAKE_PARSER_PORT}`,
      cwd: backendDir,
      url: `http://127.0.0.1:${FAKE_PARSER_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        FAKE_PARSER_PORT: String(FAKE_PARSER_PORT),
        ...storageEnv,
        ...parserEnv,
      },
    },
    {
      // Celery worker（P3-08）：入库流水线。没有 HTTP 端口，靠启动日志里的 ready 判断就绪
      command: `uv run celery -A app.worker.celery_app worker -Q ingest,maintenance -c 1 --loglevel=INFO`,
      cwd: backendDir,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "ignore",
      wait: { stderr: /ready\./ },
      env: {
        APP_ENV: "test",
        DATABASE_URL,
        MIGRATION_DATABASE_URL,
        REDIS_URL,
        JWT_SECRET,
        COOKIE_SECURE: "false",
        CELERY_BROKER_URL,
        ...storageEnv,
        ...parserEnv,
      },
    },
  ],
});

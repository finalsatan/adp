/**
 * 端到端测试的共享配置。
 *
 * 测试用开发容器里独立的 adp_e2e 库和 Redis 12 号库，不碰开发库 adp、测试库 adp_test。
 * 后端、前端端口避开本机常被占用的 8000、5173。
 * 这些值要和 scripts/e2e-prepare.sh 里的默认值保持一致。
 */

export const BACKEND_PORT = 18000;
export const FRONTEND_PORT = 14173;
export const BASE_URL = `http://127.0.0.1:${FRONTEND_PORT}`;

export const DATABASE_URL =
  "postgresql+asyncpg://adp_app:adp_app_pw@localhost:55432/adp_e2e";
export const MIGRATION_DATABASE_URL =
  "postgresql+asyncpg://adp_migrator:adp_migrator_pw@localhost:55432/adp_e2e";
export const REDIS_URL = "redis://localhost:56379/12";
/** 只用于 e2e，随便一个够长的值即可。 */
export const JWT_SECRET = "e2e-only-secret-change-me-0123456789";

// ---- 本地假模型服务（P2-12）----
// 端口要和 Makefile 的 FAKE_LLM_PORT 默认值、playwright.config.ts 里的 webServer 一致。
export const FAKE_LLM_PORT = 9999;
export const FAKE_LLM_BASE_URL = `http://127.0.0.1:${FAKE_LLM_PORT}/v1`;
/** 假模型的固定回复（backend/tests/fake_llm/server.py 的默认值）。 */
export const FAKE_LLM_REPLY =
  "这是本地假模型的回答：一二三四五六七八九十，用于端到端测试的流式输出。";

// ---- 测试账号 ----
// e2e-prepare.sh 用 ADMIN_INITIAL_PASSWORD 创建 admin，首次登录后改成 ADMIN_PASSWORD。
export const ADMIN_USERNAME = "admin";
export const ADMIN_INITIAL_PASSWORD = "E2eAdmin!2345";
export const ADMIN_PASSWORD = "E2eAdminNew!2345";

/** 新建用户的初始密码（首次登录后都要改掉）。 */
export const INITIAL_PASSWORD = "E2eInitial!2345";

export const ORG_ADMIN_USERNAME = "devadmin";
export const ORG_ADMIN_NAME = "研发管理员";
export const ORG_ADMIN_PASSWORD = "E2eOrgAdmin!2345";

export const BUILDER_USERNAME = "devbuilder";
export const BUILDER_NAME = "研发搭建者";
/** 首次登录改密后的密码。 */
export const BUILDER_PASSWORD = "E2eBuilderNew!2345";
/** 在个人中心再次改密后的密码（验证改密后页面仍可操作）。 */
export const BUILDER_NEW_PASSWORD = "E2eBuilder!5678";

export const END_USER_USERNAME = "e2euser";
export const END_USER_NAME = "终端用户";
export const END_USER_PASSWORD = "E2eUser!2345";

// ---- 测试数据（组织）----
export const TOP_ORG = "集团";
export const DEV_ORG = "研发中心";
export const PRODUCT_ORG = "产品中心";

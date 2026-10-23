#!/usr/bin/env bash
# 端到端测试环境准备（P1-19）：
#   1. 重建开发容器里的 adp_e2e 库（只动这一个库，不碰 adp、adp_test）
#   2. 用迁移账号对 adp_e2e 执行 alembic upgrade head
#   3. 用 create-admin --password-stdin 创建初始系统管理员
#
# 端口 / 库名 / Redis 库号要和 frontend/e2e/config.ts 保持一致：
# 本机 8000、5173 被其他开发任务占用，所以 e2e 后端用 18000、前端用 14173，Redis 用 12 号库。
#
# 需要开发容器在运行（make dev-up）。当前用户不在 docker 组时，用：
#   sg docker -c 'make e2e'
set -euo pipefail

cd "$(dirname "$0")/.."

E2E_DB="${E2E_DB:-adp_e2e}"
E2E_DATABASE_URL="${E2E_DATABASE_URL:-postgresql+asyncpg://adp_app:adp_app_pw@localhost:55432/${E2E_DB}}"
E2E_MIGRATION_DATABASE_URL="${E2E_MIGRATION_DATABASE_URL:-postgresql+asyncpg://adp_migrator:adp_migrator_pw@localhost:55432/${E2E_DB}}"
E2E_REDIS_URL="${E2E_REDIS_URL:-redis://localhost:56379/12}"
E2E_JWT_SECRET="${E2E_JWT_SECRET:-e2e-only-secret-change-me-0123456789}"
E2E_ADMIN_USERNAME="${E2E_ADMIN_USERNAME:-admin}"
# 与 frontend/e2e/config.ts 的 ADMIN_INITIAL_PASSWORD 一致，只用于测试
E2E_ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-E2eAdmin!2345}"

if ! docker info >/dev/null 2>&1; then
    echo "无法访问 Docker；当前用户不在 docker 组时用：sg docker -c 'make e2e'" >&2
    exit 1
fi

echo "[e2e-prepare] 重建数据库 ${E2E_DB} ..."
docker compose -f deploy/docker-compose.dev.yml exec -T postgres \
    /docker-entrypoint-initdb.d/10-adp.sh reset "${E2E_DB}"

echo "[e2e-prepare] 对 ${E2E_DB} 执行迁移 ..."
(
    cd backend
    DATABASE_URL="${E2E_DATABASE_URL}" \
        MIGRATION_DATABASE_URL="${E2E_MIGRATION_DATABASE_URL}" \
        REDIS_URL="${E2E_REDIS_URL}" \
        JWT_SECRET="${E2E_JWT_SECRET}" \
        APP_ENV=test \
        COOKIE_SECURE=false \
        uv run alembic upgrade head
)

echo "[e2e-prepare] 创建系统管理员 ${E2E_ADMIN_USERNAME} ..."
(
    cd backend
    DATABASE_URL="${E2E_DATABASE_URL}" \
        MIGRATION_DATABASE_URL="${E2E_MIGRATION_DATABASE_URL}" \
        REDIS_URL="${E2E_REDIS_URL}" \
        JWT_SECRET="${E2E_JWT_SECRET}" \
        APP_ENV=test \
        COOKIE_SECURE=false \
        uv run python -m app.cli create-admin \
        --username "${E2E_ADMIN_USERNAME}" --password-stdin \
        <<<"${E2E_ADMIN_PASSWORD}"
)

echo "[e2e-prepare] 完成：${E2E_DB} 已重置，管理员 ${E2E_ADMIN_USERNAME} 已创建。"

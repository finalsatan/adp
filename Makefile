# 常用命令统一入口。后续任务卡继续加目标。

COMPOSE_DEV := docker compose -f deploy/docker-compose.dev.yml
COMPOSE_PROD := docker compose -f deploy/docker-compose.yml --env-file deploy/.env

.PHONY: dev-up dev-down test-db-reset backend-install backend-lint backend-format \
	backend-test backend-run migrate backend-openapi \
	frontend-install frontend-dev frontend-check frontend-lint frontend-typecheck \
	frontend-test frontend-build gen-api e2e-prepare e2e \
	prod-up prod-down prod-down-v prod-smoke prod-create-admin

# ---- 本地开发环境 ----

dev-up:
	$(COMPOSE_DEV) up -d --wait

dev-down:
	$(COMPOSE_DEV) down

test-db-reset:
	$(COMPOSE_DEV) exec -T postgres /docker-entrypoint-initdb.d/10-adp.sh reset adp_test

# ---- 后端 ----

backend-install:
	cd backend && uv sync

backend-lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

backend-format:
	cd backend && uv run ruff format . && uv run ruff check --fix .

backend-test:
	cd backend && uv run pytest

# 本地开发配置：没有 .env 时从示例复制一份
backend/.env:
	cp backend/.env.example backend/.env

backend-run: backend/.env
	cd backend && uv run uvicorn app.main:app --reload --port 8000

migrate: backend/.env
	cd backend && uv run alembic upgrade head

backend-openapi: backend/.env
	mkdir -p frontend
	cd backend && uv run python -m app.cli export-openapi > ../frontend/openapi.json

# ---- 前端 ----

frontend-install:
	cd frontend && pnpm install

frontend-dev:
	cd frontend && pnpm dev

frontend-lint:
	cd frontend && pnpm lint

frontend-typecheck:
	cd frontend && pnpm typecheck

frontend-test:
	cd frontend && pnpm test

frontend-build:
	cd frontend && pnpm build

frontend-check: frontend-lint frontend-typecheck frontend-test frontend-build

gen-api: backend-openapi
	cd frontend && pnpm gen:api

# ---- 端到端测试 ----
# 用开发容器里的独立库 adp_e2e 和 Redis 12 号库，后端 / 前端端口用 18000 / 14173，
# 避开本机已被占用的 8000、5173。先重置环境再跑 Playwright。

e2e-prepare:
	scripts/e2e-prepare.sh

e2e: e2e-prepare
	cd frontend && pnpm exec playwright test --config e2e/playwright.config.ts

# ---- 本地假模型服务（P2-12，仅测试和联调）----

FAKE_LLM_PORT ?= 9999

.PHONY: fake-llm
fake-llm:
	cd backend && FAKE_LLM_PORT=$(FAKE_LLM_PORT) uv run python -m tests.fake_llm.server

# ---- 解析服务 ----

PARSER_DIR := parser

.PHONY: parser-install parser-lint parser-test parser-run parser-dev-up parser-dev-down

parser-install:
	cd $(PARSER_DIR) && uv sync

parser-lint:
	cd $(PARSER_DIR) && uv run ruff check . && uv run ruff format --check .

parser-test:
	cd $(PARSER_DIR) && uv run pytest

parser-run:
	cd $(PARSER_DIR) && uv run uvicorn app.main:app --host 0.0.0.0 --port 59001

parser-dev-up:
	docker compose -f $(PARSER_DIR)/compose.dev.yml up -d

parser-dev-down:
	docker compose -f $(PARSER_DIR)/compose.dev.yml down -v

# ---- ParadeDB 中文检索验证（X09，脚本，不属于 backend）----

PARADEDB_CHECK_ARGS ?=

.PHONY: paradedb-check
paradedb-check:
	cd scripts/paradedb_check && uv run check.py $(PARADEDB_CHECK_ARGS)

# ---- 生产部署 ----

prod-up:
	$(COMPOSE_PROD) up -d --build

prod-down:
	$(COMPOSE_PROD) down

# 连同生产自己的数据卷一起删除（会清空生产数据，仅调试用）
prod-down-v:
	$(COMPOSE_PROD) down -v

prod-smoke:
	deploy/smoke.sh

# 交互输入管理员密码；用户名默认 admin，可用 make prod-create-admin ADMIN_USERNAME=xxx 覆盖
prod-create-admin:
	$(COMPOSE_PROD) exec api python -m app.cli create-admin --username $${ADMIN_USERNAME:-admin}

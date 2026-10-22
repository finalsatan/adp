# 常用命令统一入口。后续任务卡继续加目标。

COMPOSE_DEV := docker compose -f deploy/docker-compose.dev.yml

.PHONY: dev-up dev-down test-db-reset backend-install backend-lint backend-format \
	backend-test backend-run migrate backend-openapi

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

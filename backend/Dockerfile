# 主服务镜像。同一镜像用于 api、一次性的 migrate 和运维命令（技术方案 12）。
FROM python:3.12.15-slim

# 用 uv 安装锁定版本的依赖
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 非 root 用户；app 代码用 COPY --chown 归它，不用 chown -R（否则会多一层上百 MB 的副本）
RUN useradd --create-home --uid 10001 adp

# 只装运行依赖（uv.lock 锁定版本和哈希，--no-dev 不装测试依赖）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY --chown=adp:adp app ./app
COPY --chown=adp:adp migrations ./migrations
COPY --chown=adp:adp alembic.ini ./

USER adp

EXPOSE 8000

# --no-proxy-headers：不让 uvicorn 解析 X-Forwarded-For，来源 IP 只由 nginx 写入的
# X-Real-IP 决定（P1-06，登录锁定按 IP 计）。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]

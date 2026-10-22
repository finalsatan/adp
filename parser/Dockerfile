# 解析服务镜像。LibreOffice、中文字体、Docling 模型的安装位置由 X02、X06 补上。
FROM python:3.12.3-slim

# 用 uv 安装锁定版本的依赖
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"

# 非 root 运行
RUN useradd --create-home --uid 10001 parser && chown -R parser:parser /app
USER parser

EXPOSE 59001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "59001"]

# 解析服务公共运行环境（原生支持 amd64 / arm64）。
FROM python:3.12.3-slim AS base

# 用 uv 安装锁定版本的依赖
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# X02：Office 转换、系统 Python 的 UNO 绑定和中文字体。
# UNO 是 Debian 系统 Python 的扩展，不可导入到应用的 Python 3.12 中。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-impress libreoffice-calc \
    python3-uno python3-venv fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# server 使用系统 Python + UNO；unoconvert 客户端使用应用环境。
# 从应用锁定依赖取版本，避免两个 Python 环境中的 RPC 协议版本不一致。
RUN uv venv --python /usr/bin/python3 --system-site-packages /opt/unoserver \
    && uv pip install --python /opt/unoserver/bin/python \
    "unoserver==$(.venv/bin/python -c 'from importlib.metadata import version; print(version("unoserver"))')" \
    && /opt/unoserver/bin/python -c 'import uno; import unoserver.server'

# X06：Docling 模型及离线资源可在此层追加。

COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"

# 非 root 运行
RUN useradd --create-home --uid 10001 parser && chown -R parser:parser /app

# 测试镜像：与生产共用 LibreOffice、字体、依赖，额外安装开发依赖。
FROM base AS test
RUN uv sync --frozen
COPY --chown=parser:parser tests ./tests
RUN chown -R parser:parser /app
USER parser
CMD ["pytest", "-m", "not integration"]

# 默认生产镜像不包含测试与开发依赖。
FROM base AS runtime
USER parser

EXPOSE 59001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "59001"]

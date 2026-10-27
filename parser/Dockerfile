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
# DEBIAN_MIRROR：构建参数，指向 Debian 镜像站（如 https://mirrors.tuna.tsinghua.edu.cn/debian）；留空走官方源。
ARG DEBIAN_MIRROR=
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i "s|http://deb.debian.org/debian|$DEBIAN_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-impress libreoffice-calc \
    python3-uno python3-venv fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
# PYPI_MIRROR：构建参数，指向 PyPI 镜像站（如 https://pypi.tuna.tsinghua.edu.cn/simple）时用它下载依赖。
# uv 在 --frozen 下只用 uv.lock 里记录的地址，所以这里把 lock 里的地址换掉，只在镜像内生效。
ARG PYPI_MIRROR=
RUN if [ -n "$PYPI_MIRROR" ]; then \
        sed -i "s|https://pypi.org/simple|$PYPI_MIRROR|g; s|https://files.pythonhosted.org|${PYPI_MIRROR%/simple}|g" uv.lock; \
    fi \
    && uv sync --frozen --no-dev

# server 使用系统 Python + UNO；unoconvert 客户端使用应用环境。
# 从应用锁定依赖取版本，避免两个 Python 环境中的 RPC 协议版本不一致。
RUN uv venv --python /usr/bin/python3 --system-site-packages /opt/unoserver \
    && uv pip install --python /opt/unoserver/bin/python \
    "unoserver==$(.venv/bin/python -c 'from importlib.metadata import version; print(version("unoserver"))')" \
    && /opt/unoserver/bin/python -c 'import uno; import unoserver.server'

# X06：Docling 的版面、表格和 RapidOCR 模型随镜像打包（知识库技术方案 6.3：构建时下载，运行时不联网）。
# HF_ENDPOINT：构建参数，指向 Hugging Face 镜像站（如 https://hf-mirror.com）时用镜像下载；留空走官方站点。
# HF_HUB_DISABLE_XET：hf-mirror 不代理 Xet 传输，关掉后走普通 HTTP 下载（对官方站点同样有效）。
ARG HF_ENDPOINT=
ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models
RUN mkdir -p "$DOCLING_ARTIFACTS_PATH" \
    && HF_ENDPOINT="${HF_ENDPOINT:-https://huggingface.co}" HF_HUB_DISABLE_XET=1 \
        .venv/bin/docling-tools models download layout tableformer rapidocr \
        --output-dir "$DOCLING_ARTIFACTS_PATH" --rapidocr-backend-lang onnxruntime:iso:zh

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

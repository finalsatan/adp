# MinerU 解析服务镜像（CPU，快速 + 基础档）。知识库技术方案 6.3、18。
#
# 只装 MinerU 的基础包（ONNX + llama.cpp，不装 PyTorch），提供 flash、basic 两个档位；
# 基础档模型在构建时下载进镜像，运行时 MINERU_MODEL_SOURCE=local，不联网。
# 标准、高级档需要 GPU，用官方镜像或云端 API，不在这里构建。
FROM python:3.12.3-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MINERU_HOME=/opt/mineru \
    MINERU_MODEL_SOURCE=local \
    MINERU_MODEL_SMALL_BACKEND=onnx \
    MINERU_CONCURRENCY=1

# libgl1、libglib2.0-0：DocVortex 的版面/表格后处理用到 opencv
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

# 锁定版本（知识库技术方案 6.3：mineru>=4.0,<5，构建时锁具体版本）
RUN pip install "mineru==4.0.12"

# 非 root 运行。模型下载前先建好目录并改属主，避免下载后再 chown 复制一份 800MB 的层。
RUN useradd --create-home --uid 10001 mineru \
    && mkdir -p /opt/mineru /app \
    && chown mineru:mineru /opt/mineru /app
USER mineru
WORKDIR /app

# 基础档小模型（ONNX）下载进镜像；verify 在模型不完整时构建失败
RUN mineru-kit models download --tier basic --small-backend onnx --source huggingface \
    && mineru-kit models verify --tier basic --small-backend onnx

EXPOSE 8000

# 健康检查（知识库技术方案 18：mineru 用 /v1/health）
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD curl -fsS http://127.0.0.1:8000/v1/health || exit 1

# 启动命令（知识库技术方案 6.3）。MINERU_API_KEY 为空时不启用认证（只在内网使用）。
CMD ["sh", "-c", "exec mineru-kit api-server --host 0.0.0.0 --port 8000 --tier basic --concurrency \"$MINERU_CONCURRENCY\" --api-key \"$MINERU_API_KEY\""]

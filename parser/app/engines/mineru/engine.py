"""MinerU 解析引擎：调用 MinerU 的 V1 接口，把结果转成 DoclingDocument（X08）。

默认部署的 CPU 服务（``MINERU_URL``、``MINERU_API_KEY``）提供快速、基础两档；
标准、高级档用作业请求里的 ``remote``（主服务从系统设置取出的自建 GPU 服务或
官方云端 API 地址与密钥）。Word、PPT、HTML、EPUB、OFD 由 MinerU 直接读取文件
结构，固定用快速档，不需要选档位（需求 4.1.4、知识库技术方案 6.2）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ...errors import ParseError
from ..base import ParseContext, ParseOutput
from .client import MineruClient, raise_if_cancelled
from .convert import to_document

__all__ = ["MineruEngine"]

# 引擎参数 tier 的取值（知识库技术方案 6.2）
TIERS = ("flash", "basic", "standard", "advanced")

# 需要 GPU 或云端 API 的档位，必须有 remote
REMOTE_TIERS = ("standard", "advanced")

# 固定用快速档原生解析的文件类型（需求 4.1.4）
NATIVE_TYPES = frozenset({"word", "ppt", "html", "epub", "ofd"})

# 支持的文件类型；Excel、CSV、Markdown、TXT 不在其中（需求 4.1.4 的引擎表）
SUPPORTED_TYPES = frozenset({"pdf", "image"}) | NATIVE_TYPES

# 默认档位：PDF 和图片的默认是基础档（需求 4.1.4）
DEFAULT_TIER = "basic"

# 健康检查结果缓存时间，避免 /v1/engines 每次都打网络请求
HEALTH_TTL_S = 5.0
HEALTH_TIMEOUT_S = 3.0


class MineruEngine:
    name = "mineru"
    remote = True

    def __init__(self, *, poll_interval: float = 1.0) -> None:
        self._poll_interval = poll_interval
        self._health: tuple[float, tuple[bool, str | None]] | None = None

    # ---- 引擎接口 ----
    def supports(self, file_type: str, ext: str) -> bool:
        return file_type in SUPPORTED_TYPES

    def availability(self) -> tuple[bool, str | None]:
        """默认部署的 MinerU 服务是否可用：``/v1/health`` 通不过就是“连接失败”。"""
        base_url, api_key = local_target()
        if not base_url:
            return False, "未部署"
        now = time.monotonic()
        if self._health is not None and now - self._health[0] < HEALTH_TTL_S:
            return self._health[1]
        result = MineruClient(base_url, api_key, timeout=HEALTH_TIMEOUT_S).health()
        self._health = (now, result)
        return result

    def parse(self, ctx: ParseContext) -> ParseOutput:
        tier = self._tier(ctx)
        base_url, api_key = self._target(ctx, tier)
        client = MineruClient(base_url, api_key, poll_interval=self._poll_interval)
        artifacts = client.parse(
            ctx.input_path, tier=tier, on_progress=_reporter(ctx), cancel_event=ctx.cancel_event
        )
        # 转换前再查一次：作业已取消就不做后面这些 CPU 活（X18）
        raise_if_cancelled(ctx.cancel_event)
        converted = to_document(
            artifacts.middle_json,
            images=artifacts.images,
            name=Path(ctx.input_path).stem or "mineru",
        )
        if converted.pages:
            ctx.report(converted.pages, converted.pages)
        return ParseOutput(
            document=converted.document,
            pages=converted.pages,
            ocr_pages=converted.ocr_pages,
            engine_version=f"mineru-{artifacts.parser_version or '4'}",
            empty_pages=converted.empty_pages,
        )

    # ---- 档位和地址 ----
    def _tier(self, ctx: ParseContext) -> str:
        if ctx.file_type in NATIVE_TYPES:
            # Word、PPT、HTML、EPUB、OFD 固定用快速档，忽略请求里的 tier
            return "flash"
        requested = ctx.engine_params.get("tier") if ctx.engine_params else None
        tier = str(requested).strip().lower() if requested else DEFAULT_TIER
        if tier not in TIERS:
            raise ParseError(
                "INVALID_PARAMS",
                f"不支持的 MinerU 解析档位：{requested}",
            )
        return tier

    def _target(self, ctx: ParseContext, tier: str) -> tuple[str, str | None]:
        if tier in REMOTE_TIERS:
            remote = ctx.remote
            if remote is None or not remote.base_url:
                raise ParseError(
                    "ENGINE_UNAVAILABLE",
                    "MinerU 标准、高级档未配置自建 GPU 服务或官方云端 API",
                )
            return remote.base_url, remote.api_key
        base_url, api_key = local_target()
        if not base_url:
            raise ParseError("ENGINE_UNAVAILABLE", "MinerU 服务未部署")
        return base_url, api_key


def local_target() -> tuple[str, str | None]:
    """默认部署的 MinerU 服务地址和密钥，来自 ``MINERU_URL``、``MINERU_API_KEY``。"""
    base_url = (os.environ.get("MINERU_URL") or "").strip()
    api_key = (os.environ.get("MINERU_API_KEY") or "").strip() or None
    return base_url, api_key


def _reporter(ctx: ParseContext) -> Any:
    """把 MinerU 的作业进度（已完成的文件数）报给 X01 的进度回调。"""

    def report(status: str, completed: int, total: int) -> None:
        total = max(total, 1)
        ctx.report(min(max(completed, 0), total), total)

    return report

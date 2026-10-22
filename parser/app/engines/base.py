"""解析引擎接口。

每个引擎把本地文件解析成 DoclingDocument（ADR 0005），并在子进程中运行。
引擎只处理本地文件，不访问对象存储；下载、上传由主进程的 runner 负责。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from docling_core.types.doc.document import DoclingDocument

from ..errors import ParseError

__all__ = [
    "Engine",
    "ParseContext",
    "ParseError",
    "ParseOutput",
    "RemoteConfig",
]


@dataclass
class RemoteConfig:
    """远端引擎配置（MinerU 标准/高级档、PaddleOCR 用）。"""

    base_url: str
    api_key: str | None = None


@dataclass
class ParseContext:
    """一次解析调用的上下文，在子进程内构造。"""

    # 本地输入文件路径
    input_path: str
    file_type: str
    ext: str
    # 引擎参数（dict），含义由各引擎定义
    engine_params: dict[str, Any]
    # 预处理开关
    preprocess: dict[str, Any]
    # 表格设置
    table: dict[str, Any]
    # 临时工作目录
    work_dir: str
    # 进度回调：已处理页数、总页数
    report: Callable[[int, int], None]
    # 远端引擎配置，可空
    remote: RemoteConfig | None = None


@dataclass
class ParseOutput:
    """一次解析的结果，统一为 DoclingDocument。"""

    document: DoclingDocument
    pages: int = 0
    ocr_pages: int = 0
    engine_version: str = ""
    # 没有文字层、未做 OCR 的页码（pypdfium 用）
    empty_pages: list[int] = field(default_factory=list)


class Engine(Protocol):
    """解析引擎接口。"""

    name: str

    def supports(self, file_type: str, ext: str) -> bool: ...

    def availability(self) -> tuple[bool, str | None]:
        """返回（是否可用，不可用原因：未部署 / 未配置 / 连接失败）。"""
        ...

    def parse(self, ctx: ParseContext) -> ParseOutput: ...

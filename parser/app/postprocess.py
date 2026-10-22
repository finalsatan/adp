"""所有引擎共用的后处理：预处理、图片抽取、导出（X03）。

各引擎只负责产出 DoclingDocument（ADR 0005），这里统一做：

- 预处理（需求 4.1.4，每项一个开关）：合并多余空白、删除页眉页脚、删除链接和邮箱；
- 图片：PictureItem 中的图像存成 PNG 并上传，文档里改为引用图片文件；
- 导出：``doc.json``（不内嵌 base64 图片）和 ``doc.md``（图片用引用、表格用
  Markdown 表格）。

预处理不改元素的位置信息（``prov``）。
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docling_core.types.doc.base import ImageRefMode, Size
from docling_core.types.doc.common.content_layer import ContentLayer
from docling_core.types.doc.common.reference import ImageRef
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.node import NodeItem
from docling_core.types.doc.labels import DocItemLabel

from . import doctools
from .engines.base import ParseOutput
from .storage import Storage

__all__ = [
    "PostprocessResult",
    "export",
    "merge_whitespace",
    "postprocess",
    "preprocess",
    "remove_header_footer",
    "remove_links_emails",
    "save_images",
]

# 连续空白；中文之间的空白直接删掉，不在中文字符之间插入空格
_WHITESPACE = re.compile(r"\s+")
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_WHITESPACE_BETWEEN_CJK = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")

_URL = re.compile(r"(?:https?://|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# 元素所在的各个集合，删除页眉页脚时要一并清理
_ITEM_COLLECTIONS = (
    "groups",
    "texts",
    "pictures",
    "tables",
    "key_value_items",
    "form_items",
    "field_regions",
    "field_items",
)


@dataclass
class PostprocessResult:
    """后处理结果：文档已改好并导出，``images`` 为上传的图片数。"""

    output: ParseOutput
    images: int


def postprocess(
    output: ParseOutput,
    *,
    options: Mapping[str, Any],
    work_dir: str | Path,
    images_prefix: str,
    storage: Storage,
) -> PostprocessResult:
    """执行预处理、图片抽取和导出，返回处理后的文档。"""
    document = output.document
    preprocess(document, options)
    _attach_picture_captions(document)
    work_path = Path(work_dir)
    images = save_images(document, work_path / "images", storage, images_prefix)
    export(document, work_path / "out")
    return PostprocessResult(output=output, images=images)


def preprocess(document: DoclingDocument, options: Mapping[str, Any]) -> None:
    """按开关执行预处理规则，原地修改文档。"""
    # 先删链接和邮箱，再合并空白，去掉删除后留下的多余空格
    if options.get("remove_links_emails"):
        remove_links_emails(document)
    if options.get("merge_whitespace"):
        merge_whitespace(document)
    if options.get("remove_header_footer"):
        remove_header_footer(document)


def merge_whitespace(document: DoclingDocument) -> None:
    """合并文字元素的连续空白，去掉首尾空白，不在中文之间插空格。"""
    for item in document.texts:
        item.text = _merge_text(item.text)


def _merge_text(text: str) -> str:
    text = _WHITESPACE_BETWEEN_CJK.sub("", text)
    return _WHITESPACE.sub(" ", text).strip()


def remove_header_footer(document: DoclingDocument) -> None:
    """删除标为页眉、页脚（标签或 furniture 内容层）的元素。"""
    targets: dict[str, NodeItem] = {}
    for name in _ITEM_COLLECTIONS:
        for item in getattr(document, name):
            if item.content_layer == ContentLayer.FURNITURE:
                targets.setdefault(item.self_ref, item)
    for item in document.texts:
        if item.label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
            targets.setdefault(item.self_ref, item)
    _delete_items(document, list(targets.values()))


def remove_links_emails(document: DoclingDocument) -> None:
    """删除文字元素中的 URL 和邮箱地址。"""
    for item in document.texts:
        item.text = _URL.sub("", _EMAIL.sub("", item.text))


def _delete_items(document: DoclingDocument, items: list[NodeItem]) -> None:
    """删除元素并更新引用。

    ``DoclingDocument.delete_items`` 只从 body 树里找元素，删不掉挂在
    furniture 组下的页眉页脚。这里先把它们临时挂到 body 下，再从 furniture
    组里摘掉，让 ``delete_items`` 统一完成删除和引用重编号。
    """
    if not items:
        return
    refs = [item.get_ref() for item in items]
    for ref in refs:
        if ref not in document.body.children:
            document.body.children.append(ref)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=DeprecationWarning)
        document.furniture.children = [
            ref for ref in document.furniture.children if ref not in refs
        ]
    document.delete_items(node_items=items)


def _attach_picture_captions(document: DoclingDocument) -> None:
    """把图片下的文字子元素（引擎的图中文字）作为图片说明。

    引擎也可以直接用 ``doctools.add_picture(caption=...)`` 给说明；这里只补
    没有说明的图片，分块时说明会并入所在分块（知识库技术方案 6.2）。
    """
    from docling_core.types.doc.items.text import TextItem

    for picture in document.pictures:
        if picture.captions:
            continue
        captions = []
        for child_ref in picture.children:
            if isinstance(child_ref.resolve(document), TextItem):
                captions.append(child_ref)
        if captions:
            picture.captions = captions


def save_images(
    document: DoclingDocument,
    images_dir: str | Path,
    storage: Storage,
    images_prefix: str,
) -> int:
    """把图片存成 PNG 并上传，文档里改为引用图片文件；返回图片数。"""
    directory = Path(images_dir)
    directory.mkdir(parents=True, exist_ok=True)
    prefix = images_prefix.rstrip("/")
    count = 0
    for picture in document.pictures:
        image = picture.get_image(doc=document)
        if image is None:
            continue
        count += 1
        name = f"{count}.png"
        path = directory / name
        image.save(path, format="PNG")
        storage.upload_file(path, f"{prefix}/{name}")
        dpi = picture.image.dpi if picture.image is not None else doctools.IMAGE_DPI
        picture.image = ImageRef(
            mimetype="image/png",
            dpi=dpi,
            size=Size(width=image.width, height=image.height),
            uri=doctools.image_path(name),
        )
    return count


def export(document: DoclingDocument, out_dir: str | Path) -> tuple[Path, Path]:
    """导出 ``doc.json`` 和 ``doc.md``；图片用引用，表格用 Markdown 表格。"""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "doc.json"
    md_path = directory / "doc.md"
    document.save_as_json(json_path, image_mode=ImageRefMode.PLACEHOLDER)
    md_path.write_text(
        document.export_to_markdown(image_mode=ImageRefMode.REFERENCED),
        encoding="utf-8",
    )
    return json_path, md_path

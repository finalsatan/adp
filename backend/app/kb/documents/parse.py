"""解析结果与引用位置数据（P3-14，知识库技术方案 7、11.1、11.2、13）。

从 `document_parses` 取 Markdown 与 DoclingDocument 的路径，再从对象存储读出
内容；元素列表按 DoclingDocument 的阅读顺序（`body.children`）展开，供前端
在解析结果视图里按 `chunks.positions` 的元素引用高亮段落。页码 / 坐标由检索
结果或分块里的 `positions` 提供，本模块不重复计算。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.schemas import UserBrief
from app.core.storage import Storage
from app.iam.context import Ctx
from app.iam.tables import User
from app.kb.documents.files import get_document
from app.kb.schemas import ParseElement, ParseEngine, ParseResultOut
from app.kb.tables import DocumentParse


def _resolve(document: dict[str, Any], ref: str) -> Any:
    """按 JSON 指针（如 `#/texts/12`）取节点。"""
    if not ref.startswith("#/"):
        return None
    node: Any = document
    for part in ref[2:].split("/"):
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
        if node is None:
            return None
    return node


def _table_text(table: dict[str, Any]) -> str:
    """把表格单元格按行拼成文本。"""
    cells = (table.get("data") or {}).get("table_cells") or []
    rows: dict[int, list[tuple[int, str]]] = {}
    for cell in cells:
        row = int(cell.get("start_row_offset_idx", 0))
        col = int(cell.get("start_col_offset_idx", 0))
        rows.setdefault(row, []).append((col, str(cell.get("text", ""))))
    return "\n".join(" | ".join(text for _, text in sorted(rows[row])) for row in sorted(rows))


def _picture_text(document: dict[str, Any], picture: dict[str, Any]) -> str:
    """图片用图注文字作为文本。"""
    texts: list[str] = []
    for caption in picture.get("captions") or []:
        node = _resolve(document, caption.get("$ref", ""))
        if isinstance(node, dict):
            value = node.get("text") or node.get("orig")
            if value:
                texts.append(str(value))
    return "\n".join(texts)


def _element(document: dict[str, Any], ref: str) -> ParseElement | None:
    node = _resolve(document, ref)
    if not isinstance(node, dict):
        return None
    prefix = ref[2:].split("/")[0]
    label = node.get("label")
    if prefix == "texts":
        text = node.get("text") or node.get("orig") or ""
    elif prefix == "tables":
        text = _table_text(node)
    elif prefix == "pictures":
        text = _picture_text(document, node)
    else:
        return None
    return ParseElement(
        ref=str(node.get("self_ref") or ref),
        type=str(label or prefix[:-1]),
        text=str(text),
    )


def _walk(
    document: dict[str, Any],
    children: list[Any],
    out: list[ParseElement],
    visited: set[str],
) -> None:
    for child in children:
        ref = child.get("$ref") if isinstance(child, dict) else None
        if not ref:
            continue
        if ref[2:].split("/")[0] == "groups":
            # 分组自身不是元素，递归展开它的子节点；visited 防环。
            if ref in visited:
                continue
            visited.add(ref)
            group = _resolve(document, ref)
            if isinstance(group, dict):
                _walk(document, group.get("children") or [], out, visited)
            continue
        element = _element(document, ref)
        if element is not None:
            out.append(element)


def extract_elements(document: dict[str, Any]) -> list[ParseElement]:
    """按阅读顺序（`body.children`）取出文字、表格、图片元素。"""
    out: list[ParseElement] = []
    body = document.get("body") or {}
    _walk(document, body.get("children") or [], out, set())
    return out


async def get_parse_result(
    session: AsyncSession,
    ctx: Ctx,
    storage: Storage,
    doc_id: int,
    kind: str,
) -> ParseResultOut:
    """读解析结果；没有对应版本时返回 404（前端据此回落到 original）。"""
    doc = await get_document(session, ctx, doc_id)
    parse = await session.get(DocumentParse, (doc.id, kind))
    if parse is None:
        if kind == "edited":
            raise NotFoundError("该文档没有人工修改的解析结果")
        raise NotFoundError("该文档还没有解析结果")

    markdown = ""
    if parse.md_key:
        markdown = (await storage.get_bytes(parse.md_key)).decode("utf-8")
    elements: list[ParseElement] = []
    if parse.doc_key:
        document = json.loads(await storage.get_bytes(parse.doc_key))
        if isinstance(document, dict):
            elements = extract_elements(document)

    edited_by: UserBrief | None = None
    edited_at = None
    if kind == "edited":
        edited_at = parse.created_at
        if parse.created_by is not None:
            user = await session.get(User, parse.created_by)
            if user is not None:
                edited_by = UserBrief(id=user.id, username=user.username, name=user.name)

    return ParseResultOut(
        kind=kind,  # type: ignore[arg-type]
        markdown=markdown,
        engine=ParseEngine(parse.engine) if parse.engine else ParseEngine.builtin,
        engine_version=parse.engine_version,
        pages=parse.pages,
        ocr_pages=parse.ocr_pages,
        edited_by=edited_by,
        edited_at=edited_at,
        elements=elements,
    )

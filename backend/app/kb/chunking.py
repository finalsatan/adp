"""分块：把 DoclingDocument 按入库方案切成分块（P3-10，知识库技术方案 9.1–9.4、13）。

- `build_elements` 用 docling-core 的 `HierarchicalChunker` 拆出元素流：每个段落、
  列表、表格、图片是一个元素，带标题路径和来源元素；表格序列化成 Markdown 表格。
  元素按顺序拼成全文并记录起止位置，五种分块方式共用这张位置表（9.1）。
- `chunk_document` 按分块方式切分：自动、按标题、按长度、按页、整篇；表格整表一块、
  超长按行拆且每块带表头，Excel / CSV 每 N 行一块（9.2、9.3）。
- `positions_for_items` 按知识库技术方案 13 生成位置数据（PDF 页码 / 坐标、图片
  包围框、Word 等元素引用、PPT 页码、Excel / CSV 工作表与行范围）。
- `ensure_within_limit` 做超长检查（9.4）；`write_chunks` 写 `chunks` 表（11.2）。

按规则、语义分块和父子分块是第五期，本模块只实现第三期的五种方式。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, override

from docling_core.transforms.chunker import HierarchicalChunker
from docling_core.transforms.chunker.hierarchical_chunker import ChunkingDocSerializer
from docling_core.transforms.serializer.base import BaseSerializerProvider
from docling_core.transforms.serializer.markdown import (
    MarkdownParams,
    MarkdownTableSerializer,
)
from docling_core.types.doc.base import ImageRefMode
from docling_core.types.doc.document import DocItem, DoclingDocument, TableItem
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ChunkTooLongError
from app.gateway.tokens import count_text
from app.kb.schemas import ChunkConfig, SchemeConfig
from app.kb.tables import Chunk

# Excel、CSV 不使用分块方式，按行分块（9.3）。
TABLE_FILE_TYPES = frozenset({"excel", "csv"})
# 按长度分块的默认分隔符与重叠长度（需求 4.1.4，与 ChunkLength 默认值一致）。
DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "；"]
DEFAULT_OVERLAP = 80
# 超长检查：上下文说明的预留长度与 90% 上限（9.4，两项预留各 100 tokens）。
CONTEXT_RESERVED_TOKENS = 100
MAX_INPUT_RATIO = 0.9
# 表格 meta 里的自定义字段名（X04 完成记录约定）。
SHEET_NAME_FIELD = "adp__sheet_name"
ROW_NUMBERS_FIELD = "adp__row_numbers"

_TABLE_SERIALIZER = MarkdownTableSerializer()


@dataclass
class Element:
    """元素流里的一项：一段文本或一个表格。"""

    text: str
    title_path: str | None
    items: list[DocItem]
    table: TableItem | None = None

    @property
    def is_table(self) -> bool:
        return self.table is not None


@dataclass
class ChunkData:
    """切出来的一个分块。`seq` 在 `chunk_document` 里统一编号。"""

    seq: int
    kind: str
    title_path: str | None
    content: str
    token_count: int
    positions: list[dict[str, Any]]


@dataclass
class OverlongResult:
    """超长检查命中的分块：序号与所在页（没有页码时为 None）。"""

    seq: int
    page: int | None


class _MarkdownSerializerProvider(BaseSerializerProvider):
    """把表格序列化成 Markdown 表格，并屏蔽 X04 的位置自定义字段（9.1）。"""

    @override
    def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
        return ChunkingDocSerializer(
            doc=doc,
            table_serializer=_TABLE_SERIALIZER,
            params=MarkdownParams(
                image_mode=ImageRefMode.PLACEHOLDER,
                image_placeholder="",
                escape_underscores=False,
                escape_html=False,
                blocked_meta_names={SHEET_NAME_FIELD, ROW_NUMBERS_FIELD},
            ),
        )


def build_elements(doc: DoclingDocument) -> list[Element]:
    """用 `HierarchicalChunker` 拆出元素流（9.1）。"""
    chunker = HierarchicalChunker(serializer_provider=_MarkdownSerializerProvider())
    elements: list[Element] = []
    for chunk in chunker.chunk(doc):
        table = next((item for item in chunk.meta.doc_items if isinstance(item, TableItem)), None)
        headings = chunk.meta.headings
        elements.append(
            Element(
                text=chunk.text,
                title_path=" > ".join(headings) if headings else None,
                items=list(chunk.meta.doc_items),
                table=table,
            )
        )
    return elements


# ---- 位置数据（知识库技术方案 13）----


def _bbox_list(bbox: Any) -> list[float]:
    return [float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)]


def positions_for_items(items: Sequence[DocItem], file_type: str) -> list[dict[str, Any]]:
    """把来源元素转成位置数据；表格的行范围由调用方单独处理。"""
    positions: list[dict[str, Any]] = []
    for item in items:
        if file_type == "pdf":
            for prov in item.prov or []:
                if prov.page_no is None:
                    continue
                positions.append(
                    {
                        "page": prov.page_no,
                        "bbox": _bbox_list(prov.bbox),
                        "origin": prov.bbox.coord_origin.value,
                    }
                )
        elif file_type == "image":
            for prov in item.prov or []:
                positions.append({"bbox": _bbox_list(prov.bbox)})
        elif file_type == "ppt":
            pages = sorted({prov.page_no for prov in (item.prov or []) if prov.page_no is not None})
            positions.extend({"page": page} for page in pages)
        else:
            positions.append({"ref": item.self_ref})
    return _dedupe(positions)


def _dedupe(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    result: list[dict[str, Any]] = []
    for position in positions:
        key = tuple(sorted((name, repr(value)) for name, value in position.items()))
        if key in seen:
            continue
        seen.add(key)
        result.append(position)
    return result


def _table_meta(table: TableItem) -> dict[str, Any]:
    if table.meta is None:
        return {}
    return dict(table.meta.get_custom_part())


def _rows_range(row_numbers: list[int], start: int, end: int) -> list[int]:
    """把表格网格行区间映射成原始行号区间（X04 的 `adp__row_numbers`）。"""
    if not row_numbers:
        return []
    start = max(0, min(start, len(row_numbers) - 1))
    end = max(start + 1, min(end, len(row_numbers)))
    return [row_numbers[start], row_numbers[end - 1]]


def _table_row_positions(table: TableItem, start: int, end: int) -> list[dict[str, Any]]:
    meta = _table_meta(table)
    row_numbers = list(meta.get(ROW_NUMBERS_FIELD) or [])
    return [{"sheet": meta.get(SHEET_NAME_FIELD, ""), "rows": _rows_range(row_numbers, start, end)}]


# ---- 元素拼全文与位置映射（9.1）----


def _concat(elements: Sequence[Element]) -> tuple[str, list[tuple[Element, int, int]]]:
    """把元素拼成全文，返回全文与每个元素的起止位置。"""
    parts: list[str] = []
    spans: list[tuple[Element, int, int]] = []
    position = 0
    for element in elements:
        if parts:
            parts.append("\n\n")
            position += 2
        start = position
        parts.append(element.text)
        position += len(element.text)
        spans.append((element, start, position))
    return "".join(parts), spans


def _covered_elements(
    spans: Sequence[tuple[Element, int, int]], start: int, end: int
) -> list[Element]:
    return [element for element, el_start, el_end in spans if el_start < end and el_end > start]


def _make_chunk(
    content: str,
    elements: Sequence[Element],
    file_type: str,
    *,
    kind: str = "normal",
    title_path: str | None = None,
    positions: list[dict[str, Any]] | None = None,
) -> ChunkData:
    if positions is None:
        items = [item for element in elements for item in element.items]
        positions = positions_for_items(items, file_type)
    if title_path is None and elements:
        title_path = elements[0].title_path
    return ChunkData(
        seq=-1,
        kind=kind,
        title_path=title_path,
        content=content,
        token_count=count_text(content),
        positions=positions,
    )


# ---- 五种分块方式（9.2）----


def _split_by_length(
    elements: Sequence[Element],
    *,
    separators: Sequence[str],
    max_tokens: int,
    overlap: int,
    file_type: str,
) -> list[ChunkData]:
    text, spans = _concat(elements)
    if not text.strip():
        return []
    splitter = RecursiveCharacterTextSplitter(
        separators=list(separators),
        chunk_size=max_tokens,
        # 方案允许 overlap 大于 max_tokens，切分器会直接报错，这里兜底收窄。
        chunk_overlap=max(0, min(overlap, max_tokens - 1)),
        length_function=count_text,
        add_start_index=True,
    )
    chunks: list[ChunkData] = []
    for document in splitter.create_documents([text]):
        content = document.page_content
        start = int(document.metadata.get("start_index", 0))
        covered = _covered_elements(spans, start, start + len(content))
        chunks.append(_make_chunk(content, covered, file_type))
    return chunks


def _split_whole(
    elements: Sequence[Element],
    *,
    max_tokens: int,
    file_type: str,
) -> list[ChunkData]:
    text, _ = _concat(elements)
    if count_text(text) <= max_tokens:
        return [_make_chunk(text, list(elements), file_type)]
    return _split_by_length(
        elements,
        separators=DEFAULT_SEPARATORS,
        max_tokens=max_tokens,
        overlap=DEFAULT_OVERLAP,
        file_type=file_type,
    )


def _truncate_title_path(title_path: str | None, level: int) -> str | None:
    if not title_path:
        return None
    return " > ".join(title_path.split(" > ")[:level])


def _group_by_heading(
    elements: Sequence[Element], level: int
) -> list[tuple[list[Element], str | None]]:
    """按截到第 N 级的标题路径分组；标题路径取该组的路径（9.2）。"""
    groups: list[tuple[str | None, list[Element]]] = []
    for element in elements:
        key = _truncate_title_path(element.title_path, level)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(element)
        else:
            groups.append((key, [element]))
    return [(group_elements, key) for key, group_elements in groups]


def _merge_short_groups(
    groups: Sequence[tuple[list[Element], str | None]], min_tokens: int
) -> list[tuple[list[Element], str | None]]:
    """短于最小长度的组并入下一组；最后一组没有下一组可并入，单独保留（9.2）。"""
    merged: list[tuple[list[Element], str | None]] = []
    pending: tuple[list[Element], str | None] | None = None
    for elements, title_path in groups:
        if pending is not None:
            elements = pending[0] + elements
            pending = None
        text, _ = _concat(elements)
        if count_text(text) < min_tokens:
            pending = (elements, title_path)
        else:
            merged.append((elements, title_path))
    if pending is not None:
        # 最后一组没有下一组可并入，单独保留（9.2 只写了“并入下一组”）。
        merged.append(pending)
    return merged


def _split_overlong_group(
    elements: Sequence[Element],
    *,
    max_tokens: int,
    file_type: str,
    title_path: str | None,
) -> list[ChunkData]:
    """超长的组在元素边界累积切分，单个元素仍超长时交给长度切分（9.2）。"""
    chunks: list[ChunkData] = []
    buffer: list[Element] = []

    def flush() -> None:
        if not buffer:
            return
        content, _ = _concat(buffer)
        chunks.append(_make_chunk(content, list(buffer), file_type, title_path=title_path))
        buffer.clear()

    for element in elements:
        if count_text(element.text) > max_tokens:
            flush()
            for sub_chunk in _split_by_length(
                [element],
                separators=DEFAULT_SEPARATORS,
                max_tokens=max_tokens,
                overlap=0,
                file_type=file_type,
            ):
                # 子块仍属于同一个组，标题路径取组的路径。
                sub_chunk.title_path = title_path
                chunks.append(sub_chunk)
            continue
        candidate = buffer + [element]
        content, _ = _concat(candidate)
        if buffer and count_text(content) > max_tokens:
            flush()
        buffer.append(element)
    flush()
    return chunks


def _split_by_heading(
    elements: Sequence[Element],
    *,
    level: int,
    max_tokens: int,
    min_tokens: int,
    file_type: str,
) -> list[ChunkData]:
    groups = _merge_short_groups(_group_by_heading(elements, level), min_tokens)
    chunks: list[ChunkData] = []
    for group_elements, title_path in groups:
        text, _ = _concat(group_elements)
        if count_text(text) <= max_tokens:
            chunks.append(_make_chunk(text, group_elements, file_type, title_path=title_path))
        else:
            chunks.extend(
                _split_overlong_group(
                    group_elements,
                    max_tokens=max_tokens,
                    file_type=file_type,
                    title_path=title_path,
                )
            )
    return chunks


def _element_page(element: Element) -> int | None:
    for item in element.items:
        for prov in item.prov or []:
            if prov.page_no is not None:
                return prov.page_no
    return None


def _split_by_page(
    elements: Sequence[Element],
    *,
    pages_per_chunk: int,
    max_tokens: int,
    file_type: str,
) -> list[ChunkData]:
    page_groups: list[tuple[int | None, list[Element]]] = []
    for element in elements:
        page = _element_page(element)
        if page_groups and page_groups[-1][0] == page:
            page_groups[-1][1].append(element)
        else:
            page_groups.append((page, [element]))

    chunks: list[ChunkData] = []
    for index in range(0, len(page_groups), pages_per_chunk):
        group = page_groups[index : index + pages_per_chunk]
        group_elements = [element for _, page_elements in group for element in page_elements]
        text, _ = _concat(group_elements)
        if count_text(text) <= max_tokens:
            chunks.append(_make_chunk(text, group_elements, file_type))
        else:
            chunks.extend(
                _split_by_length(
                    group_elements,
                    separators=DEFAULT_SEPARATORS,
                    max_tokens=max_tokens,
                    overlap=0,
                    file_type=file_type,
                )
            )
    return chunks


# ---- 表格（9.3）----


def _split_table(element: Element, *, max_tokens: int, file_type: str) -> list[ChunkData]:
    """文档中的表格：整表一块，超长按行拆且每块带表头。"""
    if count_text(element.text) <= max_tokens:
        return [
            _make_chunk(
                element.text,
                [element],
                file_type,
                kind="table",
                title_path=element.title_path,
            )
        ]

    header_lines, body_lines = _TABLE_SERIALIZER.get_header_and_body_lines(table_text=element.text)
    header = "".join(header_lines)
    if not body_lines:
        return [
            _make_chunk(
                element.text,
                [element],
                file_type,
                kind="table",
                title_path=element.title_path,
            )
        ]

    chunks: list[ChunkData] = []
    buffer: list[str] = []
    tokens = count_text(header)
    for line in body_lines:
        line_tokens = count_text(line)
        if buffer and tokens + line_tokens > max_tokens:
            chunks.append(_table_chunk(header + "".join(buffer), element, file_type))
            buffer = []
            tokens = count_text(header)
        buffer.append(line)
        tokens += line_tokens
    if buffer:
        chunks.append(_table_chunk(header + "".join(buffer), element, file_type))
    return chunks


def _table_chunk(content: str, element: Element, file_type: str) -> ChunkData:
    return _make_chunk(
        content,
        [element],
        file_type,
        kind="table",
        title_path=element.title_path,
    )


def _split_table_rows(element: Element, *, rows_per_chunk: int, file_type: str) -> list[ChunkData]:
    """Excel、CSV：每 N 行一块，每块带表头，位置记工作表与原始行范围。"""
    table = element.table
    assert table is not None
    header_lines, body_lines = _TABLE_SERIALIZER.get_header_and_body_lines(table_text=element.text)
    header = "".join(header_lines)
    header_rows = max(len(header_lines) - 1, 0)
    meta = _table_meta(table)
    row_numbers = list(meta.get(ROW_NUMBERS_FIELD) or [])

    if not body_lines:
        position = _table_row_positions(table, 0, max(len(row_numbers), 1))
        return [
            _make_chunk(
                header or element.text,
                [element],
                file_type,
                kind="table",
                positions=position,
            )
        ]

    chunks: list[ChunkData] = []
    for start in range(0, len(body_lines), rows_per_chunk):
        group = body_lines[start : start + rows_per_chunk]
        content = header + "".join(group)
        first = header_rows + start
        chunks.append(
            _make_chunk(
                content,
                [element],
                file_type,
                kind="table",
                positions=_table_row_positions(table, first, first + len(group)),
            )
        )
    return chunks


# ---- 入口 ----


def _segments(elements: Sequence[Element]) -> list[tuple[str, Any]]:
    """按表格把元素流切成若干段；表格是独立段，不受分块方式影响（9.3）。"""
    segments: list[tuple[str, Any]] = []
    buffer: list[Element] = []
    for element in elements:
        if element.is_table:
            if buffer:
                segments.append(("text", buffer))
                buffer = []
            segments.append(("table", element))
        else:
            buffer.append(element)
    if buffer:
        segments.append(("text", buffer))
    return segments


def _chunk_text_run(
    elements: list[Element],
    chunk_config: ChunkConfig,
    file_type: str,
) -> list[ChunkData]:
    mode = chunk_config.mode
    if mode == "auto":
        if any(element.title_path for element in elements):
            return _split_by_heading(
                elements,
                level=2,
                max_tokens=chunk_config.max_tokens,
                min_tokens=chunk_config.min_tokens,
                file_type=file_type,
            )
        if file_type == "ppt":
            return _split_by_page(
                elements,
                pages_per_chunk=1,
                max_tokens=chunk_config.max_tokens,
                file_type=file_type,
            )
        return _split_by_length(
            elements,
            separators=DEFAULT_SEPARATORS,
            max_tokens=chunk_config.max_tokens,
            overlap=getattr(chunk_config, "overlap", DEFAULT_OVERLAP),
            file_type=file_type,
        )
    if mode == "heading":
        return _split_by_heading(
            elements,
            level=chunk_config.heading_level,
            max_tokens=chunk_config.max_tokens,
            min_tokens=chunk_config.min_tokens,
            file_type=file_type,
        )
    if mode == "length":
        return _split_by_length(
            elements,
            separators=chunk_config.separators,
            max_tokens=chunk_config.max_tokens,
            overlap=chunk_config.overlap,
            file_type=file_type,
        )
    if mode == "page":
        return _split_by_page(
            elements,
            pages_per_chunk=chunk_config.pages_per_chunk,
            max_tokens=chunk_config.max_tokens,
            file_type=file_type,
        )
    if mode == "whole":
        return _split_whole(elements, max_tokens=chunk_config.max_tokens, file_type=file_type)
    raise NotImplementedError(f"分块方式 {mode} 尚未实现（第五期）")


def chunk_document(doc: DoclingDocument, config: SchemeConfig) -> list[ChunkData]:
    """把 DoclingDocument 按方案切成分块；`seq` 从 0 起。"""
    file_type = str(config.file_type)
    elements = build_elements(doc)
    chunk_config = config.chunk
    chunks: list[ChunkData] = []

    if file_type in TABLE_FILE_TYPES:
        for element in elements:
            if element.is_table:
                chunks.extend(
                    _split_table_rows(
                        element, rows_per_chunk=config.table.rows_per_chunk, file_type=file_type
                    )
                )
            elif element.text.strip():
                chunks.append(_make_chunk(element.text, [element], file_type))
    else:
        for kind, segment in _segments(elements):
            if kind == "table":
                chunks.extend(
                    _split_table(segment, max_tokens=chunk_config.max_tokens, file_type=file_type)
                )
            else:
                chunks.extend(_chunk_text_run(segment, chunk_config, file_type))

    for seq, chunk in enumerate(chunks):
        chunk.seq = seq
    return chunks


# ---- 超长检查（9.4）----


def _first_page(positions: Sequence[dict[str, Any]]) -> int | None:
    for position in positions:
        page = position.get("page")
        if isinstance(page, int):
            return page
    return None


def find_overlong(
    chunks: Sequence[ChunkData],
    *,
    embedding_max_input: int,
    title_path_enabled: bool,
    context_reserved_tokens: int = CONTEXT_RESERVED_TOKENS,
) -> OverlongResult | None:
    """向量化文本超过模型最大输入 90% 时返回第一个命中的分块（9.4）。"""
    limit = int(embedding_max_input * MAX_INPUT_RATIO)
    for chunk in chunks:
        text = chunk.content
        if title_path_enabled and chunk.title_path:
            text = f"{chunk.title_path}\n{text}"
        if count_text(text) + context_reserved_tokens > limit:
            return OverlongResult(seq=chunk.seq, page=_first_page(chunk.positions))
    return None


def ensure_within_limit(
    chunks: Sequence[ChunkData],
    *,
    embedding_max_input: int,
    title_path_enabled: bool,
    context_reserved_tokens: int = CONTEXT_RESERVED_TOKENS,
) -> None:
    """超长时抛 `ChunkTooLongError`，`error_message` 写明分块序号和页码（9.4）。"""
    result = find_overlong(
        chunks,
        embedding_max_input=embedding_max_input,
        title_path_enabled=title_path_enabled,
        context_reserved_tokens=context_reserved_tokens,
    )
    if result is None:
        return
    message = f"第 {result.seq} 个分块超过向量化模型的最大输入长度"
    if result.page is not None:
        message += f"（第 {result.page} 页）"
    raise ChunkTooLongError(message, {"seq": result.seq, "page": result.page})


# ---- 写库（11.2）----


async def write_chunks(
    session: AsyncSession,
    *,
    kb_id: int,
    doc_id: int,
    generation: int,
    chunks: Sequence[ChunkData],
    run_by: int | None,
) -> None:
    """写 `chunks` 表；`generation` 与两代切换由调用方（P3-11）决定。"""
    session.add_all(
        [
            Chunk(
                kb_id=kb_id,
                doc_id=doc_id,
                generation=generation,
                seq=chunk.seq,
                kind=chunk.kind,
                title_path=chunk.title_path,
                content=chunk.content,
                token_count=chunk.token_count,
                positions=chunk.positions or None,
                source="auto",
                edited=False,
                enabled=True,
                version=1,
                updated_by=run_by,
            )
            for chunk in chunks
        ]
    )
    await session.flush()

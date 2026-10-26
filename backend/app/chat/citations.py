"""引用快照、角标提取与引用预览 / 下载（应用技术方案 8、知识库技术方案 13）。

集中放三件事（P4-10）：

- 从 P4-07 的检索候选生成引用快照（8.2），供 P4-09 落库与 `CITATIONS` 事件；
- 从全文提取实际出现过的角标编号（8.1）；
- 终端用户的引用预览与下载（8.3）：PDF 只返回被引用页、Word 返回带上下文的
  Markdown 片段，其余类型只返回快照片段。预览与下载的公共函数供 P4-11（调试）、
  P4-12（日志）复用。

安全：原文件与预览 PDF 都带 `X-Content-Type-Options: nosniff` 与
`Content-Security-Policy: sandbox`，只有 PDF 等白名单类型允许 inline（沿用
`app/kb/documents/files.py` 的做法）。
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import anyio
import pypdfium2 as pdfium
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps import usage
from app.apps.tables import App, AppVersion
from app.apps.validation import parse_config
from app.chat.schemas import (
    CitationElementOut,
    CitationOut,
    CitationPreviewOut,
    CitationRender,
    CitationType,
)
from app.chat.tables import ChatAnswer, ChatTurn, Conversation
from app.core.errors import (
    AppError,
    AppNotPublishedError,
    CitationNotFoundError,
    DownloadNotAllowedError,
    NotFoundError,
    PermissionDeniedError,
    RangeNotSatisfiableError,
    StorageError,
)
from app.core.storage import Storage, doc_prefix, kb_original_key
from app.iam.context import Ctx
from app.kb.documents import files as kb_files
from app.kb.documents.parse import extract_elements
from app.kb.schemas import FileType
from app.kb.tables import Document, DocumentParse
from app.resources import access

if TYPE_CHECKING:  # 避免与 pipeline 的运行时依赖成环
    from app.chat.pipeline import AnswerRun

# Word 预览在被引用元素前后各带的元素数（8.3）。
WORD_CONTEXT_ELEMENTS = 3
# 流式返回原文件的块大小，避免整份读进内存。
_CHUNK_SIZE = 64 * 1024

# 第四期支持的引用预览类型（17.2）；其余类型只返回快照片段。
_PDF_TYPES = {FileType.pdf}
_WORD_TYPES = {FileType.word}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox",
}
EDITED_NOTE = "人工修改内容"
STALE_NOTE = "原文已更新"
UNSUPPORTED_NOTE = "该类型暂不支持预览"

# 实际引用过的角标：`[n]`、全角 `【n】`，以及模型把多条写成 `[1、2]` 的情况（8.1）。
_CITED_RE = re.compile(r"[\[【]([^\]】]*)[\]】]")
_NUMBER_RE = re.compile(r"\d+")


# ---- 引用快照与角标 ----


def build_citations(run: AnswerRun) -> list[CitationOut]:
    """把本次编号参考内容转成引用快照（8.2）。"""
    references = (
        run.prompt_assembly.references if run.prompt_assembly is not None else run.references
    )
    citations: list[CitationOut] = []
    for reference in references:
        candidate = reference.candidate
        if candidate is None:  # pragma: no cover - 正常都有候选
            continue
        if reference.type == CitationType.qa.value:
            citations.append(
                CitationOut(
                    n=reference.n,
                    type=CitationType.qa,
                    kb_id=getattr(candidate, "kb_id", 0),
                    kb_name=getattr(candidate, "kb_name", "") or reference.source,
                    score=reference.score,
                    qa_id=getattr(candidate, "qa_id", None),
                    question=getattr(candidate, "question", None),
                    answer=getattr(candidate, "answer", None),
                    direct=getattr(candidate, "direct", None),
                )
            )
            continue
        citations.append(
            CitationOut(
                n=reference.n,
                type=CitationType.doc,
                kb_id=candidate.kb_id,
                kb_name=candidate.kb_name,
                score=reference.score,
                doc_id=candidate.doc_id,
                doc_name=candidate.doc_name,
                file_type=candidate.file_type,
                chunk_ids=list(candidate.chunk_ids),
                generation=candidate.generation,
                content=candidate.content,
                positions=candidate.positions,
                edited=candidate.edited,
            )
        )
    return citations


def citations_for_event(citations: list[CitationOut]) -> list[CitationOut]:
    """CITATIONS 事件不含位置，点击预览时再取（9.1、8.1）。"""
    return [citation.model_copy(update={"positions": None}) for citation in citations]


def extract_cited(text: str) -> list[int]:
    """从全文提取实际出现过的编号，去重并升序（8.1）。

    支持 `[n]`、全角 `【n】`，以及 `[1、2]` 这种一个方括号里写多个编号的情况。
    没有角标时返回空列表。
    """
    numbers: set[int] = set()
    for group in _CITED_RE.findall(text or ""):
        numbers.update(int(value) for value in _NUMBER_RE.findall(group))
    return sorted(numbers)


# ---- 加载与权限 ----


async def get_owned_answer(
    session: AsyncSession, ctx: Ctx, answer_id: int
) -> tuple[ChatAnswer, Conversation, App]:
    """取本人的回答：会话不属于本人返回 403，回答 / 会话不存在返回 404（8.3）。"""
    answer = await session.get(ChatAnswer, answer_id)
    if answer is None:
        raise NotFoundError()
    turn = await session.get(ChatTurn, answer.turn_id)
    if turn is None:
        raise NotFoundError()
    conversation = await session.get(Conversation, turn.conversation_id)
    if conversation is None:
        raise NotFoundError()
    if conversation.user_id != ctx.user_id:
        raise PermissionDeniedError()
    app, _ = await usage.get_chat_app(session, ctx, conversation.app_id)
    return answer, conversation, app


def get_citation(answer: ChatAnswer, n: int) -> CitationOut:
    """按编号取快照里的引用；没有这个编号返回 404 `CITATION_NOT_FOUND`（8.3）。"""
    for item in answer.citations or []:
        citation = CitationOut.model_validate(item)
        if citation.n == n:
            return citation
    raise CitationNotFoundError()


async def current_allow_download(session: AsyncSession, app: App) -> bool:
    """应用**当前发布版本**是否开启下载原文件；不看生成回答时的版本（8.3）。"""
    if app.published_version_id is None:
        raise AppNotPublishedError()
    version = await session.get(AppVersion, app.published_version_id)
    if version is None:
        raise AppNotPublishedError()
    return parse_config(app.app_type, version.config).reply.allow_download


async def can_view_whole(session: AsyncSession, ctx: Ctx, kb_id: int) -> bool:
    """搭建者本人对知识库是否有查看权限；有权限才能看整份原文（8.3）。"""
    try:
        await access.get_for(session, ctx, kb_id, access.VIEW, type="kb")
    except AppError:
        return False
    return True


# ---- 预览 ----


def _snapshot_preview(
    citation: CitationOut,
    *,
    download_allowed: bool,
    note: str,
    stale: bool = False,
) -> CitationPreviewOut:
    return CitationPreviewOut(
        render=CitationRender.snapshot,
        file_type=citation.file_type or FileType.txt,
        kb_id=citation.kb_id,
        kb_name=citation.kb_name,
        doc_id=citation.doc_id or 0,
        doc_name=citation.doc_name or "",
        page_no=None,
        pages=[],
        positions=citation.positions or [],
        content=citation.content,
        elements=None,
        generation=citation.generation or 0,
        edited=bool(citation.edited),
        stale=stale,
        note=note,
        download_allowed=download_allowed,
    )


def _cited_pages(citation: CitationOut) -> list[int]:
    """PDF 被引用页（快照 positions 里的页码，去重升序）。"""
    pages: set[int] = set()
    for position in citation.positions or []:
        if isinstance(position, dict):
            page = position.get("page")
            if isinstance(page, int) and page > 0:
                pages.add(page)
    return sorted(pages)


def _cited_refs(citation: CitationOut) -> list[str]:
    """Word 被引用元素（快照 positions 里的元素引用）。"""
    refs: list[str] = []
    for position in citation.positions or []:
        if isinstance(position, dict) and isinstance(position.get("ref"), str):
            refs.append(position["ref"])
    return refs


def _page_key(kb_id: int, doc_id: int, generation: int, page: int) -> str:
    """被引用页 PDF 的缓存键（8.3）：`kb/{kb}/docs/{doc}/pages/{gen}/{页码}.pdf`。"""
    return f"{doc_prefix(kb_id, doc_id)}pages/{generation}/{page}.pdf"


def _extract_pdf_pages(data: bytes, pages: list[int]) -> dict[int, bytes]:
    """用 pypdfium2 从原文件取出这些页，每页一个单页 PDF。"""
    source = pdfium.PdfDocument(data)
    try:
        total = len(source)
        extracted: dict[int, bytes] = {}
        for page in pages:
            if page < 1 or page > total:
                continue
            single = pdfium.PdfDocument.new()
            try:
                single.import_pages(source, pages=[page - 1])
                buffer = io.BytesIO()
                single.save(buffer)
                extracted[page] = buffer.getvalue()
            finally:
                single.close()
        return extracted
    finally:
        source.close()


def _merge_pdfs(pages: list[bytes]) -> bytes:
    """把多个单页 PDF 合成一份返回。"""
    if len(pages) == 1:
        return pages[0]
    merged = pdfium.PdfDocument.new()
    try:
        for data in pages:
            source = pdfium.PdfDocument(data)
            try:
                merged.import_pages(source, pages=list(range(len(source))))
            finally:
                source.close()
        buffer = io.BytesIO()
        merged.save(buffer)
        return buffer.getvalue()
    finally:
        merged.close()


def _pdf_response(data: bytes, name: str) -> Response:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(f'{stem}.pdf')}",
            **_SECURITY_HEADERS,
        },
    )


async def _cached_pages(
    storage: Storage,
    doc: Document,
    generation: int,
    original: bytes,
    pages: list[int],
) -> dict[int, bytes]:
    """取被引用页：优先读缓存，缺失的从原文件取出并写回缓存（8.3）。"""
    result: dict[int, bytes] = {}
    missing: list[int] = []
    for page in pages:
        key = _page_key(doc.kb_id, doc.id, generation, page)
        if await storage.exists(key):
            result[page] = await storage.get_bytes(key)
        else:
            missing.append(page)
    if missing:
        extracted = await anyio.to_thread.run_sync(_extract_pdf_pages, original, missing)
        for page, data in extracted.items():
            result[page] = data
            await storage.put_bytes(_page_key(doc.kb_id, doc.id, generation, page), data)
    return result


async def _pdf_preview(
    storage: Storage,
    doc: Document,
    citation: CitationOut,
    *,
    download_allowed: bool,
    whole_document: bool,
) -> Response | CitationPreviewOut:
    key = kb_original_key(doc.kb_id, doc.id, doc.ext)
    try:
        original = await storage.get_bytes(key)
    except StorageError as exc:
        if kb_files.is_not_found(exc):
            return _snapshot_preview(
                citation, download_allowed=download_allowed, note=STALE_NOTE, stale=True
            )
        raise
    if whole_document:
        return _pdf_response(original, doc.name)
    pages = _cited_pages(citation)
    if not pages:
        return _snapshot_preview(citation, download_allowed=download_allowed, note=UNSUPPORTED_NOTE)
    generation = citation.generation or doc.current_generation
    cached = await _cached_pages(storage, doc, generation, original, pages)
    ordered = [cached[page] for page in pages if page in cached]
    if not ordered:
        return _snapshot_preview(citation, download_allowed=download_allowed, note=STALE_NOTE)
    return _pdf_response(_merge_pdfs(ordered), doc.name)


def _element_markdown(element: Any) -> str:
    """元素转 Markdown 片段；标题加 `## `，其余按原文。"""
    if element.type == "section_header":
        return f"## {element.text}"
    return element.text


def _context_indices(elements: list[Any], refs: list[str]) -> list[int]:
    """被引用元素前后各 `WORD_CONTEXT_ELEMENTS` 个元素的下标。"""
    index_by_ref = {element.ref: index for index, element in enumerate(elements)}
    cited = {index_by_ref[ref] for ref in refs if ref in index_by_ref}
    if not cited:
        return []
    selected: set[int] = set()
    for index in cited:
        start = max(0, index - WORD_CONTEXT_ELEMENTS)
        end = min(len(elements), index + WORD_CONTEXT_ELEMENTS + 1)
        selected.update(range(start, end))
    return sorted(selected)


async def _word_preview(
    session: AsyncSession,
    storage: Storage,
    doc: Document,
    citation: CitationOut,
    *,
    stale: bool,
    download_allowed: bool,
    whole_document: bool,
) -> Response | CitationPreviewOut:
    if stale:
        return _snapshot_preview(
            citation, download_allowed=download_allowed, note=STALE_NOTE, stale=True
        )
    parse = await session.get(DocumentParse, (doc.id, "original"))
    if parse is None or parse.doc_key is None:
        return _snapshot_preview(citation, download_allowed=download_allowed, note=STALE_NOTE)
    document = json.loads(await storage.get_bytes(parse.doc_key))
    elements = extract_elements(document)
    refs = _cited_refs(citation)
    cited_refs = set(refs)
    if whole_document:
        selected = list(range(len(elements)))
        if parse.md_key is not None:
            content = (await storage.get_bytes(parse.md_key)).decode("utf-8")
        else:
            content = "\n\n".join(_element_markdown(element) for element in elements)
    else:
        selected = _context_indices(elements, refs)
        content = "\n\n".join(_element_markdown(elements[index]) for index in selected)
    element_outs = [
        CitationElementOut(
            ref=elements[index].ref,
            type=elements[index].type,
            text=elements[index].text,
            highlight=elements[index].ref in cited_refs,
        )
        for index in selected
    ]
    return CitationPreviewOut(
        render=CitationRender.markdown,
        file_type=citation.file_type or FileType.word,
        kb_id=citation.kb_id,
        kb_name=citation.kb_name,
        doc_id=doc.id,
        doc_name=doc.name,
        page_no=None,
        pages=[],
        positions=citation.positions or [],
        content=content,
        elements=element_outs,
        generation=citation.generation or doc.current_generation,
        edited=bool(citation.edited),
        stale=stale,
        note=None,
        download_allowed=download_allowed,
    )


async def render_preview(
    session: AsyncSession,
    storage: Storage,
    citation: CitationOut,
    *,
    whole_document: bool = False,
    download_allowed: bool = False,
) -> Response:
    """渲染一条引用的预览（8.3）。

    终端用户传 `whole_document=False`；搭建者（P4-11 / P4-12）按对知识库的查看权限
    传 `can_view_whole(...)` 的结果，有权限时返回整份原文。
    """
    result = await _render_preview(
        session,
        storage,
        citation,
        whole_document=whole_document,
        download_allowed=download_allowed,
    )
    if isinstance(result, CitationPreviewOut):
        return JSONResponse(content=result.model_dump(mode="json"))
    return result


async def _render_preview(
    session: AsyncSession,
    storage: Storage,
    citation: CitationOut,
    *,
    whole_document: bool,
    download_allowed: bool,
) -> Response | CitationPreviewOut:
    if citation.type != CitationType.doc or citation.doc_id is None:
        return _snapshot_preview(citation, download_allowed=download_allowed, note=UNSUPPORTED_NOTE)
    doc = await session.get(Document, citation.doc_id)
    stale = doc is None or (
        citation.generation is not None and citation.generation != doc.current_generation
    )
    if citation.edited:
        return _snapshot_preview(
            citation, download_allowed=download_allowed, note=EDITED_NOTE, stale=stale
        )
    if doc is None:
        return _snapshot_preview(
            citation, download_allowed=download_allowed, note=STALE_NOTE, stale=True
        )
    if citation.file_type in _PDF_TYPES:
        return await _pdf_preview(
            storage, doc, citation, download_allowed=download_allowed, whole_document=whole_document
        )
    if citation.file_type in _WORD_TYPES:
        return await _word_preview(
            session,
            storage,
            doc,
            citation,
            stale=stale,
            download_allowed=download_allowed,
            whole_document=whole_document,
        )
    return _snapshot_preview(
        citation, download_allowed=download_allowed, note=UNSUPPORTED_NOTE, stale=stale
    )


# ---- 下载原文件 ----


def _iter_body(body: Any) -> Iterator[bytes]:
    try:
        while True:
            chunk = body.read(_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        close = getattr(body, "close", None)
        if close is not None:
            close()


def _content_disposition(name: str, download: bool, inline_allowed: bool) -> str:
    disposition = "inline" if inline_allowed and not download else "attachment"
    return f"{disposition}; filename*=UTF-8''{quote(name)}"


async def stream_citation_file(
    session: AsyncSession,
    storage: Storage,
    citation: CitationOut,
    *,
    download_allowed: bool,
    download: bool,
    range_header: str | None,
) -> Response:
    """下载原文件，支持 HTTP Range；未开启下载返回 403（8.3）。"""
    if not download_allowed:
        raise DownloadNotAllowedError()
    if citation.doc_id is None:
        raise NotFoundError()
    doc = await session.get(Document, citation.doc_id)
    if doc is None:
        raise NotFoundError("该文档的原文件不存在")
    key = kb_original_key(doc.kb_id, doc.id, doc.ext)
    byte_range = kb_files.parse_range(range_header)
    try:
        body, content_length, content_range = await storage.get_object(key, byte_range=byte_range)
    except StorageError as exc:
        if kb_files.is_not_found(exc):
            raise NotFoundError("该文档的原文件不存在") from exc
        if byte_range is not None and kb_files.is_invalid_range(exc):
            raise RangeNotSatisfiableError() from exc
        raise

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": _content_disposition(
            doc.name, download, kb_files.is_inline_allowed(doc.ext)
        ),
        **_SECURITY_HEADERS,
    }
    status_code = 200
    if byte_range is not None and content_range:
        status_code = 206
        headers["Content-Range"] = content_range
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return StreamingResponse(
        _iter_body(body),
        status_code=status_code,
        media_type=kb_files.content_type_for(doc.ext),
        headers=headers,
    )


__all__ = [
    "EDITED_NOTE",
    "STALE_NOTE",
    "UNSUPPORTED_NOTE",
    "WORD_CONTEXT_ELEMENTS",
    "build_citations",
    "can_view_whole",
    "citations_for_event",
    "current_allow_download",
    "extract_cited",
    "get_citation",
    "get_owned_answer",
    "render_preview",
    "stream_citation_file",
]

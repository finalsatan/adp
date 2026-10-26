"""回答流程中的文档库检索（应用技术方案 6.3、6.5）。

复用知识库检索服务（P3-13）的两段函数：按应用配置对每个文档库分别召回并融合，
再合并重排，产出供 P4-08 提示词组装与 P4-10 引用快照使用的候选分块。

- 只处理文档库（`kind=doc`）；问答库（`kind=qa`）第六期接入，本卡跳过。
- 已停用的知识库跳过；全部跳过返回空候选（由 P4-06 回复兜底话术）。
- 一次提问内多个文档库共用一份查询向量缓存（`vector_cache`），同一个向量化模型只调用一次。
- 单库向量化 / 召回失败跳过，其余照常；全部失败抛 `RETRIEVAL_FAILED`；重排失败也抛
  `RETRIEVAL_FAILED`，不退回按融合分排序（6.5）。
- 相邻分块扩展第五期，本卡接收但不生效。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.apps.config import (
    DEFAULT_DOC_SCORE_THRESHOLD,
    AppDocConfig,
    AppKbRef,
)
from app.chat.pipeline import AnswerRun, EmitFn, begin_step, end_step
from app.chat.schemas import StepName
from app.core.errors import AppError, RetrievalFailedError
from app.gateway.errors import ModelCallError
from app.kb import retrieval
from app.kb.schemas import (
    FileType,
    KbKind,
    RerankConfig,
    RetrievalConfig,
    RetrievalMode,
)
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource

logger = logging.getLogger(__name__)

SessionMaker = async_sessionmaker[AsyncSession]


class CandidatePriority(StrEnum):
    """参考内容的优先级；文档分块为普通优先级，问答对（第六期）为高优先级。"""

    high = "high"
    normal = "normal"


@dataclass
class AnswerCandidate:
    """供提示词组装（P4-08）与引用快照（P4-10）使用的候选分块。

    `chunk_ids` 现在只有一个分块；相邻分块扩展（第五期）后可能多块。`score` 为
    重排分（开启重排）或融合分（未开启）。
    """

    kb_id: int
    kb_name: str
    doc_id: int
    doc_name: str
    file_type: FileType
    chunk_ids: list[int]
    generation: int
    content: str
    title_path: str | None
    positions: list
    edited: bool
    score: float
    priority: CandidatePriority = CandidatePriority.normal


@dataclass
class KbRecallInfo:
    """处理过程里一个文档库的召回情况。"""

    kb_id: int
    kb_name: str
    ok: bool
    vector: int = 0
    fulltext: int = 0
    fusion: int = 0
    error: str | None = None


@dataclass
class RetrievalOutcome:
    """文档库检索结果：候选列表与分阶段结果（各库召回信息、召回与重排结果）。"""

    candidates: list[AnswerCandidate]
    recall: retrieval.RecallResult | None
    rerank: retrieval.RerankResult | None
    per_kb: list[KbRecallInfo] = field(default_factory=list)


@dataclass
class _DocKb:
    """一个参与检索的文档库及其应用引用配置。"""

    kb: KnowledgeBase
    name: str
    ref: AppKbRef


# ---- 加载参与的知识库 ----


async def _load_doc_kbs(run: AnswerRun) -> list[_DocKb]:
    """按应用配置顺序取 `kind=doc` 且未停用的知识库；其余（问答库、停用、缺失）跳过。"""
    refs = run.config.knowledge.kbs
    if not refs:
        return []
    kb_ids = list(dict.fromkeys(ref.kb_id for ref in refs))
    async with run.sessionmaker() as session:
        rows = (
            await session.execute(
                sa.select(KnowledgeBase, Resource)
                .join(Resource, Resource.id == KnowledgeBase.id)
                .where(KnowledgeBase.id.in_(kb_ids))
            )
        ).all()
    loaded = {kb.id: (kb, resource) for kb, resource in rows}
    result: list[_DocKb] = []
    for ref in refs:
        item = loaded.get(ref.kb_id)
        if item is None:
            continue
        kb, resource = item
        if kb.kind != KbKind.doc.value:
            continue
        if resource.deleted_at is not None or resource.status != "enabled":
            continue
        result.append(_DocKb(kb=kb, name=resource.name, ref=ref))
    return result


def _kb_config(doc_kb: _DocKb, doc: AppDocConfig) -> RetrievalConfig:
    """知识库默认检索配置被应用覆盖值覆盖；`top_k` 用文档库公共设置（6.3）。"""
    base = doc_kb.ref.retrieval or retrieval.effective_config(doc_kb.kb)
    return base.model_copy(update={"top_k": doc.top_k})


def _folder_ids(doc_kb: _DocKb) -> list[int]:
    return list(doc_kb.ref.filter.folder_ids) if doc_kb.ref.filter else []


def _metadata(doc_kb: _DocKb) -> Any:
    return doc_kb.ref.filter.metadata if doc_kb.ref.filter else None


def _merge_recalls(
    recalls: list[retrieval.RecallResult], config: RetrievalConfig, vector_cache: dict
) -> retrieval.RecallResult:
    """合并同一知识库多个文件夹条件的召回结果（分块只属于一个文件夹，直接合并排序）。"""
    vector = [c for recall in recalls for c in recall.vector]
    fulltext = [c for recall in recalls for c in recall.fulltext]
    fusion = [c for recall in recalls for c in recall.fusion]
    vector.sort(key=lambda c: c.vector_score or 0.0, reverse=True)
    fulltext.sort(key=lambda c: c.bm25_score or 0.0, reverse=True)
    fusion.sort(key=lambda c: c.fusion_score or 0.0, reverse=True)
    return retrieval.RecallResult(
        vector=vector,
        fulltext=fulltext,
        fusion=fusion,
        config=config,
        vector_cache=vector_cache,
        timings={
            key: sum(recall.timings.get(key, 0) for recall in recalls)
            for key in ("vector_ms", "fulltext_ms", "fusion_ms")
        },
    )


async def _recall_one_kb(
    run: AnswerRun, doc_kb: _DocKb, question: str, vector_cache: dict[int, list[float]]
) -> retrieval.RecallResult:
    """一个文档库的召回并融合；文件夹条件为多个时分别召回后合并。"""
    config = _kb_config(doc_kb, run.config.knowledge.doc)
    folders = _folder_ids(doc_kb) or [None]
    recalls = [
        await retrieval.recall_and_fuse(
            run.sessionmaker,
            run.redis,
            kb_ids=[doc_kb.kb.id],
            question=question,
            config=config,
            folder_id=folder_id,
            metadata=_metadata(doc_kb),
            user_id=run.user_id,
            org_id=run.org_id,
            vector_cache=vector_cache,
            source=run.source,
            source_id=run.app_id,
        )
        for folder_id in folders
    ]
    return _merge_recalls(recalls, config, vector_cache)


# ---- 处理过程与候选 ----


def _error_code(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.code
    if isinstance(exc, ModelCallError):
        return exc.error_code
    return type(exc).__name__


def _brief(candidate: retrieval.Candidate) -> dict[str, Any]:
    """处理过程里一条命中的摘要：分块 ID、文档名、分数、前 100 字。"""
    score = candidate.rerank_score if candidate.rerank_score is not None else candidate.fusion_score
    return {
        "chunk_id": candidate.chunk_id,
        "doc_id": candidate.doc_id,
        "doc_name": candidate.doc_name,
        "score": score,
        "content": candidate.content[:100],
    }


async def _load_chunk_meta(
    sessionmaker: SessionMaker, chunk_ids: list[int]
) -> dict[int, tuple[FileType, int]]:
    """取最终候选的 `file_type` 与 `generation`（P3-13 的候选结构里没有）。"""
    if not chunk_ids:
        return {}
    stmt = text(
        "SELECT c.id AS chunk_id, c.generation, d.file_type "
        "FROM public.chunks c JOIN public.documents d ON d.id = c.doc_id "
        "WHERE c.id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    async with sessionmaker() as session:
        rows = await session.execute(stmt, {"ids": list(chunk_ids)})
    return {int(row.chunk_id): (FileType(row.file_type), int(row.generation)) for row in rows}


async def _build_candidates(
    run: AnswerRun, doc_kbs: list[_DocKb], final: list[retrieval.Candidate]
) -> list[AnswerCandidate]:
    if not final:
        return []
    kb_names = {doc_kb.kb.id: doc_kb.name for doc_kb in doc_kbs}
    meta = await _load_chunk_meta(run.sessionmaker, [c.chunk_id for c in final])
    candidates: list[AnswerCandidate] = []
    for candidate in final:
        info = meta.get(candidate.chunk_id)
        if info is None:
            continue
        file_type, generation = info
        score = (
            candidate.rerank_score
            if candidate.rerank_score is not None
            else candidate.fusion_score or 0.0
        )
        candidates.append(
            AnswerCandidate(
                kb_id=candidate.kb_id,
                kb_name=kb_names.get(candidate.kb_id, ""),
                doc_id=candidate.doc_id,
                doc_name=candidate.doc_name,
                file_type=file_type,
                chunk_ids=[candidate.chunk_id],
                generation=generation,
                content=candidate.content,
                title_path=candidate.title_path,
                positions=candidate.positions,
                edited=candidate.edited,
                score=float(score),
                priority=CandidatePriority.normal,
            )
        )
    return candidates


def _final_config(doc: AppDocConfig) -> RetrievalConfig:
    """文档库公共设置（`knowledge.doc`）：top_k、阈值、重排。"""
    threshold = (
        doc.score_threshold if doc.score_threshold is not None else DEFAULT_DOC_SCORE_THRESHOLD
    )
    return RetrievalConfig(
        mode=RetrievalMode.hybrid,
        top_k=doc.top_k,
        score_threshold=threshold,
        rerank=RerankConfig(
            enabled=doc.rerank.enabled,
            model_id=doc.rerank.model_id,
            top_n=doc.rerank.keep,
        ),
    )


async def _load_model(run: AnswerRun, model_id: int | None) -> Model | None:
    if model_id is None:
        return None
    if model_id in run.models:
        return run.models[model_id]
    async with run.sessionmaker() as session:
        return await session.get(Model, model_id)


# ---- 入口 ----


async def retrieve_documents(run: AnswerRun, emit: EmitFn) -> RetrievalOutcome:
    """回答流程里的文档库检索：多库召回并融合、合并重排（6.3）。

    返回候选列表与分阶段结果；每步写进处理过程并发出 STEP_STARTED / STEP_FINISHED。
    """
    doc = run.config.knowledge.doc
    question = run.query_text or run.question
    doc_kbs = await _load_doc_kbs(run)
    vector_cache: dict[int, list[float]] = {}

    await begin_step(run, emit, StepName.retrieve)
    if not doc_kbs:
        # 没有可检索的文档库（未引用 / 全是问答库 / 全部停用）：返回空候选，由 P4-06 兜底。
        await end_step(
            run,
            emit,
            StepName.retrieve,
            input={"question": question},
            output={"question": question, "kbs": [], "timings": {}, "hits": [], "count": 0},
        )
        return RetrievalOutcome(candidates=[], recall=None, rerank=None, per_kb=[])

    recalls: list[retrieval.RecallResult] = []
    per_kb: list[KbRecallInfo] = []
    for doc_kb in doc_kbs:
        try:
            recall = await _recall_one_kb(run, doc_kb, question, vector_cache)
        except Exception as exc:  # noqa: BLE001 - 单库失败跳过，其余照常（6.5）
            logger.info("知识库 %s 检索失败，跳过: %s", doc_kb.kb.id, _error_code(exc))
            per_kb.append(
                KbRecallInfo(
                    kb_id=doc_kb.kb.id,
                    kb_name=doc_kb.name,
                    ok=False,
                    error=_error_code(exc),
                )
            )
            continue
        recalls.append(recall)
        per_kb.append(
            KbRecallInfo(
                kb_id=doc_kb.kb.id,
                kb_name=doc_kb.name,
                ok=True,
                vector=len(recall.vector),
                fulltext=len(recall.fulltext),
                fusion=len(recall.fusion),
            )
        )

    final_config = _final_config(doc)
    combined = _merge_recalls(recalls, final_config, vector_cache)
    fused = combined.fusion
    await end_step(
        run,
        emit,
        StepName.retrieve,
        input={"question": question, "kbs": [doc_kb.kb.id for doc_kb in doc_kbs]},
        output={
            "question": question,
            "kbs": [asdict(info) for info in per_kb],
            "timings": dict(combined.timings),
            "hits": [_brief(candidate) for candidate in fused],
            "count": len(fused),
        },
    )
    if not recalls:
        # 有可检索的库但全部失败：不回复兜底话术（6.5）。
        raise RetrievalFailedError()

    rerank_model = await _load_model(run, doc.rerank.model_id)
    if doc.rerank.enabled and fused:
        await begin_step(run, emit, StepName.rerank)
        # 每个文档库各取融合分前 top_k，合在一起重排（6.3）。
        rerank_input = [candidate for recall in recalls for candidate in recall.fusion[: doc.top_k]]
        rerank_input.sort(key=lambda c: c.fusion_score or 0.0, reverse=True)
        # 不让 rerank_and_expand 再按单个 top_k 截断：不同向量化模型的融合分不可比。
        call_config = final_config.model_copy(update={"top_k": max(1, len(rerank_input))})
        rerank_recall = retrieval.RecallResult(
            vector=combined.vector,
            fulltext=combined.fulltext,
            fusion=rerank_input,
            config=final_config,
            vector_cache=vector_cache,
            timings=combined.timings,
        )
        try:
            rerank_result = await retrieval.rerank_and_expand(
                run.sessionmaker,
                run.redis,
                question=question,
                recall=rerank_recall,
                config=call_config,
                user_id=run.user_id,
                org_id=run.org_id,
                source=run.source,
                source_id=run.app_id,
            )
        except Exception as exc:  # noqa: BLE001 - 重排失败不退回融合分（6.5）
            logger.info("重排失败: %s", _error_code(exc))
            await end_step(
                run,
                emit,
                StepName.rerank,
                input={"count": len(rerank_input)},
                output={"degraded": _error_code(exc)},
                model=rerank_model,
            )
            raise RetrievalFailedError() from exc
        await end_step(
            run,
            emit,
            StepName.rerank,
            input={"count": len(rerank_input)},
            output={
                "kept": len(rerank_result.final),
                "timings": dict(rerank_result.timings),
                "hits": [_brief(candidate) for candidate in rerank_result.final],
            },
            model=rerank_model,
        )
    else:
        rerank_result = await retrieval.rerank_and_expand(
            run.sessionmaker,
            run.redis,
            question=question,
            recall=combined,
            config=final_config,
            user_id=run.user_id,
            org_id=run.org_id,
            source=run.source,
            source_id=run.app_id,
        )

    candidates = await _build_candidates(run, doc_kbs, rerank_result.final)
    return RetrievalOutcome(
        candidates=candidates, recall=combined, rerank=rerank_result, per_kb=per_kb
    )

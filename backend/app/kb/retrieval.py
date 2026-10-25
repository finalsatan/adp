"""检索服务函数与命中测试（P3-13，知识库技术方案 12）。

检索分两段，供第四期知识问答复用：

- `recall_and_fuse`：确定知识库与配置、把问题向量化、向量与全文两路召回、去重、
  计算融合分（12.1 的 1–6 步）。
- `rerank_and_expand`：阈值与重排（12.1 的 7–9 步；相邻分块扩展第五期才有）。

命中测试连续调用两段；记录不保存，模型用量来源 `kb_hit_test`（知识库技术方案 17）。
第三期只处理文档库的 `entry_type='chunk'`；问答库（`entry_type='question'/'qa'`）第六期。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from time import monotonic

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import (
    EmbeddingModelUnavailableError,
    MetadataValueInvalidError,
    NotFoundError,
    ValidationError,
)
from app.gateway import service as gateway
from app.gateway.service import CallMeta
from app.iam.context import Ctx
from app.kb.index_table import index_table_name
from app.kb.schemas import (
    HitItem,
    HitScores,
    HitSteps,
    HitTestIn,
    HitTestOut,
    HitTimings,
    MetadataFieldType,
    MetadataFilter,
    MetadataGroup,
    MetadataOperator,
    RetrievalConfig,
    RetrievalMode,
)
from app.kb.tables import KbFolder, KbMetadataField, KnowledgeBase
from app.models.schemas import ModelStatus
from app.models.tables import Model
from app.resources import access
from app.resources.tables import Resource

logger = logging.getLogger(__name__)

SessionMaker = async_sessionmaker[AsyncSession]

# 每路召回的 SQL LIMIT = top_k 的 3 倍：同一分块可能有多条索引条目，留去重余量（12.1）。
RECALL_MULTIPLIER = 3

# 全文召回条件（X09 报告 6a 实测写法）。`:q` 与 `::` 之间留空格，否则 SQLAlchemy 的
# text() 不把 `:q` 当绑定参数；参数必须显式 `::text`，否则服务端类型推断失败。
FULLTEXT_CONDITION = (
    "title ||| :q ::text::pdb.boost(2) OR body ||| :q ::text "
    "OR keywords ||| :q ::text::pdb.boost(2)"
)

# 停用词表随代码发布（11.4）。切词由 ParadeDB 的 jieba 完成，这里只过滤不参与
# 关键词分的虚词。
STOPWORDS: frozenset[str] = frozenset(
    """的 了 和 是 在 有 与 及 或 等 把 被 让 对 从 到 向 给 为 以 于 而 则 就 都 也
还 又 很 更 最 太 这 那 哪 什么 怎么 怎样 如何 为什么 多少 几 个 我 你 他 她 它 我们
你们 他们 它们 自己 之 其 此 该 并 且 但 如果 因为 所以 可以 能否 是否 请问 请 一下
一个 一些 没有 不 没 要 会 能 需要 关于 对于 根据 按照 通过 进行 已经 正在 将 着 过
吗 呢 吧 啊 呀 哦 嗯 上 下 中 里 内 外 前 后 左 右
the a an of to in on for and or is are was were be with by at from as that this""".split()
)


_idf_data: tuple[dict[str, float], float] | None = None


def _idf() -> tuple[dict[str, float], float]:
    """jieba 自带 IDF 词典与中位数（11.4）。首次调用时才加载，避免拖慢启动。"""
    global _idf_data
    if _idf_data is None:
        import jieba.analyse

        tfidf = jieba.analyse.default_tfidf
        _idf_data = (tfidf.idf_freq, tfidf.median_idf)
    return _idf_data


def _idf_weight(term: str) -> float:
    idf_freq, median = _idf()
    return float(idf_freq.get(term, median))


@dataclass(frozen=True)
class QueryTerms:
    """问题的查询词与权重；切分与权重封装在这里，以后做同义词时在此展开（11.4）。"""

    terms: tuple[str, ...]
    weights: tuple[float, ...]

    @property
    def total_weight(self) -> float:
        return sum(self.weights)

    def as_params(self, prefix: str) -> dict[str, object]:
        params: dict[str, object] = {}
        for index, (term, weight) in enumerate(zip(self.terms, self.weights, strict=True)):
            params[f"{prefix}t{index}"] = term
            params[f"{prefix}w{index}"] = weight
        return params


async def build_query_terms(session: AsyncSession, question: str) -> QueryTerms:
    """用 ParadeDB 的 jieba 切出问题原词，去停用词后按 IDF 赋权（11.4）。"""
    rows = await session.scalar(
        text("SELECT :q ::text::pdb.jieba('search_mode=false')::text[]"), {"q": question}
    )
    terms = [term for term in (rows or []) if term and term not in STOPWORDS]
    if not terms:
        return QueryTerms((), ())
    return QueryTerms(tuple(terms), tuple(_idf_weight(term) for term in terms))


@dataclass
class Candidate:
    """一个候选分块及它在各阶段的分数。"""

    kb_id: int
    chunk_id: int
    doc_id: int
    doc_name: str
    content: str
    title_path: str | None
    kind: str
    positions: list
    edited: bool
    page_no: int | None = None
    vector_score: float | None = None
    keyword_score: float | None = None
    bm25_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None

    def stage_score(self, stage: str) -> float:
        if stage == "vector":
            return self.vector_score or 0.0
        if stage == "fulltext":
            return self.bm25_score or 0.0
        if stage == "rerank":
            return self.rerank_score or 0.0
        # fusion / final
        return self.fusion_score or 0.0

    def to_hit_item(self, stage: str) -> HitItem:
        return HitItem(
            chunk_id=self.chunk_id,
            doc_id=self.doc_id,
            doc_name=self.doc_name,
            content=self.content,
            title_path=self.title_path,
            kind=self.kind,
            positions=self.positions,
            score=self.stage_score(stage),
            scores=HitScores(
                vector=self.vector_score,
                keyword=self.keyword_score,
                fusion=self.fusion_score,
                rerank=self.rerank_score,
            ),
            page_no=self.page_no,
            edited=self.edited,
        )


@dataclass
class RecallResult:
    """“召回并融合”的结果，交给 `rerank_and_expand`。"""

    vector: list[Candidate]
    fulltext: list[Candidate]
    fusion: list[Candidate]
    config: RetrievalConfig
    vector_cache: dict[int, list[float]]
    timings: dict[str, int]


@dataclass
class RerankResult:
    """“重排与扩展”的结果。"""

    rerank: list[Candidate]
    final: list[Candidate]
    timings: dict[str, int]


def effective_config(kb: KnowledgeBase) -> RetrievalConfig:
    """知识库默认检索配置：库里的 JSON 覆盖契约默认值。"""
    return RetrievalConfig.model_validate(kb.retrieval_config or {})


async def get_config(session: AsyncSession, ctx: Ctx, kb_id: int) -> RetrievalConfig:
    """查看检索配置（资源“查看”权限）。"""
    await access.get_for(session, ctx, kb_id, access.VIEW, type="kb")
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError()
    return effective_config(kb)


async def save_config(
    session: AsyncSession, ctx: Ctx, kb_id: int, config: RetrievalConfig
) -> RetrievalConfig:
    """保存检索配置（资源“编辑”权限）；配置不写审计（知识库技术方案 17）。"""
    await access.get_for(session, ctx, kb_id, access.EDIT, type="kb")
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError()
    await session.execute(
        sa.update(KnowledgeBase)
        .where(KnowledgeBase.id == kb_id)
        .values(
            retrieval_config=config.model_dump(mode="json"),
            updated_at=sa.func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return config


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def _vector_literal(vector: Sequence[float]) -> str:
    """pgvector 参数用字符串字面量传（列类型决定 vector / halfvec）。"""
    return "[" + ",".join(str(float(value)) for value in vector) + "]"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _page_no(positions: list | None) -> int | None:
    for item in positions or []:
        if isinstance(item, dict) and item.get("page") is not None:
            return int(item["page"])
    return None


def _model_available(model: Model) -> bool:
    return model.deleted_at is None and model.status == ModelStatus.enabled.value


# ---- 过滤条件：元数据与文件夹（12.3）----

_NUMERIC_OPS = {
    MetadataOperator.eq: "=",
    MetadataOperator.ne: "<>",
    MetadataOperator.gt: ">",
    MetadataOperator.gte: ">=",
    MetadataOperator.lt: "<",
    MetadataOperator.lte: "<=",
}


def _coerce(field: KbMetadataField, value: object) -> object:
    """按字段类型转换过滤值（前端统一按字符串提交，P3-17）。"""
    if value is None:
        return None
    if field.type == MetadataFieldType.number.value:
        if isinstance(value, bool):
            raise MetadataValueInvalidError(detail={"field": field.name, "reason": "不是数字"})
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise MetadataValueInvalidError(
                detail={"field": field.name, "reason": "不是数字"}
            ) from exc
    if field.type == MetadataFieldType.date.value:
        if not isinstance(value, str):
            raise MetadataValueInvalidError(detail={"field": field.name, "reason": "不是日期"})
        return value
    return str(value)


def _leaf_clause(field: KbMetadataField, cond: MetadataFilter, index: int) -> tuple[str, dict]:
    name_param = f"mf{index}"
    params: dict = {name_param: field.name}
    base = f"(d.metadata ->> :{name_param})"
    is_number = field.type == MetadataFieldType.number.value
    column = f"({base})::numeric" if is_number else base

    if cond.operator == MetadataOperator.in_:
        raw = cond.value if isinstance(cond.value, list) else [cond.value]
        values = [_coerce(field, value) for value in raw]
        if not values:
            return "false", params
        placeholders = []
        for offset, value in enumerate(values):
            key = f"mv{index}_{offset}"
            params[key] = value
            placeholders.append(f":{key}")
        return f"{column} IN ({', '.join(placeholders)})", params

    if cond.operator == MetadataOperator.contains:
        key = f"mv{index}"
        params[key] = f"%{_escape_like(str(cond.value))}%"
        return f"{base} LIKE :{key} ESCAPE '\\'", params

    value = _coerce(field, cond.value)
    if value is None:
        # 值缺失：eq 表示文档没有这个字段，其余条件不匹配。
        return (f"{base} IS NULL" if cond.operator == MetadataOperator.eq else "false"), params
    key = f"mv{index}"
    params[key] = value
    return f"{column} {_NUMERIC_OPS[cond.operator]} :{key}", params


async def _metadata_clause(
    session: AsyncSession, kb_id: int, group: MetadataGroup
) -> tuple[str, dict]:
    fields = {
        field.id: field
        for field in (
            await session.execute(sa.select(KbMetadataField).where(KbMetadataField.kb_id == kb_id))
        )
        .scalars()
        .all()
    }
    parts: list[str] = []
    params: dict = {}
    for index, cond in enumerate(group.conditions):
        field = fields.get(cond.field_id)
        if field is None:
            raise ValidationError("元数据字段不存在", {"field_id": cond.field_id})
        fragment, leaf_params = _leaf_clause(field, cond, index)
        parts.append(fragment)
        params.update(leaf_params)
    joiner = " AND " if group.op == "and" else " OR "
    return "(" + joiner.join(parts) + ")", params


async def _build_doc_filter(
    session: AsyncSession,
    *,
    kb_id: int,
    folder_id: int | None,
    metadata: MetadataGroup | None,
) -> tuple[str, dict, bool]:
    """返回（文档过滤 SQL、绑定参数、是否有元数据/文件夹过滤）。"""
    clauses = ["d.kb_id = :kb_id"]
    params: dict = {"kb_id": kb_id}
    has_filter = False

    if folder_id is not None:
        folder = await session.get(KbFolder, folder_id)
        if folder is None or folder.kb_id != kb_id:
            raise NotFoundError()
        clauses.append(
            "d.folder_id IN (SELECT id FROM public.kb_folders "
            "WHERE kb_id = :kb_id AND path LIKE :folder_path ESCAPE '\\')"
        )
        params["folder_path"] = _escape_like(folder.path) + "%"
        has_filter = True

    if metadata is not None:
        fragment, meta_params = await _metadata_clause(session, kb_id, metadata)
        clauses.append(fragment)
        params.update(meta_params)
        has_filter = True

    return " AND ".join(clauses), params, has_filter


# ---- 召回与融合 ----


@dataclass
class _KbRecall:
    vector: list[Candidate]
    fulltext: list[Candidate]
    fused: list[Candidate]
    vector_ms: int
    fulltext_ms: int
    fusion_ms: int


def _candidate_from_row(row, *, kb_id: int) -> Candidate:
    positions = list(row.positions or [])
    return Candidate(
        kb_id=kb_id,
        chunk_id=int(row.chunk_id),
        doc_id=int(row.doc_id),
        doc_name=row.doc_name,
        content=row.content,
        title_path=row.title_path,
        kind=row.kind,
        positions=positions,
        edited=bool(row.edited),
        page_no=_page_no(positions),
    )


async def _load_chunk_info(session: AsyncSession, chunk_ids: Sequence[int]) -> dict[int, object]:
    if not chunk_ids:
        return {}
    stmt = text(
        "SELECT c.id AS chunk_id, c.doc_id, c.content, c.title_path, c.kind, "
        "c.positions, c.edited, d.name AS doc_name "
        "FROM public.chunks c JOIN public.documents d ON d.id = c.doc_id "
        "WHERE c.id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    rows = await session.execute(stmt, {"ids": list(chunk_ids)})
    return {int(row.chunk_id): row for row in rows}


async def _vector_scores(
    session: AsyncSession,
    *,
    kb: KnowledgeBase,
    query_vector: Sequence[float],
    chunk_ids: Sequence[int],
) -> dict[int, float]:
    if not chunk_ids:
        return {}
    table = index_table_name(kb.id)
    cast = "halfvec" if kb.embedding_dim > 2000 else "vector"
    stmt = text(
        f"SELECT i.chunk_id, 1 - (i.embedding <=> :vec ::{cast}) AS score "
        f"FROM {table} i "
        "WHERE i.active AND i.entry_type = 'chunk' AND i.chunk_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    rows = await session.execute(
        stmt, {"vec": _vector_literal(query_vector), "ids": list(chunk_ids)}
    )
    scores: dict[int, float] = {}
    for row in rows:
        chunk_id = int(row.chunk_id)
        score = float(row.score)
        if score > scores.get(chunk_id, float("-inf")):
            scores[chunk_id] = score
    return scores


async def _keyword_scores(
    session: AsyncSession,
    *,
    kb_id: int,
    terms: QueryTerms,
    chunk_ids: Sequence[int],
) -> dict[int, float]:
    if not chunk_ids or not terms.terms:
        return {}
    table = index_table_name(kb_id)
    values = ", ".join(f"(:kt{i} ::text, :kw{i} ::float8)" for i in range(len(terms.terms)))
    stmt = text(
        "SELECT i.chunk_id, "
        "COALESCE(SUM(CASE WHEN strpos(i.title, t.term) > 0 "
        "OR strpos(i.body, t.term) > 0 OR strpos(i.keywords, t.term) > 0 "
        "THEN t.weight ELSE 0 END), 0) / :total AS score "
        f"FROM {table} i "
        f"CROSS JOIN (VALUES {values}) AS t(term, weight) "
        "WHERE i.active AND i.entry_type = 'chunk' AND i.chunk_id IN :ids "
        "GROUP BY i.chunk_id"
    ).bindparams(bindparam("ids", expanding=True))
    params = terms.as_params("k")
    params["total"] = terms.total_weight or 1.0
    params["ids"] = list(chunk_ids)
    rows = await session.execute(stmt, params)
    return {int(row.chunk_id): float(row.score) for row in rows}


def _dedupe(rows: Sequence[tuple[int, float]]) -> list[tuple[int, float]]:
    """同一分块保留最高分（12.1 第 5 步）。"""
    best: dict[int, float] = {}
    for chunk_id, score in rows:
        if score > best.get(chunk_id, float("-inf")):
            best[chunk_id] = score
    return sorted(best.items(), key=lambda item: item[1], reverse=True)


async def _vector_recall(
    session: AsyncSession,
    *,
    kb: KnowledgeBase,
    query_vector: Sequence[float],
    doc_filter: str,
    params: dict,
    limit: int,
    has_filter: bool,
) -> list[tuple[int, float]]:
    table = index_table_name(kb.id)
    cast = "halfvec" if kb.embedding_dim > 2000 else "vector"
    # 召回数的 2 倍以上；有过滤时开迭代扫描，避免过滤后结果不足（11.3）。
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {int(limit) * 2}"))
    if has_filter:
        await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    stmt = text(
        f"SELECT i.chunk_id, 1 - (i.embedding <=> :vec ::{cast}) AS score "
        f"FROM {table} i "
        "JOIN public.chunks c ON c.id = i.chunk_id "
        "JOIN public.documents d ON d.id = i.doc_id "
        f"WHERE i.active AND i.entry_type = 'chunk' AND ({doc_filter}) "
        f"ORDER BY i.embedding <=> :vec ::{cast} LIMIT :limit"
    )
    rows = await session.execute(
        stmt, {**params, "vec": _vector_literal(query_vector), "limit": limit}
    )
    return [(int(row.chunk_id), float(row.score)) for row in rows]


async def _fulltext_recall(
    session: AsyncSession,
    *,
    kb_id: int,
    question: str,
    doc_filter: str,
    params: dict,
    limit: int,
) -> list[tuple[int, float]]:
    table = index_table_name(kb_id)
    stmt = text(
        f"SELECT i.chunk_id, pdb.score(i.id) AS score "
        f"FROM {table} i "
        "JOIN public.chunks c ON c.id = i.chunk_id "
        "JOIN public.documents d ON d.id = i.doc_id "
        f"WHERE i.active AND i.entry_type = 'chunk' AND ({doc_filter}) "
        f"AND ({FULLTEXT_CONDITION}) "
        "ORDER BY pdb.score(i.id) DESC LIMIT :limit"
    )
    rows = await session.execute(stmt, {**params, "q": question, "limit": limit})
    return [(int(row.chunk_id), float(row.score)) for row in rows]


def _fusion_score(candidate: Candidate, config: RetrievalConfig) -> float:
    vector = candidate.vector_score or 0.0
    keyword = candidate.keyword_score or 0.0
    if config.mode == RetrievalMode.vector:
        return vector
    if config.mode == RetrievalMode.fulltext:
        return keyword
    return config.vector_weight * vector + config.fulltext_weight * keyword


def _sort_fused(candidates: list[Candidate], config: RetrievalConfig) -> list[Candidate]:
    if config.mode == RetrievalMode.fulltext:
        return sorted(candidates, key=lambda c: c.bm25_score or 0.0, reverse=True)
    return sorted(candidates, key=lambda c: c.fusion_score or 0.0, reverse=True)


async def _recall_one(
    sessionmaker: SessionMaker,
    *,
    kb: KnowledgeBase,
    model: Model,
    config: RetrievalConfig,
    question: str,
    terms: QueryTerms,
    query_vector: Sequence[float] | None,
    folder_id: int | None,
    metadata: MetadataGroup | None,
) -> _KbRecall:
    limit = max(1, config.top_k) * RECALL_MULTIPLIER
    async with sessionmaker() as session:
        doc_filter, params, has_filter = await _build_doc_filter(
            session, kb_id=kb.id, folder_id=folder_id, metadata=metadata
        )
        vector_rows: list[tuple[int, float]] = []
        vector_ms = 0
        if config.mode in (RetrievalMode.vector, RetrievalMode.hybrid):
            assert query_vector is not None
            started = monotonic()
            vector_rows = await _vector_recall(
                session,
                kb=kb,
                query_vector=query_vector,
                doc_filter=doc_filter,
                params=params,
                limit=limit,
                has_filter=has_filter,
            )
            vector_ms = _ms(monotonic() - started)

        fulltext_rows: list[tuple[int, float]] = []
        fulltext_ms = 0
        if config.mode in (RetrievalMode.fulltext, RetrievalMode.hybrid):
            started = monotonic()
            fulltext_rows = await _fulltext_recall(
                session,
                kb_id=kb.id,
                question=question,
                doc_filter=doc_filter,
                params=params,
                limit=limit,
            )
            fulltext_ms = _ms(monotonic() - started)

        started = monotonic()
        vector_deduped = _dedupe(vector_rows)[: config.top_k]
        fulltext_deduped = _dedupe(fulltext_rows)[: config.top_k]
        chunk_ids = list({chunk_id for chunk_id, _ in vector_deduped + fulltext_deduped})
        info = await _load_chunk_info(session, chunk_ids)

        vector_scores = {chunk_id: score for chunk_id, score in vector_deduped}
        needs_vector_score = config.mode in (
            RetrievalMode.fulltext,
            RetrievalMode.hybrid,
        )
        if needs_vector_score and query_vector is not None:
            missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in vector_scores]
            vector_scores.update(
                await _vector_scores(session, kb=kb, query_vector=query_vector, chunk_ids=missing)
            )
        keyword_scores = await _keyword_scores(
            session, kb_id=kb.id, terms=terms, chunk_ids=chunk_ids
        )
        bm25_scores = {chunk_id: score for chunk_id, score in fulltext_deduped}

        def build(chunk_id: int) -> Candidate | None:
            row = info.get(chunk_id)
            if row is None:
                return None
            candidate = _candidate_from_row(row, kb_id=kb.id)
            candidate.vector_score = vector_scores.get(chunk_id)
            candidate.keyword_score = keyword_scores.get(chunk_id)
            candidate.bm25_score = bm25_scores.get(chunk_id)
            candidate.fusion_score = _fusion_score(candidate, config)
            return candidate

        vector_hits = [c for chunk_id, _ in vector_deduped if (c := build(chunk_id)) is not None]
        fulltext_hits = [
            c for chunk_id, _ in fulltext_deduped if (c := build(chunk_id)) is not None
        ]
        merged: dict[int, Candidate] = {}
        for candidate in vector_hits + fulltext_hits:
            merged.setdefault(candidate.chunk_id, candidate)
        # 父子分块替换（子块→父块、同父只保留一个）第五期实现；本卡分块都是普通分块，
        # 这里直接跳过。
        fused = _sort_fused(list(merged.values()), config)
        fusion_ms = _ms(monotonic() - started)

    return _KbRecall(
        vector=vector_hits,
        fulltext=fulltext_hits,
        fused=fused,
        vector_ms=vector_ms,
        fulltext_ms=fulltext_ms,
        fusion_ms=fusion_ms,
    )


async def _load_kbs(sessionmaker: SessionMaker, kb_ids: Sequence[int]) -> list[tuple]:
    async with sessionmaker() as session:
        rows = await session.execute(
            sa.select(KnowledgeBase, Resource, Model)
            .join(Resource, Resource.id == KnowledgeBase.id)
            .join(Model, Model.id == KnowledgeBase.embedding_model_id)
            .where(KnowledgeBase.id.in_(list(dict.fromkeys(kb_ids))))
        )
        return list(rows.all())


async def recall_and_fuse(
    sessionmaker: SessionMaker,
    redis: Redis,
    *,
    kb_ids: Sequence[int],
    question: str,
    config: RetrievalConfig | None = None,
    folder_id: int | None = None,
    metadata: MetadataGroup | None = None,
    user_id: int | None = None,
    org_id: int | None = None,
    vector_cache: dict[int, list[float]] | None = None,
) -> RecallResult:
    """召回并融合（12.1 的 1–6 步）。停用的知识库跳过；没有可检索的库返回空结果。"""
    vector_cache = vector_cache if vector_cache is not None else {}
    rows = await _load_kbs(sessionmaker, kb_ids)
    active = [(kb, resource, model) for kb, resource, model in rows if resource.status == "enabled"]
    if not active:
        return RecallResult(
            vector=[],
            fulltext=[],
            fusion=[],
            config=config or RetrievalConfig(),
            vector_cache=vector_cache,
            timings={"vector_ms": 0, "fulltext_ms": 0, "fusion_ms": 0},
        )

    configs = {kb.id: (config or effective_config(kb)) for kb, _, _ in active}
    needs_vector = {
        kb.id
        for kb, _, _ in active
        if configs[kb.id].mode in (RetrievalMode.vector, RetrievalMode.hybrid)
    }
    for kb, _, model in active:
        if kb.id in needs_vector and not _model_available(model):
            raise EmbeddingModelUnavailableError()

    async with sessionmaker() as session:
        terms = await build_query_terms(session, question)

    # 同一向量化模型一次请求内只调一次；调用方传入的缓存与问答库共用（12.1）。
    models_by_id = {model.id: model for _, _, model in active}
    pending = [
        model_id
        for model_id in models_by_id
        if model_id not in vector_cache
        and any(kb.embedding_model_id == model_id and kb.id in needs_vector for kb, _, _ in active)
    ]
    if pending:
        results = await asyncio.gather(
            *(
                gateway.embed(
                    sessionmaker,
                    redis,
                    CallMeta(source="kb_hit_test", source_id=None, user_id=user_id, org_id=org_id),
                    model_id,
                    [question],
                )
                for model_id in pending
            )
        )
        for model_id, result in zip(pending, results, strict=True):
            vector_cache[model_id] = result.vectors[0]

    recalls = await asyncio.gather(
        *(
            _recall_one(
                sessionmaker,
                kb=kb,
                model=model,
                config=configs[kb.id],
                question=question,
                terms=terms,
                query_vector=vector_cache.get(model.id) if kb.id in needs_vector else None,
                folder_id=folder_id,
                metadata=metadata,
            )
            for kb, _, model in active
        )
    )

    vector_hits: list[Candidate] = []
    fulltext_hits: list[Candidate] = []
    fused: list[Candidate] = []
    for recall in recalls:
        vector_hits.extend(recall.vector)
        fulltext_hits.extend(recall.fulltext)
        fused.extend(recall.fused)
    # 多库合并时按融合分排序；单库时顺序已定。
    fused.sort(key=lambda c: c.fusion_score or 0.0, reverse=True)
    vector_hits.sort(key=lambda c: c.vector_score or 0.0, reverse=True)
    fulltext_hits.sort(key=lambda c: c.bm25_score or 0.0, reverse=True)

    return RecallResult(
        vector=vector_hits,
        fulltext=fulltext_hits,
        fusion=fused,
        config=configs[active[0][0].id],
        vector_cache=vector_cache,
        timings={
            "vector_ms": sum(recall.vector_ms for recall in recalls),
            "fulltext_ms": sum(recall.fulltext_ms for recall in recalls),
            "fusion_ms": sum(recall.fusion_ms for recall in recalls),
        },
    )


def _rerank_text(candidate: Candidate) -> str:
    return (
        f"{candidate.title_path}\n{candidate.content}"
        if candidate.title_path
        else candidate.content
    )


def _threshold_final(recall: RecallResult, config: RetrievalConfig) -> list[Candidate]:
    """未开启重排：阈值作用于融合分（只用全文时作用于关键词分），取前 Top K。"""
    final: list[Candidate] = []
    for candidate in recall.fusion:
        score = (
            candidate.keyword_score or 0.0
            if config.mode == RetrievalMode.fulltext
            else candidate.fusion_score or 0.0
        )
        if score >= config.score_threshold:
            final.append(candidate)
        if len(final) >= config.top_k:
            break
    return final


async def rerank_and_expand(
    sessionmaker: SessionMaker,
    redis: Redis,
    *,
    question: str,
    recall: RecallResult,
    config: RetrievalConfig,
    user_id: int | None = None,
    org_id: int | None = None,
) -> RerankResult:
    """阈值与重排（12.1 的 7–9 步）；相邻分块扩展第五期实现，本卡不做。"""
    if not recall.fusion:
        return RerankResult(rerank=[], final=[], timings={"rerank_ms": 0})

    if not config.rerank.enabled:
        return RerankResult(
            rerank=[], final=_threshold_final(recall, config), timings={"rerank_ms": 0}
        )

    if config.rerank.model_id is None:
        raise ValidationError("请选择重排模型", {"field": "rerank.model_id"})

    candidates = recall.fusion[: config.top_k]
    started = monotonic()
    result = await gateway.rerank(
        sessionmaker,
        redis,
        CallMeta(source="kb_hit_test", source_id=None, user_id=user_id, org_id=org_id),
        config.rerank.model_id,
        question,
        [_rerank_text(candidate) for candidate in candidates],
        top_n=config.rerank.top_n,
    )
    rerank_ms = _ms(monotonic() - started)

    reranked: list[Candidate] = []
    for item in result.results:
        if 0 <= item.index < len(candidates):
            candidate = candidates[item.index]
            candidate.rerank_score = float(item.score)
            reranked.append(candidate)
    reranked.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)
    final = [
        candidate
        for candidate in reranked
        if (candidate.rerank_score or 0.0) >= config.score_threshold
    ]
    return RerankResult(rerank=reranked, final=final, timings={"rerank_ms": rerank_ms})


def build_hit_test_out(recall: RecallResult, rerank: RerankResult) -> HitTestOut:
    return HitTestOut(
        steps=HitSteps(
            vector=[candidate.to_hit_item("vector") for candidate in recall.vector],
            fulltext=[candidate.to_hit_item("fulltext") for candidate in recall.fulltext],
            fusion=[candidate.to_hit_item("fusion") for candidate in recall.fusion],
            rerank=[candidate.to_hit_item("rerank") for candidate in rerank.rerank],
        ),
        final=[candidate.to_hit_item("final") for candidate in rerank.final],
        timings=HitTimings(
            vector_ms=recall.timings.get("vector_ms", 0),
            fulltext_ms=recall.timings.get("fulltext_ms", 0),
            fusion_ms=recall.timings.get("fusion_ms", 0),
            rerank_ms=rerank.timings.get("rerank_ms", 0),
        ),
    )


async def run_hit_test(
    sessionmaker: SessionMaker,
    redis: Redis,
    ctx: Ctx,
    kb_id: int,
    body: HitTestIn,
) -> HitTestOut:
    """命中测试：连续调用两段检索；记录不保存，用量来源 `kb_hit_test`（17）。"""
    recall = await recall_and_fuse(
        sessionmaker,
        redis,
        kb_ids=[kb_id],
        question=body.question,
        config=body.config,
        folder_id=body.folder_id,
        metadata=body.metadata,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
    )
    rerank = await rerank_and_expand(
        sessionmaker,
        redis,
        question=body.question,
        recall=recall,
        config=recall.config,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
    )
    return build_hit_test_out(recall, rerank)

"""发布校验（应用技术方案 5.3）与保存草稿时的“重排要求”检查。

逐项检查，问题清单每项 `PublishProblemOut(code, message, field?)`。保存草稿只做格式校验
和重排要求检查（需求 5.1.3），允许不完整配置；发布再做全部检查。已停用的知识库允许发布
（检索时跳过，需求 5.1.3）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import AppConfig, AppKbFilter, KbQaAppConfig
from app.apps.refs import app_can_reference_kb
from app.apps.schemas import PublishProblemOut
from app.apps.tables import App
from app.core.errors import ValidationError
from app.kb.tables import KbFolder, KbMetadataField, KnowledgeBase
from app.models.schemas import ModelStatus
from app.models.tables import Model

KB_TYPE = "kb"
DOC_RERANK_REQUIRED = "DOC_RERANK_REQUIRED"
QA_RERANK_REQUIRED = "QA_RERANK_REQUIRED"


def parse_config(app_type: str, raw: AppConfig | dict | None) -> KbQaAppConfig:
    """把草稿 / 版本里的配置解析成 `AppConfig`；容错 `{}`，用应用的 `app_type` 补齐判别字段。"""
    if isinstance(raw, KbQaAppConfig):
        return raw
    data = dict(raw or {})
    data.setdefault("app_type", app_type)
    return KbQaAppConfig.model_validate(data)


def _problem(code: str, message: str, field: str | None = None) -> PublishProblemOut:
    return PublishProblemOut(code=code, message=message, field=field)


async def _referenced_kbs(
    session: AsyncSession, config: KbQaAppConfig
) -> dict[int, KnowledgeBase | None]:
    """引用的知识库（按 kb_id），不存在的映射为 None。"""
    kb_ids = [ref.kb_id for ref in config.knowledge.kbs]
    if not kb_ids:
        return {}
    rows = (
        (await session.execute(sa.select(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids))))
        .scalars()
        .all()
    )
    found = {kb.id: kb for kb in rows}
    return {kb_id: found.get(kb_id) for kb_id in kb_ids}


def _distinct_embedding_models(
    config: KbQaAppConfig, kbs: dict[int, KnowledgeBase | None], kind: str
) -> set[int]:
    models: set[int] = set()
    for ref in config.knowledge.kbs:
        kb = kbs.get(ref.kb_id)
        if kb is not None and kb.kind == kind:
            models.add(kb.embedding_model_id)
    return models


async def check_rerank(session: AsyncSession, config: AppConfig) -> list[PublishProblemOut]:
    """第 4 条：所选文档库 / 问答库使用了不同的向量化模型时必须开启对应重排。"""
    kbs = await _referenced_kbs(session, config)
    problems: list[PublishProblemOut] = []
    if len(_distinct_embedding_models(config, kbs, "doc")) > 1 and not (
        config.knowledge.doc.rerank.enabled
    ):
        problems.append(
            _problem(
                DOC_RERANK_REQUIRED,
                "所选文档库使用了不同的向量化模型，必须开启文档库重排",
                "knowledge.doc.rerank.enabled",
            )
        )
    if len(_distinct_embedding_models(config, kbs, "qa")) > 1 and not (
        config.knowledge.qa.rerank.enabled
    ):
        problems.append(
            _problem(
                QA_RERANK_REQUIRED,
                "所选问答库使用了不同的向量化模型，必须开启问答库重排",
                "knowledge.qa.rerank.enabled",
            )
        )
    return problems


def raise_if_rerank_problems(problems: list[PublishProblemOut]) -> None:
    """保存草稿时把重排问题按 `VALIDATION_ERROR` 抛出（草稿保存不是发布）。"""
    if problems:
        raise ValidationError(detail={"problems": [p.model_dump() for p in problems]})


async def _model_problems(session: AsyncSession, config: KbQaAppConfig) -> list[PublishProblemOut]:
    """第 1 条：回答模型必选且启用；单独选的意图 / 改写 / 重排模型同样要求存在且启用。"""
    problems: list[PublishProblemOut] = []
    chat_model_id = config.model.chat_model_id if config.model is not None else None
    if chat_model_id is None:
        problems.append(_problem("MODEL_REQUIRED", "请选择回答模型", "model.chat_model_id"))
    else:
        await _check_model(session, problems, chat_model_id, "model.chat_model_id")

    slots: list[tuple[str, int | None]] = [
        ("intent.model_id", config.intent.model_id),
        ("rewrite.model_id", config.rewrite.model_id),
        ("knowledge.doc.rerank.model_id", config.knowledge.doc.rerank.model_id),
        ("knowledge.qa.rerank.model_id", config.knowledge.qa.rerank.model_id),
    ]
    for field, model_id in slots:
        if model_id is not None:
            await _check_model(session, problems, model_id, field)
    return problems


async def _check_model(
    session: AsyncSession, problems: list[PublishProblemOut], model_id: int, field: str
) -> None:
    model = await session.get(Model, model_id)
    if model is None or model.deleted_at is not None or model.status != ModelStatus.enabled.value:
        problems.append(_problem("MODEL_UNAVAILABLE", "模型不存在或已停用", field))


async def _kb_problems(
    session: AsyncSession, app: App, config: KbQaAppConfig, kbs: dict[int, KnowledgeBase | None]
) -> list[PublishProblemOut]:
    """第 2、3 条：至少引用一个知识库；每个引用的知识库存在且应用仍能引用。"""
    problems: list[PublishProblemOut] = []
    if not config.knowledge.kbs:
        problems.append(_problem("KB_REQUIRED", "请至少引用一个知识库", "knowledge.kbs"))
    for index, ref in enumerate(config.knowledge.kbs):
        field = f"knowledge.kbs[{index}].kb_id"
        kb = kbs.get(ref.kb_id)
        if kb is None:
            problems.append(_problem("KB_NOT_FOUND", "引用的知识库不存在", field))
            continue
        if not await app_can_reference_kb(session, app, kb):
            problems.append(_problem("KB_NOT_REFERENCEABLE", "应用不能引用该知识库", field))
    return problems


async def _filter_problems(
    session: AsyncSession, config: KbQaAppConfig, kbs: dict[int, KnowledgeBase | None]
) -> list[PublishProblemOut]:
    """第 5 条：过滤条件引用的文件夹、元数据字段仍然存在（问答库分类第六期不校验）。"""
    problems: list[PublishProblemOut] = []
    for index, ref in enumerate(config.knowledge.kbs):
        filter_config: AppKbFilter | None = ref.filter
        if filter_config is None:
            continue
        field = f"knowledge.kbs[{index}].filter"
        if filter_config.folder_ids:
            existing = set(
                (
                    await session.execute(
                        sa.select(KbFolder.id).where(
                            KbFolder.kb_id == ref.kb_id,
                            KbFolder.id.in_(filter_config.folder_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for folder_id in filter_config.folder_ids:
                if folder_id not in existing:
                    problems.append(
                        _problem(
                            "FOLDER_NOT_FOUND",
                            "过滤条件引用的文件夹不存在",
                            f"{field}.folder_ids",
                        )
                    )
                    break
        if filter_config.metadata is not None:
            field_ids = [condition.field_id for condition in filter_config.metadata.conditions]
            existing_fields = set(
                (
                    await session.execute(
                        sa.select(KbMetadataField.id).where(
                            KbMetadataField.kb_id == ref.kb_id,
                            KbMetadataField.id.in_(field_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for field_id in field_ids:
                if field_id not in existing_fields:
                    problems.append(
                        _problem(
                            "METADATA_FIELD_NOT_FOUND",
                            "过滤条件引用的元数据字段不存在",
                            f"{field}.metadata",
                        )
                    )
                    break
    return problems


async def validate_publish(session: AsyncSession, app: App) -> list[PublishProblemOut]:
    """发布前的全部校验；返回空列表表示可以发布。"""
    config = parse_config(app.app_type, app.draft_config)
    kbs = await _referenced_kbs(session, config)
    problems: list[PublishProblemOut] = []
    problems.extend(await _model_problems(session, config))
    problems.extend(await _kb_problems(session, app, config, kbs))
    problems.extend(await check_rerank(session, config))
    problems.extend(await _filter_problems(session, config, kbs))
    return problems

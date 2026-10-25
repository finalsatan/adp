"""知识库管理业务逻辑（P3-04）。

路由只做参数接收、权限依赖、调用这里、写审计。权限一律复用
`app/resources/access.py`（P1-12），列表过滤拼进 SQL 的 WHERE。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    EmbeddingModelUnavailableError,
    KbEmbeddingModelImmutableError,
    KbKindImmutableError,
    NameConflictError,
    NotFoundError,
)
from app.core.schemas import (
    OrgBrief,
    Page,
    Pagination,
    ResourceStatus,
    UserBrief,
    Visibility,
)
from app.iam.context import Ctx
from app.iam.tables import Org, User
from app.kb.index_table import (
    INDEX_SCHEMA_VERSION,
    create_index_table,
    index_table_name,
)
from app.kb.schemas import KbCreate, KbKind, KbModelOut, KbOut, KbRefOut, KbUpdate
from app.kb.tables import KnowledgeBase
from app.models.schemas import ModelStatus
from app.models.tables import Model
from app.resources import access, registry
from app.resources.schemas import MemberOut
from app.resources.service import (
    add_member,
    change_visibility,
    create_resource,
    delete_resource,
    list_members,
    remove_member,
    set_status,
)
from app.resources.tables import Resource, ResourceMember, ResourceRef

logger = logging.getLogger(__name__)

KB_TYPE = "kb"
EMBEDDING_MODEL_TYPE = "embedding"


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _now() -> datetime:
    return datetime.now(UTC)


def _type_label(code: str) -> str:
    resource_type = registry.get_resource_type(code)
    return resource_type.label if resource_type is not None else code


def _model_out(model: Model, kb: KnowledgeBase) -> KbModelOut:
    return KbModelOut(
        id=model.id,
        name=model.name,
        embedding_dim=model.embedding_dim or kb.embedding_dim,
        embedding_max_input=model.embedding_max_input or 0,
        status=ModelStatus(model.status),
    )


def _build_out(
    resource: Resource,
    org: Org,
    owner: User,
    kb: KnowledgeBase,
    model: Model,
    editable: bool,
    manageable: bool,
) -> KbOut:
    return KbOut(
        id=resource.id,
        name=resource.name,
        description=resource.description,
        kind=KbKind(kb.kind),
        org=OrgBrief(id=org.id, name=org.name),
        owner=UserBrief(id=owner.id, username=owner.username, name=owner.name),
        visibility=Visibility(resource.visibility),
        status=ResourceStatus(resource.status),
        embedding_model=_model_out(model, kb),
        doc_count=kb.doc_count,
        chunk_count=kb.chunk_count,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        editable=editable,
        manageable=manageable,
    )


def _flags(
    ctx: Ctx, resource: Resource, org_path: str | None, is_member: bool
) -> tuple[bool, bool]:
    """`editable` / `manageable`：复用 access 的同一套规则，与 `get_for` 结论一致。"""
    return (
        access._can(ctx, access.EDIT, resource, org_path, is_member),
        access._can(ctx, access.MANAGE, resource, org_path, is_member),
    )


async def _is_member(session: AsyncSession, ctx: Ctx, resource_id: int) -> bool:
    return (
        await session.scalar(
            sa.select(ResourceMember.resource_id).where(
                ResourceMember.resource_id == resource_id,
                ResourceMember.user_id == ctx.user_id,
            )
        )
        is not None
    )


async def _require_embedding_model(session: AsyncSession, model_id: int) -> Model:
    model = await session.get(Model, model_id)
    if (
        model is None
        or model.deleted_at is not None
        or model.status != ModelStatus.enabled.value
        or model.model_type != EMBEDDING_MODEL_TYPE
        or model.embedding_dim is None
    ):
        raise EmbeddingModelUnavailableError()
    return model


async def list_kbs(
    session: AsyncSession,
    ctx: Ctx,
    pagination: Pagination,
    kind: KbKind | None,
    visibility: Visibility | None,
    org_id: int | None,
    include_sub: bool,
    status: ResourceStatus | None,
    keyword: str | None,
) -> Page[KbOut]:
    """我能查看的知识库列表；权限条件拼进 WHERE，按创建时间倒序。"""
    stmt = (
        sa.select(Resource, Org, User, KnowledgeBase, Model)
        .join(Org, Resource.org_id == Org.id)
        .join(KnowledgeBase, KnowledgeBase.id == Resource.id)
        .join(User, Resource.owner_id == User.id)
        .join(Model, KnowledgeBase.embedding_model_id == Model.id)
        .where(
            Resource.type == KB_TYPE,
            Resource.deleted_at.is_(None),
            access.access_clause(ctx, access.VIEW),
        )
    )

    if kind is not None:
        stmt = stmt.where(KnowledgeBase.kind == kind.value)
    if visibility is not None:
        stmt = stmt.where(Resource.visibility == visibility.value)
    if org_id is not None:
        org = await session.get(Org, org_id)
        if org is None or org.deleted_at is not None:
            raise NotFoundError()
        if include_sub:
            stmt = stmt.where(Org.path.like(f"{_escape_like(org.path)}%", escape="\\"))
        else:
            stmt = stmt.where(Resource.org_id == org.id)
    if status is not None:
        stmt = stmt.where(Resource.status == status.value)
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        stmt = stmt.where(Resource.name.ilike(pattern, escape="\\"))

    total = int(await session.scalar(sa.select(sa.func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await session.execute(
            stmt.order_by(Resource.created_at.desc(), Resource.id.desc())
            .offset((pagination.page - 1) * pagination.page_size)
            .limit(pagination.page_size)
        )
    ).all()

    ids = [resource.id for resource, *_ in rows]
    members: set[int] = set()
    if ids:
        members = set(
            (
                await session.execute(
                    sa.select(ResourceMember.resource_id).where(
                        ResourceMember.resource_id.in_(ids),
                        ResourceMember.user_id == ctx.user_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    items = []
    for resource, org, owner, kb, model in rows:
        editable, manageable = _flags(ctx, resource, org.path, resource.id in members)
        items.append(_build_out(resource, org, owner, kb, model, editable, manageable))
    return Page(items=items, total=total)


async def kb_out(session: AsyncSession, ctx: Ctx, kb: KnowledgeBase) -> KbOut:
    """拼单个知识库的返回结构。"""
    resource = await session.get(Resource, kb.id)
    if resource is None or resource.deleted_at is not None:
        raise NotFoundError()
    org = await session.get(Org, resource.org_id)
    owner = await session.get(User, resource.owner_id)
    model = await session.get(Model, kb.embedding_model_id)
    assert org is not None and owner is not None and model is not None
    editable, manageable = _flags(ctx, resource, org.path, await _is_member(session, ctx, kb.id))
    return _build_out(resource, org, owner, kb, model, editable, manageable)


async def get_kb(session: AsyncSession, ctx: Ctx, kb_id: int) -> KbOut:
    await access.get_for(session, ctx, kb_id, access.VIEW, type=KB_TYPE)
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError()
    return await kb_out(session, ctx, kb)


async def create_kb(session: AsyncSession, ctx: Ctx, body: KbCreate) -> KbOut:
    model = await _require_embedding_model(session, body.embedding_model_id)
    assert model.embedding_dim is not None
    resource = await create_resource(
        session, ctx, KB_TYPE, body.name, body.visibility, body.description, body.org_id
    )
    try:
        kb = KnowledgeBase(
            id=resource.id,
            kind=body.kind.value,
            embedding_model_id=model.id,
            embedding_dim=model.embedding_dim,
            index_table=index_table_name(resource.id),
            index_schema_version=INDEX_SCHEMA_VERSION,
            retrieval_config={},
            doc_count=0,
            chunk_count=0,
        )
        session.add(kb)
        await session.flush()
        # 建表和插 knowledge_bases 行在同一个事务里；建表失败一起回滚。
        await create_index_table(session, resource.id, model.embedding_dim)
        await session.commit()
    except Exception:
        await session.rollback()
        await _cleanup_orphan_resource(session, resource.id)
        raise
    return await kb_out(session, ctx, kb)


async def _cleanup_orphan_resource(session: AsyncSession, resource_id: int) -> None:
    """建索引表失败时，把 `create_resource` 已提交的资源软删除，不留空壳知识库。"""
    try:
        await session.execute(
            sa.update(Resource)
            .where(Resource.id == resource_id)
            .values(deleted_at=_now(), updated_at=_now())
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.error("清理未建成索引表的知识库资源失败: resource_id=%s", resource_id, exc_info=True)


async def update_kb(session: AsyncSession, ctx: Ctx, kb_id: int, body: KbUpdate) -> KbOut:
    resource = await access.get_for(session, ctx, kb_id, access.EDIT, type=KB_TYPE)
    if body.kind is not None:
        raise KbKindImmutableError()
    if body.embedding_model_id is not None:
        raise KbEmbeddingModelImmutableError()

    if body.name is not None and body.name != resource.name:
        conflict = await session.scalar(
            sa.select(Resource.id).where(
                Resource.type == KB_TYPE,
                Resource.org_id == resource.org_id,
                Resource.name == body.name,
                Resource.deleted_at.is_(None),
                Resource.id != resource.id,
            )
        )
        if conflict is not None:
            raise NameConflictError()
        resource.name = body.name
    if "description" in body.model_fields_set:
        resource.description = body.description
    resource.updated_at = _now()
    await session.commit()
    await session.refresh(resource)

    kb = await session.get(KnowledgeBase, resource.id)
    if kb is None:
        raise NotFoundError()
    return await kb_out(session, ctx, kb)


async def list_refs(session: AsyncSession, ctx: Ctx, kb_id: int) -> list[KbRefOut]:
    """引用该知识库的未删除资源（第三期没有应用，通常为空列表）。"""
    await access.get_for(session, ctx, kb_id, access.VIEW, type=KB_TYPE)
    rows = await session.execute(
        sa.select(Resource, User)
        .join(ResourceRef, ResourceRef.ref_resource_id == Resource.id)
        .join(User, Resource.owner_id == User.id)
        .where(
            ResourceRef.target_type == "resource",
            ResourceRef.target_id == kb_id,
            Resource.deleted_at.is_(None),
        )
        .order_by(Resource.id)
    )
    return [
        KbRefOut(
            resource_id=resource.id,
            type=resource.type,
            type_label=_type_label(resource.type),
            name=resource.name,
            owner=UserBrief(id=owner.id, username=owner.username, name=owner.name),
        )
        for resource, owner in rows
    ]


async def delete_kb(session: AsyncSession, ctx: Ctx, kb_id: int) -> Resource:
    return await delete_resource(session, ctx, kb_id)


async def set_kb_status(session: AsyncSession, ctx: Ctx, kb_id: int, enabled: bool) -> KbOut:
    resource = await set_status(session, ctx, kb_id, enabled)
    kb = await session.get(KnowledgeBase, resource.id)
    if kb is None:
        raise NotFoundError()
    return await kb_out(session, ctx, kb)


async def change_kb_visibility(
    session: AsyncSession, ctx: Ctx, kb_id: int, visibility: Visibility
) -> tuple[KbOut, dict]:
    resource, detail = await change_visibility(session, ctx, kb_id, visibility)
    kb = await session.get(KnowledgeBase, resource.id)
    if kb is None:
        raise NotFoundError()
    return await kb_out(session, ctx, kb), detail


async def list_kb_members(session: AsyncSession, ctx: Ctx, kb_id: int) -> list[MemberOut]:
    return await list_members(session, ctx, kb_id)


async def add_kb_member(
    session: AsyncSession, ctx: Ctx, kb_id: int, user_id: int
) -> tuple[MemberOut, Resource]:
    return await add_member(session, ctx, kb_id, user_id)


async def remove_kb_member(session: AsyncSession, ctx: Ctx, kb_id: int, user_id: int) -> Resource:
    return await remove_member(session, ctx, kb_id, user_id)

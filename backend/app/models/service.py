"""模型管理业务逻辑：列表、详情、新建、编辑、引用清单、启停与删除。

路由只做参数接收、权限依赖、调用这里的函数和写审计。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret, last4
from app.core.errors import (
    ModelFieldLockedError,
    ModelTypeImmutableError,
    NameConflictError,
    NotFoundError,
    ResourceInUseError,
    ValidationError,
)
from app.core.net import check_model_host
from app.core.schemas import OrgBrief, Page, Pagination, UserBrief
from app.iam.context import Ctx
from app.iam.tables import Org, User
from app.models.schemas import (
    ModelCreate,
    ModelOut,
    ModelRefOut,
    ModelStatus,
    ModelType,
    ModelUpdate,
    RefCounts,
)
from app.models.tables import Model
from app.resources import registry
from app.resources.tables import Resource, ResourceRef
from app.settings import service as settings_service

_REF_TYPES = ("app", "kb", "scheme")
# 这些列非空，请求里传 null 时按“未修改”处理，避免写库报错。
_NON_NULLABLE_FIELDS = {
    "name",
    "litellm_model",
    "cap_vision",
    "cap_tools",
    "cap_thinking",
    "default_params",
    "first_token_timeout_s",
    "total_timeout_s",
    "max_retries",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _type_label(code: str) -> str:
    resource_type = registry.get_resource_type(code)
    return resource_type.label if resource_type is not None else code


def _fields_locked(model: Model, ref_counts: RefCounts) -> bool:
    """向量化模型被知识库引用后，模型 ID 和向量维度不可修改。"""
    return model.model_type == ModelType.embedding.value and ref_counts.kb > 0


async def _ref_counts(session: AsyncSession, model_ids: list[int]) -> dict[int, RefCounts]:
    """按 resource_refs 联未删除的 resources，统计每个模型被各类资源引用的数量。"""
    counts: dict[int, dict[str, int]] = {
        model_id: dict.fromkeys(_REF_TYPES, 0) for model_id in model_ids
    }
    if not model_ids:
        return {model_id: RefCounts() for model_id in model_ids}
    rows = await session.execute(
        sa.select(ResourceRef.target_id, Resource.type, sa.func.count())
        .join(Resource, Resource.id == ResourceRef.ref_resource_id)
        .where(
            ResourceRef.target_type == "model",
            ResourceRef.target_id.in_(model_ids),
            Resource.deleted_at.is_(None),
        )
        .group_by(ResourceRef.target_id, Resource.type)
    )
    for model_id, resource_type, count in rows:
        if resource_type in counts.get(model_id, {}):
            counts[model_id][resource_type] = count
    return {model_id: RefCounts(**values) for model_id, values in counts.items()}


def _build_out(model: Model, ref_counts: RefCounts) -> ModelOut:
    return ModelOut(
        id=model.id,
        name=model.name,
        model_type=ModelType(model.model_type),
        litellm_model=model.litellm_model,
        api_base=model.api_base,
        api_key_last4=model.api_key_last4,
        description=model.description,
        context_length=model.context_length,
        max_output=model.max_output,
        cap_vision=model.cap_vision,
        cap_tools=model.cap_tools,
        cap_thinking=model.cap_thinking,
        embedding_dim=model.embedding_dim,
        embedding_max_input=model.embedding_max_input,
        embedding_batch_size=model.embedding_batch_size,
        default_params=model.default_params,
        first_token_timeout_s=model.first_token_timeout_s,
        total_timeout_s=model.total_timeout_s,
        max_retries=model.max_retries,
        rate_qps=model.rate_qps,
        rate_concurrency=model.rate_concurrency,
        status=ModelStatus(model.status),
        ref_counts=ref_counts,
        fields_locked=_fields_locked(model, ref_counts),
        last_test_at=model.last_test_at,
        last_test_ok=model.last_test_ok,
        last_test_message=model.last_test_message,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


async def model_out(session: AsyncSession, model: Model) -> ModelOut:
    counts = await _ref_counts(session, [model.id])
    return _build_out(model, counts[model.id])


async def get_model(session: AsyncSession, model_id: int) -> Model:
    model = await session.get(Model, model_id)
    if model is None or model.deleted_at is not None:
        raise NotFoundError()
    return model


async def list_models(
    session: AsyncSession,
    pagination: Pagination,
    type: ModelType | None,
    status: ModelStatus | None,
    keyword: str | None,
) -> Page[ModelOut]:
    stmt = sa.select(Model).where(Model.deleted_at.is_(None))
    if type is not None:
        stmt = stmt.where(Model.model_type == type.value)
    if status is not None:
        stmt = stmt.where(Model.status == status.value)
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        stmt = stmt.where(
            sa.or_(
                Model.name.ilike(pattern, escape="\\"),
                Model.litellm_model.ilike(pattern, escape="\\"),
            )
        )

    total = int(await session.scalar(sa.select(sa.func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(Model.created_at.desc(), Model.id.desc())
                .offset((pagination.page - 1) * pagination.page_size)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )
    counts = await _ref_counts(session, [model.id for model in rows])
    return Page(items=[_build_out(model, counts[model.id]) for model in rows], total=total)


async def _ensure_name_available(
    session: AsyncSession, name: str, exclude_id: int | None = None
) -> None:
    stmt = sa.select(Model.id).where(Model.name == name, Model.deleted_at.is_(None))
    if exclude_id is not None:
        stmt = stmt.where(Model.id != exclude_id)
    if await session.scalar(stmt) is not None:
        raise NameConflictError()


async def _allowlist(session: AsyncSession) -> list[str]:
    cfg = await settings_service.get_settings(session)
    return cfg.model_call.model_host_allowlist


def _check_timeouts(first_token_timeout_s: int, total_timeout_s: int) -> None:
    if first_token_timeout_s > total_timeout_s:
        raise ValidationError("首字超时不能大于总超时")


async def create_model(session: AsyncSession, ctx: Ctx, body: ModelCreate) -> Model:
    await _ensure_name_available(session, body.name)
    _check_timeouts(body.first_token_timeout_s, body.total_timeout_s)
    await check_model_host(body.api_base, await _allowlist(session))

    model = Model(
        name=body.name,
        status=ModelStatus.enabled.value,
        created_by=ctx.user_id,
        model_type=body.model_type.value,
        litellm_model=body.litellm_model,
        api_base=body.api_base,
        api_key_cipher=encrypt_secret(body.api_key) if body.api_key else None,
        api_key_last4=last4(body.api_key) if body.api_key else None,
        description=body.description,
        context_length=body.context_length,
        max_output=body.max_output,
        cap_vision=body.cap_vision,
        cap_tools=body.cap_tools,
        cap_thinking=body.cap_thinking,
        embedding_dim=body.embedding_dim,
        embedding_max_input=body.embedding_max_input,
        embedding_batch_size=body.embedding_batch_size,
        default_params=body.default_params,
        first_token_timeout_s=body.first_token_timeout_s,
        total_timeout_s=body.total_timeout_s,
        max_retries=body.max_retries,
        rate_qps=body.rate_qps,
        rate_concurrency=body.rate_concurrency,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def update_model(
    session: AsyncSession, model_id: int, body: ModelUpdate
) -> tuple[Model, list[tuple[str, dict | None]]]:
    """编辑模型；返回模型和要写的审计事件（动作，detail）。"""
    model = await get_model(session, model_id)
    provided = body.model_fields_set

    if body.model_type is not None:
        raise ModelTypeImmutableError()

    counts = (await _ref_counts(session, [model.id]))[model.id]
    if _fields_locked(model, counts):
        if "litellm_model" in provided and body.litellm_model != model.litellm_model:
            raise ModelFieldLockedError()
        if "embedding_dim" in provided and body.embedding_dim != model.embedding_dim:
            raise ModelFieldLockedError()

    if "name" in provided and body.name is not None and body.name != model.name:
        await _ensure_name_available(session, body.name, exclude_id=model.id)

    if "api_base" in provided and body.api_base != model.api_base:
        await check_model_host(body.api_base, await _allowlist(session))

    first_token_timeout_s = (
        body.first_token_timeout_s
        if "first_token_timeout_s" in provided and body.first_token_timeout_s is not None
        else model.first_token_timeout_s
    )
    total_timeout_s = (
        body.total_timeout_s
        if "total_timeout_s" in provided and body.total_timeout_s is not None
        else model.total_timeout_s
    )
    _check_timeouts(first_token_timeout_s, total_timeout_s)

    changed: list[str] = []
    for field in provided:
        if field in ("model_type", "api_key"):
            continue
        value = getattr(body, field)
        if value is None and field in _NON_NULLABLE_FIELDS:
            continue
        if getattr(model, field) != value:
            changed.append(field)
        setattr(model, field, value)

    events: list[tuple[str, dict | None]] = []
    if body.api_key:
        model.api_key_cipher = encrypt_secret(body.api_key)
        model.api_key_last4 = last4(body.api_key)
        events.append(("model.change_key", None))
    if changed:
        events.append(("model.update", {"fields": changed}))

    if events:
        model.updated_at = _now()
        await session.commit()
        await session.refresh(model)
    return model, events


async def list_refs(session: AsyncSession, model_id: int) -> list[ModelRefOut]:
    await get_model(session, model_id)
    rows = (
        await session.execute(
            sa.select(Resource, User, Org)
            .join(ResourceRef, ResourceRef.ref_resource_id == Resource.id)
            .join(User, Resource.owner_id == User.id)
            .join(Org, Resource.org_id == Org.id)
            .where(
                ResourceRef.target_type == "model",
                ResourceRef.target_id == model_id,
                Resource.deleted_at.is_(None),
            )
            .order_by(Resource.created_at, Resource.id)
        )
    ).all()
    return [
        ModelRefOut(
            resource_id=resource.id,
            type=resource.type,
            type_label=_type_label(resource.type),
            name=resource.name,
            owner=UserBrief(id=owner.id, username=owner.username, name=owner.name),
            org=OrgBrief(id=org.id, name=org.name),
        )
        for resource, owner, org in rows
    ]


async def set_status(session: AsyncSession, model_id: int, enabled: bool) -> Model:
    model = await get_model(session, model_id)
    target = ModelStatus.enabled.value if enabled else ModelStatus.disabled.value
    if model.status != target:
        model.status = target
        model.updated_at = _now()
        await session.commit()
        await session.refresh(model)
    return model


async def delete_model(session: AsyncSession, model_id: int) -> Model:
    model = await get_model(session, model_id)
    refs = await list_refs(session, model.id)
    if refs:
        raise ResourceInUseError([ref.model_dump(mode="json") for ref in refs])
    model.deleted_at = _now()
    model.updated_at = _now()
    await session.commit()
    await session.refresh(model)
    return model

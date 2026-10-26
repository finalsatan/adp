"""应用引用登记与检查（应用技术方案 5.4）。

`resource_refs` 登记的是“草稿 ∪ 当前发布版本”引用的模型与知识库；历史版本不算。
保存草稿、发布、恢复时重算。知识库缩小可见范围或移除协作者前，按这张表找出引用它的
应用，逐个用“修改后”的可见范围和协作者重新判断应用是否还能引用（需求 4.1.2、10.4）。

应用能否引用某个知识库统一由 `app_can_reference_kb` 判断（应用技术方案 5.4，2026-10-22
确认）：创建人有效时按技术方案 7.3 对创建人算查看条件；创建人失效时按应用所属组织算。
不保存“失效”状态，每次都实时计算。
"""

from __future__ import annotations

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.schemas import KbCandidateOut, KbModelBrief
from app.apps.tables import App, AppVersion
from app.core.errors import NotFoundError
from app.core.schemas import OrgBrief, Role, UserStatus, Visibility
from app.iam.context import Ctx
from app.iam.tables import Org, User
from app.kb.schemas import KbKind
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from app.resources import access
from app.resources.registry import RefBlocker
from app.resources.tables import Resource, ResourceMember, ResourceRef

APP_TYPE = "app"
KB_TYPE = "kb"


def _as_dict(config: BaseModel | dict) -> dict:
    return config.model_dump(mode="json") if isinstance(config, BaseModel) else config


def _model_ids(config: dict) -> set[int]:
    """配置里显式选择的全部模型（回答、意图识别、问题改写、文档库重排、问答库重排）。"""
    ids: set[int] = set()
    model = config.get("model") or {}
    if model.get("chat_model_id") is not None:
        ids.add(int(model["chat_model_id"]))
    for slot in ("intent", "rewrite"):
        value = (config.get(slot) or {}).get("model_id")
        if value is not None:
            ids.add(int(value))
    knowledge = config.get("knowledge") or {}
    for slot in ("doc", "qa"):
        value = ((knowledge.get(slot) or {}).get("rerank") or {}).get("model_id")
        if value is not None:
            ids.add(int(value))
    return ids


def _kb_ids(config: dict) -> set[int]:
    kbs = (config.get("knowledge") or {}).get("kbs") or []
    return {int(ref["kb_id"]) for ref in kbs if ref.get("kb_id") is not None}


async def recompute_refs(
    session: AsyncSession, app_id: int, configs: list[BaseModel | dict]
) -> None:
    """按配置列表重算应用的 `resource_refs`（草稿 ∪ 当前发布版本）。不提交。"""
    model_ids: set[int] = set()
    kb_ids: set[int] = set()
    for config in configs:
        data = _as_dict(config)
        model_ids |= _model_ids(data)
        kb_ids |= _kb_ids(data)

    await session.execute(sa.delete(ResourceRef).where(ResourceRef.ref_resource_id == app_id))
    for model_id in sorted(model_ids):
        session.add(ResourceRef(target_type="model", target_id=model_id, ref_resource_id=app_id))
    for kb_id in sorted(kb_ids):
        session.add(ResourceRef(target_type="resource", target_id=kb_id, ref_resource_id=app_id))
    await session.flush()


async def recompute_for_app(session: AsyncSession, app: App) -> None:
    """按“草稿 ∪ 当前发布版本”重算。历史版本不算（应用技术方案 5.4）。"""
    configs: list[BaseModel | dict] = [app.draft_config]
    if app.published_version_id is not None:
        published = await session.get(AppVersion, app.published_version_id)
        if published is not None:
            configs.append(published.config)
    await recompute_refs(session, app.id, configs)


def _owner_valid(owner: User, app_resource: Resource) -> bool:
    """创建人有效：账号启用，且仍属于应用所属组织；启用的系统管理员也算有效。"""
    if owner.status != UserStatus.active.value:
        return False
    if owner.role == Role.sys_admin.value:
        return True
    return owner.org_id is not None and owner.org_id == app_resource.org_id


async def _org_path(session: AsyncSession, org_id: int | None) -> str | None:
    if org_id is None:
        return None
    return await session.scalar(sa.select(Org.path).where(Org.id == org_id))


async def _owner_can_view(
    session: AsyncSession,
    owner: User,
    owner_org_path: str | None,
    kb_resource: Resource,
    visibility: str,
    is_member: bool,
) -> bool:
    """按技术方案 7.3 对创建人算查看条件（复用 access 的同一套规则）。"""
    ctx = Ctx(
        user_id=owner.id,
        username=owner.username,
        name=owner.name,
        role=Role(owner.role),
        status=owner.status,
        org_id=owner.org_id,
        org_path=owner_org_path,
        org_name=None,
        perms=frozenset(),
        session_id="",
        pwc=False,
        ip=None,
        request_id=None,
    )
    # 只用到 Resource 的 owner_id / org_id / visibility 三个字段；用轻量对象承载覆盖后的可见范围。
    view_resource = _ViewResource(
        owner_id=kb_resource.owner_id, org_id=kb_resource.org_id, visibility=visibility
    )
    kb_org_path = await _org_path(session, kb_resource.org_id)
    return access._can(ctx, access.VIEW, view_resource, kb_org_path, is_member)  # type: ignore[arg-type]


class _ViewResource:
    """`access._can` 查看判断需要的最小资源字段（可见范围可覆盖）。"""

    __slots__ = ("owner_id", "org_id", "visibility")

    def __init__(self, owner_id: int, org_id: int, visibility: str) -> None:
        self.owner_id = owner_id
        self.org_id = org_id
        self.visibility = visibility


async def app_can_reference_kb(
    session: AsyncSession,
    app: App,
    kb: KnowledgeBase,
    *,
    visibility: str | None = None,
    removed_user_id: int | None = None,
) -> bool:
    """应用能否引用该知识库。`visibility` / `removed_user_id` 供缩小可见范围、移除协作者时预演。"""
    app_resource = await session.get(Resource, app.id)
    kb_resource = await session.get(Resource, kb.id)
    if app_resource is None or kb_resource is None:
        return False
    owner = await session.get(User, app_resource.owner_id)
    if owner is None:
        return False

    kb_visibility = visibility if visibility is not None else kb_resource.visibility
    if _owner_valid(owner, app_resource):
        is_member = False
        if removed_user_id is None or removed_user_id != owner.id:
            is_member = (
                await session.scalar(
                    sa.select(ResourceMember.resource_id).where(
                        ResourceMember.resource_id == kb.id,
                        ResourceMember.user_id == owner.id,
                    )
                )
                is not None
            )
        return await _owner_can_view(
            session,
            owner,
            await _org_path(session, owner.org_id),
            kb_resource,
            kb_visibility,
            is_member,
        )

    # 创建人失效：按应用所属组织计算，协作者关系不算（应用技术方案 5.4）。
    if kb_visibility == Visibility.platform.value:
        return True
    if kb_visibility == Visibility.org.value:
        app_path = await _org_path(session, app_resource.org_id)
        kb_path = await _org_path(session, kb_resource.org_id)
        return app_path is not None and kb_path is not None and app_path.startswith(kb_path)
    return False


async def _kb_ref_checker(
    session: AsyncSession,
    resource: Resource,
    new_visibility: str | None,
    removed_user_id: int | None,
) -> list[RefBlocker]:
    """知识库缩小可见范围 / 移除协作者前，找出会因此不能再引用它的应用。"""
    if resource.type != KB_TYPE:
        return []
    kb = await session.get(KnowledgeBase, resource.id)
    if kb is None:
        return []

    app_resources = (
        (
            await session.execute(
                sa.select(Resource)
                .join(ResourceRef, ResourceRef.ref_resource_id == Resource.id)
                .where(
                    ResourceRef.target_type == "resource",
                    ResourceRef.target_id == resource.id,
                    Resource.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    blockers: list[RefBlocker] = []
    for app_resource in app_resources:
        app = await session.get(App, app_resource.id)
        if app is None:
            continue
        ok = await app_can_reference_kb(
            session,
            app,
            kb,
            visibility=new_visibility,
            removed_user_id=removed_user_id,
        )
        if not ok:
            blockers.append(RefBlocker(app_resource.id, APP_TYPE, app_resource.name))
    return blockers


def _candidate_reason(owner_valid: bool, owner_name: str | None) -> str:
    if owner_valid:
        return f"应用创建人 {owner_name} 不能查看" if owner_name else "应用创建人不能查看"
    return "应用创建人已失效，应用所属组织不能查看"


async def list_kb_candidates(session: AsyncSession, ctx: Ctx, app_id: int) -> list[KbCandidateOut]:
    """列出操作人能查看的知识库，并标出是否已在草稿、是否可选（原型 `#d-addkb`）。"""
    app_resource = await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    app = await session.get(App, app_resource.id)
    if app is None:
        raise NotFoundError()

    draft_kbs = _kb_ids(app.draft_config)
    owner = await session.get(User, app_resource.owner_id)
    owner_valid = owner is not None and _owner_valid(owner, app_resource)

    rows = (
        await session.execute(
            sa.select(Resource, KnowledgeBase, Model, Org)
            .join(KnowledgeBase, KnowledgeBase.id == Resource.id)
            .join(Model, Model.id == KnowledgeBase.embedding_model_id)
            .join(Org, Resource.org_id == Org.id)
            .where(
                Resource.type == KB_TYPE,
                Resource.deleted_at.is_(None),
                access.access_clause(ctx, access.VIEW),
            )
            .order_by(Resource.created_at.desc(), Resource.id.desc())
        )
    ).all()

    candidates = []
    for resource, kb, model, org in rows:
        selectable = await app_can_reference_kb(session, app, kb)
        reason = (
            None if selectable else _candidate_reason(owner_valid, owner.name if owner else None)
        )
        candidates.append(
            KbCandidateOut(
                kb_id=resource.id,
                name=resource.name,
                kind=KbKind(kb.kind),
                embedding_model=KbModelBrief(id=model.id, name=model.name),
                org=OrgBrief(id=org.id, name=org.name),
                visibility=Visibility(resource.visibility),
                status=resource.status,
                added=resource.id in draft_kbs,
                selectable=selectable,
                reason=reason,
            )
        )
    return candidates

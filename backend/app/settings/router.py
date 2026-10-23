"""系统设置接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget
from app.core.db import get_session
from app.core.errors import ValidationError
from app.iam.deps import CurrentCtx, require_perm
from app.settings import service
from app.settings.schemas import SettingsOut, SettingsPatch
from app.settings.service import PlatformSettings

router = APIRouter(prefix="/api/v1", tags=["系统设置"])

SETTINGS_MANAGE = [Depends(require_perm("settings.manage"))]


def _validation_detail(exc: PydanticValidationError) -> dict:
    """把 pydantic 的校验错误整理成统一的 detail.errors。"""
    errors = [{k: v for k, v in err.items() if k in ("type", "loc", "msg")} for err in exc.errors()]
    return {"errors": errors}


def _changes(before: PlatformSettings, after: PlatformSettings) -> dict[str, list]:
    """只列出真正变化的字段，键为“分组.字段”。"""
    before_values = before.model_dump()
    after_values = after.model_dump()
    changes: dict[str, list] = {}
    for group, fields in after_values.items():
        for name, new_value in fields.items():
            old_value = before_values[group][name]
            if old_value != new_value:
                changes[f"{group}.{name}"] = [old_value, new_value]
    return changes


@router.get(
    "/settings",
    response_model=SettingsOut,
    dependencies=SETTINGS_MANAGE,
    summary="查看系统设置",
)
async def get_settings(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SettingsOut:
    settings = await service.get_settings(session)
    return SettingsOut.model_validate(settings.model_dump())


@router.patch(
    "/settings",
    response_model=SettingsOut,
    dependencies=[Depends(audited("settings.update")), *SETTINGS_MANAGE],
    summary="修改系统设置",
)
async def patch_settings(
    body: SettingsPatch,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SettingsOut:
    patch = body.model_dump(exclude_unset=True, exclude_none=True)
    try:
        before, after = await service.update_settings(session, patch, actor_id=ctx.user_id)
    except PydanticValidationError as exc:
        raise ValidationError(detail=_validation_detail(exc)) from None

    changes = _changes(before, after)
    if changes:
        await audit_success(
            request,
            AuditTarget("settings", None, "系统设置"),
            {"changes": changes},
        )
    return SettingsOut.model_validate(after.model_dump())

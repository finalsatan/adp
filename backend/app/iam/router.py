"""个人信息接口。PATCH /me 由 P1-11 填充。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget
from app.core.db import get_session
from app.iam import service, users_service
from app.iam.context import Ctx, current_ctx_allow_pwc
from app.iam.deps import CurrentCtx
from app.iam.schemas import MeOut, MePatch

router = APIRouter(prefix="/api/v1", tags=["个人"])


@router.get("/me", response_model=MeOut, summary="查看我的信息")
async def read_me(
    ctx: Annotated[Ctx, Depends(current_ctx_allow_pwc)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeOut:
    return await service.get_me(session, ctx)


@router.patch(
    "/me",
    response_model=MeOut,
    summary="修改我的资料",
    dependencies=[Depends(audited("user.update"))],
)
async def patch_me(
    body: MePatch,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeOut:
    me, changes = await users_service.update_me(session, ctx, body)
    await audit_success(
        request, AuditTarget("user", ctx.user_id, ctx.username, ctx.org_id), changes or None
    )
    return me

"""个人信息接口。PATCH /me 由 P1-11 填充。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.iam import service
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


@router.patch("/me", response_model=MeOut, summary="修改我的资料")
async def patch_me(body: MePatch, ctx: CurrentCtx) -> MeOut:
    raise NotImplementedError

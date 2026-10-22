"""个人信息接口。实现留空，P1-06 填充。"""

from fastapi import APIRouter

from app.iam.deps import CurrentCtx
from app.iam.schemas import MeOut, MePatch

router = APIRouter(prefix="/api/v1", tags=["个人"])


@router.get("/me", response_model=MeOut, summary="查看我的信息")
async def get_me(ctx: CurrentCtx) -> MeOut:
    raise NotImplementedError


@router.patch("/me", response_model=MeOut, summary="修改我的资料")
async def patch_me(body: MePatch, ctx: CurrentCtx) -> MeOut:
    raise NotImplementedError

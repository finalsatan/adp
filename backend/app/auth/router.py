"""认证接口：登录、刷新、登出、改密、密码策略。实现留空，P1-08 填充。"""

from fastapi import APIRouter, Response

from app.auth.schemas import AuthPolicyOut, ChangePasswordIn, LoginIn, MustChangePasswordOut
from app.iam.deps import CurrentCtx

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])


@router.post("/login", response_model=MustChangePasswordOut, summary="登录")
async def login(body: LoginIn, response: Response) -> MustChangePasswordOut:
    raise NotImplementedError


@router.post("/refresh", response_model=MustChangePasswordOut, summary="刷新令牌")
async def refresh(response: Response) -> MustChangePasswordOut:
    raise NotImplementedError


@router.post("/logout", status_code=204, summary="登出")
async def logout(response: Response) -> None:
    raise NotImplementedError


@router.post(
    "/change-password",
    response_model=MustChangePasswordOut,
    summary="修改密码",
)
async def change_password(
    body: ChangePasswordIn,
    response: Response,
    ctx: CurrentCtx,
) -> MustChangePasswordOut:
    raise NotImplementedError


@router.get("/policy", response_model=AuthPolicyOut, summary="密码与令牌策略")
async def get_policy(ctx: CurrentCtx) -> AuthPolicyOut:
    raise NotImplementedError

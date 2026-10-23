"""认证 Cookie：访问令牌与刷新令牌的设置、清除。

技术方案 8.1：两个令牌都放 Cookie，HttpOnly、SameSite=Strict、Secure 取配置；
访问令牌路径 /api，刷新令牌路径 /api/v1/auth/refresh。设置和清除的路径必须一致。
"""

from fastapi import Response

from app.core.config import get_settings
from app.iam.context import ACCESS_COOKIE

REFRESH_COOKIE = "adp_rt"
ACCESS_COOKIE_PATH = "/api"
REFRESH_COOKIE_PATH = "/api/v1/auth/refresh"


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    access_minutes: int,
    refresh_days: int,
) -> None:
    """设置两个认证 Cookie。"""
    secure = get_settings().COOKIE_SECURE
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=access_minutes * 60,
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=secure,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=refresh_days * 86400,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=secure,
    )


def clear_auth_cookies(response: Response) -> None:
    """清除两个认证 Cookie，路径与设置时一致。"""
    secure = get_settings().COOKIE_SECURE
    response.delete_cookie(
        ACCESS_COOKIE,
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=secure,
    )
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=secure,
    )

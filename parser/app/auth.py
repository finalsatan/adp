"""Bearer 认证：除 /healthz 外都要带 PARSER_TOKEN，用常量时间比较。"""

from __future__ import annotations

import secrets

from fastapi import Header, Request

from .errors import AppError


async def require_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    expected = f"Bearer {request.app.state.settings.parser_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise AppError(401, "UNAUTHORIZED")

"""请求上下文与权限依赖。"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends

from app.core.errors import PermissionDeniedError
from app.iam.context import Ctx, current_ctx


def require_perm(code: str) -> Callable[..., Coroutine[Any, Any, Ctx]]:
    """返回校验权限码 code 的依赖；通过时返回当前上下文。"""

    async def _dependency(ctx: Annotated[Ctx, Depends(current_ctx)]) -> Ctx:
        if code not in ctx.perms:
            raise PermissionDeniedError()
        return ctx

    return _dependency


CurrentCtx = Annotated[Ctx, Depends(current_ctx)]

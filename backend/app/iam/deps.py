"""请求上下文与权限依赖。本卡只放占位，P1-06 实现。"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends


async def current_ctx() -> Any:
    """占位：取当前请求上下文。P1-06 实现。"""
    raise NotImplementedError


def require_perm(code: str) -> Callable[[], Coroutine[Any, Any, None]]:
    """占位：返回校验权限码 code 的依赖。P1-06 实现。"""

    async def _dependency() -> None:
        raise NotImplementedError

    return _dependency


CurrentCtx = Annotated[Any, Depends(current_ctx)]

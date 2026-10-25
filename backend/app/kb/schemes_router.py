"""入库方案、解析引擎与知识库默认方案接口（P3-01 契约，实现见 P3-06）。

方案列表与详情、引擎列表走 `console.use`；知识库默认方案列表走资源“查看”、
PUT 走资源“编辑”（在 service 里判断）。本卡不写审计（方案增删改是第五期）。
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.schemas import Page, Pagination
from app.iam.deps import CurrentCtx, require_perm
from app.kb import schemes
from app.kb.parser_client import ParserClient
from app.kb.schemas import (
    DefaultSchemeIn,
    DefaultSchemeOut,
    FileType,
    ParseEngineOut,
    SchemeOut,
)

router = APIRouter(prefix="/api/v1", tags=["入库方案"])

CONSOLE_USE = [Depends(require_perm("console.use"))]


async def get_parser_client(request: Request) -> AsyncIterator[ParserClient]:
    """每个请求建一个解析服务客户端，用完关闭；测试用 `dependency_overrides` 注入。"""
    client = ParserClient(request.app.state.settings)
    try:
        yield client
    finally:
        await client.aclose()


@router.get(
    "/schemes",
    response_model=Page[SchemeOut],
    dependencies=CONSOLE_USE,
    summary="入库方案列表",
)
async def list_schemes(
    pagination: Annotated[Pagination, Depends()],
    session: Annotated[AsyncSession, Depends(get_session)],
    file_type: FileType | None = None,
    builtin: bool | None = None,
    keyword: str | None = None,
) -> Page[SchemeOut]:
    return await schemes.list_schemes(session, file_type, builtin, keyword, pagination)


@router.get(
    "/schemes/{id:int}",
    response_model=SchemeOut,
    dependencies=CONSOLE_USE,
    summary="入库方案详情",
)
async def get_scheme(id: int, session: Annotated[AsyncSession, Depends(get_session)]) -> SchemeOut:
    return await schemes.get_scheme(session, id)


@router.get(
    "/parse-engines",
    response_model=list[ParseEngineOut],
    dependencies=CONSOLE_USE,
    summary="解析引擎列表",
)
async def list_parse_engines(
    client: Annotated[ParserClient, Depends(get_parser_client)],
) -> list[ParseEngineOut]:
    return await schemes.list_parse_engines(client)


@router.get(
    "/kbs/{id:int}/default-schemes",
    response_model=DefaultSchemeOut,
    dependencies=CONSOLE_USE,
    summary="知识库默认入库方案",
)
async def get_default_schemes(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DefaultSchemeOut:
    return await schemes.get_default_schemes(session, ctx, id)


@router.put(
    "/kbs/{id:int}/default-schemes",
    response_model=DefaultSchemeOut,
    summary="保存知识库默认入库方案",
)
async def put_default_schemes(
    id: int,
    body: DefaultSchemeIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DefaultSchemeOut:
    return await schemes.set_default_schemes(session, ctx, id, body.items)

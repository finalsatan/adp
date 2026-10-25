"""入库方案、解析引擎与知识库默认方案接口（P3-01 契约，实现见 P3-06）。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.schemas import Page, Pagination
from app.iam.deps import CurrentCtx, require_perm
from app.kb.schemas import (
    DefaultSchemeIn,
    DefaultSchemeOut,
    FileType,
    ParseEngineOut,
    SchemeOut,
)

router = APIRouter(prefix="/api/v1", tags=["入库方案"])

CONSOLE_USE = [Depends(require_perm("console.use"))]


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
    raise NotImplementedError


@router.get(
    "/schemes/{id:int}",
    response_model=SchemeOut,
    dependencies=CONSOLE_USE,
    summary="入库方案详情",
)
async def get_scheme(id: int, session: Annotated[AsyncSession, Depends(get_session)]) -> SchemeOut:
    raise NotImplementedError


@router.get(
    "/parse-engines",
    response_model=list[ParseEngineOut],
    dependencies=CONSOLE_USE,
    summary="解析引擎列表",
)
async def list_parse_engines() -> list[ParseEngineOut]:
    raise NotImplementedError


@router.get(
    "/kbs/{id:int}/default-schemes",
    response_model=DefaultSchemeOut,
    dependencies=CONSOLE_USE,
    summary="知识库默认入库方案",
)
async def get_default_schemes(
    id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> DefaultSchemeOut:
    raise NotImplementedError


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
    raise NotImplementedError

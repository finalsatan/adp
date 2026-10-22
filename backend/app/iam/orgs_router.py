"""组织管理接口。实现留空，P1-10 填充。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.schemas import Page, Pagination
from app.iam.deps import require_perm
from app.iam.schemas import OrgCreate, OrgMemberOut, OrgOut, OrgPatch

router = APIRouter(
    prefix="/api/v1",
    tags=["组织"],
    dependencies=[Depends(require_perm("org.manage"))],
)


@router.get("/orgs", response_model=list[OrgOut], summary="组织列表")
async def list_orgs() -> list[OrgOut]:
    raise NotImplementedError


@router.post("/orgs", response_model=OrgOut, status_code=201, summary="新建组织")
async def create_org(body: OrgCreate) -> OrgOut:
    raise NotImplementedError


@router.patch("/orgs/{id:int}", response_model=OrgOut, summary="编辑组织")
async def patch_org(id: int, body: OrgPatch) -> OrgOut:
    raise NotImplementedError


@router.delete("/orgs/{id:int}", status_code=204, summary="删除组织")
async def delete_org(id: int) -> None:
    raise NotImplementedError


@router.get(
    "/orgs/{id:int}/members",
    response_model=Page[OrgMemberOut],
    summary="组织成员",
)
async def list_org_members(
    id: int,
    pagination: Annotated[Pagination, Depends()],
) -> Page[OrgMemberOut]:
    raise NotImplementedError

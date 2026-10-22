"""系统设置接口。实现留空，P1-09 填充。"""

from fastapi import APIRouter, Depends

from app.iam.deps import require_perm
from app.settings.schemas import SettingsOut, SettingsPatch

router = APIRouter(
    prefix="/api/v1",
    tags=["系统设置"],
    dependencies=[Depends(require_perm("settings.manage"))],
)


@router.get("/settings", response_model=SettingsOut, summary="查看系统设置")
async def get_settings() -> SettingsOut:
    raise NotImplementedError


@router.patch("/settings", response_model=SettingsOut, summary="修改系统设置")
async def patch_settings(body: SettingsPatch) -> SettingsOut:
    raise NotImplementedError

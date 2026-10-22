"""个人信息的查询逻辑。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthRequiredError
from app.core.schemas import OrgBrief
from app.iam.context import Ctx
from app.iam.schemas import MeOut
from app.iam.tables import Org, User


async def get_me(session: AsyncSession, ctx: Ctx) -> MeOut:
    user = await session.get(User, ctx.user_id)
    if user is None or user.status != "active":
        raise AuthRequiredError()

    org = None
    if user.org_id is not None:
        org_row = await session.get(Org, user.org_id)
        if org_row is not None:
            org = OrgBrief(id=org_row.id, name=org_row.name)

    return MeOut(
        id=user.id,
        username=user.username,
        name=user.name,
        email=user.email,
        phone=user.phone,
        role=user.role,
        org=org,
        permissions=sorted(ctx.perms),
        must_change_password=ctx.pwc,
    )

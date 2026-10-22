"""权限码与“角色 → 权限码”映射。照技术方案 7.1，写在代码里，不入库。"""

from app.core.schemas import Role

SETTINGS_MANAGE = "settings.manage"
MODEL_MANAGE = "model.manage"
ORG_MANAGE = "org.manage"
USER_MANAGE = "user.manage"
AUDIT_VIEW = "audit.view"
OVERVIEW_VIEW = "overview.view"
USAGE_VIEW = "usage.view"
RESOURCE_CREATE = "resource.create"
CONSOLE_USE = "console.use"

ALL_PERMISSIONS = frozenset(
    {
        SETTINGS_MANAGE,
        MODEL_MANAGE,
        ORG_MANAGE,
        USER_MANAGE,
        AUDIT_VIEW,
        OVERVIEW_VIEW,
        USAGE_VIEW,
        RESOURCE_CREATE,
        CONSOLE_USE,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.sys_admin: frozenset(
        {
            SETTINGS_MANAGE,
            MODEL_MANAGE,
            ORG_MANAGE,
            USER_MANAGE,
            AUDIT_VIEW,
            OVERVIEW_VIEW,
            USAGE_VIEW,
            RESOURCE_CREATE,
            CONSOLE_USE,
        }
    ),
    Role.org_admin: frozenset(
        {
            ORG_MANAGE,
            USER_MANAGE,
            AUDIT_VIEW,
            OVERVIEW_VIEW,
            USAGE_VIEW,
            RESOURCE_CREATE,
            CONSOLE_USE,
        }
    ),
    Role.builder: frozenset({RESOURCE_CREATE, CONSOLE_USE}),
    Role.end_user: frozenset(),
}

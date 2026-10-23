"""审计动作代码与对象类型的中文名。

以后各期往 ACTION_LABELS 里加动作。资源的对象类型中文名从资源类型注册表取
（P1-12 提供注册表），本卡先留一个查找函数，取不到时显示代码本身。
"""

ACTION_LABELS: dict[str, str] = {
    "auth.login": "登录",
    "auth.change_password": "修改密码",
    "user.create": "创建用户",
    "user.update": "修改用户资料",
    "user.change_role": "调整角色",
    "user.change_org": "调整所属组织",
    "user.disable": "停用用户",
    "user.enable": "启用用户",
    "user.reset_password": "重置密码",
    "user.unlock": "解锁用户",
    "user.grant_sys_admin": "任命系统管理员",
    "user.revoke_sys_admin": "撤销系统管理员",
    "org.create": "创建组织",
    "org.update": "修改组织",
    "org.delete": "删除组织",
    "settings.update": "修改系统设置",
    "resource.change_visibility": "调整可见范围",
    "resource.add_member": "添加协作者",
    "resource.remove_member": "移除协作者",
    "resource.disable": "停用资源",
    "resource.enable": "启用资源",
    "resource.delete": "删除资源",
}

TARGET_TYPE_LABELS: dict[str, str] = {
    "user": "用户",
    "org": "组织",
    "settings": "系统设置",
}


def action_label(action: str) -> str:
    """动作的中文名；未登记的动作显示代码本身。"""
    return ACTION_LABELS.get(action, action)


def target_type_label(code: str | None) -> str | None:
    """对象类型的中文名；未登记的类型显示代码本身。"""
    if code is None:
        return None
    if code in TARGET_TYPE_LABELS:
        return TARGET_TYPE_LABELS[code]
    return _resource_type_label(code)


def list_actions() -> list[tuple[str, str]]:
    """全部动作（代码，中文名）。"""
    return list(ACTION_LABELS.items())


def list_target_types() -> list[tuple[str, str]]:
    """全部对象类型（代码，中文名）。"""
    return list(TARGET_TYPE_LABELS.items()) + list(_resource_types())


def _resource_type_label(code: str) -> str:
    """资源类型的对象类型中文名。P1-12 接入注册表后改为查注册表。"""
    return code


def _resource_types() -> list[tuple[str, str]]:
    """资源类型注册表内容。第一期不注册任何类型。"""
    return []

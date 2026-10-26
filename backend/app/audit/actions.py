"""审计动作代码与对象类型的中文名。

以后各期往 ACTION_LABELS 里加动作。资源的对象类型中文名从资源类型注册表取
（P1-12 提供注册表），取不到时显示代码本身。
"""

from app.resources import registry

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
    "model.create": "添加模型",
    "model.update": "编辑模型",
    "model.change_key": "更换模型密钥",
    "model.disable": "停用模型",
    "model.enable": "启用模型",
    "model.delete": "删除模型",
    "kb.create": "创建知识库",
    "kb.update": "修改知识库",
    "kb.delete": "删除知识库",
    "kb.change_visibility": "调整可见范围",
    "kb.add_member": "添加协作者",
    "kb.remove_member": "移除协作者",
    "kb.disable": "停用知识库",
    "kb.enable": "启用知识库",
    "document.delete": "删除文档",
    "document.batch_delete": "批量删除文档",
    "app.create": "创建应用",
    "app.update": "修改应用",
    "app.delete": "删除应用",
    "app.change_visibility": "调整可见范围",
    "app.add_member": "添加协作者",
    "app.remove_member": "移除协作者",
    "app.disable": "停用应用",
    "app.enable": "启用应用",
}

TARGET_TYPE_LABELS: dict[str, str] = {
    "user": "用户",
    "org": "组织",
    "settings": "系统设置",
    "model": "模型",
    "kb": "知识库",
    "app": "应用",
    "document": "文档",
    "scheme": "入库方案",
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
    """全部对象类型（代码，中文名）；同名代码去重。"""
    seen: dict[str, str] = {}
    for code, label in list(TARGET_TYPE_LABELS.items()) + list(_resource_types()):
        seen.setdefault(code, label)
    return list(seen.items())


def _resource_type_label(code: str) -> str:
    """资源类型的对象类型中文名从注册表取。"""
    resource_type = registry.get_resource_type(code)
    return resource_type.label if resource_type is not None else code


def _resource_types() -> list[tuple[str, str]]:
    """资源类型注册表内容。"""
    return [
        (resource_type.code, resource_type.label)
        for resource_type in registry.list_resource_types()
    ]

import {
  AppstoreOutlined,
  BookOutlined,
  CompassOutlined,
  DashboardOutlined,
  KeyOutlined,
  RobotOutlined,
  SafetyOutlined,
  ApiOutlined,
} from "@ant-design/icons";
import type { ComponentType } from "react";

import type { Me } from "../api/me";
import { hasPerm } from "./permissions";

export interface MenuItem {
  key: string;
  label: string;
  path: string;
  permission: string;
}

export interface MenuGroup {
  key: string;
  label: string;
  icon: ComponentType;
  items: MenuItem[];
}

/**
 * 侧栏配置。只放已上线的菜单项，后续分期往对应分组里加。
 * 分组顺序照 A1 原型；没有可见项的分组由 buildMenu 整组过滤掉。
 */
export const MENU_GROUPS: MenuGroup[] = [
  { key: "models", label: "模型服务", icon: ApiOutlined, items: [] },
  { key: "resources", label: "资源库", icon: BookOutlined, items: [] },
  { key: "apps", label: "应用开发", icon: AppstoreOutlined, items: [] },
  { key: "explore", label: "探索广场", icon: CompassOutlined, items: [] },
  { key: "observe", label: "系统观测", icon: DashboardOutlined, items: [] },
  { key: "agents", label: "通用智能体", icon: RobotOutlined, items: [] },
  { key: "apikeys", label: "API Key", icon: KeyOutlined, items: [] },
  {
    key: "system",
    label: "系统管理",
    icon: SafetyOutlined,
    items: [
      { key: "users", label: "用户管理", path: "/system/users", permission: "user.manage" },
      { key: "orgs", label: "组织管理", path: "/system/orgs", permission: "org.manage" },
      {
        key: "overview",
        label: "资源总览",
        path: "/system/overview",
        permission: "overview.view",
      },
      { key: "audit", label: "审计日志", path: "/system/audit", permission: "audit.view" },
      {
        key: "settings",
        label: "系统设置",
        path: "/system/settings",
        permission: "settings.manage",
      },
    ],
  },
];

/** 按当前用户的权限码过滤菜单；没有可见项的分组整组不显示。 */
export function buildMenu(me: Me | undefined | null): MenuGroup[] {
  if (!me) {
    return [];
  }
  return MENU_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => hasPerm(me, item.permission)),
  })).filter((group) => group.items.length > 0);
}

/** 按路径找菜单项，供顶栏面包屑使用。 */
export function findMenuItem(
  pathname: string,
): { group: MenuGroup; item: MenuItem } | null {
  for (const group of MENU_GROUPS) {
    for (const item of group.items) {
      if (item.path === pathname) {
        return { group, item };
      }
    }
  }
  return null;
}

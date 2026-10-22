import type { components } from "../api/schema";

type Role = components["schemas"]["Role"];
type UserStatus = components["schemas"]["UserStatus"];
type Visibility = components["schemas"]["Visibility"];
type ResourceStatus = components["schemas"]["ResourceStatus"];

export const ROLE_LABELS: Record<Role, string> = {
  sys_admin: "系统管理员",
  org_admin: "组织管理员",
  builder: "搭建者",
  end_user: "终端用户",
};

export const USER_STATUS_LABELS: Record<UserStatus, string> = {
  active: "启用",
  disabled: "已停用",
};

export const VISIBILITY_LABELS: Record<Visibility, string> = {
  private: "仅自己",
  org: "本组织",
  platform: "全平台",
};

export const RESOURCE_STATUS_LABELS: Record<ResourceStatus, string> = {
  enabled: "启用",
  disabled: "已停用",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role as Role] ?? role;
}

export function userStatusLabel(status: string): string {
  return USER_STATUS_LABELS[status as UserStatus] ?? status;
}

export function visibilityLabel(visibility: string): string {
  return VISIBILITY_LABELS[visibility as Visibility] ?? visibility;
}

export function resourceStatusLabel(status: string): string {
  return RESOURCE_STATUS_LABELS[status as ResourceStatus] ?? status;
}

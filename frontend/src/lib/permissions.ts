import type { Me } from "../api/me";
import { useMe } from "../api/me";

/** 是否拥有某个权限码；me 未加载时一律为 false。 */
export function hasPerm(me: Me | undefined | null, code: string): boolean {
  return me?.permissions?.includes(code) ?? false;
}

/** 当前用户是否拥有某个权限码（仅用于显示，安全以后端校验为准）。 */
export function usePermission(code: string): boolean {
  const { data: me } = useMe();
  return hasPerm(me, code);
}

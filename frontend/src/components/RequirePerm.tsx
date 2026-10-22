import { Result } from "antd";
import type { ReactNode } from "react";

import { usePermission } from "../lib/permissions";

/** 有路由但没有权限码时显示 403 页，不跳走。 */
export function RequirePerm({
  perm,
  children,
}: {
  perm: string;
  children: ReactNode;
}) {
  const allowed = usePermission(perm);
  if (!allowed) {
    return (
      <Result status="403" title="403" subTitle="没有权限访问该页面" />
    );
  }
  return <>{children}</>;
}

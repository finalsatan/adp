import { Result, Spin } from "antd";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { getErrorMessage, isApiError } from "../api/errors";
import { useMe } from "../api/me";

function redirectTarget(pathname: string, search: string): string {
  return `/login?redirect=${encodeURIComponent(pathname + search)}`;
}

/** 需要登录的路由外壳：加载中转圈、未登录跳登录页、待改密跳改密页。 */
export function RequireAuth() {
  const location = useLocation();
  const { data: me, isLoading, error } = useMe();

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", paddingTop: 120 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error) {
    if (isApiError(error) && error.status === 401) {
      return (
        <Navigate
          to={redirectTarget(location.pathname, location.search)}
          replace
        />
      );
    }
    return (
      <Result
        status="error"
        title="加载失败"
        subTitle={getErrorMessage(error)}
      />
    );
  }

  if (!me) {
    return (
      <div style={{ display: "flex", justifyContent: "center", paddingTop: 120 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (me.must_change_password && location.pathname !== "/change-password") {
    return <Navigate to="/change-password" replace />;
  }

  return <Outlet />;
}

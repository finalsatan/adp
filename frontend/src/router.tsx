import { Spin } from "antd";
import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { useMe } from "./api/me";
import { RequireAuth } from "./components/RequireAuth";
import { RequirePerm } from "./components/RequirePerm";
import ChatLayout from "./layouts/ChatLayout";
import ConsoleLayout from "./layouts/ConsoleLayout";
import { buildMenu } from "./lib/menu";

const LoginPage = lazy(() => import("./pages/auth/LoginPage"));
const ChangePasswordPage = lazy(() => import("./pages/auth/ChangePasswordPage"));
const ProfilePage = lazy(() => import("./pages/profile/ProfilePage"));
const MyAppsPage = lazy(() => import("./pages/myapps/MyAppsPage"));
const UsersPage = lazy(() => import("./pages/system/users/UsersPage"));
const OrgsPage = lazy(() => import("./pages/system/orgs/OrgsPage"));
const OverviewPage = lazy(() => import("./pages/system/overview/OverviewPage"));
const AuditPage = lazy(() => import("./pages/system/audit/AuditPage"));
const SettingsPage = lazy(() => import("./pages/system/settings/SettingsPage"));
const ModelsPage = lazy(() => import("./pages/models/ModelsPage"));
const PlaygroundPage = lazy(() => import("./pages/models/PlaygroundPage"));
const UsagePage = lazy(() => import("./pages/observe/UsagePage"));
const KbListPage = lazy(() => import("./pages/kb/KbListPage"));
const DocumentsPage = lazy(() => import("./pages/kb/documents/DocumentsPage"));
const KbSettingsPage = lazy(() => import("./pages/kb/KbSettingsPage"));
const KbPermissionsPage = lazy(() => import("./pages/kb/KbPermissionsPage"));
const NotFoundPage = lazy(() => import("./pages/NotFoundPage"));

function Lazy({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div style={{ display: "flex", justifyContent: "center", paddingTop: 120 }}>
          <Spin size="large" />
        </div>
      }
    >
      {children}
    </Suspense>
  );
}

/** 根路径去向：终端用户去“我的应用”，其他角色去侧栏第一个可见菜单。 */
function HomeRedirect() {
  const { data: me, isLoading } = useMe();

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", paddingTop: 120 }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!me) {
    return <Navigate to="/login" replace />;
  }
  if (me.role === "end_user") {
    return <Navigate to="/my-apps" replace />;
  }
  const first = buildMenu(me)[0]?.items[0];
  return <Navigate to={first ? first.path : "/profile"} replace />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <Lazy>
            <LoginPage />
          </Lazy>
        }
      />

      <Route element={<RequireAuth />}>
        <Route
          path="/change-password"
          element={
            <Lazy>
              <ChangePasswordPage />
            </Lazy>
          }
        />

        <Route element={<ChatLayout />}>
          <Route
            path="/my-apps"
            element={
              <Lazy>
                <MyAppsPage />
              </Lazy>
            }
          />
        </Route>

        <Route element={<ConsoleLayout />}>
          <Route
            path="/profile"
            element={
              <Lazy>
                <ProfilePage />
              </Lazy>
            }
          />
          <Route
            path="/models"
            element={
              <RequirePerm perm="console.use">
                <Lazy>
                  <ModelsPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/models/playground"
            element={
              <RequirePerm perm="console.use">
                <Lazy>
                  <PlaygroundPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/system/users"
            element={
              <RequirePerm perm="user.manage">
                <Lazy>
                  <UsersPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/system/orgs"
            element={
              <RequirePerm perm="org.manage">
                <Lazy>
                  <OrgsPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/system/overview"
            element={
              <RequirePerm perm="overview.view">
                <Lazy>
                  <OverviewPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/system/audit"
            element={
              <RequirePerm perm="audit.view">
                <Lazy>
                  <AuditPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/system/settings"
            element={
              <RequirePerm perm="settings.manage">
                <Lazy>
                  <SettingsPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/observe/usage"
            element={
              <RequirePerm perm="usage.view">
                <Lazy>
                  <UsagePage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/kb"
            element={
              <RequirePerm perm="console.use">
                <Lazy>
                  <KbListPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/kb/:id"
            element={
              <RequirePerm perm="console.use">
                <Lazy>
                  <DocumentsPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/kb/:id/settings"
            element={
              <RequirePerm perm="console.use">
                <Lazy>
                  <KbSettingsPage />
                </Lazy>
              </RequirePerm>
            }
          />
          <Route
            path="/kb/:id/permissions"
            element={
              <RequirePerm perm="console.use">
                <Lazy>
                  <KbPermissionsPage />
                </Lazy>
              </RequirePerm>
            }
          />
        </Route>
      </Route>

      <Route path="/" element={<HomeRedirect />} />
      <Route
        path="*"
        element={
          <Lazy>
            <NotFoundPage />
          </Lazy>
        }
      />
    </Routes>
  );
}

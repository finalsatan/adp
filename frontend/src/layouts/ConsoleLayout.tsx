import { RobotOutlined } from "@ant-design/icons";
import { Breadcrumb, Layout, Menu, Space } from "antd";
import { Link, Navigate, Outlet, useLocation } from "react-router-dom";

import { useMe } from "../api/me";
import { UserMenu } from "../components/UserMenu";
import { buildMenu, findMenuItem } from "../lib/menu";

/** 控制台布局：左侧菜单 + 顶栏（面包屑、我的应用、用户菜单）。 */
export default function ConsoleLayout() {
  const { data: me } = useMe();
  const location = useLocation();
  const groups = buildMenu(me);
  const found = findMenuItem(location.pathname);

  // 终端用户不进控制台（技术方案 11、应用技术方案 11）
  if (me?.role === "end_user") {
    return <Navigate to="/my-apps" replace />;
  }

  const menuItems = groups.map((group) => {
    const Icon = group.icon;
    return {
      key: group.key,
      icon: <Icon />,
      label: group.label,
      children: group.items.map((item) => ({
        key: item.path,
        label: <Link to={item.path}>{item.label}</Link>,
      })),
    };
  });

  const breadcrumbItems = found
    ? [{ title: found.group.label }, { title: found.item.label }]
    : location.pathname === "/profile"
      ? [{ title: "个人中心" }]
      : [];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Sider theme="light" width={220}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            height: 56,
            padding: "0 16px",
            fontWeight: 600,
            fontSize: 15,
          }}
        >
          <RobotOutlined />
          <span>智能体开发平台</span>
        </div>
        <Menu
          mode="inline"
          items={menuItems}
          selectedKeys={[location.pathname]}
          defaultOpenKeys={groups.map((group) => group.key)}
          style={{ borderInlineEnd: "none" }}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header
          style={{
            background: "#fff",
            padding: "0 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottom: "1px solid #f0f0f0",
          }}
        >
          <Breadcrumb items={breadcrumbItems} />
          <Space size="large">
            <Link to="/my-apps">我的应用</Link>
            <UserMenu />
          </Space>
        </Layout.Header>
        <Layout.Content style={{ padding: 24 }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}

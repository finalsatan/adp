import { RobotOutlined } from "@ant-design/icons";
import { Layout, Space } from "antd";
import { Link, Outlet } from "react-router-dom";

import { useMe } from "../api/me";
import { UserMenu } from "../components/UserMenu";

/** “我的应用”布局：顶栏不带侧栏；终端用户不显示“返回控制台”。 */
export default function ChatLayout() {
  const { data: me } = useMe();

  return (
    <Layout style={{ minHeight: "100vh" }}>
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
        <Space>
          <RobotOutlined />
          <span style={{ fontWeight: 600 }}>智能体开发平台</span>
        </Space>
        <Space size="large">
          {me?.role !== "end_user" ? <Link to="/">返回控制台</Link> : null}
          <UserMenu />
        </Space>
      </Layout.Header>
      <Layout.Content style={{ padding: 24 }}>
        <Outlet />
      </Layout.Content>
    </Layout>
  );
}

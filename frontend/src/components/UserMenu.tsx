import { LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import { Avatar, Dropdown, Space } from "antd";
import { useNavigate } from "react-router-dom";

import { client } from "../api/client";
import { useMe } from "../api/me";

/** 顶栏用户菜单：所属组织、个人中心、退出登录。 */
export function UserMenu() {
  const { data: me } = useMe();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const orgLabel = me?.org?.name ?? "系统管理员";

  const onClick = async ({ key }: { key: string }) => {
    if (key === "profile") {
      navigate("/profile");
      return;
    }
    if (key === "logout") {
      try {
        await client.POST("/api/v1/auth/logout");
      } catch {
        // 登出失败也清掉本地状态
      }
      queryClient.clear();
      navigate("/login", { replace: true });
    }
  };

  return (
    <Dropdown
      menu={{
        items: [
          { key: "org", label: orgLabel, disabled: true },
          { type: "divider" },
          { key: "profile", icon: <UserOutlined />, label: "个人中心" },
          { key: "logout", icon: <LogoutOutlined />, label: "退出登录" },
        ],
        onClick,
      }}
    >
      <a style={{ color: "inherit", cursor: "pointer" }}>
        <Space>
          <Avatar size="small">{me?.name?.slice(0, 1) ?? "?"}</Avatar>
          <span>{me?.name ?? ""}</span>
        </Space>
      </a>
    </Dropdown>
  );
}

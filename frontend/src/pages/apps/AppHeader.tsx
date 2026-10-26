import { Space, Tabs, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";

import { visibilityLabel } from "../../lib/labels";
import { appStatusLabel, appTypeLabel, versionLabel, type App } from "./api";

export type AppTabKey = "config" | "versions" | "logs" | "feedback" | "permissions";

interface AppHeaderProps {
  app: App;
  active: AppTabKey;
}

function tabPath(appId: number, key: AppTabKey): string {
  switch (key) {
    case "config":
      return `/apps/${appId}`;
    case "versions":
      return `/apps/${appId}/versions`;
    case "logs":
      return `/apps/${appId}/logs`;
    case "feedback":
      return `/apps/${appId}/feedback`;
    case "permissions":
      return `/apps/${appId}/permissions`;
  }
}

/** 应用页头与页签，供配置、发布历史、对话日志、用户反馈、权限与设置页面复用（原型 #v-app / #v-appperm）。 */
export function AppHeader({ app, active }: AppHeaderProps) {
  const navigate = useNavigate();

  return (
    <div style={{ marginBottom: 16 }}>
      <Space align="center" wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {app.name}
        </Typography.Title>
        <Tag>{appTypeLabel(app.app_type)}</Tag>
        <Tag>{visibilityLabel(app.visibility)}</Tag>
        <Tag color={app.status === "published" ? "green" : undefined}>
          {appStatusLabel(app.status)}
        </Tag>
        {app.status === "published" && app.draft_has_changes ? (
          <Tag color="orange">草稿有修改</Tag>
        ) : null}
      </Space>
      <Typography.Paragraph type="secondary" style={{ margin: "4px 0 8px" }}>
        当前版本 {versionLabel(app.published_version)} · 创建人 {app.owner.name}
      </Typography.Paragraph>
      <Tabs
        activeKey={active}
        onChange={(key) => navigate(tabPath(app.id, key as AppTabKey))}
        items={[
          { key: "config", label: "配置与调试" },
          { key: "versions", label: "发布历史" },
          { key: "logs", label: "对话日志" },
          { key: "feedback", label: "用户反馈" },
          { key: "permissions", label: "权限与设置" },
        ]}
      />
    </div>
  );
}

import { Alert, Button, Card, Col, Empty, Row, Spin, Tag, Typography } from "antd";
import { Link } from "react-router-dom";

import { getErrorMessage } from "../../api/errors";
import { PageHeader } from "../../components/PageHeader";
import { appTypeLabel } from "../apps/api";
import { conversationPath, useMyApps } from "../chat/api";

/**
 * 终端用户“我的应用”（原型 #v-myapps，应用技术方案 11）。
 * 只列出使用范围包含本人、已发布且未停用的应用；卡片进入对话页。
 */
export default function MyAppsPage() {
  const { data, isLoading, error } = useMyApps();
  const apps = data ?? [];

  return (
    <>
      <PageHeader title="我的应用" description="你能使用的、已发布的应用。" />

      {isLoading ? (
        <div style={{ display: "flex", justifyContent: "center", paddingTop: 80 }}>
          <Spin size="large" />
        </div>
      ) : error ? (
        <Alert type="error" showIcon message={getErrorMessage(error)} />
      ) : apps.length === 0 ? (
        <Empty description="暂无可用的应用" />
      ) : (
        <Row gutter={[16, 16]}>
          {apps.map((app) => (
            <Col key={app.id} xs={24} md={12} xl={8}>
              <Card
                title={app.name}
                extra={<Tag>{appTypeLabel(app.app_type)}</Tag>}
                actions={[
                  <Link key="chat" to={conversationPath(app.id)}>
                    <Button type="primary">开始提问</Button>
                  </Link>,
                ]}
              >
                <Typography.Paragraph
                  type="secondary"
                  ellipsis={{ rows: 2 }}
                  style={{ minHeight: 44, marginBottom: 8 }}
                >
                  {app.description ?? "—"}
                </Typography.Paragraph>
                <Typography.Text type="secondary">
                  {app.org.name} · {appTypeLabel(app.app_type)}
                </Typography.Text>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
        只显示使用范围包含你、已发布且未停用的应用。更完整的浏览和搜索在 M4 探索广场。
      </Typography.Paragraph>
    </>
  );
}

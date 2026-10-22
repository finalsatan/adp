import { Space, Typography } from "antd";
import type { ReactNode } from "react";

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  extra?: ReactNode;
}

/** 页面标题区，照原型 .page-head。 */
export function PageHeader({ title, description, extra }: PageHeaderProps) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        gap: 16,
        marginBottom: 16,
      }}
    >
      <div>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
        {description ? (
          <Typography.Paragraph type="secondary" style={{ margin: "4px 0 0" }}>
            {description}
          </Typography.Paragraph>
        ) : null}
      </div>
      {extra ? <Space>{extra}</Space> : null}
    </div>
  );
}

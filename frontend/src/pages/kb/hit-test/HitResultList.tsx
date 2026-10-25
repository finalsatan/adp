import { Space, Tag, Typography } from "antd";
import { Link } from "react-router-dom";

import { formatScore, type HitItem } from "./api";

interface HitResultListProps {
  kbId: number;
  items: HitItem[];
  emptyText: string;
}

/** 命中结果列表（原型 #v-kbhit 右栏的每条结果）。 */
export function HitResultList({ kbId, items, emptyText }: HitResultListProps) {
  if (items.length === 0) {
    return <Typography.Text type="secondary">{emptyText}</Typography.Text>;
  }

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      {items.map((item, index) => (
        <div
          key={`${item.doc_id}-${item.chunk_id}`}
          style={{ border: "1px solid #f0f0f0", borderRadius: 8, padding: 12 }}
        >
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <b>{index + 1}</b>
            <Typography.Text strong>{formatScore(item.score)}</Typography.Text>
            <span>{item.doc_name}</span>
            {item.page_no != null ? <span>第 {item.page_no} 页</span> : null}
            {item.edited ? <Tag color="gold">已人工修改</Tag> : null}
            {item.kind === "table" ? <Tag>表格</Tag> : null}
            <span style={{ flex: 1 }} />
            <Link to={`/kb/${kbId}/documents/${item.doc_id}?chunk=${item.chunk_id}`}>
              原文位置
            </Link>
          </div>
          {item.title_path ? (
            <Typography.Text type="secondary">{item.title_path}</Typography.Text>
          ) : null}
          <Typography.Paragraph
            style={{
              marginBottom: 0,
              marginTop: 4,
              whiteSpace: "pre-wrap",
              fontFamily: item.kind === "table" ? "monospace" : undefined,
            }}
          >
            {item.content}
          </Typography.Paragraph>
        </div>
      ))}
    </Space>
  );
}

import { Space, Tabs, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";

import { visibilityLabel } from "../../lib/labels";
import { kbKindLabel, type Kb } from "./api";

export type KbTabKey = "documents" | "hit-test" | "settings" | "permissions";

interface KbHeaderProps {
  kb: Kb;
  active: KbTabKey;
}

function tabPath(kbId: number, key: KbTabKey): string {
  switch (key) {
    case "documents":
      return `/kb/${kbId}`;
    case "hit-test":
      return `/kb/${kbId}/hit-test`;
    case "settings":
      return `/kb/${kbId}/settings`;
    case "permissions":
      return `/kb/${kbId}/permissions`;
  }
}

/** 知识库页头与页签，供文档、命中测试、设置、权限页面复用（原型 #v-kbset / #v-kbperm）。 */
export function KbHeader({ kb, active }: KbHeaderProps) {
  const navigate = useNavigate();

  const countText =
    kb.kind === "doc"
      ? `${kb.doc_count} 个文档 · ${kb.chunk_count} 个分块`
      : `${kb.doc_count} 个问答对`;

  return (
    <div style={{ marginBottom: 16 }}>
      <Space align="center" wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {kb.name}
        </Typography.Title>
        <Tag>{kbKindLabel(kb.kind)}</Tag>
        <Tag>{visibilityLabel(kb.visibility)}</Tag>
      </Space>
      <Typography.Paragraph type="secondary" style={{ margin: "4px 0 8px" }}>
        {countText} · 向量化：{kb.embedding_model.name} · 创建人 {kb.owner.name}
      </Typography.Paragraph>
      <Tabs
        activeKey={active}
        onChange={(key) => navigate(tabPath(kb.id, key as KbTabKey))}
        items={[
          { key: "documents", label: "文档" },
          { key: "hit-test", label: "检索与命中测试" },
          { key: "settings", label: "设置" },
          { key: "permissions", label: "权限" },
        ]}
      />
    </div>
  );
}

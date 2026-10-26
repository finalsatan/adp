import { MoreOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Dropdown, Empty, Input, List, Modal, Typography } from "antd";
import { useState } from "react";

import { groupConversations, type Conversation } from "./api";

interface ConversationListProps {
  conversations: Conversation[];
  activeId: number | null;
  loading?: boolean;
  onNew: () => void;
  onSelect: (id: number) => void;
  onRename: (conversation: Conversation, title: string) => void;
  onDelete: (conversation: Conversation) => void;
}

/**
 * 对话页左侧会话列表（原型 #v-chat）：新建会话、按“今天 / 更早”分组、重命名、删除。
 * 删除确认框写明“只从你的列表中移除，对话日志仍保留”。
 */
export function ConversationList({
  conversations,
  activeId,
  loading = false,
  onNew,
  onSelect,
  onRename,
  onDelete,
}: ConversationListProps) {
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);

  const openRename = (conversation: Conversation) => {
    setRenameTarget(conversation);
    setRenameTitle(conversation.title);
  };

  const submitRename = () => {
    const title = renameTitle.trim();
    if (renameTarget && title.length > 0) {
      onRename(renameTarget, title);
    }
    setRenameTarget(null);
  };

  const groups = groupConversations(conversations);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Button
        block
        icon={<PlusOutlined />}
        style={{ marginBottom: 12 }}
        onClick={onNew}
        data-testid="new-conversation"
      >
        新建会话
      </Button>

      <div style={{ flex: 1, overflow: "auto" }}>
        {groups.length === 0 && !loading ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有会话" />
        ) : null}
        {groups.map((group) => (
          <div key={group.key}>
            <Typography.Text
              type="secondary"
              style={{ display: "block", padding: "8px", fontSize: 12 }}
            >
              {group.label}
            </Typography.Text>
            <List
              loading={loading}
              dataSource={group.items}
              split={false}
              renderItem={(item) => (
                <List.Item
                  data-testid={`conversation-${item.id}`}
                  onClick={() => onSelect(item.id)}
                  style={{
                    cursor: "pointer",
                    padding: "6px 8px",
                    borderRadius: 6,
                    background: item.id === activeId ? "#e6f4ff" : undefined,
                  }}
                  extra={
                    <Dropdown
                      trigger={["click"]}
                      menu={{
                        items: [
                          { key: "rename", label: "重命名" },
                          { key: "delete", label: "删除" },
                        ],
                        onClick: ({ key, domEvent }) => {
                          domEvent.stopPropagation();
                          if (key === "rename") {
                            openRename(item);
                          } else {
                            setDeleteTarget(item);
                          }
                        },
                      }}
                    >
                      <Button
                        type="text"
                        size="small"
                        icon={<MoreOutlined />}
                        aria-label={`会话操作 ${item.title}`}
                        onClick={(event) => event.stopPropagation()}
                      />
                    </Dropdown>
                  }
                >
                  <Typography.Text ellipsis style={{ maxWidth: 160 }}>
                    {item.title}
                  </Typography.Text>
                </List.Item>
              )}
            />
          </div>
        ))}
      </div>

      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12 }}>
        会话保存在服务端，换设备也能看到。点 ··· 重命名或删除；删除只从你的列表中移除，对话日志仍保留，供应用维护人员改进回答。
      </Typography.Paragraph>

      <Modal
        open={renameTarget !== null}
        title="重命名会话"
        okText="保存"
        cancelText="取消"
        onOk={submitRename}
        onCancel={() => setRenameTarget(null)}
      >
        <Input
          value={renameTitle}
          aria-label="会话名称"
          onChange={(event) => setRenameTitle(event.target.value)}
          onPressEnter={submitRename}
        />
      </Modal>

      <Modal
        open={deleteTarget !== null}
        title="删除会话"
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        onOk={() => {
          if (deleteTarget) {
            onDelete(deleteTarget);
          }
          setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      >
        <Typography.Paragraph>
          只从你的列表中移除，对话日志仍保留，供应用维护人员改进回答。
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}

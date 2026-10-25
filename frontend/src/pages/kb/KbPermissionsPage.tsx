import { App, Button, Card, Popconfirm, Radio, Space, Table, Typography, type TableProps } from "antd";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { getErrorMessage } from "../../api/errors";
import { formatTime } from "../../lib/format";
import {
  kbInUseRefs,
  useChangeKbVisibility,
  useKb,
  useKbMembers,
  useRemoveKbMember,
  type KbRef,
  type Member,
  type Visibility,
} from "./api";
import { KbCollaboratorModal } from "./KbCollaboratorModal";
import { KbHeader } from "./KbHeader";

const VISIBILITY_OPTIONS: { value: Visibility; label: string }[] = [
  { value: "private", label: "仅自己" },
  { value: "org", label: "本组织" },
  { value: "platform", label: "全平台" },
];

export default function KbPermissionsPage() {
  const { message } = App.useApp();
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const kbId = Number.isFinite(parsedId) ? parsedId : null;
  const { data: kb, isLoading } = useKb(kbId);
  const members = useKbMembers(kbId, kb != null);
  const changeVisibility = useChangeKbVisibility();
  const removeMember = useRemoveKbMember();

  const [addOpen, setAddOpen] = useState(false);
  const [blockedRefs, setBlockedRefs] = useState<KbRef[] | null>(null);

  if (isLoading || !kb) {
    return null;
  }

  const manageable = kb.manageable;
  const memberList = members.data ?? [];

  const handleVisibility = (visibility: Visibility) => {
    setBlockedRefs(null);
    changeVisibility.mutate(
      { id: kb.id, visibility },
      {
        onSuccess: () => message.success("已保存"),
        onError: (error) => {
          const refs = kbInUseRefs(error);
          if (refs.length > 0) {
            setBlockedRefs(refs);
            return;
          }
          message.error(getErrorMessage(error));
        },
      },
    );
  };

  const handleRemove = (userId: number) =>
    removeMember.mutate(
      { id: kb.id, userId },
      {
        onSuccess: () => message.success("已移除"),
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );

  const columns: TableProps<Member>["columns"] = [
    {
      title: "用户",
      render: (_value, member) => `${member.user.username} ${member.user.name}`,
    },
    { title: "所属组织", render: (_value, member) => member.user.org?.name ?? "—" },
    { title: "添加人", render: (_value, member) => member.added_by.name },
    { title: "时间", render: (_value, member) => formatTime(member.added_at) },
    {
      title: "操作",
      render: (_value, member) =>
        manageable ? (
          <Popconfirm
            title="移除该协作者？"
            description="移除后该用户将不能再查看和编辑本知识库。"
            okText="移除"
            cancelText="取消"
            onConfirm={() => handleRemove(member.user.id)}
          >
            <a>移除</a>
          </Popconfirm>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <div>
      <KbHeader kb={kb} active="permissions" />

      <Card title="可见范围" style={{ marginBottom: 16 }}>
        <Radio.Group
          value={kb.visibility}
          disabled={!manageable}
          onChange={(event) => handleVisibility(event.target.value as Visibility)}
        >
          <Space direction="vertical">
            {VISIBILITY_OPTIONS.map((option) => (
              <Radio key={option.value} value={option.value}>
                {option.label}
              </Radio>
            ))}
          </Space>
        </Radio.Group>
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          “本组织”包含下级组织：{kb.org.name}及下级组织的用户都能查看和在应用中引用。缩小可见范围前会检查引用它的应用。
        </Typography.Paragraph>
        {blockedRefs ? (
          <Typography.Paragraph type="warning" style={{ marginTop: 12, marginBottom: 0 }}>
            这些应用会因此不能再引用它，请先在应用中解除引用：
            {blockedRefs.map((ref) => `${ref.name}（${ref.type_label}）`).join("、")}
          </Typography.Paragraph>
        ) : null}
      </Card>

      <Card
        title="协作者"
        extra={
          manageable ? (
            <Button type="primary" onClick={() => setAddOpen(true)}>
              添加协作者
            </Button>
          ) : null
        }
        style={{ marginBottom: 16 }}
      >
        <Typography.Paragraph type="secondary">
          可查看和编辑文档、解析结果、分块和配置；不能改可见范围、增减协作者、停用或删除
        </Typography.Paragraph>
        <Table<Member>
          rowKey={(member) => member.user.id}
          columns={columns}
          dataSource={memberList}
          loading={members.isLoading}
          pagination={false}
          size="small"
        />
      </Card>

      <Typography.Paragraph type="secondary">
        管理权限（改可见范围、增减协作者、停用、删除）：创建人 {kb.owner.name}、
        {kb.org.name}及上级组织的组织管理员、系统管理员。
      </Typography.Paragraph>

      <KbCollaboratorModal
        open={addOpen}
        kb={kb}
        existingIds={new Set(memberList.map((member) => member.user.id))}
        onClose={() => setAddOpen(false)}
      />
    </div>
  );
}

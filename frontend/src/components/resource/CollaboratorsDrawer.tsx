import { PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Drawer,
  Modal,
  Popconfirm,
  Select,
  Table,
  Typography,
  type TableProps,
} from "antd";
import { useEffect, useState, type ReactNode } from "react";

import { getErrorMessage } from "../../api/errors";
import { formatTime } from "../../lib/format";
import {
  resourceInUseRefs,
  useAddResourceMember,
  useRemoveResourceMember,
  useResourceMembers,
  useUserSearch,
  type Member,
  type ResourceRef,
} from "./api";

export interface CollaboratorTarget {
  id: number;
  name: string;
}

interface CollaboratorsDrawerProps {
  open: boolean;
  resource: CollaboratorTarget | null;
  /** 添加协作者的说明文字，由调用方按资源类型传入。 */
  hint?: ReactNode;
  onClose: () => void;
}

/**
 * 资源共用的“协作者”抽屉（需求 10.4）。
 * 第三期起的知识库、入库方案、应用页面直接复用。
 */
export function CollaboratorsDrawer({
  open,
  resource,
  hint,
  onClose,
}: CollaboratorsDrawerProps) {
  const { message } = App.useApp();
  const resourceId = resource?.id ?? 0;
  const members = useResourceMembers(resourceId, open && resourceId > 0);
  const removeMember = useRemoveResourceMember();
  const [addOpen, setAddOpen] = useState(false);
  const [refs, setRefs] = useState<ResourceRef[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setRefs([]);
      setError(null);
    }
  }, [open]);

  const handleRemove = (userId: number) => {
    removeMember.mutate(
      { id: resourceId, userId },
      {
        onSuccess: () => message.success("已移除"),
        onError: (err) => {
          const blockers = resourceInUseRefs(err);
          if (blockers.length > 0) {
            setRefs(blockers);
          } else {
            setError(getErrorMessage(err));
          }
        },
      },
    );
  };

  const columns: TableProps<Member>["columns"] = [
    {
      title: "用户名",
      render: (_value, member) => <Typography.Text strong>{member.user.username}</Typography.Text>,
    },
    { title: "姓名", render: (_value, member) => member.user.name },
    { title: "所属组织", render: (_value, member) => member.user.org?.name ?? "—" },
    { title: "添加人", render: (_value, member) => member.added_by.name },
    { title: "添加时间", render: (_value, member) => formatTime(member.added_at) },
    {
      title: "操作",
      render: (_value, member) => (
        <Popconfirm
          title="移除该协作者？"
          description="移除后该用户将不能再查看和编辑本资源。"
          okText="移除"
          cancelText="取消"
          onConfirm={() => handleRemove(member.user.id)}
        >
          <a>移除</a>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Drawer
      open={open}
      width={640}
      title={resource ? `协作者 · ${resource.name}` : "协作者"}
      onClose={onClose}
      destroyOnHidden
    >
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>
          添加协作者
        </Button>
      </div>

      {refs.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="这些应用会因此不能再引用它，请先在应用中解除引用"
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {refs.map((ref) => (
                <li key={`${ref.type}-${ref.id}`}>
                  {ref.name}（{ref.type}）
                </li>
              ))}
            </ul>
          }
        />
      ) : null}

      {error ? (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message={error} />
      ) : null}

      <Table<Member>
        rowKey={(member) => member.user.id}
        columns={columns}
        dataSource={members.data ?? []}
        loading={members.isLoading}
        pagination={false}
        size="small"
      />

      <AddCollaboratorModal
        open={addOpen}
        resource={resource}
        hint={hint}
        existingIds={new Set((members.data ?? []).map((member) => member.user.id))}
        onClose={() => setAddOpen(false)}
      />
    </Drawer>
  );
}

interface AddCollaboratorModalProps {
  open: boolean;
  resource: CollaboratorTarget | null;
  hint?: ReactNode;
  existingIds: Set<number>;
  onClose: () => void;
}

function AddCollaboratorModal({
  open,
  resource,
  hint,
  existingIds,
  onClose,
}: AddCollaboratorModalProps) {
  const { message } = App.useApp();
  const addMember = useAddResourceMember();
  const [keyword, setKeyword] = useState("");
  const [userId, setUserId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const search = useUserSearch(keyword);

  useEffect(() => {
    if (open) {
      setKeyword("");
      setUserId(null);
      setError(null);
    }
  }, [open]);

  const options = (search.data ?? [])
    .filter((user) => !existingIds.has(user.id))
    .map((user) => ({
      value: user.id,
      label: `${user.username} ${user.name} · ${user.org?.name ?? "无组织"}`,
    }));

  const handleAdd = () => {
    if (!resource || userId == null) {
      setError("请选择要添加的用户");
      return;
    }
    setError(null);
    addMember.mutate(
      { id: resource.id, userId },
      {
        onSuccess: () => {
          message.success("已添加");
          onClose();
        },
        onError: (err) => setError(getErrorMessage(err)),
      },
    );
  };

  return (
    <Modal
      open={open}
      title={resource ? `添加协作者 · ${resource.name}` : "添加协作者"}
      okText="添加"
      cancelText="取消"
      confirmLoading={addMember.isPending}
      onOk={handleAdd}
      onCancel={onClose}
      destroyOnHidden
    >
      <Select
        showSearch
        value={userId ?? undefined}
        placeholder="搜索用户名或姓名，可选任何组织的用户"
        style={{ width: "100%" }}
        filterOption={false}
        onSearch={setKeyword}
        onChange={(value) => setUserId(value)}
        options={options}
        notFoundContent={
          keyword.trim().length > 0
            ? search.isFetching
              ? "搜索中…"
              : "没有匹配的用户"
            : "输入关键字搜索"
        }
      />

      {error ? (
        <Alert type="error" showIcon style={{ marginTop: 12 }} message={error} />
      ) : null}

      {hint ? (
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          {hint}
        </Typography.Paragraph>
      ) : null}
    </Modal>
  );
}

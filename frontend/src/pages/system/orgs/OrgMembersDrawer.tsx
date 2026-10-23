import { Button, Drawer, Space, Table, Tag, Typography, type TableProps } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { useMe } from "../../../api/me";
import { roleLabel } from "../../../lib/labels";
import type { OperatorScope, Org } from "../../../lib/orgTree";
import { UserEditDrawer } from "../users/UserEditDrawer";
import { useOrgMembers, useUser, type OrgMember } from "./api";

interface OrgMembersDrawerProps {
  open: boolean;
  org: Org | null;
  orgs: Org[];
  scope: OperatorScope;
  onClose: () => void;
}

function memberStatusTag(member: OrgMember): ReactNode {
  if (member.status === "active") {
    return <Tag color="green">启用</Tag>;
  }
  return <Tag>已停用</Tag>;
}

/** 组织成员抽屉（原型 #d-orgmembers）：只列出直接属于该组织的用户。 */
export function OrgMembersDrawer({
  open,
  org,
  orgs,
  scope,
  onClose,
}: OrgMembersDrawerProps) {
  const { data: me } = useMe();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [editId, setEditId] = useState<number | null>(null);

  const { data, isLoading } = useOrgMembers(open ? (org?.id ?? null) : null, {
    page,
    page_size: pageSize,
  });
  const { data: editUser } = useUser(editId);

  useEffect(() => {
    if (!open) {
      setPage(1);
      setEditId(null);
    }
  }, [open]);

  const columns: TableProps<OrgMember>["columns"] = [
    {
      title: "用户",
      render: (_value, member) => (
        <Space size={6}>
          <Typography.Text strong>{member.username}</Typography.Text>
          <Typography.Text type="secondary">{member.name}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "角色",
      dataIndex: "role",
      render: (role: OrgMember["role"]) => roleLabel(role),
    },
    {
      title: "状态",
      render: (_value, member) => memberStatusTag(member),
    },
    {
      title: "操作",
      render: (_value, member) => {
        if (member.id === me?.id) {
          return <Link to="/profile">个人中心</Link>;
        }
        if (!member.manageable) {
          return null;
        }
        return <a onClick={() => setEditId(member.id)}>编辑</a>;
      },
    },
  ];

  return (
    <Drawer
      title={org ? `成员 · ${org.name}` : "成员"}
      width={640}
      open={open}
      onClose={onClose}
      destroyOnHidden
      footer={
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>关闭</Button>
        </Space>
      }
    >
      <Table<OrgMember>
        rowKey="id"
        columns={columns}
        dataSource={data?.items ?? []}
        loading={isLoading}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (count) => `共 ${count} 条`,
          onChange: (nextPage, nextPageSize) => {
            if (nextPageSize !== pageSize) {
              setPageSize(nextPageSize);
              setPage(1);
              return;
            }
            setPage(nextPage);
          },
        }}
      />
      <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
        {`只列出直接属于${org?.name ?? "该组织"}的用户，下级组织的用户在各自组织中查看。每个用户只属于一个组织；要把其他组织的用户调进来，在用户管理中修改其所属组织。`}
      </Typography.Paragraph>

      <UserEditDrawer
        open={editId !== null && editUser != null}
        user={editUser ?? null}
        orgs={orgs}
        scope={scope}
        onClose={() => setEditId(null)}
        onSaved={() => {
          void queryClient.invalidateQueries({ queryKey: ["org-members"] });
          void queryClient.invalidateQueries({ queryKey: ["orgs"] });
        }}
      />
    </Drawer>
  );
}

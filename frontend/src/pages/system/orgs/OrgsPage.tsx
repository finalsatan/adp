import { Alert, App, Button, Popconfirm, Space, Table, Typography, type TableProps } from "antd";
import dayjs from "dayjs";
import { useEffect, useMemo, useState, type Key, type ReactNode } from "react";

import { getErrorMessage, isApiError } from "../../../api/errors";
import { useMe } from "../../../api/me";
import { PageHeader } from "../../../components/PageHeader";
import {
  buildOrgTree,
  type OperatorScope,
  type Org,
  type OrgTreeNode,
} from "../../../lib/orgTree";
import { useDeleteOrg, useOrgs } from "./api";
import { OrgFormDrawer, type OrgFormMode } from "./OrgFormDrawer";
import { OrgMembersDrawer } from "./OrgMembersDrawer";

interface OrgRow extends Org {
  children?: OrgRow[];
}

function toRow(node: OrgTreeNode): OrgRow {
  return { ...node.org, children: node.children.map(toRow) };
}

interface FormState {
  open: boolean;
  mode: OrgFormMode;
  org: Org | null;
  parent: Org | null;
}

/** 组织管理页（原型 #v-orgs）。 */
export default function OrgsPage() {
  const { message, modal } = App.useApp();
  const { data: me } = useMe();
  const { data: orgs = [], isLoading } = useOrgs();
  const deleteOrg = useDeleteOrg();

  const [formState, setFormState] = useState<FormState>({
    open: false,
    mode: "create",
    org: null,
    parent: null,
  });
  const [membersOrg, setMembersOrg] = useState<Org | null>(null);
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([]);

  const orgTree = useMemo(() => buildOrgTree(orgs), [orgs]);
  const dataSource = useMemo(() => orgTree.map(toRow), [orgTree]);

  // 默认展开两级（根节点和它的直接下级）。
  useEffect(() => {
    const keys: Key[] = [];
    const walk = (nodes: OrgTreeNode[], depth: number) => {
      for (const node of nodes) {
        if (depth < 2 && node.children.length > 0) {
          keys.push(node.org.id);
        }
        walk(node.children, depth + 1);
      }
    };
    walk(orgTree, 0);
    setExpandedKeys(keys);
  }, [orgTree]);

  const myOrgPath = me?.org
    ? (orgs.find((org) => org.id === me.org?.id)?.path ?? null)
    : null;
  const scope: OperatorScope = { role: me?.role ?? "end_user", orgPath: myOrgPath };

  const openCreateTop = () =>
    setFormState({ open: true, mode: "create", org: null, parent: null });
  const openCreateChild = (org: Org) =>
    setFormState({ open: true, mode: "create", org: null, parent: org });
  const openEdit = (org: Org) =>
    setFormState({ open: true, mode: "edit", org, parent: null });
  const closeForm = () => setFormState((state) => ({ ...state, open: false }));

  const handleDelete = (org: Org) => {
    deleteOrg.mutate(org.id, {
      onSuccess: () => message.success("组织已删除"),
      onError: (error) => {
        if (isApiError(error) && error.code === "ORG_NOT_EMPTY") {
          const detail = (error.detail ?? {}) as Record<string, unknown>;
          modal.warning({
            title: "组织不能删除",
            content: `还有 ${detail.children ?? 0} 个下级组织、${detail.users ?? 0} 个用户、${detail.resources ?? 0} 个资源。`,
          });
          return;
        }
        message.error(getErrorMessage(error));
      },
    });
  };

  const renderActions = (org: OrgRow): ReactNode => {
    const nodes: ReactNode[] = [];
    if (org.editable) {
      nodes.push(
        <a key="edit" onClick={() => openEdit(org)}>
          编辑
        </a>,
      );
    }
    nodes.push(
      <a key="child" onClick={() => openCreateChild(org)}>
        添加下级
      </a>,
    );
    if (org.user_count > 0) {
      nodes.push(
        <a key="members" onClick={() => setMembersOrg(org)}>
          成员
        </a>,
      );
    }
    if (org.editable) {
      nodes.push(
        <Popconfirm
          key="delete"
          title="删除该组织？"
          description="删除后不可恢复。"
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => handleDelete(org)}
        >
          <a>删除</a>
        </Popconfirm>,
      );
    }
    return <Space size="middle">{nodes}</Space>;
  };

  const columns: TableProps<OrgRow>["columns"] = [
    {
      title: "组织名称",
      dataIndex: "name",
      render: (name: string) => <Typography.Text strong>{name}</Typography.Text>,
    },
    {
      title: "组织管理员",
      render: (_value, org) =>
        org.admins.length > 0
          ? org.admins.map((admin) => admin.username).join("、")
          : "—",
    },
    { title: "用户数", dataIndex: "user_count" },
    { title: "资源数", dataIndex: "resource_count" },
    { title: "下级组织", dataIndex: "child_count" },
    {
      title: "创建时间",
      dataIndex: "created_at",
      render: (value: string) => dayjs(value).format("YYYY-MM-DD"),
    },
    {
      title: "操作",
      render: (_value, org) => renderActions(org),
    },
  ];

  return (
    <>
      <PageHeader
        title="组织管理"
        description="组织为多级树，对应子公司、部门；由管理员手工维护。"
        extra={
          <Button type="primary" onClick={openCreateTop}>
            {me?.role === "sys_admin" ? "新建顶级组织" : "新建下级组织"}
          </Button>
        }
      />

      <Alert
        type="info"
        showIcon
        message="删除组织的前提：无下级组织、无用户、无资源。不做组织停用。组织管理员由系统管理员或上级组织的组织管理员任命（在用户管理中把用户的角色设为组织管理员）。"
        style={{ marginBottom: 16 }}
      />

      <Typography.Paragraph type="secondary">
        共 {orgs.length} 个组织
      </Typography.Paragraph>

      <Table<OrgRow>
        rowKey="id"
        columns={columns}
        dataSource={dataSource}
        loading={isLoading}
        pagination={false}
        expandable={{
          expandedRowKeys: expandedKeys,
          onExpandedRowsChange: (keys) => setExpandedKeys([...keys]),
        }}
      />

      <OrgFormDrawer
        open={formState.open}
        mode={formState.mode}
        org={formState.org}
        parentOrg={formState.parent}
        orgs={orgs}
        scope={scope}
        onClose={closeForm}
      />
      <OrgMembersDrawer
        open={membersOrg !== null}
        org={membersOrg}
        orgs={orgs}
        scope={scope}
        onClose={() => setMembersOrg(null)}
      />
    </>
  );
}

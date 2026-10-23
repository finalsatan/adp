import { SearchOutlined } from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Card,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tree,
  Typography,
  type TableProps,
  type TreeDataNode,
} from "antd";
import { useEffect, useMemo, useState, type Key, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { useMe } from "../../../api/me";
import { PageHeader } from "../../../components/PageHeader";
import { formatRelative } from "../../../lib/format";
import { roleLabel } from "../../../lib/labels";
import {
  buildOrgTree,
  type OperatorScope,
  type OrgTreeNode,
} from "../../../lib/orgTree";
import {
  useDisableUser,
  useEnableUser,
  useOrgs,
  useUnlockUser,
  useUsers,
  type Role,
  type User,
  type UserListParams,
  type UserStatus,
} from "./api";
import { ResetPasswordModal } from "./ResetPasswordModal";
import { RevokeSysAdminModal } from "./RevokeSysAdminModal";
import { UserCreateDrawer } from "./UserCreateDrawer";
import { UserEditDrawer } from "./UserEditDrawer";

type Selection = { kind: "org"; orgId: number } | { kind: "sys_admin" };

const ROLE_OPTIONS: { value: Role | "all"; label: string }[] = [
  { value: "all", label: "全部角色" },
  { value: "org_admin", label: "组织管理员" },
  { value: "builder", label: "搭建者" },
  { value: "end_user", label: "终端用户" },
];

const STATUS_OPTIONS: { value: UserStatus | "all"; label: string }[] = [
  { value: "all", label: "全部状态" },
  { value: "active", label: "启用" },
  { value: "disabled", label: "已停用" },
];

function toTreeDataNode(node: OrgTreeNode): TreeDataNode {
  return {
    key: `org:${node.org.id}`,
    title: (
      <span>
        {node.org.name}{" "}
        <Typography.Text type="secondary">{node.totalUserCount}</Typography.Text>
      </span>
    ),
    children: node.children.map(toTreeDataNode),
  };
}

function statusTag(user: User): ReactNode {
  if (user.locked) {
    return <Tag color="orange">已锁定</Tag>;
  }
  if (user.status === "active") {
    return <Tag color="green">启用</Tag>;
  }
  return <Tag>已停用</Tag>;
}

/** 用户管理页（原型 #v-users）。 */
export default function UsersPage() {
  const { message } = App.useApp();
  const { data: me } = useMe();
  const { data: orgs = [] } = useOrgs();

  const [selection, setSelection] = useState<Selection | null>(null);
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([]);
  const [keyword, setKeyword] = useState("");
  const [roleFilter, setRoleFilter] = useState<Role | "all">("all");
  const [statusFilter, setStatusFilter] = useState<UserStatus | "all">("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const [createOpen, setCreateOpen] = useState(false);
  const [editUser, setEditUser] = useState<User | null>(null);
  const [resetUser, setResetUser] = useState<User | null>(null);
  const [revokeUser, setRevokeUser] = useState<User | null>(null);

  const disableUser = useDisableUser();
  const enableUser = useEnableUser();
  const unlockUser = useUnlockUser();

  // 默认选中操作人自己的组织；系统管理员（无所属组织）选第一个顶级组织。
  useEffect(() => {
    if (selection !== null || !me || orgs.length === 0) {
      return;
    }
    if (me.org) {
      const own = orgs.find((org) => org.id === me.org?.id);
      if (own) {
        setSelection({ kind: "org", orgId: own.id });
        return;
      }
    }
    const first = buildOrgTree(orgs)[0];
    if (first) {
      setSelection({ kind: "org", orgId: first.org.id });
    } else if (me.role === "sys_admin") {
      setSelection({ kind: "sys_admin" });
    }
  }, [selection, me, orgs]);

  const isSysAdminGroup = selection?.kind === "sys_admin";

  const params = useMemo<UserListParams>(() => {
    const value: UserListParams = { page, page_size: pageSize, include_sub: true };
    if (isSysAdminGroup) {
      value.role = "sys_admin";
    } else if (selection?.kind === "org") {
      value.org_id = selection.orgId;
    }
    if (!isSysAdminGroup && roleFilter !== "all") {
      value.role = roleFilter;
    }
    if (statusFilter !== "all") {
      value.status = statusFilter;
    }
    if (keyword) {
      value.keyword = keyword;
    }
    return value;
  }, [selection, isSysAdminGroup, roleFilter, statusFilter, keyword, page, pageSize]);

  const { data, isLoading } = useUsers(params);

  const myOrgPath = me?.org
    ? (orgs.find((org) => org.id === me.org?.id)?.path ?? null)
    : null;
  const scope: OperatorScope = { role: me?.role ?? "end_user", orgPath: myOrgPath };

  const treeData = useMemo<TreeDataNode[]>(() => {
    const nodes = buildOrgTree(orgs).map(toTreeDataNode);
    if (me?.role === "sys_admin") {
      return [{ key: "sys_admin", title: "系统管理员", isLeaf: true }, ...nodes];
    }
    return nodes;
  }, [orgs, me]);

  // orgs 是异步到达的，defaultExpandAll 在首次渲染（空树）时已失效，改为按树数据展开。
  useEffect(() => {
    const keys: Key[] = [];
    const walk = (nodes: TreeDataNode[]) => {
      for (const node of nodes) {
        if (node.children && node.children.length > 0) {
          keys.push(node.key);
          walk(node.children);
        }
      }
    };
    walk(treeData);
    setExpandedKeys(keys);
  }, [treeData]);

  const selectedKeys = selection
    ? [selection.kind === "sys_admin" ? "sys_admin" : `org:${selection.orgId}`]
    : [];

  const selectedOrg =
    selection?.kind === "org"
      ? orgs.find((org) => org.id === selection.orgId)
      : undefined;
  const countLabel = isSysAdminGroup
    ? `系统管理员共 ${data?.total ?? 0} 人`
    : `${selectedOrg?.name ?? "全部"}（含下级）共 ${data?.total ?? 0} 人`;

  const handleSelect = (keys: Key[]) => {
    const key = keys[0];
    if (typeof key !== "string") {
      return;
    }
    if (key === "sys_admin") {
      setSelection({ kind: "sys_admin" });
    } else if (key.startsWith("org:")) {
      setSelection({ kind: "org", orgId: Number(key.slice(4)) });
    }
    setPage(1);
  };

  const handleDisable = (user: User) =>
    disableUser.mutate(user.id, { onSuccess: () => message.success("已停用") });
  const handleEnable = (user: User) =>
    enableUser.mutate(user.id, { onSuccess: () => message.success("已启用") });
  const handleUnlock = (user: User) =>
    unlockUser.mutate(user.id, { onSuccess: () => message.success("已解锁") });

  const renderActions = (user: User): ReactNode => {
    const isSelf = user.id === me?.id;

    if (isSysAdminGroup) {
      const nodes: ReactNode[] = [];
      if (isSelf) {
        nodes.push(
          <Link key="profile" to="/profile">
            个人中心
          </Link>,
        );
      } else if (user.manageable) {
        nodes.push(
          <a key="edit" onClick={() => setEditUser(user)}>
            编辑
          </a>,
          <a key="reset" onClick={() => setResetUser(user)}>
            重置密码
          </a>,
          user.status === "active" ? (
            <Popconfirm
              key="disable"
              title="停用该用户？"
              description="停用后账号、资源和审计记录保留；已登录的会话最迟在访问令牌到期时失效。"
              okText="停用"
              cancelText="取消"
              onConfirm={() => handleDisable(user)}
            >
              <a>停用</a>
            </Popconfirm>
          ) : (
            <a key="enable" onClick={() => handleEnable(user)}>
              启用
            </a>
          ),
        );
      }
      nodes.push(
        <a key="revoke" onClick={() => setRevokeUser(user)}>
          撤销系统管理员
        </a>,
      );
      return <Space size="middle">{nodes}</Space>;
    }

    if (isSelf) {
      return (
        <Link to="/profile">个人中心</Link>
      );
    }
    if (!user.manageable) {
      return null;
    }

    const nodes: ReactNode[] = [];
    if (user.locked) {
      nodes.push(
        <a key="unlock" onClick={() => handleUnlock(user)}>
          解锁
        </a>,
      );
    }
    nodes.push(
      <a key="edit" onClick={() => setEditUser(user)}>
        编辑
      </a>,
      <a key="reset" onClick={() => setResetUser(user)}>
        重置密码
      </a>,
      user.status === "active" ? (
        <Popconfirm
          key="disable"
          title="停用该用户？"
          description="停用后账号、资源和审计记录保留；已登录的会话最迟在访问令牌到期时失效。"
          okText="停用"
          cancelText="取消"
          onConfirm={() => handleDisable(user)}
        >
          <a>停用</a>
        </Popconfirm>
      ) : (
        <a key="enable" onClick={() => handleEnable(user)}>
          启用
        </a>
      ),
    );
    return <Space size="middle">{nodes}</Space>;
  };

  const columns: TableProps<User>["columns"] = [
    {
      title: "用户名",
      dataIndex: "username",
      render: (_value, user) => (
        <Space size={4}>
          <Typography.Text strong>{user.username}</Typography.Text>
          {user.id === me?.id ? <Tag>我</Tag> : null}
        </Space>
      ),
    },
    { title: "姓名", dataIndex: "name" },
    {
      title: "所属组织",
      render: (_value, user) => user.org?.name ?? "-",
    },
    {
      title: "角色",
      dataIndex: "role",
      render: (role: Role) => <Tag>{roleLabel(role)}</Tag>,
    },
    {
      title: "邮箱",
      dataIndex: "email",
      render: (email: string | null) => (
        <Typography.Text type="secondary">{email ?? "-"}</Typography.Text>
      ),
    },
    {
      title: "状态",
      render: (_value, user) => statusTag(user),
    },
    {
      title: "最后登录",
      dataIndex: "last_login_at",
      render: (value: string | null) => (
        <Typography.Text type="secondary">{formatRelative(value)}</Typography.Text>
      ),
    },
    {
      title: "操作",
      render: (_value, user) => renderActions(user),
    },
  ];

  const identityAlert =
    me?.role === "sys_admin"
      ? "当前身份：系统管理员，可管理所有用户。"
      : me?.org
        ? `当前身份：${me.org.name}的组织管理员。可管理 ${me.org.name} 及下级组织的用户；同级、上级组织管理员和系统管理员的账号不能操作，只能把下级组织的用户设为组织管理员。`
        : "当前身份：组织管理员。";

  return (
    <>
      <PageHeader
        title="用户管理"
        description="用户由本平台创建；每个用户只属于一个组织、只有一个角色。停用后账号、资源和审计记录保留。"
        extra={
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建用户
          </Button>
        }
      />

      <Alert
        type="info"
        showIcon
        message={identityAlert}
        style={{ marginBottom: 16 }}
      />

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <Card title="组织" style={{ width: 260, flex: "0 0 auto" }}>
          <Tree
            treeData={treeData}
            selectedKeys={selectedKeys}
            expandedKeys={expandedKeys}
            onExpand={(keys) => setExpandedKeys([...keys])}
            onSelect={handleSelect}
          />
        </Card>

        <Card style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
              marginBottom: 16,
            }}
          >
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索用户名 / 姓名 / 邮箱"
              style={{ width: 240 }}
              onChange={(event) => {
                if (!event.target.value) {
                  setKeyword("");
                  setPage(1);
                }
              }}
              onPressEnter={(event) => {
                setKeyword((event.target as HTMLInputElement).value);
                setPage(1);
              }}
            />
            <Select
              value={roleFilter}
              disabled={isSysAdminGroup}
              style={{ width: 140 }}
              options={ROLE_OPTIONS}
              onChange={(value) => {
                setRoleFilter(value);
                setPage(1);
              }}
            />
            <Select
              value={statusFilter}
              style={{ width: 140 }}
              options={STATUS_OPTIONS}
              onChange={(value) => {
                setStatusFilter(value);
                setPage(1);
              }}
            />
            <Typography.Text type="secondary" style={{ marginLeft: "auto" }}>
              {countLabel}
            </Typography.Text>
          </div>

          <Table<User>
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
        </Card>
      </div>

      <UserCreateDrawer
        open={createOpen}
        orgs={orgs}
        scope={scope}
        onClose={() => setCreateOpen(false)}
      />
      <UserEditDrawer
        open={editUser !== null}
        user={editUser}
        orgs={orgs}
        scope={scope}
        onClose={() => setEditUser(null)}
      />
      <ResetPasswordModal
        open={resetUser !== null}
        user={resetUser}
        onClose={() => setResetUser(null)}
      />
      <RevokeSysAdminModal
        open={revokeUser !== null}
        user={revokeUser}
        orgs={orgs}
        onClose={() => setRevokeUser(null)}
      />
    </>
  );
}

import { MoreOutlined, SearchOutlined } from "@ant-design/icons";
import {
  App,
  Button,
  Dropdown,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  TreeSelect,
  Typography,
  type MenuProps,
  type TableProps,
} from "antd";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getErrorMessage } from "../../api/errors";
import { useMe } from "../../api/me";
import { useOrgs } from "../../api/orgs";
import { PageHeader } from "../../components/PageHeader";
import { formatRelative } from "../../lib/format";
import { visibilityLabel } from "../../lib/labels";
import { buildOrgTree, toOrgSelectData } from "../../lib/orgTree";
import { hasPerm } from "../../lib/permissions";
import { useTablePage } from "../../lib/useTablePage";
import {
  APP_STATUS_OPTIONS,
  APP_TYPE_OPTIONS,
  APP_VISIBILITY_OPTIONS,
  appStatusLabel,
  appTypeLabel,
  buildAppListParams,
  usageScopeText,
  useApps,
  useDeleteApp,
  useDisableApp,
  useEnableApp,
  versionLabel,
  type App as AppResource,
  type AppStatus,
  type AppType,
  type Visibility,
} from "./api";
import { AppCreateModal } from "./AppCreateModal";

/** 应用列表页（原型 #v-apps）。 */
export default function AppListPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { data: me } = useMe();
  const canCreate = hasPerm(me, "resource.create");
  const canFilterOrg = hasPerm(me, "org.manage");
  const { data: orgs = [] } = useOrgs({ enabled: canFilterOrg });

  const [appType, setAppType] = useState<AppType | "all">("all");
  const [status, setStatus] = useState<AppStatus | "all">("all");
  const [visibility, setVisibility] = useState<Visibility | "all">("all");
  const [orgId, setOrgId] = useState<number | undefined>(undefined);
  const [keyword, setKeyword] = useState("");
  const { page, page_size, pagination } = useTablePage();

  const [createOpen, setCreateOpen] = useState(false);
  const [stopTarget, setStopTarget] = useState<AppResource | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AppResource | null>(null);

  const params = useMemo(
    () =>
      buildAppListParams({ appType, status, visibility, orgId, keyword }, page, page_size),
    [appType, status, visibility, orgId, keyword, page, page_size],
  );

  const { data, isLoading } = useApps(params);
  const enableApp = useEnableApp();
  const disableApp = useDisableApp();
  const deleteApp = useDeleteApp();

  const orgTreeData = useMemo(() => toOrgSelectData(buildOrgTree(orgs)), [orgs]);

  const handleEnable = (app: AppResource) =>
    enableApp.mutate(app.id, { onSuccess: () => message.success("已启用") });

  const handleDisable = () => {
    if (!stopTarget) {
      return;
    }
    disableApp.mutate(stopTarget.id, {
      onSuccess: () => {
        message.success("已停用");
        setStopTarget(null);
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });
  };

  const handleDelete = () => {
    if (!deleteTarget) {
      return;
    }
    deleteApp.mutate(deleteTarget.id, {
      onSuccess: () => {
        message.success("已删除");
        setDeleteTarget(null);
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });
  };

  const renderActions = (app: AppResource): ReactNode => {
    const items: MenuProps["items"] = [
      {
        key: "versions",
        label: "发布历史",
        onClick: () => navigate(`/apps/${app.id}/versions`),
      },
    ];
    if (app.editable) {
      items.push({
        key: "logs",
        label: "对话日志",
        onClick: () => navigate(`/apps/${app.id}/logs`),
      });
      items.push({
        key: "feedback",
        label: "用户反馈",
        onClick: () => navigate(`/apps/${app.id}/feedback`),
      });
    }
    if (app.manageable) {
      items.push({
        key: "permissions",
        label: "权限与设置",
        onClick: () => navigate(`/apps/${app.id}/permissions`),
      });
      items.push({
        key: "toggle",
        label: app.status === "disabled" ? "启用" : "停用",
        onClick: () => (app.status === "disabled" ? handleEnable(app) : setStopTarget(app)),
      });
      items.push({
        key: "delete",
        label: "删除",
        danger: true,
        onClick: () => setDeleteTarget(app),
      });
    }

    return (
      <Space size="middle">
        <Link to={`/apps/${app.id}`}>配置</Link>
        <Link to={app.chat_path}>对话页</Link>
        <Dropdown menu={{ items }} trigger={["click"]}>
          <Button type="text" size="small" aria-label="更多操作" icon={<MoreOutlined />} />
        </Dropdown>
      </Space>
    );
  };

  const columns: TableProps<AppResource>["columns"] = [
    {
      title: "名称",
      render: (_value, app) => (
        <div>
          <Typography.Text strong>{app.name}</Typography.Text>
          {app.description ? (
            <div>
              <Typography.Text type="secondary">{app.description}</Typography.Text>
            </div>
          ) : null}
        </div>
      ),
    },
    { title: "类型", render: (_value, app) => <Tag>{appTypeLabel(app.app_type)}</Tag> },
    {
      title: "发布状态",
      render: (_value, app) => (
        <Space size={4} wrap>
          <Tag color={app.status === "published" ? "green" : undefined}>
            {appStatusLabel(app.status)}
          </Tag>
          {app.status === "published" && app.draft_has_changes ? (
            <Tag color="orange">草稿有修改</Tag>
          ) : null}
        </Space>
      ),
    },
    { title: "当前版本", render: (_value, app) => versionLabel(app.published_version) },
    { title: "可见范围", render: (_value, app) => visibilityLabel(app.visibility) },
    { title: "使用范围", render: (_value, app) => usageScopeText(app.usage_scope) },
    { title: "所属组织", render: (_value, app) => app.org.name },
    { title: "创建人", render: (_value, app) => app.owner.username },
    { title: "更新时间", render: (_value, app) => formatRelative(app.updated_at) },
    { title: "操作", render: (_value, app) => renderActions(app) },
  ];

  return (
    <>
      <PageHeader
        title="应用"
        description="把模型和知识库组合成应用，发布给终端用户使用。本阶段只有知识问答；工作流、智能体待细化。"
        extra={
          canCreate ? (
            <Button type="primary" onClick={() => setCreateOpen(true)}>
              新建应用
            </Button>
          ) : null
        }
      />

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
          placeholder="搜索名称 / 描述"
          style={{ width: 220 }}
          onChange={(event) => {
            if (!event.target.value) {
              setKeyword("");
            }
          }}
          onPressEnter={(event) => setKeyword((event.target as HTMLInputElement).value)}
        />
        <Select
          value={appType}
          style={{ width: 140 }}
          options={APP_TYPE_OPTIONS}
          onChange={setAppType}
        />
        <Select
          value={status}
          style={{ width: 160 }}
          options={APP_STATUS_OPTIONS}
          onChange={setStatus}
        />
        <Select
          value={visibility}
          style={{ width: 160 }}
          options={APP_VISIBILITY_OPTIONS}
          onChange={setVisibility}
        />
        {canFilterOrg ? (
          <TreeSelect
            value={orgId}
            allowClear
            placeholder="全部组织"
            style={{ width: 180 }}
            treeData={orgTreeData}
            onChange={(value: number | undefined) => setOrgId(value)}
          />
        ) : null}
        <Typography.Text type="secondary" style={{ marginLeft: "auto" }}>
          我能查看的 {data?.total ?? 0} 个
        </Typography.Text>
      </div>

      <Table<AppResource>
        rowKey="id"
        columns={columns}
        dataSource={data?.items ?? []}
        loading={isLoading}
        pagination={pagination(data?.total ?? 0)}
      />

      <AppCreateModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(app) => navigate(`/apps/${app.id}`)}
      />

      <Modal
        open={stopTarget !== null}
        title={stopTarget ? `停用应用「${stopTarget.name}」？` : "停用应用"}
        okText="确认停用"
        cancelText="取消"
        confirmLoading={disableApp.isPending}
        onOk={handleDisable}
        onCancel={() => setStopTarget(null)}
        destroyOnHidden
      >
        <Typography.Paragraph style={{ marginBottom: 0 }}>
          停用后终端用户打不开，“我的应用”中也不显示。可随时重新启用。
        </Typography.Paragraph>
      </Modal>

      <Modal
        open={deleteTarget !== null}
        title={deleteTarget ? `删除应用「${deleteTarget.name}」？` : "删除应用"}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={deleteApp.isPending}
        onOk={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        destroyOnHidden
      >
        <Typography.Paragraph style={{ marginBottom: 0 }}>
          删除后终端用户无法访问，控制台中也不再显示；会话、对话日志和用户反馈保留在系统中。
        </Typography.Paragraph>
      </Modal>
    </>
  );
}

import { useQueryClient } from "@tanstack/react-query";
import {
  App,
  Button,
  Card,
  Modal,
  Radio,
  Select,
  Space,
  TreeSelect,
  Typography,
} from "antd";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { getErrorMessage } from "../../api/errors";
import { useOrgTree } from "../../api/orgs";
import { CollaboratorsDrawer } from "../../components/resource/CollaboratorsDrawer";
import { VisibilityModal } from "../../components/resource/VisibilityModal";
import { useUserSearch } from "../../components/resource/api";
import { visibilityLabel } from "../../lib/labels";
import { toOrgTreeSelectData } from "../../lib/orgTree";
import {
  buildUsageScopeIn,
  USAGE_SCOPE_OPTIONS,
  usageScopeLabel,
  useApp,
  useAppUsageScope,
  useDeleteApp,
  useDisableApp,
  useEnableApp,
  useUpdateAppUsageScope,
  type UsageScopeType,
} from "./api";
import { AppHeader } from "./AppHeader";
import { AppBasicInfoCard } from "./AppSettingsPage";

const COLLABORATOR_HINT =
  "可修改草稿、调试、发布，查看对话日志和用户反馈；不能改可见范围、使用范围，不能增减协作者、停用或删除";

/** 应用权限页：基本信息、使用范围、可见范围、协作者、停用与删除（原型 #v-appperm）。 */
export default function AppPermissionsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const appId = Number.isFinite(parsedId) ? parsedId : null;
  const { data: app, isLoading } = useApp(appId);

  const [visibilityOpen, setVisibilityOpen] = useState(false);
  const [collaboratorsOpen, setCollaboratorsOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const disableApp = useDisableApp();
  const enableApp = useEnableApp();
  const deleteApp = useDeleteApp();

  if (isLoading || !app) {
    return null;
  }

  const manageable = app.manageable;

  const handleDisable = () =>
    disableApp.mutate(app.id, {
      onSuccess: () => {
        message.success("已停用");
        setStopOpen(false);
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });

  const handleEnable = () =>
    enableApp.mutate(app.id, { onSuccess: () => message.success("已启用") });

  const handleDelete = () =>
    deleteApp.mutate(app.id, {
      onSuccess: () => {
        message.success("已删除");
        setDeleteOpen(false);
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });

  return (
    <div>
      <AppHeader app={app} active="permissions" />

      <AppBasicInfoCard app={app} />

      <UsageScopeCard appId={app.id} manageable={manageable} />

      <Card
        title="可见范围"
        style={{ marginBottom: 16 }}
        extra={
          manageable ? (
            <Button onClick={() => setVisibilityOpen(true)}>修改可见范围</Button>
          ) : null
        }
      >
        <Typography.Paragraph style={{ marginBottom: 8 }}>
          {visibilityLabel(app.visibility)}
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          可见范围内的人能在控制台查看应用配置和发布历史；修改配置、发布、看对话日志和用户反馈需要编辑权限。
        </Typography.Paragraph>
      </Card>

      <Card
        title="协作者"
        style={{ marginBottom: 16 }}
        extra={
          manageable ? (
            <Button onClick={() => setCollaboratorsOpen(true)}>管理协作者</Button>
          ) : null
        }
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {COLLABORATOR_HINT}
        </Typography.Paragraph>
      </Card>

      <Card title="停用与删除">
        <Typography.Paragraph>
          <Typography.Text strong>停用应用</Typography.Text>
          <br />
          停用后终端用户打不开，“我的应用”中也不显示。可随时重新启用。
        </Typography.Paragraph>
        {manageable ? (
          app.status === "disabled" ? (
            <Button style={{ marginBottom: 16 }} onClick={handleEnable}>
              启用
            </Button>
          ) : (
            <Button style={{ marginBottom: 16 }} onClick={() => setStopOpen(true)}>
              停用
            </Button>
          )
        ) : null}

        <Typography.Paragraph>
          <Typography.Text strong>删除应用</Typography.Text>
          <br />
          删除后终端用户无法访问，控制台中也不再显示；会话、对话日志和用户反馈保留在系统中。
        </Typography.Paragraph>
        {manageable ? (
          <Button danger onClick={() => setDeleteOpen(true)}>
            删除
          </Button>
        ) : null}

        <Modal
          open={stopOpen}
          title={`停用应用「${app.name}」？`}
          okText="确认停用"
          cancelText="取消"
          confirmLoading={disableApp.isPending}
          onOk={handleDisable}
          onCancel={() => setStopOpen(false)}
          destroyOnHidden
        >
          <Typography.Paragraph style={{ marginBottom: 0 }}>
            停用后终端用户打不开，“我的应用”中也不显示。可随时重新启用。
          </Typography.Paragraph>
        </Modal>

        <Modal
          open={deleteOpen}
          title={`删除应用「${app.name}」？`}
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          confirmLoading={deleteApp.isPending}
          onOk={handleDelete}
          onCancel={() => setDeleteOpen(false)}
          destroyOnHidden
        >
          <Typography.Paragraph style={{ marginBottom: 0 }}>
            删除后终端用户无法访问，控制台中也不再显示；会话、对话日志和用户反馈保留在系统中。
          </Typography.Paragraph>
        </Modal>
      </Card>

      <VisibilityModal
        open={visibilityOpen}
        resource={{ id: app.id, name: app.name, visibility: app.visibility }}
        onClose={() => {
          setVisibilityOpen(false);
          void queryClient.invalidateQueries({ queryKey: ["app", app.id] });
          void queryClient.invalidateQueries({ queryKey: ["apps"] });
        }}
      />

      <CollaboratorsDrawer
        open={collaboratorsOpen}
        resource={{ id: app.id, name: app.name }}
        hint={COLLABORATOR_HINT}
        onClose={() => setCollaboratorsOpen(false)}
      />
    </div>
  );
}

interface UsageScopeCardProps {
  appId: number;
  manageable: boolean;
}

/** 使用范围三档；修改后立即生效，不需要重新发布。 */
function UsageScopeCard({ appId, manageable }: UsageScopeCardProps) {
  const { message } = App.useApp();
  const { data: usageScope } = useAppUsageScope(appId);
  const updateScope = useUpdateAppUsageScope();
  const { data: orgs = [] } = useOrgTree();
  const [scope, setScope] = useState<UsageScopeType>("platform");
  const [orgIds, setOrgIds] = useState<number[]>([]);
  const [userIds, setUserIds] = useState<number[]>([]);
  const [userLabels, setUserLabels] = useState<Record<number, string>>({});
  const [userKeyword, setUserKeyword] = useState("");
  const search = useUserSearch(userKeyword);

  const orgTreeData = useMemo(() => toOrgTreeSelectData(orgs), [orgs]);

  useEffect(() => {
    if (!usageScope) {
      return;
    }
    setScope(usageScope.scope);
    setOrgIds(usageScope.orgs.map((org) => org.id));
    setUserIds(usageScope.users.map((user) => user.id));
    setUserLabels(
      Object.fromEntries(usageScope.users.map((user) => [user.id, `${user.username} ${user.name}`])),
    );
  }, [usageScope]);

  const searchOptions = (search.data ?? [])
    .filter((user) => !userIds.includes(user.id))
    .map((user) => ({
      value: user.id,
      label: `${user.username} ${user.name} · ${user.org?.name ?? "无组织"}`,
    }));
  const selectedOptions = userIds
    .filter((id) => !searchOptions.some((option) => option.value === id))
    .map((id) => ({ value: id, label: userLabels[id] ?? String(id) }));

  const handleUserChange = (ids: number[]) => {
    setUserIds(ids);
    setUserLabels((previous) => {
      const next = { ...previous };
      for (const option of [...selectedOptions, ...searchOptions]) {
        if (ids.includes(option.value)) {
          next[option.value] = option.label;
        }
      }
      return next;
    });
  };

  const handleSave = () => {
    updateScope.mutate(
      {
        id: appId,
        body: buildUsageScopeIn(scope, orgIds, userIds),
      },
      {
        onSuccess: () => message.success("已保存"),
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );
  };

  return (
    <Card
      title="使用范围"
      style={{ marginBottom: 16 }}
      extra={
        manageable ? (
          <Button type="primary" onClick={handleSave} loading={updateScope.isPending}>
            保存
          </Button>
        ) : null
      }
    >
      <Typography.Paragraph type="secondary">
        谁能在已发布的应用里提问；修改后立即生效，不需要重新发布。当前：{usageScopeLabel(scope)}
      </Typography.Paragraph>
      <Radio.Group
        value={scope}
        disabled={!manageable}
        onChange={(event) => setScope(event.target.value as UsageScopeType)}
      >
        <Space direction="vertical">
          {USAGE_SCOPE_OPTIONS.map((option) => (
            <Radio key={option.value} value={option.value}>
              {option.label}
              <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                {option.description}
              </Typography.Text>
            </Radio>
          ))}
        </Space>
      </Radio.Group>

      {scope === "orgs" ? (
        <div style={{ marginTop: 12 }}>
          <TreeSelect
            multiple
            treeData={orgTreeData}
            value={orgIds}
            disabled={!manageable}
            treeDefaultExpandAll
            allowClear
            placeholder="选择组织（含下级组织）"
            style={{ width: "100%", maxWidth: 480 }}
            onChange={(value: number[]) => setOrgIds(value)}
          />
        </div>
      ) : null}

      {scope === "users" ? (
        <div style={{ marginTop: 12 }}>
          <Select
            mode="multiple"
            value={userIds}
            disabled={!manageable}
            filterOption={false}
            options={[...selectedOptions, ...searchOptions]}
            placeholder="搜索用户名或姓名添加用户"
            style={{ width: "100%", maxWidth: 480 }}
            loading={search.isFetching}
            onChange={(ids) => handleUserChange(ids as number[])}
            onSearch={setUserKeyword}
            notFoundContent={userKeyword.trim().length > 0 ? "没有匹配的用户" : "输入关键字搜索"}
          />
        </div>
      ) : null}

      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
        必须登录平台账号才能使用，不限角色。使用范围与可见范围分开：可见范围管控制台里谁能看到这个应用，使用范围管谁能用。
      </Typography.Paragraph>
    </Card>
  );
}

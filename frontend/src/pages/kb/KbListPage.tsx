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
import { resourceStatusLabel, visibilityLabel } from "../../lib/labels";
import { buildOrgTree, toOrgSelectData } from "../../lib/orgTree";
import { hasPerm } from "../../lib/permissions";
import { useTablePage } from "../../lib/useTablePage";
import {
  buildKbListParams,
  KIND_OPTIONS,
  kbInUseRefs,
  STATUS_OPTIONS,
  useDeleteKb,
  useEnableKb,
  useKbs,
  VISIBILITY_OPTIONS,
  type Kb,
  type KbKind,
  type KbRef,
  type ResourceStatus,
  type Visibility,
} from "./api";
import { KbCreateModal } from "./KbCreateModal";
import { KbStopModal } from "./KbStopModal";

interface BlockedRefs {
  name: string;
  refs: KbRef[];
}

/** 知识库列表页（原型 #v-kbs）。 */
export default function KbListPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { data: me } = useMe();
  const canCreate = hasPerm(me, "resource.create");
  const canFilterOrg = hasPerm(me, "org.manage");
  const { data: orgs = [] } = useOrgs({ enabled: canFilterOrg });

  const [kind, setKind] = useState<KbKind | "all">("all");
  const [visibility, setVisibility] = useState<Visibility | "all">("all");
  const [orgId, setOrgId] = useState<number | undefined>(undefined);
  const [status, setStatus] = useState<ResourceStatus | "all">("all");
  const [keyword, setKeyword] = useState("");
  const { page, page_size, pagination } = useTablePage();

  const [createOpen, setCreateOpen] = useState(false);
  const [stopTarget, setStopTarget] = useState<Kb | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Kb | null>(null);
  const [blockedRefs, setBlockedRefs] = useState<BlockedRefs | null>(null);

  const params = useMemo(
    () => buildKbListParams({ kind, visibility, orgId, status, keyword }, page, page_size),
    [kind, visibility, orgId, status, keyword, page, page_size],
  );

  const { data, isLoading } = useKbs(params);
  const enableKb = useEnableKb();
  const deleteKb = useDeleteKb();

  const orgTreeData = useMemo(() => toOrgSelectData(buildOrgTree(orgs)), [orgs]);

  const handleEnable = (kb: Kb) =>
    enableKb.mutate(kb.id, { onSuccess: () => message.success("已启用") });

  const handleDelete = () => {
    if (!deleteTarget) {
      return;
    }
    deleteKb.mutate(deleteTarget.id, {
      onSuccess: () => {
        message.success("已删除");
        setDeleteTarget(null);
      },
      onError: (error) => {
        const refs = kbInUseRefs(error);
        if (refs.length > 0) {
          setBlockedRefs({ name: deleteTarget.name, refs });
          setDeleteTarget(null);
          return;
        }
        message.error(getErrorMessage(error));
      },
    });
  };

  const renderActions = (kb: Kb): ReactNode => {
    const items: MenuProps["items"] = [];
    if (kb.editable) {
      items.push({
        key: "settings",
        label: "设置",
        onClick: () => navigate(`/kb/${kb.id}/settings`),
      });
    }
    if (kb.manageable) {
      items.push({
        key: "permissions",
        label: "权限",
        onClick: () => navigate(`/kb/${kb.id}/permissions`),
      });
      items.push({
        key: "toggle",
        label: kb.status === "enabled" ? "停用" : "启用",
        onClick: () => (kb.status === "enabled" ? setStopTarget(kb) : handleEnable(kb)),
      });
      items.push({
        key: "delete",
        label: "删除",
        danger: true,
        onClick: () => setDeleteTarget(kb),
      });
    }

    return (
      <Space size="middle">
        <Link to={`/kb/${kb.id}`}>进入</Link>
        <Link to={`/kb/${kb.id}/hit-test`}>命中测试</Link>
        {items.length > 0 ? (
          <Dropdown menu={{ items }} trigger={["click"]}>
            <Button type="text" size="small" aria-label="更多操作" icon={<MoreOutlined />} />
          </Dropdown>
        ) : null}
      </Space>
    );
  };

  const columns: TableProps<Kb>["columns"] = [
    {
      title: "名称",
      render: (_value, kb) => (
        <div>
          <Typography.Text strong>{kb.name}</Typography.Text>
          {kb.description ? (
            <div>
              <Typography.Text type="secondary">{kb.description}</Typography.Text>
            </div>
          ) : null}
        </div>
      ),
    },
    { title: "类型", dataIndex: "kind", render: (value: string) => <Tag>{value === "qa" ? "问答库" : "文档库"}</Tag> },
    {
      title: "文档 / 问答对",
      render: (_value, kb) => (kb.kind === "qa" ? `${kb.doc_count} 个问答对` : `${kb.doc_count} 个文档`),
    },
    {
      title: "分块",
      render: (_value, kb) =>
        kb.kind === "qa" ? "—" : kb.chunk_count.toLocaleString(),
    },
    { title: "向量化模型", render: (_value, kb) => kb.embedding_model.name },
    { title: "可见范围", render: (_value, kb) => visibilityLabel(kb.visibility) },
    { title: "所属组织", render: (_value, kb) => kb.org.name },
    {
      title: "状态",
      render: (_value, kb) =>
        kb.status === "enabled" ? (
          <Tag color="green">{resourceStatusLabel(kb.status)}</Tag>
        ) : (
          <Tag>{resourceStatusLabel(kb.status)}</Tag>
        ),
    },
    { title: "更新时间", render: (_value, kb) => formatRelative(kb.updated_at) },
    { title: "操作", render: (_value, kb) => renderActions(kb) },
  ];

  return (
    <>
      <PageHeader
        title="知识库"
        description="文档库存放文档，问答库存放问答对。类型和向量化模型创建后不可修改。"
        extra={
          canCreate ? (
            <Button type="primary" onClick={() => setCreateOpen(true)}>
              新建知识库
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
        <Select value={kind} style={{ width: 140 }} options={KIND_OPTIONS} onChange={setKind} />
        <Select
          value={visibility}
          style={{ width: 160 }}
          options={VISIBILITY_OPTIONS}
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
        <Select value={status} style={{ width: 140 }} options={STATUS_OPTIONS} onChange={setStatus} />
        <Typography.Text type="secondary" style={{ marginLeft: "auto" }}>
          我能查看的 {data?.total ?? 0} 个
        </Typography.Text>
      </div>

      <Table<Kb>
        rowKey="id"
        columns={columns}
        dataSource={data?.items ?? []}
        loading={isLoading}
        pagination={pagination(data?.total ?? 0)}
      />

      <KbCreateModal open={createOpen} onClose={() => setCreateOpen(false)} />
      <KbStopModal open={stopTarget !== null} kb={stopTarget} onClose={() => setStopTarget(null)} />

      <Modal
        open={deleteTarget !== null}
        title={deleteTarget ? `删除知识库「${deleteTarget.name}」？` : "删除知识库"}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={deleteKb.isPending}
        onOk={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        destroyOnHidden
      >
        <Typography.Paragraph style={{ marginBottom: 0 }}>
          删除后不可恢复。被应用引用时需先解除引用。
        </Typography.Paragraph>
      </Modal>

      <Modal
        open={blockedRefs !== null}
        title={blockedRefs ? `无法删除「${blockedRefs.name}」` : "无法删除"}
        footer={
          <Button type="primary" onClick={() => setBlockedRefs(null)}>
            知道了
          </Button>
        }
        onCancel={() => setBlockedRefs(null)}
        destroyOnHidden
      >
        <Typography.Paragraph>
          该知识库被 {blockedRefs?.refs.length ?? 0} 个应用引用，需先在应用中解除引用。
        </Typography.Paragraph>
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          {(blockedRefs?.refs ?? []).map((ref) => (
            <li key={`${ref.type}-${ref.resource_id}`}>
              {ref.name}（{ref.type_label}）· 负责人 {ref.owner.name}
            </li>
          ))}
        </ul>
      </Modal>
    </>
  );
}

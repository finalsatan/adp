import { SearchOutlined } from "@ant-design/icons";
import {
  Alert,
  App,
  Card,
  Empty,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  TreeSelect,
  Typography,
  type TableProps,
} from "antd";
import dayjs from "dayjs";
import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { useMe } from "../../../api/me";
import { useOrgs } from "../../../api/orgs";
import { PageHeader } from "../../../components/PageHeader";
import { CollaboratorsDrawer } from "../../../components/resource/CollaboratorsDrawer";
import { VisibilityModal } from "../../../components/resource/VisibilityModal";
import {
  resourceInUseRefs,
  useUserSearch,
  type ResourceRef,
} from "../../../components/resource/api";
import { visibilityLabel } from "../../../lib/labels";
import { buildOrgTree, toOrgSelectData } from "../../../lib/orgTree";
import { resourceDetailPath } from "../../../lib/resourceRoutes";
import {
  useDeleteResource,
  useDisableResource,
  useEnableResource,
  useResources,
  useResourceTypes,
  type Resource,
  type ResourceListParams,
  type Visibility,
} from "./api";

const VISIBILITY_OPTIONS: { value: Visibility; label: string }[] = [
  { value: "private", label: "仅自己" },
  { value: "org", label: "本组织" },
  { value: "platform", label: "全平台" },
];

/** 资源总览页（原型 #v-overview）。第一期没有资源类型，列表为空。 */
export default function OverviewPage() {
  const { message } = App.useApp();
  const { data: me } = useMe();
  const { data: orgs = [] } = useOrgs();
  const { data: resourceTypes = [] } = useResourceTypes();

  const [keyword, setKeyword] = useState("");
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const [orgId, setOrgId] = useState<number | undefined>(undefined);
  const [visibility, setVisibility] = useState<Visibility | undefined>(undefined);
  const [ownerId, setOwnerId] = useState<number | undefined>(undefined);
  const [ownerKeyword, setOwnerKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const [visibilityResource, setVisibilityResource] = useState<Resource | null>(null);
  const [collabResource, setCollabResource] = useState<Resource | null>(null);
  const [inUse, setInUse] = useState<{ name: string; refs: ResourceRef[] } | null>(null);

  const disableResource = useDisableResource();
  const enableResource = useEnableResource();
  const deleteResource = useDeleteResource();
  const ownerSearch = useUserSearch(ownerKeyword);

  const fixedVisibility = useMemo(() => {
    const map = new Map<string, Visibility>();
    for (const item of resourceTypes) {
      if (item.fixed_visibility) {
        map.set(item.code, item.fixed_visibility);
      }
    }
    return map;
  }, [resourceTypes]);

  const params = useMemo<ResourceListParams>(() => {
    const value: ResourceListParams = { page, page_size: pageSize };
    if (typeFilter) {
      value.type = typeFilter;
    }
    if (orgId != null) {
      value.org_id = orgId;
      value.include_sub = true;
    }
    if (visibility) {
      value.visibility = visibility;
    }
    if (ownerId != null) {
      value.owner_id = ownerId;
    }
    if (keyword) {
      value.keyword = keyword;
    }
    return value;
  }, [typeFilter, orgId, visibility, ownerId, keyword, page, pageSize]);

  const { data, isLoading } = useResources(params);

  const orgTreeData = useMemo(() => toOrgSelectData(buildOrgTree(orgs)), [orgs]);

  const handleDisable = (resource: Resource) =>
    disableResource.mutate(resource.id, {
      onSuccess: () => message.success("已停用"),
    });

  const handleEnable = (resource: Resource) =>
    enableResource.mutate(resource.id, {
      onSuccess: () => message.success("已启用"),
    });

  const handleDelete = (resource: Resource) => {
    deleteResource.mutate(resource.id, {
      onSuccess: () => message.success("已删除"),
      onError: (error) => {
        const refs = resourceInUseRefs(error);
        if (refs.length > 0) {
          setInUse({ name: resource.name, refs });
        }
      },
    });
  };

  const renderActions = (resource: Resource): ReactNode => {
    const nodes: ReactNode[] = [];
    const detailPath = resourceDetailPath(resource.type, resource.id);
    if (detailPath) {
      nodes.push(
        <Link key="view" to={detailPath}>
          查看
        </Link>,
        <Link key="edit" to={detailPath}>
          编辑
        </Link>,
      );
    }
    if (!fixedVisibility.has(resource.type)) {
      nodes.push(
        <a key="visibility" onClick={() => setVisibilityResource(resource)}>
          可见范围
        </a>,
      );
    }
    nodes.push(
      <a key="collaborators" onClick={() => setCollabResource(resource)}>
        协作者
      </a>,
    );
    if (resource.status === "enabled") {
      nodes.push(
        <Popconfirm
          key="disable"
          title="停用该资源？"
          description="停用后资源保留，但不能在应用中使用。"
          okText="停用"
          cancelText="取消"
          onConfirm={() => handleDisable(resource)}
        >
          <a>停用</a>
        </Popconfirm>,
      );
    } else {
      nodes.push(
        <a key="enable" onClick={() => handleEnable(resource)}>
          启用
        </a>,
      );
    }
    nodes.push(
      <Popconfirm
        key="delete"
        title="删除该资源？"
        description="删除后不可恢复。"
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        onConfirm={() => handleDelete(resource)}
      >
        <a style={{ color: "#ff4d4f" }}>删除</a>
      </Popconfirm>,
    );
    return <Space size="middle">{nodes}</Space>;
  };

  const columns: TableProps<Resource>["columns"] = [
    {
      title: "名称",
      dataIndex: "name",
      render: (name: string) => <Typography.Text strong>{name}</Typography.Text>,
    },
    {
      title: "类型",
      dataIndex: "type_label",
      render: (label: string) => <Tag>{label}</Tag>,
    },
    {
      title: "所属组织",
      render: (_value, resource) => resource.org.name,
    },
    {
      title: "创建人",
      render: (_value, resource) => resource.owner.username,
    },
    {
      title: "可见范围",
      dataIndex: "visibility",
      render: (value: Visibility, resource) => {
        const fixed = fixedVisibility.get(resource.type);
        return (
          <Tag
            color="blue"
            title={fixed ? `${resource.type_label}固定${visibilityLabel(fixed)}可用` : undefined}
          >
            {visibilityLabel(value)}
          </Tag>
        );
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      render: (value: string) => dayjs(value).format("YYYY-MM-DD"),
    },
    {
      title: "操作",
      render: (_value, resource) => renderActions(resource),
    },
  ];

  const identityAlert =
    me?.role === "sys_admin"
      ? "当前身份：系统管理员，可对所有资源执行查看、编辑、停用、删除、调整可见范围、增减协作者。组织管理员对本组织及下级组织的资源有同样的操作。"
      : me?.org
        ? `当前身份：${me.org.name}的组织管理员，可对本组织及下级组织的资源执行查看、编辑、停用、删除、调整可见范围、增减协作者。`
        : "当前身份：组织管理员。";

  return (
    <>
      <PageHeader
        title="资源总览"
        description="跨类型查看组织内的资源，便于管理员掌握全局。"
      />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={identityAlert}
      />

      <Card>
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
            placeholder="搜索资源名称"
            style={{ width: 200 }}
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
            allowClear
            value={typeFilter}
            placeholder="全部类型"
            style={{ width: 140 }}
            options={resourceTypes.map((item) => ({
              value: item.code,
              label: item.label,
            }))}
            onChange={(value) => {
              setTypeFilter(value ?? undefined);
              setPage(1);
            }}
          />
          <TreeSelect
            allowClear
            value={orgId}
            placeholder="全部组织"
            style={{ width: 180 }}
            treeData={orgTreeData}
            treeDefaultExpandAll
            onChange={(value) => {
              setOrgId(value ?? undefined);
              setPage(1);
            }}
          />
          <Select
            allowClear
            value={visibility}
            placeholder="全部可见范围"
            style={{ width: 150 }}
            options={VISIBILITY_OPTIONS}
            onChange={(value) => {
              setVisibility(value ?? undefined);
              setPage(1);
            }}
          />
          <Select
            allowClear
            showSearch
            value={ownerId}
            placeholder="全部创建人"
            style={{ width: 200 }}
            filterOption={false}
            onSearch={setOwnerKeyword}
            onClear={() => {
              setOwnerId(undefined);
              setOwnerKeyword("");
            }}
            onChange={(value) => {
              setOwnerId(value ?? undefined);
              setPage(1);
            }}
            options={(ownerSearch.data ?? []).map((user) => ({
              value: user.id,
              label: `${user.username} ${user.name} · ${user.org?.name ?? "无组织"}`,
            }))}
          />
        </div>

        {inUse ? (
          <Alert
            type="warning"
            showIcon
            closable
            style={{ marginBottom: 16 }}
            onClose={() => setInUse(null)}
            message={`“${inUse.name}”被以下资源引用，请先解除引用`}
            description={
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {inUse.refs.map((ref) => (
                  <li key={`${ref.type}-${ref.id}`}>
                    {ref.name}（{ref.type}）
                  </li>
                ))}
              </ul>
            }
          />
        ) : null}

        <Table<Resource>
          rowKey="id"
          columns={columns}
          dataSource={data?.items ?? []}
          loading={isLoading}
          locale={{
            emptyText: (
              <Empty description="暂无资源。知识库、入库方案、应用上线后会出现在这里。" />
            ),
          }}
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

      <VisibilityModal
        open={visibilityResource !== null}
        resource={visibilityResource}
        onClose={() => setVisibilityResource(null)}
      />
      <CollaboratorsDrawer
        open={collabResource !== null}
        resource={collabResource}
        onClose={() => setCollabResource(null)}
      />
    </>
  );
}

import { SearchOutlined } from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  type TableProps,
} from "antd";
import { useMemo, useState, type ReactNode } from "react";

import { getErrorMessage, isApiError } from "../../api/errors";
import { useMe } from "../../api/me";
import { PageHeader } from "../../components/PageHeader";
import { formatRelative } from "../../lib/format";
import { hasPerm } from "../../lib/permissions";
import { useTablePage } from "../../lib/useTablePage";
import {
  useDeleteModel,
  useEnableModel,
  useModels,
  type Model,
  type ModelListParams,
  type ModelRef,
  type ModelStatus,
  type ModelType,
} from "./api";
import { ModelDisableModal, ModelRefsModal } from "./ModelDisableModal";
import { ModelFormDrawer } from "./ModelFormDrawer";
import { ModelTestModal } from "./ModelTestModal";
import {
  capabilityLabels,
  modelStatusLabel,
  modelTypeLabel,
  refCountText,
} from "./format";

const TYPE_OPTIONS: { value: ModelType | "all"; label: string }[] = [
  { value: "all", label: "全部类型" },
  { value: "chat", label: "对话" },
  { value: "embedding", label: "向量化" },
  { value: "rerank", label: "重排" },
];

const STATUS_OPTIONS: { value: ModelStatus | "all"; label: string }[] = [
  { value: "all", label: "全部状态" },
  { value: "enabled", label: "启用" },
  { value: "disabled", label: "已停用" },
];

interface FormState {
  open: boolean;
  model: Model | null;
}

interface BlockedRefs {
  name: string;
  refs: ModelRef[];
}

/** 模型管理页（原型 #v-models）。 */
export default function ModelsPage() {
  const { message } = App.useApp();
  const { data: me } = useMe();
  const canManage = hasPerm(me, "model.manage");

  const [keyword, setKeyword] = useState("");
  const [typeFilter, setTypeFilter] = useState<ModelType | "all">("all");
  const [statusFilter, setStatusFilter] = useState<ModelStatus | "all">("all");
  const { page, page_size, pagination } = useTablePage();

  const [formState, setFormState] = useState<FormState>({ open: false, model: null });
  const [testModel, setTestModel] = useState<Model | null>(null);
  const [disableModel, setDisableModel] = useState<Model | null>(null);
  const [blockedRefs, setBlockedRefs] = useState<BlockedRefs | null>(null);

  const params = useMemo<ModelListParams>(() => {
    const value: ModelListParams = { page, page_size };
    if (typeFilter !== "all") {
      value.type = typeFilter;
    }
    if (statusFilter !== "all") {
      value.status = statusFilter;
    }
    if (keyword) {
      value.keyword = keyword;
    }
    return value;
  }, [page, page_size, typeFilter, statusFilter, keyword]);

  const { data, isLoading } = useModels(params);
  const enableModel = useEnableModel();
  const deleteModel = useDeleteModel();

  const openCreate = () => setFormState({ open: true, model: null });
  const openEdit = (model: Model) => setFormState({ open: true, model });
  const closeForm = () => setFormState({ open: false, model: null });

  const handleEnable = (model: Model) =>
    enableModel.mutate(model.id, { onSuccess: () => message.success("已启用") });

  const handleDelete = (model: Model) =>
    deleteModel.mutate(model.id, {
      onSuccess: () => message.success("已删除"),
      onError: (error) => {
        if (isApiError(error) && error.code === "RESOURCE_IN_USE") {
          const detail = (error.detail ?? {}) as { refs?: ModelRef[] };
          setBlockedRefs({ name: model.name, refs: detail.refs ?? [] });
          return;
        }
        message.error(getErrorMessage(error));
      },
    });

  const identityAlert =
    me?.role === "sys_admin"
      ? "模型由系统管理员统一添加和维护，添加后全平台可用，不按组织区分。当前身份：系统管理员。"
      : "模型由系统管理员统一添加和维护，添加后全平台可用，不按组织区分。搭建者和组织管理员在此只能查看和测试。";

  const renderActions = (model: Model): ReactNode => {
    const nodes: ReactNode[] = [];
    if (canManage) {
      nodes.push(
        <a key="edit" onClick={() => openEdit(model)}>
          编辑
        </a>,
      );
    }
    nodes.push(
      <a key="test" onClick={() => setTestModel(model)}>
        测试
      </a>,
    );
    if (canManage) {
      if (model.status === "enabled") {
        nodes.push(
          <a key="disable" onClick={() => setDisableModel(model)}>
            停用
          </a>,
        );
      } else {
        nodes.push(
          <a key="enable" onClick={() => handleEnable(model)}>
            启用
          </a>,
        );
      }
      nodes.push(
        <Popconfirm
          key="delete"
          title="删除该模型？"
          description="删除后不可恢复；被应用、知识库或入库方案引用的模型需先解除引用。"
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => handleDelete(model)}
        >
          <a>删除</a>
        </Popconfirm>,
      );
    }
    return <Space size="middle">{nodes}</Space>;
  };

  const columns: TableProps<Model>["columns"] = [
    {
      title: "名称 / 模型 ID",
      render: (_value, model) => (
        <div>
          <Typography.Text strong>{model.name}</Typography.Text>
          <div>
            <Typography.Text type="secondary">{model.litellm_model}</Typography.Text>
          </div>
        </div>
      ),
    },
    {
      title: "类型",
      dataIndex: "model_type",
      render: (type: ModelType) => <Tag>{modelTypeLabel(type)}</Tag>,
    },
    {
      title: "能力",
      render: (_value, model) => (
        <Space size={4} wrap>
          {capabilityLabels(model).map((label) => (
            <Tag key={label}>{label}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: "状态",
      render: (_value, model) =>
        model.status === "enabled" ? (
          <Tag color="green">{modelStatusLabel(model.status)}</Tag>
        ) : (
          <Tag>{modelStatusLabel(model.status)}</Tag>
        ),
    },
    {
      title: "被引用",
      render: (_value, model) => refCountText(model.ref_counts),
    },
    {
      title: "最近测试",
      render: (_value, model) => {
        if (model.last_test_at == null || model.last_test_ok == null) {
          return <Typography.Text type="secondary">—</Typography.Text>;
        }
        return (
          <div>
            <Typography.Text type={model.last_test_ok ? "success" : "danger"}>
              {model.last_test_ok ? "✓ 连通" : "✕ 失败"} {formatRelative(model.last_test_at)}
            </Typography.Text>
            {!model.last_test_ok && model.last_test_message ? (
              <div>
                <Typography.Text type="secondary">{model.last_test_message}</Typography.Text>
              </div>
            ) : null}
          </div>
        );
      },
    },
    {
      title: "操作",
      render: (_value, model) => renderActions(model),
    },
  ];

  return (
    <>
      <PageHeader
        title="模型管理"
        description="所有模型调用经由 LiteLLM 统一转发。密钥加密保存，界面和接口不显示明文。"
        extra={
          canManage ? (
            <Button type="primary" onClick={openCreate}>
              添加模型
            </Button>
          ) : null
        }
      />

      <Alert type="info" showIcon message={identityAlert} style={{ marginBottom: 16 }} />

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
          placeholder="搜索名称 / 模型 ID"
          style={{ width: 240 }}
          onChange={(event) => {
            if (!event.target.value) {
              setKeyword("");
            }
          }}
          onPressEnter={(event) => {
            setKeyword((event.target as HTMLInputElement).value);
          }}
        />
        <Select
          value={typeFilter}
          style={{ width: 140 }}
          options={TYPE_OPTIONS}
          onChange={setTypeFilter}
        />
        <Select
          value={statusFilter}
          style={{ width: 140 }}
          options={STATUS_OPTIONS}
          onChange={setStatusFilter}
        />
        <Typography.Text type="secondary" style={{ marginLeft: "auto" }}>
          共 {data?.total ?? 0} 个模型
        </Typography.Text>
      </div>

      <Table<Model>
        rowKey="id"
        columns={columns}
        dataSource={data?.items ?? []}
        loading={isLoading}
        pagination={pagination(data?.total ?? 0)}
      />

      <ModelFormDrawer open={formState.open} model={formState.model} onClose={closeForm} />
      <ModelTestModal
        open={testModel !== null}
        model={testModel}
        onClose={() => setTestModel(null)}
      />
      <ModelDisableModal
        open={disableModel !== null}
        model={disableModel}
        onClose={() => setDisableModel(null)}
      />
      <ModelRefsModal
        open={blockedRefs !== null}
        modelName={blockedRefs?.name ?? ""}
        refs={blockedRefs?.refs ?? []}
        onClose={() => setBlockedRefs(null)}
      />
    </>
  );
}

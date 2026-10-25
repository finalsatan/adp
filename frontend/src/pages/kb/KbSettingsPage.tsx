import {
  App,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Typography,
  type TableProps,
} from "antd";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { getErrorMessage } from "../../api/errors";
import {
  kbInUseRefs,
  metadataFieldTypeLabel,
  useCreateMetadataField,
  useDefaultSchemes,
  useDeleteKb,
  useDeleteMetadataField,
  useKb,
  useKbRefs,
  useMetadataFields,
  useSchemes,
  useUpdateDefaultSchemes,
  useUpdateKb,
  useUpdateMetadataField,
  type DefaultSchemeItem,
  type KbRef,
  type MetadataField,
  type MetadataFieldType,
} from "./api";
import { KbHeader } from "./KbHeader";
import { KbStopModal } from "./KbStopModal";

const PLATFORM_DEFAULT = "platform-default";

export default function KbSettingsPage() {
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const kbId = Number.isFinite(parsedId) ? parsedId : null;
  const { data: kb, isLoading } = useKb(kbId);

  if (isLoading || !kb) {
    return null;
  }

  return (
    <div>
      <KbHeader kb={kb} active="settings" />
      <BasicInfoCard kbId={kb.id} name={kb.name} description={kb.description} editable={kb.editable} kind={kb.kind} modelName={kb.embedding_model.name} />
      {kb.kind === "doc" ? (
        <>
          <DefaultSchemesCard kbId={kb.id} editable={kb.editable} />
          <MetadataFieldsCard kbId={kb.id} editable={kb.editable} />
        </>
      ) : null}
      <StopAndDeleteCard kbId={kb.id} name={kb.name} manageable={kb.manageable} />
    </div>
  );
}

interface BasicInfoCardProps {
  kbId: number;
  name: string;
  description: string | null;
  editable: boolean;
  kind: string;
  modelName: string;
}

function BasicInfoCard({ kbId, name, description, editable, kind, modelName }: BasicInfoCardProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ name: string; description?: string }>();
  const updateKb = useUpdateKb();

  useEffect(() => {
    form.setFieldsValue({ name, description: description ?? "" });
  }, [form, name, description]);

  const handleSave = async () => {
    const values = await form.validateFields();
    updateKb.mutate(
      { id: kbId, body: { name: values.name, description: values.description ? values.description : null } },
      {
        onSuccess: () => message.success("已保存"),
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );
  };

  return (
    <Card title="基本信息" style={{ marginBottom: 16 }}>
      <Form form={form} layout="vertical" style={{ maxWidth: 520 }}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
          <Input maxLength={50} disabled={!editable} />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input maxLength={200} disabled={!editable} />
        </Form.Item>
        <Form.Item label="类型">
          <Typography.Text>{kind === "qa" ? "问答库" : "文档库"}</Typography.Text>
          <div>
            <Typography.Text type="secondary">创建后不可修改</Typography.Text>
          </div>
        </Form.Item>
        <Form.Item label="向量化模型">
          <Typography.Text>{modelName}</Typography.Text>
          <div>
            <Typography.Text type="secondary">创建后不可修改；要更换请新建知识库</Typography.Text>
          </div>
        </Form.Item>
        {editable ? (
          <Button type="primary" onClick={handleSave} loading={updateKb.isPending}>
            保存
          </Button>
        ) : null}
      </Form>
    </Card>
  );
}

interface DefaultSchemesCardProps {
  kbId: number;
  editable: boolean;
}

function DefaultSchemesCard({ kbId, editable }: DefaultSchemesCardProps) {
  const { message } = App.useApp();
  const { data } = useDefaultSchemes(kbId);
  const { data: schemes } = useSchemes();
  const updateSchemes = useUpdateDefaultSchemes();
  const [rows, setRows] = useState<DefaultSchemeItem[]>([]);

  useEffect(() => {
    if (data) {
      setRows(data.items);
    }
  }, [data]);

  const optionsByType = useMemo(() => {
    const map = new Map<string, { value: number; label: string }[]>();
    for (const scheme of schemes?.items ?? []) {
      const list = map.get(scheme.file_type) ?? [];
      list.push({ value: scheme.id, label: scheme.name });
      map.set(scheme.file_type, list);
    }
    return map;
  }, [schemes]);

  const setScheme = (fileType: string, value: string | number) => {
    setRows((previous) =>
      previous.map((row) =>
        row.file_type === fileType
          ? { ...row, scheme_id: value === PLATFORM_DEFAULT ? null : Number(value) }
          : row,
      ),
    );
  };

  const handleSave = () => {
    updateSchemes.mutate(
      {
        id: kbId,
        items: rows.map((row) => ({ file_type: row.file_type, scheme_id: row.scheme_id })),
      },
      {
        onSuccess: () => message.success("已保存"),
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );
  };

  return (
    <Card
      title="默认入库方案"
      extra={
        editable ? (
          <Button type="primary" onClick={handleSave} loading={updateSchemes.isPending}>
            保存
          </Button>
        ) : null
      }
      style={{ marginBottom: 16 }}
    >
      <Typography.Paragraph type="secondary">
        上传时按文件类型自动选中，可在上传时更改
      </Typography.Paragraph>
      <Table<DefaultSchemeItem>
        rowKey="file_type"
        pagination={false}
        size="small"
        dataSource={rows}
        columns={[
          { title: "文件类型", dataIndex: "file_type", render: (value: string) => value.toUpperCase() },
          {
            title: "默认入库方案",
            render: (_value, row) => (
              <Select
                style={{ width: 280 }}
                disabled={!editable}
                value={row.scheme_id ?? PLATFORM_DEFAULT}
                onChange={(value) => setScheme(row.file_type, value)}
                options={[
                  {
                    value: PLATFORM_DEFAULT,
                    label: `平台默认：${row.fallback_scheme_name ?? "未配置"}`,
                  },
                  ...(optionsByType.get(row.file_type) ?? []),
                ]}
              />
            ),
          },
        ]}
      />
    </Card>
  );
}

interface MetadataFieldsCardProps {
  kbId: number;
  editable: boolean;
}

function MetadataFieldsCard({ kbId, editable }: MetadataFieldsCardProps) {
  const { message } = App.useApp();
  const { data: fields = [] } = useMetadataFields(kbId);
  const deleteField = useDeleteMetadataField();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<MetadataField | null>(null);

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };
  const openEdit = (field: MetadataField) => {
    setEditing(field);
    setModalOpen(true);
  };

  const handleDelete = (field: MetadataField) =>
    deleteField.mutate(
      { kbId, fieldId: field.id },
      {
        onSuccess: () => message.success("已删除"),
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );

  const columns: TableProps<MetadataField>["columns"] = [
    { title: "字段名", dataIndex: "name" },
    { title: "类型", render: (_value, field) => metadataFieldTypeLabel(field.type) },
    {
      title: "选项",
      render: (_value, field) => (field.options?.length ? field.options.join("、") : "—"),
    },
    { title: "已设值的文档", dataIndex: "doc_count" },
    {
      title: "操作",
      render: (_value, field) =>
        editable ? (
          <Space size="middle">
            <a onClick={() => openEdit(field)}>编辑</a>
            <Popconfirm
              title="删除该元数据字段？"
              description="删除后文档上该字段的值也会一并删除。"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => handleDelete(field)}
            >
              <a>删除</a>
            </Popconfirm>
          </Space>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <Card
      title="元数据字段"
      extra={
        editable ? (
          <Button type="primary" onClick={openCreate}>
            添加字段
          </Button>
        ) : null
      }
      style={{ marginBottom: 16 }}
    >
      <Typography.Paragraph type="secondary">检索时可按字段过滤</Typography.Paragraph>
      <Table<MetadataField>
        rowKey="id"
        columns={columns}
        dataSource={fields}
        pagination={false}
        size="small"
        summary={() => (
          <Table.Summary.Row>
            <Table.Summary.Cell index={0}>
              文件名 / 文件类型 / 上传人 / 上传时间 / 所在文件夹
            </Table.Summary.Cell>
            <Table.Summary.Cell index={1}>系统字段</Table.Summary.Cell>
            <Table.Summary.Cell index={2}>只读</Table.Summary.Cell>
            <Table.Summary.Cell index={3}>全部</Table.Summary.Cell>
            <Table.Summary.Cell index={4}>—</Table.Summary.Cell>
          </Table.Summary.Row>
        )}
      />
      <MetadataFieldModal
        open={modalOpen}
        kbId={kbId}
        field={editing}
        onClose={() => setModalOpen(false)}
      />
    </Card>
  );
}

interface MetadataFieldModalProps {
  open: boolean;
  kbId: number;
  field: MetadataField | null;
  onClose: () => void;
}

function MetadataFieldModal({ open, kbId, field, onClose }: MetadataFieldModalProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ name: string; type: MetadataFieldType; options?: string[] }>();
  const fieldType = Form.useWatch("type", form);
  const createField = useCreateMetadataField();
  const updateField = useUpdateMetadataField();

  useEffect(() => {
    if (open) {
      form.resetFields();
      form.setFieldsValue({
        name: field?.name ?? "",
        type: field?.type ?? "text",
        options: field?.options ?? [],
      });
    }
  }, [open, field, form]);

  const handleSave = async () => {
    const values = await form.validateFields();
    const options = values.type === "select" ? (values.options ?? []) : null;
    if (field) {
      updateField.mutate(
        { kbId, fieldId: field.id, body: { name: values.name, options } },
        {
          onSuccess: () => {
            message.success("已保存");
            onClose();
          },
          onError: (error) => message.error(getErrorMessage(error)),
        },
      );
      return;
    }
    createField.mutate(
      { id: kbId, body: { name: values.name, type: values.type, options } },
      {
        onSuccess: () => {
          message.success("已保存");
          onClose();
        },
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );
  };

  return (
    <Modal
      open={open}
      title={field ? "编辑元数据字段" : "添加元数据字段"}
      okText="保存"
      cancelText="取消"
      confirmLoading={createField.isPending || updateField.isPending}
      onOk={handleSave}
      onCancel={onClose}
      destroyOnHidden
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="字段名" rules={[{ required: true, message: "请输入字段名" }]}>
          <Input maxLength={50} />
        </Form.Item>
        <Form.Item name="type" label="类型" rules={[{ required: true }]}>
          <Radio.Group disabled={field !== null}>
            <Radio value="text">文本</Radio>
            <Radio value="number">数字</Radio>
            <Radio value="date">日期</Radio>
            <Radio value="select">单选</Radio>
          </Radio.Group>
        </Form.Item>
        {fieldType === "select" ? (
          <Form.Item name="options" label="选项">
            <Select mode="tags" placeholder="回车添加" open={false} />
          </Form.Item>
        ) : null}
      </Form>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        命中测试和应用检索时可按此字段过滤，条件可用“且 / 或”组合。
      </Typography.Paragraph>
    </Modal>
  );
}

interface StopAndDeleteCardProps {
  kbId: number;
  name: string;
  manageable: boolean;
}

function StopAndDeleteCard({ kbId, name, manageable }: StopAndDeleteCardProps) {
  const { message } = App.useApp();
  const refs = useKbRefs(kbId);
  const deleteKb = useDeleteKb();
  const [stopOpen, setStopOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [blockedRefs, setBlockedRefs] = useState<KbRef[] | null>(null);

  const refList = refs.data ?? [];
  const deleteHint =
    refList.length > 0
      ? `被 ${refList.length} 个应用引用（${refList.map((ref) => ref.name).join("、")}），需先解除引用。`
      : "删除后不可恢复。";

  const handleDelete = () =>
    deleteKb.mutate(kbId, {
      onSuccess: () => {
        message.success("已删除");
        setDeleteOpen(false);
      },
      onError: (error) => {
        const blockers = kbInUseRefs(error);
        if (blockers.length > 0) {
          setBlockedRefs(blockers);
          setDeleteOpen(false);
          return;
        }
        message.error(getErrorMessage(error));
      },
    });

  return (
    <Card title="停用与删除">
      <Typography.Paragraph>
        <Typography.Text strong>停用知识库</Typography.Text>
        <br />
        停用后，引用它的应用检索不到其内容，但不会报错。创建人、组织管理员或系统管理员可操作。
      </Typography.Paragraph>
      {manageable ? (
        <Button onClick={() => setStopOpen(true)} style={{ marginBottom: 16 }}>
          停用
        </Button>
      ) : null}

      <Typography.Paragraph>
        <Typography.Text strong>删除知识库</Typography.Text>
        <br />
        {deleteHint}
      </Typography.Paragraph>
      {manageable ? (
        <Button danger disabled={refList.length > 0} onClick={() => setDeleteOpen(true)}>
          删除
        </Button>
      ) : null}

      <KbStopModal open={stopOpen} kb={{ id: kbId, name }} onClose={() => setStopOpen(false)} />

      <Modal
        open={deleteOpen}
        title={`删除知识库「${name}」？`}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={deleteKb.isPending}
        onOk={handleDelete}
        onCancel={() => setDeleteOpen(false)}
        destroyOnHidden
      >
        <Typography.Paragraph style={{ marginBottom: 0 }}>删除后不可恢复。</Typography.Paragraph>
      </Modal>

      <Modal
        open={blockedRefs !== null}
        title={`无法删除「${name}」`}
        footer={
          <Button type="primary" onClick={() => setBlockedRefs(null)}>
            知道了
          </Button>
        }
        onCancel={() => setBlockedRefs(null)}
        destroyOnHidden
      >
        <Typography.Paragraph>
          该知识库被 {blockedRefs?.length ?? 0} 个应用引用，需先在应用中解除引用。
        </Typography.Paragraph>
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          {(blockedRefs ?? []).map((ref) => (
            <li key={`${ref.type}-${ref.resource_id}`}>
              {ref.name}（{ref.type_label}）· 负责人 {ref.owner.name}
            </li>
          ))}
        </ul>
      </Modal>
    </Card>
  );
}

import { Alert, App, Form, Input, Modal, Radio, Select, TreeSelect, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import { isApiError } from "../../api/errors";
import { useMe } from "../../api/me";
import { useOrgs } from "../../api/orgs";
import { buildOrgTree, toOrgSelectData } from "../../lib/orgTree";
import { useCreateKb, useEmbeddingModels, type Kb, type KbCreate, type Visibility } from "./api";

interface KbCreateModalProps {
  open: boolean;
  onClose: () => void;
  onCreated?: (kb: Kb) => void;
}

interface FormValues {
  kind: KbCreate["kind"];
  name: string;
  description?: string;
  embedding_model_id: number;
  visibility: Visibility;
  org_id?: number;
}

const VISIBILITY_OPTIONS: { value: Visibility; label: string }[] = [
  { value: "private", label: "仅自己" },
  { value: "org", label: "本组织" },
  { value: "platform", label: "全平台" },
];

/** 新建知识库弹窗（原型 #d-newkb）。 */
export function KbCreateModal({ open, onClose, onCreated }: KbCreateModalProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [nameError, setNameError] = useState<string | null>(null);
  const { data: me } = useMe();
  const isSysAdmin = me?.role === "sys_admin";
  const { data: orgs = [] } = useOrgs({ enabled: isSysAdmin });
  const { data: models } = useEmbeddingModels();
  const createKb = useCreateKb();

  const orgTreeData = useMemo(() => toOrgSelectData(buildOrgTree(orgs)), [orgs]);

  useEffect(() => {
    if (open) {
      form.resetFields();
      setNameError(null);
    }
  }, [open, form]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    createKb.mutate(
      {
        kind: values.kind,
        name: values.name,
        description: values.description ? values.description : null,
        embedding_model_id: values.embedding_model_id,
        visibility: values.visibility,
        org_id: isSysAdmin ? values.org_id : null,
      },
      {
        onSuccess: (kb) => {
          message.success("已创建");
          onCreated?.(kb);
          onClose();
        },
        onError: (error) => {
          if (isApiError(error) && error.code === "NAME_CONFLICT") {
            setNameError(error.message);
            return;
          }
          message.error(error instanceof Error ? error.message : "创建失败");
        },
      },
    );
  };

  return (
    <Modal
      open={open}
      title="新建知识库"
      okText="创建"
      cancelText="取消"
      confirmLoading={createKb.isPending}
      onOk={handleSubmit}
      onCancel={onClose}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" preserve={false} initialValues={{ kind: "doc", visibility: "org" }}>
        <Form.Item
          name="kind"
          label="类型"
          extra="创建后不可修改"
          rules={[{ required: true, message: "请选择类型" }]}
        >
          <Radio.Group>
            <Radio.Button value="doc">文档库</Radio.Button>
            <Radio.Button value="qa">问答库</Radio.Button>
          </Radio.Group>
        </Form.Item>

        <Form.Item
          name="name"
          label="名称"
          validateStatus={nameError ? "error" : undefined}
          help={nameError}
          rules={[{ required: true, message: "请输入名称" }, { max: 50, message: "最多 50 字" }]}
        >
          <Input
            placeholder="如：员工制度库"
            maxLength={50}
            onChange={() => setNameError(null)}
          />
        </Form.Item>

        <Form.Item name="description" label="描述" rules={[{ max: 200, message: "最多 200 字" }]}>
          <Input placeholder="选填" maxLength={200} />
        </Form.Item>

        <Form.Item
          name="embedding_model_id"
          label="向量化模型"
          extra="创建后不可修改；要更换只能新建知识库"
          rules={[{ required: true, message: "请选择向量化模型" }]}
        >
          <Select
            placeholder="选择已启用的向量化模型"
            options={(models?.items ?? []).map((model) => ({
              value: model.id,
              label: `${model.name} · ${model.embedding_dim ?? "-"} 维`,
            }))}
          />
        </Form.Item>

        <Form.Item
          name="visibility"
          label="可见范围"
          rules={[{ required: true, message: "请选择可见范围" }]}
        >
          <Radio.Group options={VISIBILITY_OPTIONS} />
        </Form.Item>

        {isSysAdmin ? (
          <Form.Item
            name="org_id"
            label="所属组织"
            rules={[{ required: true, message: "请选择所属组织" }]}
          >
            <TreeSelect
              treeData={orgTreeData}
              placeholder="选择所属组织"
              treeDefaultExpandAll
              allowClear
            />
          </Form.Item>
        ) : null}

        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          默认入库方案、元数据字段和检索配置可在创建后到“设置”中修改。
        </Typography.Paragraph>

        {isSysAdmin ? (
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 12 }}
            message="系统管理员创建知识库需指定所属组织。"
          />
        ) : null}
      </Form>
    </Modal>
  );
}

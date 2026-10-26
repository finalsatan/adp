import { Alert, App, Form, Input, Modal, Radio, Space, Tag, TreeSelect, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import { isApiError } from "../../api/errors";
import { useMe } from "../../api/me";
import { useOrgs } from "../../api/orgs";
import { buildOrgTree, toOrgSelectData } from "../../lib/orgTree";
import {
  useCreateApp,
  VISIBILITY_OPTIONS,
  type App as AppResource,
  type AppCreate,
  type Visibility,
} from "./api";

interface AppCreateModalProps {
  open: boolean;
  onClose: () => void;
  onCreated?: (app: AppResource) => void;
}

interface FormValues {
  app_type: AppCreate["app_type"];
  name: string;
  description?: string;
  visibility: Visibility;
  org_id?: number;
}

/** 新建应用弹窗（原型 #d-newapp）。 */
export function AppCreateModal({ open, onClose, onCreated }: AppCreateModalProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [nameError, setNameError] = useState<string | null>(null);
  const { data: me } = useMe();
  const isSysAdmin = me?.role === "sys_admin";
  const { data: orgs = [] } = useOrgs({ enabled: isSysAdmin });
  const createApp = useCreateApp();

  const orgTreeData = useMemo(() => toOrgSelectData(buildOrgTree(orgs)), [orgs]);

  useEffect(() => {
    if (open) {
      form.resetFields();
      setNameError(null);
    }
  }, [open, form]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    createApp.mutate(
      {
        app_type: values.app_type,
        name: values.name,
        description: values.description ? values.description : null,
        visibility: values.visibility,
        org_id: isSysAdmin ? values.org_id : null,
      },
      {
        onSuccess: (app) => {
          message.success("已创建");
          onCreated?.(app);
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
      title="新建应用"
      okText="创建并配置"
      cancelText="取消"
      confirmLoading={createApp.isPending}
      onOk={handleSubmit}
      onCancel={onClose}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        preserve={false}
        initialValues={{ app_type: "kb_qa", visibility: "org" }}
      >
        <Form.Item
          name="app_type"
          label="类型"
          rules={[{ required: true, message: "请选择类型" }]}
        >
          <Radio.Group>
            <Space direction="vertical">
              <Radio value="kb_qa">
                知识问答
                <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                  基于知识库回答问题，流程固定，用表单配置每一步
                </Typography.Text>
              </Radio>
              <Radio value="workflow" disabled>
                工作流 <Tag>待细化</Tag>
              </Radio>
              <Radio value="agent" disabled>
                智能体 <Tag>待细化</Tag>
              </Radio>
            </Space>
          </Radio.Group>
        </Form.Item>

        <Form.Item
          name="name"
          label="名称"
          validateStatus={nameError ? "error" : undefined}
          help={nameError}
          rules={[{ required: true, message: "请输入名称" }, { max: 50, message: "最多 50 字" }]}
        >
          <Input placeholder="如：HR 政策问答" maxLength={50} onChange={() => setNameError(null)} />
        </Form.Item>

        <Form.Item name="description" label="描述" rules={[{ max: 200, message: "最多 200 字" }]}>
          <Input placeholder="选填" maxLength={200} />
        </Form.Item>

        <Form.Item
          name="visibility"
          label="可见范围"
          rules={[{ required: true, message: "请选择可见范围" }]}
          extra="控制谁能在控制台看到这个应用。谁能使用在发布前设置“使用范围”。"
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

        {isSysAdmin ? (
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 12 }}
            message="系统管理员创建应用需指定所属组织。"
          />
        ) : null}
      </Form>
    </Modal>
  );
}

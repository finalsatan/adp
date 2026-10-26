import { App, Button, Card, Form, Input, Typography } from "antd";
import { useEffect } from "react";
import { useParams } from "react-router-dom";

import { getErrorMessage } from "../../api/errors";
import { appTypeLabel, useApp, useUpdateApp, type App as AppResource } from "./api";
import { AppHeader } from "./AppHeader";

/** 应用设置页（基本信息，原型 #v-appperm 的“基本信息”卡片）。 */
export default function AppSettingsPage() {
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const appId = Number.isFinite(parsedId) ? parsedId : null;
  const { data: app, isLoading } = useApp(appId);

  if (isLoading || !app) {
    return null;
  }

  return (
    <div>
      <AppHeader app={app} active="permissions" />
      <AppBasicInfoCard app={app} />
    </div>
  );
}

interface AppBasicInfoCardProps {
  app: AppResource;
}

/** 名称、描述可改；类型只读。 */
export function AppBasicInfoCard({ app }: AppBasicInfoCardProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ name: string; description?: string }>();
  const updateApp = useUpdateApp();

  useEffect(() => {
    form.setFieldsValue({ name: app.name, description: app.description ?? "" });
  }, [form, app.name, app.description]);

  const handleSave = async () => {
    const values = await form.validateFields();
    updateApp.mutate(
      {
        id: app.id,
        body: {
          name: values.name,
          description: values.description ? values.description : null,
        },
      },
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
          <Input maxLength={50} disabled={!app.editable} />
        </Form.Item>
        <Form.Item name="description" label="描述" rules={[{ max: 200, message: "最多 200 字" }]}>
          <Input maxLength={200} disabled={!app.editable} />
        </Form.Item>
        <Form.Item label="类型">
          <Typography.Text>{appTypeLabel(app.app_type)}</Typography.Text>
          <div>
            <Typography.Text type="secondary">创建后不可修改</Typography.Text>
          </div>
        </Form.Item>
        {app.editable ? (
          <Button type="primary" onClick={handleSave} loading={updateApp.isPending}>
            保存
          </Button>
        ) : null}
      </Form>
    </Card>
  );
}

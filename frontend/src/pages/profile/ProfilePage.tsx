import { useMutation } from "@tanstack/react-query";
import { Alert, App, Button, Card, Form, Input, Typography } from "antd";
import { useEffect, useState } from "react";

import { changePassword, usePolicy, useUpdateMe, type MePatch } from "../../api/auth";
import { getErrorMessage } from "../../api/errors";
import { useMe } from "../../api/me";
import {
  PasswordRules,
  parseWeakReasons,
  type PasswordPolicy,
} from "../../components/PasswordRules";
import { PageHeader } from "../../components/PageHeader";
import { roleLabel } from "../../lib/labels";

const DEFAULT_POLICY: PasswordPolicy = {
  password_min_length: 8,
  password_min_classes: 3,
};

interface ProfileForm {
  name: string;
  email?: string | null;
  phone?: string | null;
}

interface PasswordForm {
  old_password: string;
  new_password: string;
  confirm_password: string;
}

export default function ProfilePage() {
  const { message } = App.useApp();
  const { data: me } = useMe();
  const { data: policy } = usePolicy();
  const updateMe = useUpdateMe();

  const [profileForm] = Form.useForm<ProfileForm>();
  const [passwordForm] = Form.useForm<PasswordForm>();
  const [reasons, setReasons] = useState<string[]>([]);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const newPassword = Form.useWatch("new_password", passwordForm);

  useEffect(() => {
    if (me) {
      profileForm.setFieldsValue({
        name: me.name,
        email: me.email,
        phone: me.phone,
      });
    }
  }, [me, profileForm]);

  const saveProfile = (values: ProfileForm) => {
    const body: MePatch = {
      name: values.name,
      email: values.email?.trim() ? values.email.trim() : null,
      phone: values.phone?.trim() ? values.phone.trim() : null,
    };
    updateMe.mutate(body, {
      onSuccess: () => message.success("已保存"),
    });
  };

  const changePw = useMutation({
    mutationFn: (values: PasswordForm) =>
      changePassword({
        old_password: values.old_password,
        new_password: values.new_password,
      }),
    meta: { skipErrorToast: true },
    onSuccess: () => {
      passwordForm.resetFields();
      setReasons([]);
      setPasswordError(null);
      message.success("密码已修改");
    },
    onError: (err) => {
      setReasons(parseWeakReasons(err));
      setPasswordError(getErrorMessage(err));
    },
  });

  return (
    <>
      <PageHeader title="个人中心" description="查看和修改自己的资料与密码。" />

      <Card title="基本信息" style={{ marginBottom: 16 }}>
        <Form
          form={profileForm}
          layout="vertical"
          style={{ maxWidth: 480 }}
          onFinish={saveProfile}
        >
          <Form.Item label="用户名">
            <Input value={me?.username ?? ""} disabled />
          </Form.Item>
          <Form.Item
            name="name"
            label="姓名"
            rules={[
              { required: true, message: "请输入姓名" },
              { max: 50, message: "姓名最多 50 字" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item label="所属组织">
            <Input value={me?.org?.name ?? "系统管理员"} disabled />
          </Form.Item>
          <Form.Item
            label="角色"
            extra="用户名、所属组织、角色由管理员维护"
          >
            <Input value={me ? roleLabel(me.role) : ""} disabled />
          </Form.Item>
          <Form.Item
            name="email"
            label="邮箱"
            rules={[{ type: "email", message: "邮箱格式不正确" }]}
          >
            <Input allowClear />
          </Form.Item>
          <Form.Item name="phone" label="手机">
            <Input allowClear />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={updateMe.isPending}>
            保存
          </Button>
        </Form>
      </Card>

      <Card title="修改密码">
        {passwordError ? (
          <Alert
            type="error"
            showIcon
            message={passwordError}
            style={{ marginBottom: 16 }}
          />
        ) : null}
        <Form
          form={passwordForm}
          layout="vertical"
          style={{ maxWidth: 480 }}
          onFinish={(values: PasswordForm) => changePw.mutate(values)}
        >
          <Form.Item
            name="old_password"
            label="原密码"
            rules={[{ required: true, message: "请输入原密码" }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[{ required: true, message: "请输入新密码" }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新密码"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_rule, value) {
                  if (!value || getFieldValue("new_password") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error("两次输入的密码不一致"));
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>

          <div style={{ marginBottom: 12 }}>
            <PasswordRules
              password={newPassword ?? ""}
              username={me?.username ?? ""}
              policy={policy ?? DEFAULT_POLICY}
              reasons={reasons}
            />
          </div>

          <Typography.Paragraph type="secondary">
            修改后当前页面保持登录；其他设备上的登录最迟{" "}
            {policy ? policy.access_token_minutes : "—"} 分钟后失效。
          </Typography.Paragraph>

          <Button type="primary" htmlType="submit" loading={changePw.isPending}>
            修改密码
          </Button>
        </Form>
      </Card>
    </>
  );
}

import { LogoutOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, Space, Typography } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { changePassword, logout, usePolicy } from "../../api/auth";
import { getErrorMessage } from "../../api/errors";
import { useMe } from "../../api/me";
import {
  PasswordRules,
  parseWeakReasons,
  type PasswordPolicy,
} from "../../components/PasswordRules";

const DEFAULT_POLICY: PasswordPolicy = {
  password_min_length: 8,
  password_min_classes: 3,
};

interface ChangeForm {
  new_password: string;
  confirm_password: string;
}

export default function ChangePasswordPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: policy } = usePolicy();
  const [reasons, setReasons] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form] = Form.useForm<ChangeForm>();
  const newPassword = Form.useWatch("new_password", form);

  const mutation = useMutation({
    mutationFn: (values: ChangeForm) =>
      changePassword({ new_password: values.new_password }),
    meta: { skipErrorToast: true },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["me"] });
      navigate("/", { replace: true });
    },
    onError: (err) => {
      setReasons(parseWeakReasons(err));
      setError(getErrorMessage(err));
    },
  });

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      // 登出失败也清掉本地状态回登录页
    }
    queryClient.clear();
    navigate("/login", { replace: true });
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#f0f2f5",
        padding: 24,
      }}
    >
      <Card style={{ width: 400 }}>
        <Space
          style={{ width: "100%", justifyContent: "space-between", marginBottom: 8 }}
        >
          <Typography.Title level={4} style={{ margin: 0 }}>
            请修改初始密码
          </Typography.Title>
          <Button type="link" icon={<LogoutOutlined />} onClick={handleLogout}>
            退出登录
          </Button>
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          这是你首次登录（或密码刚被管理员重置），需要先设置新密码。
        </Typography.Paragraph>

        {error ? (
          <Alert
            type="error"
            showIcon
            message={error}
            style={{ marginBottom: 16 }}
          />
        ) : null}

        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(values: ChangeForm) => mutation.mutate(values)}
        >
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

          <div style={{ marginBottom: 24 }}>
            <PasswordRules
              password={newPassword ?? ""}
              username={me?.username ?? ""}
              policy={policy ?? DEFAULT_POLICY}
              reasons={reasons}
            />
          </div>

          <Button
            type="primary"
            htmlType="submit"
            block
            loading={mutation.isPending}
          >
            确认并进入平台
          </Button>
        </Form>
      </Card>
    </div>
  );
}

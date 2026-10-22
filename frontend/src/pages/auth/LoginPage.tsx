import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { login, useOptionalMe } from "../../api/auth";
import { getErrorMessage } from "../../api/errors";

/** 只接受站内路径；拒绝 https://、//evil.com、/\evil.com 这类开放跳转。 */
export function safeRedirect(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return "/";
  }
  return raw;
}

interface LoginForm {
  username: string;
  password: string;
}

export default function LoginPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const { data: me } = useOptionalMe();

  const mutation = useMutation({
    mutationFn: (values: LoginForm) => login(values.username, values.password),
    meta: { skipErrorToast: true },
    onSuccess: (data) => {
      if (data.must_change_password) {
        navigate("/change-password", { replace: true });
        return;
      }
      navigate(safeRedirect(params.get("redirect")), { replace: true });
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (me) {
    return <Navigate to="/" replace />;
  }

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
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            智能体开发平台
          </Typography.Title>
          <Typography.Text type="secondary">
            文档入库 · 带引用问答 · 反馈 · 评测
          </Typography.Text>
        </div>

        {error ? (
          <Alert
            type="error"
            showIcon
            message={error}
            style={{ marginBottom: 16 }}
          />
        ) : null}

        <Form
          layout="vertical"
          requiredMark={false}
          onFinish={(values: LoginForm) => mutation.mutate(values)}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input prefix={<UserOutlined />} autoComplete="username" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              autoComplete="current-password"
            />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            block
            loading={mutation.isPending}
          >
            登录
          </Button>
        </Form>

        <Typography.Paragraph
          type="secondary"
          style={{ marginTop: 16, marginBottom: 0, textAlign: "center", fontSize: 12 }}
        >
          首次登录或密码被重置后，需要先修改密码。
        </Typography.Paragraph>
      </Card>
    </div>
  );
}

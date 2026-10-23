import { App, Form, Input, Modal, Typography } from "antd";
import { useEffect, useState } from "react";

import { usePolicy } from "../../../api/auth";
import { getErrorMessage } from "../../../api/errors";
import {
  PasswordRules,
  parseWeakReasons,
  type PasswordPolicy,
} from "../../../components/PasswordRules";
import { useResetPassword, type User } from "./api";

const DEFAULT_POLICY: PasswordPolicy = {
  password_min_length: 8,
  password_min_classes: 3,
};

interface ResetPasswordModalProps {
  open: boolean;
  user: User | null;
  onClose: () => void;
  onSuccess?: () => void;
}

/** 重置密码弹窗（原型 #m-resetpw）。 */
export function ResetPasswordModal({
  open,
  user,
  onClose,
  onSuccess,
}: ResetPasswordModalProps) {
  const { message } = App.useApp();
  const { data: policy } = usePolicy();
  const resetPassword = useResetPassword();
  const [form] = Form.useForm<{ new_password: string }>();
  const [reasons, setReasons] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const newPassword = Form.useWatch("new_password", form);

  useEffect(() => {
    if (open) {
      form.resetFields();
      setReasons([]);
      setFormError(null);
    }
  }, [open, form]);

  const submit = (values: { new_password: string }) => {
    if (!user) {
      return;
    }
    setFormError(null);
    resetPassword.mutate(
      { id: user.id, newPassword: values.new_password },
      {
        onSuccess: () => {
          message.success("密码已重置");
          onSuccess?.();
          onClose();
        },
        onError: (error) => {
          setReasons(parseWeakReasons(error));
          setFormError(getErrorMessage(error));
        },
      },
    );
  };

  return (
    <Modal
      open={open}
      title={user ? `重置「${user.username}」的密码` : "重置密码"}
      okText="重置"
      cancelText="取消"
      confirmLoading={resetPassword.isPending}
      onCancel={onClose}
      onOk={() => form.submit()}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" onFinish={submit}>
        <Form.Item
          name="new_password"
          label="新密码"
          rules={[{ required: true, message: "请输入新密码" }]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <div style={{ marginBottom: 12 }}>
          <PasswordRules
            password={newPassword ?? ""}
            username={user?.username ?? ""}
            policy={policy ?? DEFAULT_POLICY}
            reasons={reasons}
          />
        </div>
        {formError ? (
          <Typography.Paragraph type="danger">{formError}</Typography.Paragraph>
        ) : null}
        <Typography.Paragraph type="secondary">
          重置后，用户下次登录时必须修改密码。
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          已登录的会话不会立即退出：刷新令牌随即作废，访问令牌最迟在到期时失效（默认{" "}
          {policy ? policy.access_token_minutes : "—"} 分钟）。
        </Typography.Paragraph>
      </Form>
    </Modal>
  );
}

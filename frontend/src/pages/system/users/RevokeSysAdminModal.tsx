import { Alert, App, Form, Modal, Select } from "antd";
import { useEffect, useState } from "react";

import { getErrorMessage } from "../../../api/errors";
import { ROLE_LABELS } from "../../../lib/labels";
import { useRevokeSysAdmin, type AssignableRole, type Org, type User } from "./api";

interface RevokeForm {
  org_id: number;
  role: AssignableRole;
}

interface RevokeSysAdminModalProps {
  open: boolean;
  user: User | null;
  orgs: Org[];
  onClose: () => void;
  onSuccess?: () => void;
}

/** 撤销系统管理员（补充）：撤销后必须为其指定所属组织和角色。 */
export function RevokeSysAdminModal({
  open,
  user,
  orgs,
  onClose,
  onSuccess,
}: RevokeSysAdminModalProps) {
  const { message } = App.useApp();
  const revoke = useRevokeSysAdmin();
  const [form] = Form.useForm<RevokeForm>();
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      form.resetFields();
      setFormError(null);
    }
  }, [open, form]);

  const submit = (values: RevokeForm) => {
    if (!user) {
      return;
    }
    setFormError(null);
    revoke.mutate(
      { id: user.id, body: { org_id: values.org_id, role: values.role } },
      {
        onSuccess: () => {
          message.success("已撤销系统管理员");
          onSuccess?.();
          onClose();
        },
        onError: (error) => setFormError(getErrorMessage(error)),
      },
    );
  };

  return (
    <Modal
      open={open}
      title={user ? `撤销「${user.username}」的系统管理员` : "撤销系统管理员"}
      okText="确认撤销"
      cancelText="取消"
      confirmLoading={revoke.isPending}
      onCancel={onClose}
      onOk={() => form.submit()}
      destroyOnHidden
    >
      {formError ? (
        <Alert type="error" showIcon message={formError} style={{ marginBottom: 16 }} />
      ) : null}
      <Form
        form={form}
        layout="vertical"
        initialValues={{ role: "builder" }}
        onFinish={submit}
      >
        <Form.Item
          name="org_id"
          label="所属组织"
          rules={[{ required: true, message: "请选择所属组织" }]}
        >
          <Select
            showSearch
            optionFilterProp="label"
            placeholder="请选择"
            options={orgs.map((org) => ({ value: org.id, label: org.name }))}
          />
        </Form.Item>
        <Form.Item
          name="role"
          label="角色"
          rules={[{ required: true, message: "请选择角色" }]}
        >
          <Select
            options={(["org_admin", "builder", "end_user"] as AssignableRole[]).map(
              (role) => ({ value: role, label: ROLE_LABELS[role] }),
            )}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}

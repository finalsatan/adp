import { Alert, App, Button, Drawer, Form, Input, Radio, Space, TreeSelect, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import { usePolicy } from "../../../api/auth";
import { getErrorMessage, isApiError } from "../../../api/errors";
import {
  PasswordRules,
  parseWeakReasons,
  type PasswordPolicy,
} from "../../../components/PasswordRules";
import {
  buildOrgTree,
  canAppointOrgAdmin,
  descendantNames,
  toOrgSelectData,
  type OperatorScope,
  type Org,
} from "../../../lib/orgTree";
import {
  useCreateUser,
  type AssignableRole,
  type User,
  type UserCreate,
} from "./api";

const DEFAULT_POLICY: PasswordPolicy = {
  password_min_length: 8,
  password_min_classes: 3,
};

interface CreateForm {
  username: string;
  name: string;
  password: string;
  email?: string | null;
  phone?: string | null;
  org_id: number;
  role: AssignableRole;
  remark?: string | null;
}

interface UserCreateDrawerProps {
  open: boolean;
  orgs: Org[];
  scope: OperatorScope;
  onClose: () => void;
  onCreated?: (user: User) => void;
}

/** 新建用户抽屉（原型 #d-newuser）。 */
export function UserCreateDrawer({
  open,
  orgs,
  scope,
  onClose,
  onCreated,
}: UserCreateDrawerProps) {
  const { message } = App.useApp();
  const { data: policy } = usePolicy();
  const createUser = useCreateUser();
  const [form] = Form.useForm<CreateForm>();
  const [formError, setFormError] = useState<string | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const password = Form.useWatch("password", form);
  const username = Form.useWatch("username", form);
  const orgId = Form.useWatch("org_id", form);

  useEffect(() => {
    if (open) {
      form.resetFields();
      setFormError(null);
      setReasons([]);
    }
  }, [open, form]);

  const treeData = useMemo(() => toOrgSelectData(buildOrgTree(orgs)), [orgs]);
  const selectedOrgPath = orgs.find((org) => org.id === orgId)?.path ?? null;
  const canAppoint = canAppointOrgAdmin(scope, selectedOrgPath);
  const appointable = descendantNames(orgs, scope.orgPath);
  const roleHint =
    "搭建者：创建和管理知识库、入库方案、应用；终端用户：只使用发布给自己的应用，不进控制台" +
    (scope.role === "org_admin"
      ? `；组织管理员：只能把下级组织${
          appointable.length > 0 ? `（${appointable.join("、")}）` : ""
        }的用户设为组织管理员`
      : "");

  const submit = (values: CreateForm) => {
    setFormError(null);
    const body: UserCreate = {
      username: values.username.trim(),
      name: values.name.trim(),
      password: values.password,
      email: values.email?.trim() ? values.email.trim() : null,
      phone: values.phone?.trim() ? values.phone.trim() : null,
      remark: values.remark?.trim() ? values.remark.trim() : null,
      org_id: values.org_id,
      role: values.role,
    };
    createUser.mutate(body, {
      onSuccess: (user) => {
        message.success("用户已创建");
        onCreated?.(user);
        onClose();
      },
      onError: (error) => {
        setReasons(parseWeakReasons(error));
        if (isApiError(error) && error.code === "USERNAME_EXISTS") {
          form.setFields([{ name: "username", errors: [error.message] }]);
          return;
        }
        setFormError(getErrorMessage(error));
      },
    });
  };

  return (
    <Drawer
      title="新建用户"
      width={480}
      open={open}
      onClose={onClose}
      destroyOnHidden
      footer={
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={createUser.isPending} onClick={() => form.submit()}>
            创建
          </Button>
        </Space>
      }
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
          name="username"
          label="用户名"
          rules={[
            { required: true, message: "请输入用户名" },
            { min: 3, max: 32, message: "用户名 3–32 位" },
            {
              pattern: /^[A-Za-z0-9_.-]+$/,
              message: "只能包含字母、数字、_、.、-",
            },
          ]}
        >
          <Input placeholder="登录用，唯一，不区分大小写" />
        </Form.Item>
        <Form.Item
          name="name"
          label="姓名"
          rules={[
            { required: true, message: "请输入姓名" },
            { max: 50, message: "姓名最多 50 字" },
          ]}
        >
          <Input placeholder="真实姓名" />
        </Form.Item>
        <Form.Item
          name="password"
          label="初始密码"
          rules={[{ required: true, message: "请输入初始密码" }]}
          extra="首次登录需修改"
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <div style={{ margin: "-8px 0 16px" }}>
          <PasswordRules
            password={password ?? ""}
            username={username ?? ""}
            policy={policy ?? DEFAULT_POLICY}
            reasons={reasons}
          />
        </div>
        <Form.Item
          name="email"
          label="邮箱"
          rules={[{ type: "email", message: "邮箱格式不正确" }]}
        >
          <Input allowClear placeholder="name@corp.com" />
        </Form.Item>
        <Form.Item name="phone" label="手机">
          <Input allowClear placeholder="选填" />
        </Form.Item>
        <Form.Item
          name="org_id"
          label="所属组织"
          rules={[{ required: true, message: "请选择所属组织" }]}
          extra="只列出你管理范围内的组织"
        >
          <TreeSelect
            treeData={treeData}
            treeDefaultExpandAll
            showSearch
            treeNodeFilterProp="title"
            placeholder="请选择"
          />
        </Form.Item>
        <Form.Item name="role" label="角色" rules={[{ required: true }]}>
          <Radio.Group>
            <Space direction="vertical">
              <Radio value="builder">搭建者</Radio>
              <Radio value="end_user">终端用户</Radio>
              <Radio value="org_admin" disabled={!canAppoint}>
                组织管理员
              </Radio>
            </Space>
          </Radio.Group>
        </Form.Item>
        <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
          {roleHint}
        </Typography.Paragraph>
        <Form.Item name="remark" label="备注" rules={[{ max: 200, message: "备注最多 200 字" }]}>
          <Input.TextArea rows={2} placeholder="选填" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}

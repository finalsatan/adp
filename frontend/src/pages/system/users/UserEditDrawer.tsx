import {
  Alert,
  App,
  Button,
  Drawer,
  Form,
  Input,
  Radio,
  Space,
  TreeSelect,
  Typography,
} from "antd";
import { useEffect, useMemo, useState } from "react";

import { getErrorMessage } from "../../../api/errors";
import {
  buildOrgTree,
  canAppointOrgAdmin,
  descendantNames,
  toOrgSelectData,
  type OperatorScope,
  type Org,
} from "../../../lib/orgTree";
import {
  useGrantSysAdmin,
  useUpdateUser,
  type AssignableRole,
  type Role,
  type User,
  type UserPatch,
} from "./api";

interface EditForm {
  name: string;
  email?: string | null;
  phone?: string | null;
  org_id?: number;
  role?: Role;
  remark?: string | null;
}

interface UserEditDrawerProps {
  open: boolean;
  user: User | null;
  orgs: Org[];
  scope: OperatorScope;
  onClose: () => void;
  onSaved?: (user: User) => void;
}

/**
 * 编辑用户抽屉（原型 #d-edituser），P1-15 复用。
 * 系统管理员编辑普通用户时可以把角色设为“系统管理员”，保存时走任命接口。
 */
export function UserEditDrawer({
  open,
  user,
  orgs,
  scope,
  onClose,
  onSaved,
}: UserEditDrawerProps) {
  const { message, modal } = App.useApp();
  const updateUser = useUpdateUser();
  const grant = useGrantSysAdmin();
  const [form] = Form.useForm<EditForm>();
  const [formError, setFormError] = useState<string | null>(null);
  const orgId = Form.useWatch("org_id", form);

  useEffect(() => {
    if (open && user) {
      form.setFieldsValue({
        name: user.name,
        email: user.email,
        phone: user.phone,
        org_id: user.org?.id,
        role: user.role,
        remark: user.remark,
      });
      setFormError(null);
    }
  }, [open, user, form]);

  const treeData = useMemo(() => toOrgSelectData(buildOrgTree(orgs)), [orgs]);
  const selectedOrgPath = orgs.find((org) => org.id === orgId)?.path ?? null;
  const canAppoint = canAppointOrgAdmin(scope, selectedOrgPath);
  const appointable = descendantNames(orgs, scope.orgPath);
  const isSysAdminTarget = user?.role === "sys_admin";
  const canGrantSysAdmin = scope.role === "sys_admin" && !isSysAdminTarget;
  const orgChanged =
    !isSysAdminTarget && user?.org != null && orgId != null && orgId !== user.org.id;

  const submit = (values: EditForm) => {
    if (!user) {
      return;
    }
    setFormError(null);

    if (values.role === "sys_admin" && !isSysAdminTarget) {
      modal.confirm({
        title: "任命为系统管理员",
        content: "任命后该用户不再属于任何组织，可管理整个平台",
        okText: "确认",
        cancelText: "取消",
        onOk: () => {
          grant.mutate(user.id, {
            onSuccess: () => {
              message.success("已任命为系统管理员");
              onSaved?.(user);
              onClose();
            },
            onError: (error) => setFormError(getErrorMessage(error)),
          });
        },
      });
      return;
    }

    const common: UserPatch = {
      name: values.name.trim(),
      email: values.email?.trim() ? values.email.trim() : null,
      phone: values.phone?.trim() ? values.phone.trim() : null,
      remark: values.remark?.trim() ? values.remark.trim() : null,
    };
    const body: UserPatch = isSysAdminTarget
      ? common
      : { ...common, org_id: values.org_id, role: values.role as AssignableRole };

    updateUser.mutate(
      { id: user.id, body },
      {
        onSuccess: (saved) => {
          message.success("已保存");
          onSaved?.(saved);
          onClose();
        },
        onError: (error) => {
          setFormError(getErrorMessage(error));
        },
      },
    );
  };

  return (
    <Drawer
      title={user ? `编辑用户 · ${user.username}` : "编辑用户"}
      width={480}
      open={open}
      onClose={onClose}
      destroyOnHidden
      footer={
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            loading={updateUser.isPending || grant.isPending}
            onClick={() => form.submit()}
          >
            保存
          </Button>
        </Space>
      }
    >
      {formError ? (
        <Alert type="error" showIcon message={formError} style={{ marginBottom: 16 }} />
      ) : null}
      <Form form={form} layout="vertical" onFinish={submit}>
        <Form.Item label="用户名">
          <Input value={user?.username ?? ""} disabled />
          <Typography.Text type="secondary">不可修改</Typography.Text>
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
        <Form.Item
          name="email"
          label="邮箱"
          rules={[{ type: "email", message: "邮箱格式不正确" }]}
        >
          <Input allowClear placeholder="选填" />
        </Form.Item>
        <Form.Item name="phone" label="手机">
          <Input allowClear placeholder="选填" />
        </Form.Item>
        {!isSysAdminTarget ? (
          <>
            <Form.Item
              name="org_id"
              label="所属组织"
              rules={[{ required: true, message: "请选择所属组织" }]}
              extra="只能在你的管理范围内调整"
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
                  {canGrantSysAdmin ? (
                    <Radio value="sys_admin">系统管理员</Radio>
                  ) : null}
                </Space>
              </Radio.Group>
            </Form.Item>
            {scope.role === "org_admin" ? (
              <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
                {`组织管理员只能任命下级组织${
                  appointable.length > 0 ? `（${appointable.join("、")}）` : ""
                }的用户`}
              </Typography.Paragraph>
            ) : null}
          </>
        ) : null}
        <Form.Item name="remark" label="备注" rules={[{ max: 200, message: "备注最多 200 字" }]}>
          <Input.TextArea rows={2} placeholder="选填" />
        </Form.Item>
      </Form>

      {orgChanged && user && user.owned_resource_count > 0 ? (
        <Alert
          type="warning"
          showIcon
          message={`调整所属组织后，${user.username} 在 ${user.org?.name} 创建的 ${user.owned_resource_count} 个资源留在 ${user.org?.name}，由 ${user.org?.name} 的组织管理员管理；${user.username} 不再拥有这些资源的创建人权限，需要时可把他加为协作者。`}
        />
      ) : null}
    </Drawer>
  );
}

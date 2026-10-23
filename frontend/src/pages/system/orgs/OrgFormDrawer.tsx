import { Alert, App, Button, Drawer, Form, Input, Space, TreeSelect } from "antd";
import { useEffect, useMemo, useState } from "react";

import { getErrorMessage, isApiError } from "../../../api/errors";
import {
  buildOrgTree,
  toOrgSelectData,
  type OperatorScope,
  type Org,
} from "../../../lib/orgTree";
import { useCreateOrg, useUpdateOrg, type OrgCreate, type OrgPatch } from "./api";

interface OrgForm {
  parent_id?: number | null;
  name: string;
  remark?: string | null;
}

export type OrgFormMode = "create" | "edit";

interface OrgFormDrawerProps {
  open: boolean;
  mode: OrgFormMode;
  /** 编辑时被编辑的组织。 */
  org: Org | null;
  /** 新建时预选的上级（“添加下级”）。 */
  parentOrg: Org | null;
  orgs: Org[];
  scope: OperatorScope;
  onClose: () => void;
  onSaved?: () => void;
}

/** 新建 / 编辑组织抽屉（原型 #d-neworg），两种模式共用一个表单。 */
export function OrgFormDrawer({
  open,
  mode,
  org,
  parentOrg,
  orgs,
  scope,
  onClose,
  onSaved,
}: OrgFormDrawerProps) {
  const { message } = App.useApp();
  const createOrg = useCreateOrg();
  const updateOrg = useUpdateOrg();
  const [form] = Form.useForm<OrgForm>();
  const [formError, setFormError] = useState<string | null>(null);

  const isEdit = mode === "edit";
  // 系统管理员可在任意组织下建，也可建顶级；组织管理员只能在本组织及下级下建。
  const allowTopLevel = scope.role === "sys_admin";
  const selectableOrgs = useMemo(() => {
    if (scope.role === "sys_admin") {
      return orgs;
    }
    if (!scope.orgPath) {
      return [];
    }
    return orgs.filter((item) => item.path.startsWith(scope.orgPath as string));
  }, [orgs, scope.role, scope.orgPath]);

  const treeData = useMemo(
    () => toOrgSelectData(buildOrgTree(isEdit ? orgs : selectableOrgs)),
    [isEdit, orgs, selectableOrgs],
  );

  useEffect(() => {
    if (!open) {
      return;
    }
    setFormError(null);
    if (isEdit && org) {
      form.setFieldsValue({
        parent_id: org.parent_id ?? undefined,
        name: org.name,
        remark: org.remark ?? undefined,
      });
      return;
    }
    form.resetFields();
    form.setFieldsValue({ parent_id: parentOrg?.id ?? undefined });
  }, [open, isEdit, org, parentOrg, form]);

  const submit = (values: OrgForm) => {
    setFormError(null);
    const name = values.name.trim();
    const remark = values.remark?.trim() ? values.remark.trim() : null;

    const onError = (error: unknown) => {
      if (isApiError(error) && error.code === "NAME_CONFLICT") {
        form.setFields([{ name: "name", errors: [error.message] }]);
        return;
      }
      setFormError(getErrorMessage(error));
    };

    if (isEdit) {
      if (!org) {
        return;
      }
      const body: OrgPatch = { name, remark };
      updateOrg.mutate(
        { id: org.id, body },
        {
          onSuccess: () => {
            message.success("已保存");
            onSaved?.();
            onClose();
          },
          onError,
        },
      );
      return;
    }

    const body: OrgCreate = {
      parent_id: values.parent_id ?? null,
      name,
      remark,
    };
    createOrg.mutate(body, {
      onSuccess: () => {
        message.success("组织已创建");
        onSaved?.();
        onClose();
      },
      onError,
    });
  };

  const title = isEdit
    ? `编辑组织 · ${org?.name ?? ""}`
    : parentOrg
      ? `新建下级组织 · ${parentOrg.name}`
      : allowTopLevel
        ? "新建顶级组织"
        : "新建下级组织";

  return (
    <Drawer
      title={title}
      width={480}
      open={open}
      onClose={onClose}
      destroyOnHidden
      footer={
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            loading={createOrg.isPending || updateOrg.isPending}
            onClick={() => form.submit()}
          >
            {isEdit ? "保存" : "创建"}
          </Button>
        </Space>
      }
    >
      {formError ? (
        <Alert type="error" showIcon message={formError} style={{ marginBottom: 16 }} />
      ) : null}
      <Form form={form} layout="vertical" onFinish={submit}>
        <Form.Item
          name="parent_id"
          label="上级组织"
          rules={
            !isEdit && !allowTopLevel
              ? [{ required: true, message: "请选择上级组织" }]
              : undefined
          }
          extra={
            isEdit
              ? "创建后不可修改"
              : "只能在你管理的组织下新建。系统管理员可新建顶级组织。创建后不可修改，编辑组织时此项只读"
          }
        >
          <TreeSelect
            treeData={treeData}
            treeDefaultExpandAll
            showSearch
            treeNodeFilterProp="title"
            placeholder={allowTopLevel ? "不选则创建顶级组织" : "请选择"}
            allowClear={allowTopLevel && !isEdit}
            disabled={isEdit}
          />
        </Form.Item>
        <Form.Item
          name="name"
          label="名称"
          rules={[
            { required: true, message: "请输入组织名称" },
            { max: 50, message: "名称最多 50 字" },
          ]}
        >
          <Input placeholder="如：数据组" />
        </Form.Item>
        <Form.Item
          name="remark"
          label="备注"
          rules={[{ max: 200, message: "备注最多 200 字" }]}
        >
          <Input.TextArea rows={2} placeholder="选填" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}

import { App, Input, Modal, Radio, Select, Space, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import { getErrorMessage } from "../../../api/errors";
import {
  buildBatchPayload,
  useBatchDocuments,
  type DocumentOut,
  type MetadataField,
} from "./api";

interface DocumentMetaModalProps {
  open: boolean;
  kbId: number;
  docs: DocumentOut[];
  fields: MetadataField[];
  onClose: () => void;
}

type FieldMode = "keep" | "clear" | "value";

interface FieldState {
  mode: FieldMode;
  value: unknown;
}

/** 批量 / 单个设置文档元数据（原型 #d-docmeta）。 */
export function DocumentMetaModal({ open, kbId, docs, fields, onClose }: DocumentMetaModalProps) {
  const { message } = App.useApp();
  const batch = useBatchDocuments(kbId);
  const [states, setStates] = useState<Record<number, FieldState>>({});

  useEffect(() => {
    if (open) {
      setStates({});
    }
  }, [open]);

  const setMode = (fieldId: number, mode: FieldMode) => {
    setStates((previous) => ({
      ...previous,
      [fieldId]: { mode, value: previous[fieldId]?.value ?? "" },
    }));
  };

  const setValue = (fieldId: number, value: unknown) => {
    setStates((previous) => ({
      ...previous,
      [fieldId]: { mode: "value", value },
    }));
  };

  const names = useMemo(() => docs.map((doc) => doc.name).join("、"), [docs]);

  const handleSave = () => {
    const metadata: Record<string, unknown> = {};
    for (const field of fields) {
      const state = states[field.id];
      if (!state || state.mode === "keep") {
        continue;
      }
      metadata[field.name] = state.mode === "clear" ? null : state.value;
    }
    if (Object.keys(metadata).length === 0) {
      message.info("没有要修改的字段");
      return;
    }
    batch.mutate(buildBatchPayload("set_metadata", docs.map((doc) => doc.id), { metadata }), {
      onSuccess: () => {
        message.success("已保存");
        onClose();
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });
  };

  return (
    <Modal
      open={open}
      title={`设置元数据 · 已选 ${docs.length} 个文档`}
      okText="保存"
      cancelText="取消"
      confirmLoading={batch.isPending}
      onOk={handleSave}
      onCancel={onClose}
      destroyOnHidden
      width={560}
    >
      <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }}>
        {names}
      </Typography.Paragraph>

      {fields.length === 0 ? (
        <Typography.Text type="secondary">
          还没有元数据字段，请先在“设置 · 元数据字段”中定义。
        </Typography.Text>
      ) : null}

      {fields.map((field) => {
        const state = states[field.id] ?? { mode: "keep", value: "" };
        return (
          <div key={field.id} style={{ marginBottom: 12 }}>
            <Typography.Text strong>{field.name}</Typography.Text>
            <div style={{ marginTop: 4 }}>
              <Space align="start" wrap>
                <Radio.Group
                  value={state.mode}
                  onChange={(event) => setMode(field.id, event.target.value as FieldMode)}
                  optionType="button"
                  buttonStyle="solid"
                  options={[
                    { value: "keep", label: "保持不变" },
                    { value: "value", label: "设置" },
                    { value: "clear", label: "清空" },
                  ]}
                />
                {state.mode === "value" ? (
                  field.type === "select" ? (
                    <Select
                      style={{ minWidth: 180 }}
                      value={state.value === "" ? undefined : (state.value as string)}
                      options={(field.options ?? []).map((option) => ({
                        value: option,
                        label: option,
                      }))}
                      onChange={(value) => setValue(field.id, value)}
                    />
                  ) : (
                    <Input
                      style={{ width: 180 }}
                      type={field.type === "number" ? "number" : field.type === "date" ? "date" : "text"}
                      value={state.value === "" ? "" : String(state.value)}
                      onChange={(event) => setValue(field.id, event.target.value)}
                    />
                  )
                ) : null}
              </Space>
            </div>
          </div>
        );
      })}

      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
        只修改填写了的字段。字段在“设置 · 元数据字段”中定义；单个文档可在文档列表的“···”中设置。
      </Typography.Paragraph>
    </Modal>
  );
}

import { Alert, Modal, Radio, Space, Typography } from "antd";
import { useEffect, useState } from "react";

import { getErrorMessage } from "../../api/errors";
import {
  resourceInUseRefs,
  useUpdateResourceVisibility,
  type ResourceRef,
  type Visibility,
} from "./api";

export interface VisibilityTarget {
  id: number;
  name: string;
  visibility: Visibility;
}

interface VisibilityModalProps {
  open: boolean;
  resource: VisibilityTarget | null;
  onClose: () => void;
}

const OPTIONS: { value: Visibility; label: string }[] = [
  { value: "private", label: "仅自己" },
  { value: "org", label: "本组织（含下级组织）" },
  { value: "platform", label: "全平台" },
];

/**
 * 资源共用的“可见范围”弹窗（需求 10.4）。
 * 第三期起的知识库、入库方案、应用页面直接复用。
 */
export function VisibilityModal({ open, resource, onClose }: VisibilityModalProps) {
  const update = useUpdateResourceVisibility();
  const [value, setValue] = useState<Visibility>("org");
  const [refs, setRefs] = useState<ResourceRef[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open && resource) {
      setValue(resource.visibility);
      setRefs([]);
      setError(null);
    }
  }, [open, resource]);

  const handleSave = () => {
    if (!resource) {
      return;
    }
    setRefs([]);
    setError(null);
    update.mutate(
      { id: resource.id, visibility: value },
      {
        onSuccess: onClose,
        onError: (err) => {
          const blockers = resourceInUseRefs(err);
          if (blockers.length > 0) {
            setRefs(blockers);
          } else {
            setError(getErrorMessage(err));
          }
        },
      },
    );
  };

  return (
    <Modal
      open={open}
      title={resource ? `修改可见范围 · ${resource.name}` : "修改可见范围"}
      okText="保存"
      cancelText="取消"
      confirmLoading={update.isPending}
      onOk={handleSave}
      onCancel={onClose}
      destroyOnHidden
    >
      <Radio.Group
        value={value}
        onChange={(event) => setValue(event.target.value as Visibility)}
      >
        <Space direction="vertical">
          {OPTIONS.map((option) => (
            <Radio key={option.value} value={option.value}>
              {option.label}
            </Radio>
          ))}
        </Space>
      </Radio.Group>

      {refs.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 16 }}
          message="这些应用会因此不能再引用它，请先在应用中解除引用"
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {refs.map((ref) => (
                <li key={`${ref.type}-${ref.id}`}>
                  {ref.name}（{ref.type}）
                </li>
              ))}
            </ul>
          }
        />
      ) : null}

      {error ? (
        <Alert type="error" showIcon style={{ marginTop: 16 }} message={error} />
      ) : null}

      <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
        “本组织”包含下级组织；缩小可见范围前会检查引用它的应用。
      </Typography.Paragraph>
    </Modal>
  );
}

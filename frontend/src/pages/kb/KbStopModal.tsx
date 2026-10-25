import { App, Modal, Table, Typography, type TableProps } from "antd";

import { getErrorMessage } from "../../api/errors";
import { useDisableKb, useKbRefs, type KbRef } from "./api";

export interface KbStopTarget {
  id: number;
  name: string;
}

interface KbStopModalProps {
  open: boolean;
  kb: KbStopTarget | null;
  onClose: () => void;
}

const columns: TableProps<KbRef>["columns"] = [
  { title: "引用的应用", render: (_value, ref) => `${ref.name}（${ref.type_label}）` },
  { title: "负责人", render: (_value, ref) => ref.owner.name },
];

/** 停用知识库确认弹窗（原型 #m-kbstop）：先列出引用它的应用。 */
export function KbStopModal({ open, kb, onClose }: KbStopModalProps) {
  const { message } = App.useApp();
  const refs = useKbRefs(kb?.id ?? null, open);
  const disableKb = useDisableKb();

  const handleConfirm = () => {
    if (!kb) {
      return;
    }
    disableKb.mutate(kb.id, {
      onSuccess: () => {
        message.success("已停用");
        onClose();
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });
  };

  return (
    <Modal
      open={open}
      title={kb ? `停用知识库「${kb.name}」？` : "停用知识库"}
      okText="确认停用"
      cancelText="取消"
      confirmLoading={disableKb.isPending}
      onOk={handleConfirm}
      onCancel={onClose}
      destroyOnHidden
    >
      <Typography.Paragraph>
        停用后，引用它的应用检索不到其内容，但不会报错。可随时重新启用。
      </Typography.Paragraph>
      {(refs.data ?? []).length > 0 ? (
        <Table<KbRef>
          rowKey={(ref) => `${ref.type}-${ref.resource_id}`}
          columns={columns}
          dataSource={refs.data ?? []}
          pagination={false}
          size="small"
        />
      ) : null}
    </Modal>
  );
}

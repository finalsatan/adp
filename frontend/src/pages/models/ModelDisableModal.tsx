import { App, Modal, Table, Typography, type TableProps } from "antd";

import { getErrorMessage } from "../../api/errors";
import { useDisableModel, useModelRefs, type Model, type ModelRef } from "./api";

/** 引用清单表格：停用确认和删除被引用时共用。 */
export function ModelRefsTable({ refs }: { refs: ModelRef[] }) {
  const columns: TableProps<ModelRef>["columns"] = [
    { title: "引用它的资源", dataIndex: "name" },
    { title: "类型", dataIndex: "type_label" },
    {
      title: "负责人",
      render: (_value, ref) => ref.owner.username,
    },
  ];
  return (
    <Table<ModelRef>
      rowKey={(ref) => `${ref.type}-${ref.resource_id}`}
      columns={columns}
      dataSource={refs}
      size="small"
      pagination={false}
    />
  );
}

interface ModelDisableModalProps {
  open: boolean;
  model: Model | null;
  onClose: () => void;
}

/** 停用确认弹窗（原型 #m-disable）：先拉引用清单再确认。 */
export function ModelDisableModal({ open, model, onClose }: ModelDisableModalProps) {
  const { message } = App.useApp();
  const disableModel = useDisableModel();
  const { data: refs, isLoading } = useModelRefs(open ? (model?.id ?? null) : null);

  const confirm = () => {
    if (!model) {
      return;
    }
    disableModel.mutate(model.id, {
      onSuccess: () => {
        message.success("已停用");
        onClose();
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });
  };

  const count = refs?.length ?? 0;

  return (
    <Modal
      open={open}
      title={model ? `停用模型「${model.name}」？` : "停用模型"}
      okText="确认停用"
      cancelText="取消"
      confirmLoading={disableModel.isPending}
      onCancel={onClose}
      onOk={confirm}
      destroyOnHidden
    >
      {isLoading ? (
        <Typography.Paragraph type="secondary">正在加载引用清单…</Typography.Paragraph>
      ) : count > 0 ? (
        <>
          <Typography.Paragraph>
            停用后，以下 {count} 个资源调用该模型时会提示“模型不可用”。可稍后重新启用。
          </Typography.Paragraph>
          <ModelRefsTable refs={refs ?? []} />
        </>
      ) : (
        <Typography.Paragraph>
          停用后，该模型在模型体验、应用、知识库和入库方案中不可用。可稍后重新启用。
        </Typography.Paragraph>
      )}
    </Modal>
  );
}

interface ModelRefsModalProps {
  open: boolean;
  modelName: string;
  refs: ModelRef[];
  onClose: () => void;
}

/** 删除被引用时的提示弹窗：列出引用清单，需先解除引用（需求 3.4）。 */
export function ModelRefsModal({ open, modelName, refs, onClose }: ModelRefsModalProps) {
  return (
    <Modal
      open={open}
      title={`模型「${modelName}」不能删除`}
      okText="知道了"
      cancelButtonProps={{ style: { display: "none" } }}
      onOk={onClose}
      onCancel={onClose}
      destroyOnHidden
    >
      <Typography.Paragraph>
        以下 {refs.length} 个资源引用了该模型，需先解除引用后才能删除。
      </Typography.Paragraph>
      <ModelRefsTable refs={refs} />
    </Modal>
  );
}

import { Alert, App, List, Modal, Radio, Select, Space, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import { getErrorMessage } from "../../../api/errors";
import {
  buildBatchPayload,
  useBatchDocuments,
  useReparseDocument,
  type DocumentOut,
  type ReparseMode,
  type Scheme,
} from "./api";

interface ReparseModalProps {
  open: boolean;
  kbId: number;
  docs: DocumentOut[];
  schemes: Scheme[];
  onClose: () => void;
}

const MODE_DESCRIPTIONS: Record<ReparseMode, string> = {
  reparse: "按方案重新生成解析结果和分块，覆盖全部人工修改",
  rechunk:
    "沿用现有解析结果（含人工修改），只按方案重新分块，覆盖分块上的人工修改；不再调用解析引擎，扫描件不会再次 OCR",
};

/** 重新解析 / 仅重新分块（原型 #m-reparse）。 */
export function ReparseModal({ open, kbId, docs, schemes, onClose }: ReparseModalProps) {
  const { message } = App.useApp();
  const reparse = useReparseDocument(kbId);
  const batch = useBatchDocuments(kbId);
  const single = docs.length === 1 ? docs[0] : undefined;

  const [mode, setMode] = useState<ReparseMode>("reparse");
  const [schemeId, setSchemeId] = useState<number | null>(null);

  useEffect(() => {
    if (open) {
      setMode("reparse");
      setSchemeId(single?.scheme?.id ?? null);
    }
  }, [open, single?.scheme?.id]);

  const schemeOptions = useMemo(
    () =>
      schemes
        .filter((scheme) => single === undefined || scheme.file_type === single.file_type)
        .map((scheme) => ({ value: scheme.id, label: scheme.name })),
    [schemes, single],
  );

  const editedDocs = docs.filter((doc) => doc.edited);
  const pending = reparse.isPending || batch.isPending;

  const handleOk = () => {
    if (single) {
      reparse.mutate(
        { docId: single.id, mode, schemeId },
        {
          onSuccess: () => {
            message.success("已开始重新入库");
            onClose();
          },
          onError: (error) => message.error(getErrorMessage(error)),
        },
      );
      return;
    }
    batch.mutate(buildBatchPayload("reparse", docs.map((doc) => doc.id), { mode, schemeId: null }), {
      onSuccess: (result) => {
        message.success(`已开始重新入库（${result.succeeded.length} 个）`);
        onClose();
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });
  };

  return (
    <Modal
      open={open}
      title={
        single ? `重新解析「${single.name}」` : `重新解析 · 已选 ${docs.length} 个文档`
      }
      okText="确定"
      cancelText="取消"
      confirmLoading={pending}
      onOk={handleOk}
      onCancel={onClose}
      destroyOnHidden
      width={600}
    >
      {editedDocs.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="以下文档有人工修改"
          description={
            <List
              size="small"
              dataSource={editedDocs}
              renderItem={(doc) => (
                <List.Item>解析结果或分块有人工修改：{doc.name}</List.Item>
              )}
            />
          }
        />
      ) : null}

      {single ? (
        <div style={{ marginBottom: 12 }}>
          <Space>
            <Typography.Text type="secondary">入库方案</Typography.Text>
            <Select
              style={{ width: 260 }}
              value={schemeId ?? undefined}
              options={schemeOptions}
              onChange={(value: number) => setSchemeId(value)}
              placeholder="保持当前方案"
            />
          </Space>
          <div>
            <Typography.Text type="secondary">
              文档当前使用的方案快照：{single.scheme_snapshot_name ?? single.scheme?.name ?? "—"}
            </Typography.Text>
          </div>
        </div>
      ) : (
        <Typography.Paragraph type="secondary">
          各文档沿用当前方案快照；批量重新解析统一选择方式，完成后在列表中查看进度。
        </Typography.Paragraph>
      )}

      <Radio.Group
        value={mode}
        onChange={(event) => setMode(event.target.value as ReparseMode)}
        style={{ width: "100%" }}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          {(Object.keys(MODE_DESCRIPTIONS) as ReparseMode[]).map((key) => (
            <Radio key={key} value={key}>
              <Typography.Text strong>
                {key === "reparse" ? "重新解析" : "仅重新分块"}
              </Typography.Text>
              <div>
                <Typography.Text type="secondary">{MODE_DESCRIPTIONS[key]}</Typography.Text>
              </div>
            </Radio>
          ))}
        </Space>
      </Radio.Group>

      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
        两种方式任何文档都可以选；没有人工修改的文档不显示上方的修改清单。批量重新解析时统一选择方式，有人工修改的文档在确认时汇总列出。重新入库完成前，旧分块仍可被检索。
      </Typography.Paragraph>
    </Modal>
  );
}

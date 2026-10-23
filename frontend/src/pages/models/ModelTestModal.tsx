import { Button, Descriptions, Modal, Space, Spin, Typography } from "antd";
import { useEffect } from "react";

import { getErrorMessage } from "../../api/errors";
import { formatRelative } from "../../lib/format";
import { useTestModel, type Model } from "./api";
import { modelTypeLabel } from "./format";

const TEST_REQUEST_HINTS: Record<string, string> = {
  chat: "发送“你好”，最多输出 16 tokens（测试时关闭深度思考）",
  embedding: "向量化一个短句，并校验返回维度与登记的向量维度一致",
  rerank: "对两个短文本重排",
};

interface ModelTestModalProps {
  open: boolean;
  model: Model | null;
  onClose: () => void;
}

/** 已保存模型的连通测试弹窗（原型 #m-modeltest）。 */
export function ModelTestModal({ open, model, onClose }: ModelTestModalProps) {
  const testModel = useTestModel();
  const { mutate: runTest, data, error, isPending, reset } = testModel;
  const modelId = model?.id ?? null;

  // 打开（或换模型）时自动测一次；关闭时清掉上次结果。
  useEffect(() => {
    if (open && modelId !== null) {
      runTest(modelId);
    } else if (!open) {
      reset();
    }
  }, [open, modelId, runTest, reset]);

  const result = data;
  const hint = model ? TEST_REQUEST_HINTS[model.model_type] : undefined;

  return (
    <Modal
      open={open}
      title={model ? `测试连通 · ${model.name}` : "测试连通"}
      width={560}
      onCancel={onClose}
      destroyOnHidden
      footer={
        <Space style={{ float: "right" }}>
          <Button onClick={onClose}>关闭</Button>
          <Button
            type="primary"
            loading={isPending}
            disabled={modelId === null}
            onClick={() => {
              if (modelId !== null) {
                runTest(modelId);
              }
            }}
          >
            重新测试
          </Button>
        </Space>
      }
    >
      {isPending ? (
        <div style={{ textAlign: "center", padding: "24px 0" }}>
          <Spin />
          <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
            正在测试连通…
          </Typography.Paragraph>
        </div>
      ) : error ? (
        <Typography.Paragraph type="danger" style={{ marginBottom: 0 }}>
          ✕ 连通失败：{getErrorMessage(error)}
        </Typography.Paragraph>
      ) : result ? (
        <>
          <Typography.Paragraph
            type={result.ok ? "success" : "danger"}
            strong
            style={{ marginBottom: 12 }}
          >
            {result.ok ? "✓ 连通" : "✕ 失败"} · 耗时 {result.latency_ms}ms
          </Typography.Paragraph>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="模型 ID">
              {model?.litellm_model ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label="测试请求">{hint ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="返回">
              {result.reply ?? (result.dims != null ? `${result.dims} 维` : "—")}
            </Descriptions.Item>
            <Descriptions.Item label="测试时间">
              {formatRelative(result.tested_at)}
            </Descriptions.Item>
          </Descriptions>
          {!result.ok ? (
            <Typography.Paragraph type="danger" style={{ marginTop: 12, marginBottom: 0 }}>
              失败原因：{result.error_message ?? result.error_code ?? "未知错误"}
            </Typography.Paragraph>
          ) : null}
        </>
      ) : (
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {model ? `类型：${modelTypeLabel(model.model_type)}` : null}
        </Typography.Paragraph>
      )}
    </Modal>
  );
}

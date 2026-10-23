import {
  Alert,
  App,
  Button,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Radio,
  Space,
  Switch,
  Typography,
} from "antd";
import { useEffect, useState } from "react";

import { getErrorMessage, isApiError } from "../../api/errors";
import {
  useCreateModel,
  useTestModelForm,
  useUpdateModel,
  type Model,
  type ModelTestIn,
  type ModelTestResult,
} from "./api";
import { buildModelPayload, type ModelFormValues } from "./format";

interface ModelFormDrawerProps {
  open: boolean;
  model: Model | null;
  onClose: () => void;
  onSaved?: (model: Model) => void;
}

function numberOrUndefined(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function successText(result: ModelTestResult): string {
  const parts = ["✓ 连通成功"];
  if (result.first_token_ms != null) {
    parts.push(`首字 ${(result.first_token_ms / 1000).toFixed(2)}s`);
  } else {
    parts.push(`耗时 ${result.latency_ms}ms`);
  }
  if (result.returned_model) {
    parts.push(`返回模型 ${result.returned_model}`);
  } else if (result.dims != null) {
    parts.push(`返回 ${result.dims} 维`);
  }
  return parts.join(" · ");
}

function failureText(result: ModelTestResult): string {
  return `✕ 连通失败：${result.error_message ?? result.error_code ?? "未知错误"}`;
}

/** 添加、编辑模型抽屉（原型 #d-newmodel、#d-editmodel）。 */
export function ModelFormDrawer({ open, model, onClose, onSaved }: ModelFormDrawerProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm<ModelFormValues>();
  const createModel = useCreateModel();
  const updateModel = useUpdateModel();
  const testForm = useTestModelForm();
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [changeKey, setChangeKey] = useState(false);

  const isEdit = model !== null;
  const locked = model?.fields_locked ?? false;
  const modelType = Form.useWatch("model_type", form) ?? "chat";

  useEffect(() => {
    if (!open) {
      return;
    }
    setFormError(null);
    setTestResult(null);
    setTestError(null);
    setChangeKey(false);
    if (model) {
      form.setFieldsValue({
        name: model.name,
        model_type: model.model_type,
        litellm_model: model.litellm_model,
        api_base: model.api_base,
        api_key: undefined,
        description: model.description,
        context_length: model.context_length,
        max_output: model.max_output,
        cap_vision: model.cap_vision,
        cap_tools: model.cap_tools,
        cap_thinking: model.cap_thinking,
        embedding_dim: model.embedding_dim,
        embedding_max_input: model.embedding_max_input,
        embedding_batch_size: model.embedding_batch_size,
        temperature: numberOrUndefined(model.default_params.temperature),
        top_p: numberOrUndefined(model.default_params.top_p),
        first_token_timeout_s: model.first_token_timeout_s,
        total_timeout_s: model.total_timeout_s,
        max_retries: model.max_retries,
        rate_qps: model.rate_qps,
        rate_concurrency: model.rate_concurrency,
      });
    } else {
      form.resetFields();
    }
  }, [open, model, form]);

  const handleFormError = (error: unknown) => {
    if (isApiError(error)) {
      if (error.code === "NAME_CONFLICT") {
        form.setFields([{ name: "name", errors: [error.message] }]);
        return;
      }
      if (error.code === "MODEL_HOST_NOT_ALLOWED") {
        form.setFields([{ name: "api_base", errors: [error.message] }]);
        return;
      }
      if (error.code === "MODEL_FIELD_LOCKED") {
        form.setFields([{ name: "litellm_model", errors: [error.message] }]);
        return;
      }
    }
    setFormError(getErrorMessage(error));
  };

  const submit = (values: ModelFormValues) => {
    setFormError(null);
    if (model) {
      updateModel.mutate(
        { id: model.id, body: buildModelPayload(values, "update") },
        {
          onSuccess: (saved) => {
            message.success("已保存");
            onSaved?.(saved);
            onClose();
          },
          onError: handleFormError,
        },
      );
      return;
    }
    createModel.mutate(buildModelPayload(values, "create"), {
      onSuccess: (created) => {
        message.success("模型已添加");
        onSaved?.(created);
        onClose();
      },
      onError: handleFormError,
    });
  };

  const handleTest = () => {
    form
      .validateFields()
      .then((values) => {
        setTestResult(null);
        setTestError(null);
        const body: ModelTestIn = {
          ...buildModelPayload(values, "create"),
          model_id: model?.id ?? null,
        };
        testForm.mutate(body, {
          onSuccess: (result) => {
            setTestResult(result);
            if (!result.ok) {
              setTestError(null);
            }
          },
          onError: (error) => {
            setTestError(getErrorMessage(error));
            setTestResult(null);
          },
        });
      })
      .catch(() => {
        // 校验不通过时 antd 已在表单上显示
      });
  };

  const testLine = testResult ? (
    <Typography.Text type={testResult.ok ? "success" : "danger"}>
      {testResult.ok ? successText(testResult) : failureText(testResult)}
    </Typography.Text>
  ) : testError ? (
    <Typography.Text type="danger">✕ 连通失败：{testError}</Typography.Text>
  ) : null;

  return (
    <Drawer
      title={model ? `编辑模型 · ${model.name}` : "添加模型"}
      width={560}
      open={open}
      onClose={onClose}
      destroyOnHidden
      footer={
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {testLine}
          <Space style={{ float: "right" }}>
            <Button onClick={onClose}>取消</Button>
            <Button loading={testForm.isPending} onClick={handleTest}>
              测试连通
            </Button>
            <Button
              type="primary"
              loading={createModel.isPending || updateModel.isPending}
              onClick={() => form.submit()}
            >
              保存
            </Button>
          </Space>
        </div>
      }
    >
      {formError ? (
        <Alert type="error" showIcon message={formError} style={{ marginBottom: 16 }} />
      ) : null}
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          model_type: "chat",
          cap_vision: false,
          cap_tools: false,
          cap_thinking: false,
          first_token_timeout_s: 30,
          total_timeout_s: 300,
          max_retries: 2,
        }}
        onFinish={submit}
      >
        <Divider orientation="left" plain>
          基本信息
        </Divider>
        <Form.Item
          name="model_type"
          label="模型类型"
          rules={[{ required: true }]}
          extra={
            isEdit
              ? "类型创建后不可修改"
              : "扫描件和图片的文字识别由入库方案中的解析引擎完成，不在这里登记模型"
          }
        >
          <Radio.Group disabled={isEdit}>
            <Radio value="chat">对话</Radio>
            <Radio value="embedding">向量化</Radio>
            <Radio value="rerank">重排</Radio>
          </Radio.Group>
        </Form.Item>
        <Form.Item
          name="name"
          label="显示名称"
          rules={[
            { required: true, message: "请输入显示名称" },
            { max: 50, message: "最多 50 字" },
          ]}
        >
          <Input placeholder="如：通义千问-Max" />
        </Form.Item>
        <Form.Item
          name="litellm_model"
          label="模型 ID（LiteLLM 格式）"
          rules={[{ required: true, message: "请输入模型 ID" }]}
          extra={locked ? "向量化模型被知识库引用后，模型 ID 和向量维度不可修改" : undefined}
        >
          <Input disabled={locked} placeholder="供应商/模型，如 dashscope/qwen-max" />
        </Form.Item>
        <Form.Item name="api_base" label="接口地址">
          <Input placeholder="留空使用供应商默认地址" />
        </Form.Item>
        {isEdit ? (
          <Form.Item
            label="API Key"
            extra="密钥不回显明文，更换会写入审计日志；不更换时测试连通使用已保存的密钥"
          >
            {changeKey ? (
              <Form.Item name="api_key" noStyle>
                <Input.Password
                  autoComplete="new-password"
                  placeholder="粘贴新的 API Key"
                />
              </Form.Item>
            ) : (
              <Space>
                <Typography.Text>
                  {model?.api_key_last4 ? `sk-••••${model.api_key_last4}` : "未设置"}
                </Typography.Text>
                <Button size="small" onClick={() => setChangeKey(true)}>
                  更换密钥
                </Button>
              </Space>
            )}
          </Form.Item>
        ) : (
          <Form.Item
            name="api_key"
            label="API Key"
            extra="加密保存，保存后不再显示明文"
          >
            <Input.Password autoComplete="new-password" placeholder="sk-..." />
          </Form.Item>
        )}
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={2} placeholder="选填" />
        </Form.Item>

        {modelType !== "rerank" ? (
          <>
            <Divider orientation="left" plain>
              能力参数
            </Divider>
            <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
              应用开发选模型时按能力过滤
            </Typography.Paragraph>
            {modelType === "embedding" ? (
              <>
                <Form.Item
                  name="embedding_dim"
                  label="向量维度"
                  rules={[{ required: true, message: "请输入向量维度" }]}
                >
                  <InputNumber min={1} max={4000} style={{ width: "100%" }} disabled={locked} />
                </Form.Item>
                <Form.Item
                  name="embedding_max_input"
                  label="最大输入长度（tokens）"
                  rules={[{ required: true, message: "请输入最大输入长度" }]}
                >
                  <InputNumber min={1} style={{ width: "100%" }} />
                </Form.Item>
                <Form.Item
                  name="embedding_batch_size"
                  label="单次请求最多条数"
                  rules={[{ required: true, message: "请输入单次请求最多条数" }]}
                >
                  <InputNumber min={1} style={{ width: "100%" }} />
                </Form.Item>
              </>
            ) : (
              <>
                <Form.Item
                  name="context_length"
                  label="上下文长度（tokens）"
                  rules={[{ required: true, message: "请输入上下文长度" }]}
                >
                  <InputNumber min={1} style={{ width: "100%" }} placeholder="如 128000" />
                </Form.Item>
                <Form.Item
                  name="max_output"
                  label="最大输出（tokens）"
                  rules={[{ required: true, message: "请输入最大输出" }]}
                >
                  <InputNumber min={1} style={{ width: "100%" }} />
                </Form.Item>
                <Form.Item name="cap_vision" label="支持视觉输入" valuePropName="checked">
                  <Switch />
                </Form.Item>
                <Form.Item name="cap_tools" label="支持函数调用" valuePropName="checked">
                  <Switch />
                </Form.Item>
                <Form.Item name="cap_thinking" label="支持深度思考开关" valuePropName="checked">
                  <Switch />
                </Form.Item>
              </>
            )}
          </>
        ) : null}

        {modelType === "chat" ? (
          <>
            <Divider orientation="left" plain>
              默认参数
            </Divider>
            <Form.Item name="temperature" label="温度">
              <InputNumber min={0} max={2} step={0.1} style={{ width: "100%" }} placeholder="选填" />
            </Form.Item>
            <Form.Item name="top_p" label="Top P">
              <InputNumber min={0} max={1} step={0.1} style={{ width: "100%" }} placeholder="选填" />
            </Form.Item>
          </>
        ) : null}

        <Divider orientation="left" plain>
          调用设置
        </Divider>
        <Form.Item
          name="first_token_timeout_s"
          label="首字超时（秒）"
          rules={[{ required: true, message: "请输入首字超时" }]}
        >
          <InputNumber min={1} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name="total_timeout_s"
          label="总超时（秒）"
          rules={[{ required: true, message: "请输入总超时" }]}
        >
          <InputNumber min={1} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name="max_retries"
          label="失败重试次数"
          extra="只在尚未输出内容时重试"
          rules={[{ required: true, message: "请输入失败重试次数" }]}
        >
          <InputNumber min={0} max={5} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item name="rate_qps" label="限流 QPS" extra="留空表示不限流">
          <InputNumber min={1} style={{ width: "100%" }} placeholder="留空表示不限流" />
        </Form.Item>
        <Form.Item name="rate_concurrency" label="最大并发">
          <InputNumber min={1} style={{ width: "100%" }} placeholder="选填" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}

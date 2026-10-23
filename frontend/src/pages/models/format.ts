import type { components } from "../../api/schema";

export type Model = components["schemas"]["ModelOut"];
export type ModelCreate = components["schemas"]["ModelCreate"];
export type ModelUpdate = components["schemas"]["ModelUpdate"];
export type ModelType = components["schemas"]["ModelType"];
export type ModelStatus = components["schemas"]["ModelStatus"];
export type RefCounts = components["schemas"]["RefCounts"];

export const MODEL_TYPE_LABELS: Record<ModelType, string> = {
  chat: "对话",
  embedding: "向量化",
  rerank: "重排",
};

export const MODEL_STATUS_LABELS: Record<ModelStatus, string> = {
  enabled: "启用",
  disabled: "已停用",
};

export function modelTypeLabel(type: string): string {
  return MODEL_TYPE_LABELS[type as ModelType] ?? type;
}

export function modelStatusLabel(status: string): string {
  return MODEL_STATUS_LABELS[status as ModelStatus] ?? status;
}

/** 上下文长度：128000 → “128K”；不足 1000 显示原值。 */
export function formatContextLength(value: number | null | undefined): string {
  if (value == null) {
    return "-";
  }
  if (value >= 1000) {
    const thousands = value / 1000;
    return `${Number.isInteger(thousands) ? thousands : thousands.toFixed(1)}K`;
  }
  return String(value);
}

/** 能力列：对话显示上下文长度和视觉/函数调用/深度思考；向量化显示维度；重排为“—”。 */
export function capabilityLabels(
  model: Pick<
    Model,
    | "model_type"
    | "context_length"
    | "cap_vision"
    | "cap_tools"
    | "cap_thinking"
    | "embedding_dim"
  >,
): string[] {
  if (model.model_type === "rerank") {
    return ["—"];
  }
  if (model.model_type === "embedding") {
    return model.embedding_dim != null ? [`${model.embedding_dim} 维`] : ["—"];
  }
  const labels: string[] = [];
  if (model.context_length != null) {
    labels.push(`上下文 ${formatContextLength(model.context_length)}`);
  }
  if (model.cap_vision) {
    labels.push("视觉");
  }
  if (model.cap_tools) {
    labels.push("函数调用");
  }
  if (model.cap_thinking) {
    labels.push("深度思考");
  }
  return labels.length > 0 ? labels : ["—"];
}

/** 被引用列：应用 12、知识库 8、入库方案 3；都为 0 时显示 0。 */
export function refCountText(refCounts: RefCounts): string {
  const parts: string[] = [];
  if (refCounts.app > 0) {
    parts.push(`应用 ${refCounts.app}`);
  }
  if (refCounts.kb > 0) {
    parts.push(`知识库 ${refCounts.kb}`);
  }
  if (refCounts.scheme > 0) {
    parts.push(`入库方案 ${refCounts.scheme}`);
  }
  return parts.length > 0 ? parts.join("、") : "0";
}

/** 抽屉表单的值。按类型只提交该类型需要的字段（见 buildModelPayload）。 */
export interface ModelFormValues {
  name: string;
  model_type: ModelType;
  litellm_model: string;
  api_base?: string | null;
  api_key?: string | null;
  description?: string | null;
  context_length?: number | null;
  max_output?: number | null;
  cap_vision?: boolean;
  cap_tools?: boolean;
  cap_thinking?: boolean;
  embedding_dim?: number | null;
  embedding_max_input?: number | null;
  embedding_batch_size?: number | null;
  temperature?: number | null;
  top_p?: number | null;
  first_token_timeout_s: number;
  total_timeout_s: number;
  max_retries: number;
  rate_qps?: number | null;
  rate_concurrency?: number | null;
}

function normalized(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function defaultParams(values: ModelFormValues): Record<string, number> | undefined {
  const params: Record<string, number> = {};
  if (values.temperature != null) {
    params.temperature = values.temperature;
  }
  if (values.top_p != null) {
    params.top_p = values.top_p;
  }
  return Object.keys(params).length > 0 ? params : undefined;
}

/**
 * 把抽屉表单转成请求体：对话只带对话能力参数和默认参数，向量化只带维度三项，
 * 重排两者都不带；编辑时不带 model_type（后端返回 422 MODEL_TYPE_IMMUTABLE）。
 */
export function buildModelPayload(
  values: ModelFormValues,
  mode: "create",
): ModelCreate;
export function buildModelPayload(
  values: ModelFormValues,
  mode: "update",
): ModelUpdate;
export function buildModelPayload(
  values: ModelFormValues,
  mode: "create" | "update",
): ModelCreate | ModelUpdate {
  const payload: ModelCreate = {
    name: values.name.trim(),
    model_type: values.model_type,
    litellm_model: values.litellm_model.trim(),
    api_base: normalized(values.api_base),
    api_key: normalized(values.api_key),
    description: normalized(values.description),
    cap_vision: values.cap_vision ?? false,
    cap_tools: values.cap_tools ?? false,
    cap_thinking: values.cap_thinking ?? false,
    first_token_timeout_s: values.first_token_timeout_s,
    total_timeout_s: values.total_timeout_s,
    max_retries: values.max_retries,
    rate_qps: values.rate_qps ?? null,
    rate_concurrency: values.rate_concurrency ?? null,
  };
  if (values.model_type === "chat") {
    payload.context_length = values.context_length ?? null;
    payload.max_output = values.max_output ?? null;
    const params = defaultParams(values);
    if (params) {
      payload.default_params = params;
    }
  } else if (values.model_type === "embedding") {
    payload.embedding_dim = values.embedding_dim ?? null;
    payload.embedding_max_input = values.embedding_max_input ?? null;
    payload.embedding_batch_size = values.embedding_batch_size ?? null;
  }
  if (mode === "update") {
    const rest: ModelUpdate = { ...payload };
    delete rest.model_type;
    return rest;
  }
  return payload;
}

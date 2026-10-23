import { describe, expect, it } from "vitest";

import type { Model, ModelFormValues } from "./format";
import {
  buildModelPayload,
  capabilityLabels,
  formatContextLength,
  refCountText,
} from "./format";

function model(partial: Partial<Model> & Pick<Model, "model_type">): Model {
  return {
    id: 1,
    name: "模型",
    litellm_model: "dashscope/qwen-max",
    api_base: null,
    api_key_last4: null,
    description: null,
    context_length: null,
    max_output: null,
    cap_vision: false,
    cap_tools: false,
    cap_thinking: false,
    embedding_dim: null,
    embedding_max_input: null,
    embedding_batch_size: null,
    default_params: {},
    first_token_timeout_s: 30,
    total_timeout_s: 300,
    max_retries: 2,
    rate_qps: null,
    rate_concurrency: null,
    status: "enabled",
    ref_counts: { app: 0, kb: 0, scheme: 0 },
    fields_locked: false,
    last_test_at: null,
    last_test_ok: null,
    last_test_message: null,
    created_at: "2025-01-01T00:00:00+08:00",
    updated_at: "2025-01-01T00:00:00+08:00",
    ...partial,
  };
}

function baseValues(partial: Partial<ModelFormValues>): ModelFormValues {
  return {
    name: "模型",
    model_type: "chat",
    litellm_model: "dashscope/qwen-max",
    first_token_timeout_s: 30,
    total_timeout_s: 300,
    max_retries: 2,
    ...partial,
  };
}

describe("formatContextLength", () => {
  it("128000 显示为 128K", () => {
    expect(formatContextLength(128000)).toBe("128K");
  });

  it("非整千显示一位小数", () => {
    expect(formatContextLength(8192)).toBe("8.2K");
  });

  it("空值显示 -", () => {
    expect(formatContextLength(null)).toBe("-");
  });
});

describe("capabilityLabels", () => {
  it("对话模型显示上下文和开关", () => {
    const labels = capabilityLabels(
      model({
        model_type: "chat",
        context_length: 128000,
        cap_vision: true,
        cap_tools: true,
        cap_thinking: true,
      }),
    );
    expect(labels).toEqual(["上下文 128K", "视觉", "函数调用", "深度思考"]);
  });

  it("向量化模型显示维度", () => {
    expect(capabilityLabels(model({ model_type: "embedding", embedding_dim: 1024 }))).toEqual([
      "1024 维",
    ]);
  });

  it("重排模型显示 —", () => {
    expect(capabilityLabels(model({ model_type: "rerank" }))).toEqual(["—"]);
  });
});

describe("refCountText", () => {
  it("按类型顿号分隔", () => {
    expect(refCountText({ app: 12, kb: 8, scheme: 0 })).toBe("应用 12、知识库 8");
  });

  it("都为 0 时显示 0", () => {
    expect(refCountText({ app: 0, kb: 0, scheme: 0 })).toBe("0");
  });
});

describe("buildModelPayload", () => {
  it("对话模型只带对话能力参数和默认参数", () => {
    const payload = buildModelPayload(
      baseValues({
        model_type: "chat",
        context_length: 128000,
        max_output: 4096,
        cap_tools: true,
        temperature: 0.7,
        top_p: 0.9,
        embedding_dim: 1024,
      }),
      "create",
    );
    expect(payload.context_length).toBe(128000);
    expect(payload.max_output).toBe(4096);
    expect(payload.cap_tools).toBe(true);
    expect(payload.default_params).toEqual({ temperature: 0.7, top_p: 0.9 });
    expect(payload).not.toHaveProperty("embedding_dim");
  });

  it("向量化模型只带维度三项，不带对话参数和默认参数", () => {
    const payload = buildModelPayload(
      baseValues({
        model_type: "embedding",
        context_length: 128000,
        max_output: 4096,
        temperature: 0.7,
        embedding_dim: 1024,
        embedding_max_input: 8192,
        embedding_batch_size: 10,
      }),
      "create",
    );
    expect(payload.embedding_dim).toBe(1024);
    expect(payload.embedding_max_input).toBe(8192);
    expect(payload.embedding_batch_size).toBe(10);
    expect(payload).not.toHaveProperty("context_length");
    expect(payload).not.toHaveProperty("max_output");
    expect(payload).not.toHaveProperty("default_params");
  });

  it("重排模型不带能力参数和默认参数", () => {
    const payload = buildModelPayload(
      baseValues({
        model_type: "rerank",
        context_length: 128000,
        temperature: 0.7,
        embedding_dim: 1024,
      }),
      "create",
    );
    expect(payload).not.toHaveProperty("context_length");
    expect(payload).not.toHaveProperty("embedding_dim");
    expect(payload).not.toHaveProperty("default_params");
  });

  it("编辑时不带 model_type", () => {
    const payload = buildModelPayload(
      baseValues({ model_type: "chat", context_length: 128000, max_output: 4096 }),
      "update",
    );
    expect(payload).not.toHaveProperty("model_type");
    expect(payload.name).toBe("模型");
  });

  it("空字符串的接口地址和密钥转成 null", () => {
    const payload = buildModelPayload(
      baseValues({ api_base: "  ", api_key: "" }),
      "create",
    );
    expect(payload.api_base).toBeNull();
    expect(payload.api_key).toBeNull();
  });
});

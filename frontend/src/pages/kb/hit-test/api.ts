import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, unwrap } from "../../../api/client";
import type { components } from "../../../api/schema";

export type RetrievalConfig = components["schemas"]["RetrievalConfig"];
export type RetrievalMode = components["schemas"]["RetrievalMode"];
export type RerankConfig = components["schemas"]["RerankConfig"];
export type HitItem = components["schemas"]["HitItem"];
export type HitSteps = components["schemas"]["HitSteps"];
export type HitTestOut = components["schemas"]["HitTestOut"];
export type HitTimings = components["schemas"]["HitTimings"];
export type HitTestIn = components["schemas"]["HitTestIn"];
export type MetadataGroup = components["schemas"]["MetadataGroup"];
export type MetadataFilter = components["schemas"]["MetadataFilter"];
export type MetadataOperator = components["schemas"]["MetadataOperator"];
export type Model = components["schemas"]["ModelOut"];

/**
 * 表单里的检索配置：第三期不出现 `adjacent_n`（相邻分块扩展第五期做）。
 * 提交时也不带该字段，后端按默认值 0 处理。
 */
export type RetrievalConfigInput = Omit<RetrievalConfig, "adjacent_n">;

export interface HitTestInInput {
  question: string;
  config: RetrievalConfigInput;
  folder_id: number | null;
  metadata: MetadataGroup | null;
}

export type StepKey = "vector" | "fulltext" | "fusion" | "rerank";

export const STEP_KEYS: StepKey[] = ["vector", "fulltext", "fusion", "rerank"];

export const STEP_LABELS: Record<StepKey, string> = {
  vector: "向量召回",
  fulltext: "全文召回",
  fusion: "融合",
  rerank: "重排",
};

export const OPERATOR_LABELS: Record<MetadataOperator, string> = {
  eq: "等于",
  ne: "不等于",
  contains: "包含",
  gt: "大于",
  gte: "大于等于",
  lt: "小于",
  lte: "小于等于",
  in: "属于",
};

/** 元数据条件：字段、操作符、值（值统一按字符串提交，后端按字段类型转换）。 */
export interface MetadataCondition {
  field_id: number | undefined;
  operator: MetadataOperator;
  value: string;
}

/** 按字段类型给出可用的操作符。 */
export function operatorsForType(type: string): MetadataOperator[] {
  switch (type) {
    case "number":
    case "date":
      return ["eq", "ne", "gt", "gte", "lt", "lte"];
    case "select":
      return ["eq", "ne"];
    default:
      return ["eq", "ne", "contains"];
  }
}

/** 去掉 `adjacent_n` 并把可选的 `rerank` 补成完整结构，供表单使用。 */
export function defaultRetrievalConfig(config: RetrievalConfig): RetrievalConfigInput {
  return {
    mode: config.mode,
    vector_weight: config.vector_weight,
    fulltext_weight: config.fulltext_weight,
    top_k: config.top_k,
    score_threshold: config.score_threshold,
    rerank: config.rerank ?? { enabled: false, model_id: null, top_n: 10 },
  };
}

/** 表单配置转提交对象：不带 `adjacent_n`（契约里是可选默认值）。 */
export function buildConfigPayload(config: RetrievalConfigInput): RetrievalConfig {
  return config as RetrievalConfig;
}

/** 混合检索时两路权重之和必须为 1，否则返回校验提示；其他方式返回 null。 */
export function hybridWeightError(
  config: Pick<RetrievalConfigInput, "mode" | "vector_weight" | "fulltext_weight">,
): string | null {
  if (config.mode !== "hybrid") {
    return null;
  }
  if (Math.abs(config.vector_weight + config.fulltext_weight - 1) > 0.001) {
    return "混合检索时向量权重与全文权重之和必须为 1";
  }
  return null;
}

/** 拼装命中测试请求；文件夹“全部”下发 null。 */
export function buildHitTestPayload(
  question: string,
  config: RetrievalConfigInput,
  folderId: number | "all",
  metadata: MetadataGroup | null,
): HitTestInInput {
  return {
    question,
    config,
    folder_id: folderId === "all" ? null : folderId,
    metadata,
  };
}

/** 一层“且 / 或”多条件（不嵌套）；没有有效条件时返回 null。 */
export function buildMetadataGroup(
  conditions: MetadataCondition[],
  op: "and" | "or",
): MetadataGroup | null {
  const valid = conditions.filter(
    (condition): condition is MetadataCondition & { field_id: number } =>
      condition.field_id != null && condition.value !== "",
  );
  if (valid.length === 0) {
    return null;
  }
  return {
    op,
    conditions: valid.map((condition) => ({
      field_id: condition.field_id,
      operator: condition.operator,
      value: condition.value,
    })),
  };
}

export function formatScore(score: number): string {
  return score.toFixed(2);
}

/** 耗时行：向量 X ms · 全文 Y ms · 重排 Z ms（原型 #v-kbhit）。 */
export function formatTimings(timings: HitTimings): string {
  return `向量 ${Math.round(timings.vector_ms)}ms · 全文 ${Math.round(timings.fulltext_ms)}ms · 重排 ${Math.round(timings.rerank_ms)}ms`;
}

export interface StepItem {
  key: StepKey;
  label: string;
  count: number;
}

export function stepItems(steps: HitSteps): StepItem[] {
  return STEP_KEYS.map((key) => ({
    key,
    label: STEP_LABELS[key],
    count: steps[key].length,
  }));
}

export function stepResults(steps: HitSteps, key: StepKey): HitItem[] {
  return steps[key];
}

/** 当前步骤结果列表的说明文案。 */
export function currentStepText(key: StepKey, count: number): string {
  switch (key) {
    case "vector":
      return `向量召回的 ${count} 条`;
    case "fulltext":
      return `全文召回的 ${count} 条`;
    case "fusion":
      return `融合后的 ${count} 条`;
    case "rerank":
      return `重排后保留的 ${count} 条`;
  }
}

export function useRetrievalConfig(id: number | null) {
  return useQuery({
    queryKey: ["kb-retrieval-config", id],
    enabled: id !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/kbs/{id}/retrieval-config", {
          params: { path: { id: id as number } },
        }),
      ),
  });
}

export function useUpdateRetrievalConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, config }: { id: number; config: RetrievalConfigInput }) =>
      unwrap(
        await client.PUT("/api/v1/kbs/{id}/retrieval-config", {
          params: { path: { id } },
          body: buildConfigPayload(config),
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["kb-retrieval-config", variables.id] });
    },
  });
}

/** 命中测试：问题 + 临时检索配置，不保存记录。 */
export function useHitTest(id: number | null) {
  return useMutation({
    mutationFn: async (body: HitTestInInput) =>
      unwrap(
        await client.POST("/api/v1/kbs/{id}/hit-test", {
          params: { path: { id: id as number } },
          body: body as HitTestIn,
        }),
      ),
    meta: { skipErrorToast: true },
  });
}

/** 已启用的重排模型（重排模型选择器）。 */
export function useRerankModels() {
  return useQuery({
    queryKey: ["models", "rerank", "enabled"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/models", {
          params: { query: { type: "rerank", status: "enabled", page_size: 100 } },
        }),
      ),
    staleTime: 60_000,
  });
}

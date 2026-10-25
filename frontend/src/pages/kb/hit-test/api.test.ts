import { describe, expect, it } from "vitest";

import {
  buildConfigPayload,
  buildHitTestPayload,
  buildMetadataGroup,
  currentStepText,
  defaultRetrievalConfig,
  formatScore,
  formatTimings,
  hybridWeightError,
  operatorsForType,
  stepItems,
  type HitSteps,
  type HitTestOut,
  type RetrievalConfig,
} from "./api";

const CONFIG: RetrievalConfig = {
  mode: "hybrid",
  vector_weight: 0.7,
  fulltext_weight: 0.3,
  top_k: 20,
  score_threshold: 0.35,
  rerank: { enabled: true, model_id: 9, top_n: 5 },
  adjacent_n: 2,
};

function hitSteps(): HitSteps {
  const item = {
    chunk_id: 1,
    doc_id: 1,
    doc_name: "a.pdf",
    content: "内容",
    title_path: "第一章",
    kind: "text",
    positions: [],
    score: 0.9,
    scores: { vector: 0.9 },
    page_no: 1,
    edited: false,
  };
  return {
    vector: [item],
    fulltext: [item, item],
    fusion: [item],
    rerank: [],
  };
}

describe("defaultRetrievalConfig", () => {
  it("去掉 adjacent_n 并补全 rerank", () => {
    const result = defaultRetrievalConfig(CONFIG);
    expect(result).not.toHaveProperty("adjacent_n");
    expect(result).toMatchObject({
      mode: "hybrid",
      vector_weight: 0.7,
      fulltext_weight: 0.3,
      top_k: 20,
      score_threshold: 0.35,
      rerank: { enabled: true, model_id: 9, top_n: 5 },
    });
  });

  it("缺少 rerank 时补成关闭状态", () => {
    const result = defaultRetrievalConfig({ ...CONFIG, rerank: undefined });
    expect(result.rerank).toEqual({ enabled: false, model_id: null, top_n: 10 });
  });
});

describe("hybridWeightError", () => {
  it("非混合方式不校验", () => {
    expect(hybridWeightError({ mode: "vector", vector_weight: 0.7, fulltext_weight: 0.7 })).toBeNull();
  });

  it("混合方式权重和为 1 通过", () => {
    expect(hybridWeightError({ mode: "hybrid", vector_weight: 0.7, fulltext_weight: 0.3 })).toBeNull();
  });

  it("混合方式权重和不为 1 给出提示", () => {
    expect(
      hybridWeightError({ mode: "hybrid", vector_weight: 0.7, fulltext_weight: 0.5 }),
    ).toContain("必须为 1");
  });
});

describe("buildConfigPayload", () => {
  it("提交对象里没有 adjacent_n", () => {
    const payload = buildConfigPayload(defaultRetrievalConfig(CONFIG));
    expect(payload).not.toHaveProperty("adjacent_n");
    expect(payload.mode).toBe("hybrid");
    expect(payload.rerank).toEqual({ enabled: true, model_id: 9, top_n: 5 });
  });
});

describe("buildHitTestPayload", () => {
  it("文件夹“全部”下发 null", () => {
    const body = buildHitTestPayload("问题", defaultRetrievalConfig(CONFIG), "all", null);
    expect(body.folder_id).toBeNull();
    expect(body.question).toBe("问题");
    expect(body.config).not.toHaveProperty("adjacent_n");
  });

  it("指定文件夹下发 id", () => {
    const body = buildHitTestPayload("问题", defaultRetrievalConfig(CONFIG), 3, null);
    expect(body.folder_id).toBe(3);
  });
});

describe("buildMetadataGroup", () => {
  it("没有有效条件返回 null", () => {
    expect(buildMetadataGroup([{ field_id: undefined, operator: "eq", value: "" }], "and")).toBeNull();
    expect(buildMetadataGroup([], "or")).toBeNull();
  });

  it("一层且/或多条件", () => {
    const group = buildMetadataGroup(
      [
        { field_id: 1, operator: "eq", value: "财务" },
        { field_id: 2, operator: "gt", value: "3" },
        { field_id: 3, operator: "eq", value: "" },
      ],
      "or",
    );
    expect(group).toEqual({
      op: "or",
      conditions: [
        { field_id: 1, operator: "eq", value: "财务" },
        { field_id: 2, operator: "gt", value: "3" },
      ],
    });
  });
});

describe("operatorsForType", () => {
  it("按字段类型给出操作符", () => {
    expect(operatorsForType("text")).toEqual(["eq", "ne", "contains"]);
    expect(operatorsForType("number")).toContain("gt");
    expect(operatorsForType("select")).toEqual(["eq", "ne"]);
  });
});

describe("格式化", () => {
  it("分数保留两位", () => {
    expect(formatScore(0.934)).toBe("0.93");
    expect(formatScore(1)).toBe("1.00");
  });

  it("耗时行照原型", () => {
    expect(
      formatTimings({ vector_ms: 46, fulltext_ms: 12, fusion_ms: 3, rerank_ms: 210 }),
    ).toBe("向量 46ms · 全文 12ms · 重排 210ms");
  });
});

describe("步骤", () => {
  it("四个步骤带结果数", () => {
    expect(stepItems(hitSteps())).toEqual([
      { key: "vector", label: "向量召回", count: 1 },
      { key: "fulltext", label: "全文召回", count: 2 },
      { key: "fusion", label: "融合", count: 1 },
      { key: "rerank", label: "重排", count: 0 },
    ]);
  });

  it("当前步骤说明文案", () => {
    expect(currentStepText("rerank", 5)).toBe("重排后保留的 5 条");
    expect(currentStepText("vector", 20)).toBe("向量召回的 20 条");
  });
});

describe("HitTestOut 结构", () => {
  it("final 与 steps 共存", () => {
    const out = {
      steps: hitSteps(),
      final: [],
      timings: { vector_ms: 1, fulltext_ms: 2, fusion_ms: 3, rerank_ms: 4 },
    } satisfies HitTestOut;
    expect(out.steps.vector).toHaveLength(1);
  });
});

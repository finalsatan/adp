import { describe, expect, it } from "vitest";

import { citedNumbers, parseCitations, toCitationMarkdown, type Citation } from "./citation";

function citation(n: number, overrides: Partial<Citation> = {}): Citation {
  return {
    n,
    type: "doc",
    kb_id: 101,
    kb_name: "员工制度库",
    score: 0.83,
    answer: null,
    chunk_ids: [5512],
    content: "第十八条……",
    direct: null,
    doc_id: 3021,
    doc_name: "员工考勤管理制度.docx",
    edited: false,
    file_type: "word",
    generation: 2,
    positions: null,
    qa_id: null,
    question: null,
    ...overrides,
  };
}

describe("parseCitations", () => {
  it("识别 [1] 与 [1][2]", () => {
    const citations = [citation(1), citation(2)];
    expect(parseCitations("答案[1]，补充[1][2]。", citations)).toEqual([
      { type: "text", text: "答案" },
      { type: "citation", n: 1, citation: citations[0] },
      { type: "text", text: "，补充" },
      { type: "citation", n: 1, citation: citations[0] },
      { type: "citation", n: 2, citation: citations[1] },
      { type: "text", text: "。" },
    ]);
  });

  it("识别全角 【1】", () => {
    const citations = [citation(1)];
    expect(parseCitations("结论【1】。", citations)).toEqual([
      { type: "text", text: "结论" },
      { type: "citation", n: 1, citation: citations[0] },
      { type: "text", text: "。" },
    ]);
  });

  it("跨片段拼接后的角标正确", () => {
    const citations = [citation(1), citation(2)];
    const streamed = "金额不超过 500 元[1" + "][2]。";
    expect(parseCitations(streamed, citations)).toEqual([
      { type: "text", text: "金额不超过 500 元" },
      { type: "citation", n: 1, citation: citations[0] },
      { type: "citation", n: 2, citation: citations[1] },
      { type: "text", text: "。" },
    ]);
  });

  it("超出引用范围的编号按普通文字保留", () => {
    const citations = [citation(1)];
    expect(parseCitations("编号[9]不渲染。", citations)).toEqual([
      { type: "text", text: "编号[9]不渲染。" },
    ]);
  });
});

describe("citedNumbers", () => {
  it("只含出现过的编号", () => {
    expect(citedNumbers("a[1]b[2]c【1】d[9]")).toEqual([1, 2, 9]);
  });
});

describe("toCitationMarkdown", () => {
  it("把范围内的角标换成锚点链接，超范围原样保留", () => {
    const citations = [citation(1), citation(2)];
    expect(toCitationMarkdown("金额[1]，补充【2】和[9]。", citations)).toBe(
      "金额[1](#cite-1)，补充[2](#cite-2)和[9]。",
    );
  });
});

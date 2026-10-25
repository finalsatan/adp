import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ParseResultOut } from "./api";
import { ParseResultView } from "./ParseResultView";

const RESULT = {
  kind: "original",
  markdown:
    "# 第三章 住宿标准\n\n**第十条** 员工出差住宿费实行限额标准。\n\n**第十一条** 一类城市：普通员工每人每晚不超过 500 元。\n\n**第十二条** 二类城市：不超过 400 元。\n",
  elements: [
    { ref: "#/texts/11", type: "text", text: "第十条 员工出差住宿费实行限额标准。" },
    {
      ref: "#/texts/12",
      type: "text",
      text: "第十一条 一类城市：普通员工每人每晚不超过 500 元。",
    },
    { ref: "#/texts/13", type: "text", text: "第十二条 二类城市：不超过 400 元。" },
  ],
  engine: "docling",
  engine_version: "1.0",
  pages: 12,
  ocr_pages: 0,
  edited_at: null,
  edited_by: null,
} as unknown as ParseResultOut;

function paragraphWith(text: string): HTMLElement {
  const blocks = screen.getByTestId("parse-result").querySelectorAll("p");
  for (const block of blocks) {
    if ((block.textContent ?? "").includes(text)) {
      return block as HTMLElement;
    }
  }
  throw new Error(`没有找到包含「${text}」的段落`);
}

describe("ParseResultView", () => {
  it("按元素引用高亮对应 Markdown 段落", () => {
    render(<ParseResultView result={RESULT} highlightRefs={["#/texts/12"]} />);

    expect(paragraphWith("第十一条").style.backgroundColor).not.toBe("");
    expect(paragraphWith("第十条").style.backgroundColor).toBe("");
    expect(paragraphWith("第十二条").style.backgroundColor).toBe("");
  });

  it("没有元素引用时不加高亮", () => {
    render(<ParseResultView result={RESULT} highlightRefs={[]} />);
    expect(paragraphWith("第十一条").style.backgroundColor).toBe("");
  });

  it("人工修改版显示修改提示", () => {
    render(
      <ParseResultView
        result={
          {
            ...RESULT,
            kind: "edited",
            edited_at: "2026-10-21T09:40:00+08:00",
            edited_by: { id: 2, name: "lisi" },
          } as unknown as ParseResultOut
        }
      />,
    );
    expect(screen.getByText(/解析结果已被lisi于 10-21 09:40 修改/)).toBeInTheDocument();
  });
});

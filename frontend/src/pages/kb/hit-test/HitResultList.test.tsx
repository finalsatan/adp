import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { HitResultList } from "./HitResultList";
import type { HitItem } from "./api";

function hit(overrides: Partial<HitItem>): HitItem {
  return {
    chunk_id: 1,
    doc_id: 10,
    doc_name: "差旅管理办法.pdf",
    content: "第十一条 一类城市……",
    title_path: "差旅管理办法 > 第三章 住宿标准",
    kind: "text",
    positions: [],
    score: 0.93,
    scores: { vector: 0.93 },
    page_no: 3,
    edited: false,
    ...overrides,
  };
}

function renderList(items: HitItem[]) {
  return render(
    <MemoryRouter>
      <HitResultList kbId={1} items={items} emptyText="该步骤没有结果" />
    </MemoryRouter>,
  );
}

describe("HitResultList", () => {
  it("展示序号、分数、文档、页码、标题路径与原文位置链接", () => {
    renderList([hit({})]);

    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("0.93")).toBeInTheDocument();
    expect(screen.getByText("差旅管理办法.pdf")).toBeInTheDocument();
    expect(screen.getByText("第 3 页")).toBeInTheDocument();
    expect(screen.getByText("差旅管理办法 > 第三章 住宿标准")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "原文位置" })).toHaveAttribute(
      "href",
      "/kb/1/documents/10?chunk=1",
    );
  });

  it("已人工修改与表格标记", () => {
    renderList([hit({ edited: true, kind: "table", content: "| 一类 | 500 |" })]);

    expect(screen.getByText("已人工修改")).toBeInTheDocument();
    expect(screen.getByText("表格")).toBeInTheDocument();
  });

  it("没有结果时显示空文案", () => {
    renderList([]);
    expect(screen.getByText("该步骤没有结果")).toBeInTheDocument();
  });
});

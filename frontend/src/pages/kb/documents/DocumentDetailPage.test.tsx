import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useKb, type Kb } from "../api";
import {
  useDocument,
  useDocumentParse,
  useSchemes,
  type DocumentOut,
  type ParseResultOut,
} from "./api";
import DocumentDetailPage from "./DocumentDetailPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, useKb: vi.fn() };
});

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useDocument: vi.fn(),
    useDocumentParse: vi.fn(),
    useSchemes: vi.fn(),
  };
});

vi.mock("./PdfPreview", () => ({
  PdfPreview: (props: {
    fileType: string;
    positions: unknown[];
    targetPage: number | null;
  }) => (
    <div
      data-testid="pdf-preview"
      data-file-type={props.fileType}
      data-positions={props.positions.length}
      data-target={String(props.targetPage)}
    />
  ),
}));

vi.mock("./ReparseModal", () => ({ ReparseModal: () => null }));

const mockedUseKb = vi.mocked(useKb);
const mockedDocument = vi.mocked(useDocument);
const mockedParse = vi.mocked(useDocumentParse);
const mockedSchemes = vi.mocked(useSchemes);

const KB = {
  id: 1,
  name: "员工制度库",
  kind: "doc",
  doc_count: 3,
  chunk_count: 100,
  embedding_model: { id: 1, name: "text-embedding-v3" },
  visibility: "org",
  owner: { id: 1, name: "张三" },
  editable: true,
  manageable: true,
} as unknown as Kb;

const DOC = {
  id: 10,
  kb_id: 1,
  name: "差旅管理办法.pdf",
  file_type: "pdf",
  ext: "pdf",
  size: 1,
  status: "done",
  progress: 0,
  queue_position: null,
  error_message: null,
  page_count: 12,
  chunk_count: 86,
  metadata: {},
  enabled: true,
  edited: true,
  scheme: { id: 1, name: "PDF · 通用", file_type: "pdf", builtin: true },
  scheme_snapshot_name: "PDF · 通用",
} as unknown as DocumentOut;

const PARSE = {
  kind: "original",
  markdown: "**第十一条** 一类城市：普通员工每人每晚不超过 500 元。",
  elements: [
    {
      ref: "#/texts/12",
      type: "text",
      text: "第十一条 一类城市：普通员工每人每晚不超过 500 元。",
    },
  ],
  engine: "docling",
  engine_version: "1.0",
  pages: 12,
  ocr_pages: 0,
  edited_at: null,
  edited_by: null,
} as unknown as ParseResultOut;

beforeEach(() => {
  mockedUseKb.mockReturnValue({ data: KB } as unknown as ReturnType<typeof useKb>);
  mockedDocument.mockReturnValue({ data: DOC } as unknown as ReturnType<typeof useDocument>);
  mockedParse.mockReturnValue({ data: PARSE } as unknown as ReturnType<typeof useDocumentParse>);
  mockedSchemes.mockReturnValue({ data: { items: [], total: 0 } } as unknown as ReturnType<
    typeof useSchemes
  >);
});

afterEach(() => vi.clearAllMocks());

interface Entry {
  search?: string;
  state?: unknown;
}

function renderPage({ search = "", state }: Entry = {}) {
  return render(
    <MemoryRouter
      initialEntries={[{ pathname: "/kb/1/documents/10", search, state }]}
    >
      <Routes>
        <Route path="/kb/:id/documents/:docId" element={<DocumentDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DocumentDetailPage", () => {
  it("显示文件名、状态、页数分块数、入库方案与人工修改标记", () => {
    renderPage();

    expect(screen.getByText("差旅管理办法.pdf")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("12 页")).toBeInTheDocument();
    expect(screen.getByText("86 个分块")).toBeInTheDocument();
    expect(screen.getByText(/入库方案：PDF · 通用/)).toBeInTheDocument();
    expect(screen.getByText("已人工修改")).toBeInTheDocument();
    expect(screen.getByText("解析结果对照")).toBeInTheDocument();
  });

  it("下载原文件链接带 download=1", () => {
    renderPage();
    const link = screen.getByRole("link", { name: "下载原文件" });
    expect(link.getAttribute("href")).toBe(
      "/api/v1/documents/10/file?variant=original&download=1",
    );
  });

  it("用路由 state 的位置数据定位：跳到对应页并显示清除高亮", () => {
    renderPage({
      search: "?chunk=5",
      state: { page: 3, positions: [{ page: 3, bbox: [0, 0, 1, 1], origin: "BOTTOMLEFT" }] },
    });

    expect(screen.getByTestId("pdf-preview").getAttribute("data-target")).toBe("3");
    expect(screen.getByTestId("pdf-preview").getAttribute("data-positions")).toBe("1");

    fireEvent.click(screen.getByRole("button", { name: "清除高亮" }));
    expect(screen.getByTestId("pdf-preview").getAttribute("data-positions")).toBe("0");
    expect(screen.queryByRole("button", { name: "清除高亮" })).toBeNull();
  });

  it("定位参数 page 在没有 state 时生效", () => {
    renderPage({ search: "?page=5" });
    expect(screen.getByTestId("pdf-preview").getAttribute("data-target")).toBe("5");
  });

  it("Word 文档按元素引用在解析结果视图高亮段落", () => {
    mockedDocument.mockReturnValue({
      data: { ...DOC, file_type: "word", name: "制度.docx" },
    } as unknown as ReturnType<typeof useDocument>);

    renderPage({ state: { positions: [{ ref: "#/texts/12" }] } });

    const paragraph = screen.getByText(/一类城市/).closest("p") as HTMLElement;
    expect(paragraph.style.backgroundColor).not.toBe("");
  });
});

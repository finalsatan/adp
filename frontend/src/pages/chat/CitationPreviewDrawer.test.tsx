import { App as AntApp } from "antd";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CitationPreviewDrawer } from "./CitationPreviewDrawer";
import { fetchCitationPreview, type Citation, type CitationPreview } from "./api";

vi.mock("react-pdf", async () => {
  const React = await import("react");
  return {
    Document: ({
      file,
      children,
    }: {
      file: string;
      children?: React.ReactNode;
    }) =>
      React.createElement("div", { "data-testid": "pdf-document", "data-file": file }, children),
    Page: ({ children }: { children?: React.ReactNode }) =>
      React.createElement("div", { "data-testid": "pdf-page" }, children),
    pdfjs: { GlobalWorkerOptions: {} },
  };
});

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchCitationPreview: vi.fn() };
});

const mockedFetch = vi.mocked(fetchCitationPreview);

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    n: 1,
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
    positions: [],
    qa_id: null,
    question: null,
    ...overrides,
  };
}

function preview(overrides: Partial<CitationPreview> = {}): CitationPreview {
  return {
    render: "markdown",
    file_type: "word",
    kb_id: 101,
    kb_name: "员工制度库",
    doc_id: 3021,
    doc_name: "员工考勤管理制度.docx",
    page_no: null,
    pages: [],
    positions: [],
    content: "## 员工考勤管理制度\n\n第十八条 新入职员工……",
    elements: [
      { ref: "#/texts/12", type: "text", text: "第十八条 新入职员工……", highlight: true },
    ],
    generation: 2,
    edited: false,
    stale: false,
    note: null,
    download_allowed: true,
    ...overrides,
  };
}

function renderDrawer(props: Partial<Parameters<typeof CitationPreviewDrawer>[0]> = {}) {
  return render(
    <AntApp>
      <CitationPreviewDrawer
        open
        answerId={1}
        citation={citation()}
        allowDownload={false}
        onClose={() => {}}
        {...props}
      />
    </AntApp>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("CitationPreviewDrawer", () => {
  it("PDF 引用渲染只含被引用页的 PDF", async () => {
    mockedFetch.mockResolvedValue({ kind: "pdf", url: "/api/v1/answers/1/citations/1/preview" });
    renderDrawer({
      citation: citation({ file_type: "pdf", doc_name: "员工手册.pdf" }),
    });

    expect(await screen.findByTestId("citation-pdf")).toBeInTheDocument();
    expect(screen.getByTestId("pdf-document").getAttribute("data-file")).toBe(
      "/api/v1/answers/1/citations/1/preview",
    );
  });

  it("Word 引用按解析结果渲染并高亮被引用元素", async () => {
    mockedFetch.mockResolvedValue({ kind: "json", preview: preview() });
    renderDrawer();

    expect(await screen.findByTestId("parse-result")).toBeInTheDocument();
    expect(screen.getByText(/第十八条 新入职员工/)).toBeInTheDocument();
  });

  it("文档已重新入库时显示快照片段与降级文案", async () => {
    mockedFetch.mockResolvedValue({
      kind: "json",
      preview: preview({
        render: "snapshot",
        content: "快照里的第十八条片段",
        elements: null,
        stale: true,
        note: "原文已更新",
      }),
    });
    renderDrawer();

    expect(await screen.findByText("原文已更新")).toBeInTheDocument();
    expect(screen.getByText("快照里的第十八条片段")).toBeInTheDocument();
  });

  it("开启下载时显示下载按钮，未开启时显示提示", async () => {
    mockedFetch.mockResolvedValue({ kind: "json", preview: preview() });
    const { unmount } = renderDrawer({ allowDownload: true });
    expect(await screen.findByRole("link", { name: "下载原文件" })).toHaveAttribute(
      "href",
      "/api/v1/answers/1/citations/1/file?download=true",
    );
    unmount();

    mockedFetch.mockResolvedValue({ kind: "json", preview: preview() });
    renderDrawer({ allowDownload: false });
    expect(await screen.findByText("本应用未开启下载")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "下载原文件" })).toBeNull();
  });
});

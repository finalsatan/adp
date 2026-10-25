import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PdfHighlightLayer, PdfPreview } from "./PdfPreview";

vi.mock("react-pdf", async () => {
  const React = await import("react");
  return {
    Document: ({
      file,
      children,
      onLoadError,
    }: {
      file: string;
      children?: React.ReactNode;
      onLoadError?: (error: Error) => void;
    }) => {
      React.useEffect(() => {
        if (typeof file === "string" && file.includes("variant=preview")) {
          onLoadError?.(new Error("404"));
        }
      }, [file, onLoadError]);
      return React.createElement("div", { "data-testid": "pdf-document", "data-file": file }, children);
    },
    Page: ({ children }: { children?: React.ReactNode }) =>
      React.createElement("div", { "data-testid": "pdf-page" }, children),
    pdfjs: { GlobalWorkerOptions: {} },
  };
});

afterEach(() => vi.clearAllMocks());

describe("PdfPreview", () => {
  it("同页多个 bbox 渲染多个高亮框", () => {
    render(
      <PdfHighlightLayer
        rects={[
          { left: 1, top: 2, width: 3, height: 4 },
          { left: 5, top: 6, width: 7, height: 8 },
        ]}
      />,
    );
    expect(screen.getAllByTestId("pdf-highlight")).toHaveLength(2);
  });

  it("没有高亮框时不渲染叠加层", () => {
    const { container } = render(<PdfHighlightLayer rects={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("预览 PDF 不存在且原文件是 PDF 时回落到原文件", async () => {
    render(<PdfPreview docId={10} fileType="pdf" positions={[]} />);

    const document = await screen.findByTestId("pdf-document");
    expect(document.getAttribute("data-file")).toContain("variant=original");
    expect(screen.queryByText("该文档没有可预览的 PDF，可下载原文件")).toBeNull();
  });

  it("没有可预览的 PDF 时显示降级提示", async () => {
    render(<PdfPreview docId={10} fileType="word" positions={[]} />);

    expect(await screen.findByText("该文档没有可预览的 PDF，可下载原文件")).toBeInTheDocument();
  });

  it("显示页码并跳到目标页", () => {
    render(<PdfPreview docId={10} fileType="pdf" positions={[]} targetPage={3} />);
    expect(screen.getByText(/第 3 \//)).toBeInTheDocument();
  });
});

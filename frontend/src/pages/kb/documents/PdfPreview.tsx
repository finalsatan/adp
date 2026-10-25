import { Alert, Button, Space, Spin, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { documentFileUrl } from "./api";
import { pageRects, type HighlightRect, type PageSize, type Position } from "./locate";

// pdf.js worker 用 Vite 的 URL 资源打包，生产构建会一起带上（react-pdf 10 / pdfjs-dist 5）。
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const PAGE_SCALE = 1.2;

interface PdfHighlightLayerProps {
  rects: HighlightRect[];
}

/** 叠加在 PDF 页面上的高亮框：同页多个 bbox 渲染多个框（原型 #v-doc 的原文高亮）。 */
export function PdfHighlightLayer({ rects }: PdfHighlightLayerProps) {
  if (rects.length === 0) {
    return null;
  }
  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {rects.map((rect, index) => (
        <div
          key={index}
          data-testid="pdf-highlight"
          style={{
            position: "absolute",
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
            backgroundColor: "rgba(250, 173, 20, 0.35)",
            border: "1px solid #faad14",
            borderRadius: 2,
          }}
        />
      ))}
    </div>
  );
}

interface PdfPreviewProps {
  docId: number;
  fileType: string;
  positions?: Position[];
  targetPage?: number | null;
}

/**
 * PDF 原文预览：优先预览 PDF，没有时用原文件（原文件是 PDF 的情况）。
 * 两者都没有时显示降级提示；PDF 页面上按坐标叠加高亮框。
 */
export function PdfPreview({ docId, fileType, positions = [], targetPage = null }: PdfPreviewProps) {
  const [variant, setVariant] = useState<"preview" | "original">("preview");
  const [unavailable, setUnavailable] = useState(false);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [page, setPage] = useState(targetPage && targetPage > 0 ? targetPage : 1);
  const [pageSize, setPageSize] = useState<PageSize | null>(null);

  useEffect(() => {
    if (targetPage != null && targetPage > 0) {
      setPage(targetPage);
    }
  }, [targetPage]);

  const fileUrl = useMemo(
    () => new URL(documentFileUrl(docId, variant), window.location.origin).toString(),
    [docId, variant],
  );

  const rects = useMemo(
    () => (pageSize ? pageRects(positions, page, pageSize, PAGE_SCALE) : []),
    [positions, page, pageSize],
  );

  const handleLoadError = (error: unknown) => {
    // 切换文件时 pdf.js 会以取消错误回调，忽略，避免误判为没有预览。
    const name = (error as { name?: string } | null)?.name;
    if (name === "AbortException" || name === "RenderingCancelledException") {
      return;
    }
    // 预览 PDF 不存在时回落到原文件（原文件就是 PDF）；再失败才降级。
    if (variant === "preview" && fileType === "pdf") {
      setVariant("original");
      return;
    }
    setUnavailable(true);
  };

  if (unavailable) {
    return (
      <Alert
        type="info"
        showIcon
        message="该文档没有可预览的 PDF，可下载原文件"
      />
    );
  }

  return (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Typography.Text type="secondary">
          第 {page} / {numPages ?? "…"} 页
        </Typography.Text>
        <Button size="small" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>
          ‹
        </Button>
        <Button
          size="small"
          disabled={numPages != null && page >= numPages}
          onClick={() => setPage((current) => current + 1)}
        >
          ›
        </Button>
      </Space>
      <div style={{ position: "relative", display: "inline-block", maxWidth: "100%" }}>
        <Document
          file={fileUrl}
          loading={<Spin />}
          onLoadSuccess={({ numPages: total }) => setNumPages(total)}
          onLoadError={handleLoadError}
        >
          <Page
            pageNumber={page}
            scale={PAGE_SCALE}
            onLoadSuccess={(pdfPage) => {
              const viewport = pdfPage.getViewport({ scale: 1 });
              setPageSize({ width: viewport.width, height: viewport.height });
            }}
          />
        </Document>
        <PdfHighlightLayer rects={rects} />
      </div>
    </div>
  );
}

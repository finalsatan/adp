import { Alert, Button, Drawer, Space, Spin, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { Document, Page } from "react-pdf";

import { getErrorMessage } from "../../api/errors";
import { SafeMarkdown } from "../../components/SafeMarkdown";
import { ParseResultView } from "../kb/documents/ParseResultView";
import { PdfHighlightLayer } from "../kb/documents/PdfPreview";
import {
  asPositions,
  isPdfPosition,
  pageRects,
  type PageSize,
  type Position,
} from "../kb/documents/locate";
import type { ParseResultOut } from "../kb/documents/api";
import {
  citationFileUrl,
  fetchCitationPreview,
  type Citation,
  type CitationPreview,
  type CitationPreviewResult,
} from "./api";

const PAGE_SCALE = 1.2;

interface CitationPreviewDrawerProps {
  open: boolean;
  answerId: number | null;
  citation: Citation | null;
  /** 应用当前发布版本是否开启“允许下载原文件”（应用技术方案 8.3）。 */
  allowDownload: boolean;
  onClose: () => void;
}

/** 引用预览抽屉（原型 #v-chat 右侧）：PDF 用只含被引用页的 PDF，文本类高亮被引用元素。 */
export function CitationPreviewDrawer({
  open,
  answerId,
  citation,
  allowDownload,
  onClose,
}: CitationPreviewDrawerProps) {
  const [result, setResult] = useState<CitationPreviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const n = citation?.n ?? null;

  useEffect(() => {
    if (!open || answerId == null || n == null) {
      setResult(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResult(null);
    fetchCitationPreview(answerId, n)
      .then((value) => {
        if (!cancelled) {
          setResult(value);
        }
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(getErrorMessage(cause));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, answerId, n]);

  const positions = useMemo(() => asPositions(citation?.positions ?? []), [citation]);

  return (
    <Drawer
      open={open}
      width={520}
      title={citation ? `引用 [${citation.n}] · ${citation.doc_name ?? citation.kb_name}` : "引用"}
      onClose={onClose}
      destroyOnClose
    >
      {loading ? <Spin /> : null}
      {error ? <Alert type="error" showIcon message={error} /> : null}

      {result?.kind === "pdf" ? (
        <CitationPdfView url={result.url} positions={positions} />
      ) : null}

      {result?.kind === "json" ? (
        <CitationPreviewBody preview={result.preview} />
      ) : null}

      <div style={{ marginTop: 16, display: "flex", gap: 8, alignItems: "center" }}>
        {allowDownload && answerId != null && n != null ? (
          <Button
            href={citationFileUrl(answerId, n)}
            target="_blank"
            rel="noopener noreferrer"
          >
            下载原文件
          </Button>
        ) : (
          <Typography.Text type="secondary">本应用未开启下载</Typography.Text>
        )}
      </div>
    </Drawer>
  );
}

interface CitationPreviewBodyProps {
  preview: CitationPreview;
}

function CitationPreviewBody({ preview }: CitationPreviewBodyProps) {
  const elements = (preview.elements ?? []).map((element) => ({
    ref: element.ref,
    type: element.type,
    text: element.text,
    bbox: null,
    page_no: null,
  }));
  const highlightRefs = (preview.elements ?? [])
    .filter((element) => element.highlight)
    .map((element) => element.ref);

  return (
    <div data-testid="citation-preview-body">
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        {preview.doc_name}（{preview.kb_name}）
      </Typography.Paragraph>

      {preview.note ? (
        <Alert type="warning" showIcon message={preview.note} style={{ marginBottom: 12 }} />
      ) : null}

      {preview.render === "markdown" && preview.content ? (
        <ParseResultView
          result={toParseResult(preview, elements)}
          highlightRefs={highlightRefs}
        />
      ) : preview.content ? (
        <SafeMarkdown content={preview.content} />
      ) : null}
    </div>
  );
}

function toParseResult(
  preview: CitationPreview,
  elements: ParseResultOut["elements"],
): ParseResultOut {
  return {
    markdown: preview.content ?? "",
    elements,
    edited_at: null,
    edited_by: null,
    engine: "builtin",
    engine_version: null,
    kind: "original",
    ocr_pages: null,
    pages: null,
  };
}

interface CitationPdfViewProps {
  url: string;
  positions: Position[];
}

/**
 * 只含被引用页的 PDF：按快照坐标高亮。返回的 PDF 页码是被引用页排序后的序号，
 * 高亮时先映射回快照里的原始页码（应用技术方案 8.3）。
 */
function CitationPdfView({ url, positions }: CitationPdfViewProps) {
  const [numPages, setNumPages] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize | null>(null);

  const citedPages = useMemo(
    () =>
      [...new Set(positions.filter(isPdfPosition).map((position) => position.page))].sort(
        (left, right) => left - right,
      ),
    [positions],
  );

  const rects = useMemo(() => {
    if (!pageSize) {
      return [];
    }
    const originalPage = citedPages[page - 1] ?? page;
    return pageRects(positions, originalPage, pageSize, PAGE_SCALE);
  }, [positions, citedPages, page, pageSize]);

  return (
    <div data-testid="citation-pdf">
      <Space style={{ marginBottom: 8 }}>
        <Typography.Text type="secondary">
          第 {page} / {numPages ?? "…"} 页
        </Typography.Text>
        <Button size="small" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
          ‹
        </Button>
        <Button
          size="small"
          disabled={numPages != null && page >= numPages}
          onClick={() => setPage((value) => value + 1)}
        >
          ›
        </Button>
      </Space>
      <div style={{ position: "relative", display: "inline-block", maxWidth: "100%" }}>
        <Document
          file={url}
          loading={<Spin />}
          onLoadSuccess={({ numPages: total }) => setNumPages(total)}
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

import { ArrowLeftOutlined } from "@ant-design/icons";
import { Alert, Button, Space, Tabs, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";

import { getErrorMessage } from "../../../api/errors";
import { useKb } from "../api";
import {
  documentFileUrl,
  documentStatusView,
  useDocument,
  useDocumentParse,
  useSchemes,
  type Scheme,
} from "./api";
import { ParseResultView } from "./ParseResultView";
import { PdfPreview } from "./PdfPreview";
import { elementRefs, locateFromState, parseLocateSearch, resolveTargetPage } from "./locate";
import { ReparseModal } from "./ReparseModal";

/** 文档详情页（原型 #v-doc）：原文预览 + 只读解析结果对照。 */
export default function DocumentDetailPage() {
  const { id: idParam, docId: docIdParam } = useParams();
  const kbId = Number.isFinite(Number(idParam)) ? Number(idParam) : null;
  const docId = Number.isFinite(Number(docIdParam)) ? Number(docIdParam) : null;

  const location = useLocation();
  const [, setSearchParams] = useSearchParams();

  const { data: kb } = useKb(kbId);
  const { data: doc, isLoading: docLoading } = useDocument(docId);
  const { data: parse, error: parseError } = useDocumentParse(docId, doc?.edited ?? false);
  const { data: schemePage } = useSchemes();
  const [reparseOpen, setReparseOpen] = useState(false);
  const [cleared, setCleared] = useState(false);

  const locateState = useMemo(() => locateFromState(location.state), [location.state]);
  const locateParams = useMemo(() => parseLocateSearch(location.search), [location.search]);

  const isPdf = doc?.file_type === "pdf";
  const positions = useMemo(
    () => (cleared ? [] : locateState.positions),
    [cleared, locateState.positions],
  );
  const targetPage = resolveTargetPage(locateState, locateParams, positions);

  const highlightRefs = useMemo(() => {
    if (cleared || isPdf) {
      return [];
    }
    const refs = elementRefs(positions);
    if (locateParams.ref && !refs.includes(locateParams.ref)) {
      refs.push(locateParams.ref);
    }
    return refs;
  }, [cleared, isPdf, positions, locateParams.ref]);

  const clearHighlight = () => {
    setCleared(true);
    setSearchParams({}, { replace: true });
  };

  if (docLoading || !doc) {
    return null;
  }

  const statusView = documentStatusView(doc);
  const schemes = (schemePage?.items ?? []) as Scheme[];
  const hasHighlight = positions.length > 0 || highlightRefs.length > 0;

  const parsePane = () => {
    if (parse) {
      return <ParseResultView result={parse} highlightRefs={highlightRefs} />;
    }
    if (parseError) {
      return (
        <Alert
          type={doc.status === "failed" ? "error" : "info"}
          showIcon
          message={doc.status === "done" ? getErrorMessage(parseError) : statusView.label}
          description={statusView.error ?? undefined}
        />
      );
    }
    return <Alert type="info" showIcon message="解析结果生成中，请稍后刷新。" />;
  };

  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        <Link to={`/kb/${doc.kb_id}`}>
          <ArrowLeftOutlined /> 返回文档列表
        </Link>
      </div>

      <Space direction="vertical" size={8} style={{ width: "100%", marginBottom: 12 }}>
        <Space wrap align="center">
          <Typography.Title level={4} style={{ margin: 0 }}>
            {doc.name}
          </Typography.Title>
          <Tag color={statusView.tagColor}>{statusView.label}</Tag>
          {doc.page_count != null ? <span>{doc.page_count} 页</span> : null}
          <span>{doc.chunk_count} 个分块</span>
          <span>
            入库方案：{doc.scheme_snapshot_name ?? doc.scheme?.name ?? "—"}
          </span>
          {doc.edited ? <Tag color="gold">已人工修改</Tag> : null}
        </Space>
        <Space>
          <Button href={documentFileUrl(doc.id, "original", true)}>下载原文件</Button>
          {kb?.editable ? <Button onClick={() => setReparseOpen(true)}>重新解析</Button> : null}
          {hasHighlight ? <Button onClick={clearHighlight}>清除高亮</Button> : null}
        </Space>
      </Space>

      <Tabs activeKey="parse" items={[{ key: "parse", label: "解析结果对照" }]} />

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Typography.Text strong>原文</Typography.Text>
          <div style={{ marginTop: 8 }}>
            <PdfPreview docId={doc.id} fileType={doc.file_type} positions={positions} targetPage={targetPage} />
          </div>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Space>
            <Typography.Text strong>解析结果</Typography.Text>
            <Typography.Text type="secondary">Markdown</Typography.Text>
          </Space>
          <div style={{ marginTop: 8 }}>{parsePane()}</div>
        </div>
      </div>

      <ReparseModal
        open={reparseOpen}
        kbId={doc.kb_id}
        docs={[doc]}
        schemes={schemes}
        onClose={() => setReparseOpen(false)}
      />
    </div>
  );
}

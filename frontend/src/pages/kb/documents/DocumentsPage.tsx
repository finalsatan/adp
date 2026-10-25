import { MoreOutlined, SearchOutlined } from "@ant-design/icons";
import {
  App,
  Button,
  Card,
  Dropdown,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  type MenuProps,
  type TableProps,
} from "antd";
import { useMemo, useState, type Key, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { getErrorMessage } from "../../../api/errors";
import { fileTypeLabel, useKb, type FileType } from "../api";
import { KbHeader } from "../KbHeader";
import {
  buildBatchPayload,
  buildDocumentListParams,
  documentStatusView,
  processingCounts,
  shouldPoll,
  useBatchDocuments,
  useDeleteDocument,
  useDocuments,
  useDocumentPolling,
  useFolders,
  useMetadataFields,
  useRetryDocument,
  useSchemes,
  useUpdateDocument,
  type DocumentOut,
  type DocumentStatusFilter,
  type MetadataCondition,
} from "./api";
import { DocumentMetaModal } from "./DocumentMetaModal";
import { FolderTree } from "./FolderTree";
import { ReparseModal } from "./ReparseModal";
import { UploadModal } from "./UploadModal";

const STATUS_FILTER_OPTIONS: { value: DocumentStatusFilter; label: string }[] = [
  { value: "all", label: "全部状态" },
  { value: "failed", label: "失败" },
  { value: "edited", label: "有人工修改" },
];

const FILE_TYPE_OPTIONS = [
  "pdf",
  "word",
  "ppt",
  "excel",
  "csv",
  "markdown",
  "txt",
  "html",
  "epub",
  "ofd",
  "image",
].map((type) => ({ value: type as FileType, label: fileTypeLabel(type) }));

function metadataTags(doc: DocumentOut): ReactNode {
  const entries = Object.entries(doc.metadata ?? {});
  if (entries.length === 0) {
    return "—";
  }
  return (
    <>
      {entries.map(([key, value]) => (
        <Tag key={key} style={{ marginBottom: 2 }}>
          {key}：{value === null || value === undefined || value === "" ? "—" : String(value)}
        </Tag>
      ))}
    </>
  );
}

/** 知识库文档页（原型 #v-kb）。 */
export default function DocumentsPage() {
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const kbId = Number.isFinite(parsedId) ? parsedId : null;

  const { message } = App.useApp();
  const { data: kb, isLoading } = useKb(kbId);
  const { data: folders = [] } = useFolders(kbId);
  const { data: schemePage } = useSchemes();
  const { data: metadataFields = [] } = useMetadataFields(kbId);

  const [selectedFolderId, setSelectedFolderId] = useState<number | "all" | "uncategorized">("all");
  const [statusFilter, setStatusFilter] = useState<DocumentStatusFilter>("all");
  const [fileType, setFileType] = useState<FileType | "all">("all");
  const [schemeId, setSchemeId] = useState<number | "all">("all");
  const [keyword, setKeyword] = useState("");
  const [metadataFieldId, setMetadataFieldId] = useState<number | undefined>(undefined);
  const [metadataValue, setMetadataValue] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selectedKeys, setSelectedKeys] = useState<number[]>([]);

  const [uploadOpen, setUploadOpen] = useState(false);
  const [metaDocs, setMetaDocs] = useState<DocumentOut[] | null>(null);
  const [reparseDocs, setReparseDocs] = useState<DocumentOut[] | null>(null);
  const [moveDocs, setMoveDocs] = useState<DocumentOut[] | null>(null);
  const [moveFolderId, setMoveFolderId] = useState<number | null>(null);
  const [deleteDocs, setDeleteDocs] = useState<DocumentOut[] | null>(null);

  const updateDocument = useUpdateDocument(kbId);
  const batch = useBatchDocuments(kbId);
  const retry = useRetryDocument(kbId);
  const deleteDocument = useDeleteDocument(kbId);

  const metadata = useMemo<MetadataCondition | null>(() => {
    if (metadataFieldId == null || metadataValue === "") {
      return null;
    }
    return { field_id: metadataFieldId, operator: "eq", value: metadataValue };
  }, [metadataFieldId, metadataValue]);

  const params = useMemo(
    () =>
      buildDocumentListParams(
        { folderId: selectedFolderId, statusFilter, fileType, schemeId, keyword, metadata },
        page,
        pageSize,
      ),
    [selectedFolderId, statusFilter, fileType, schemeId, keyword, metadata, page, pageSize],
  );

  const { data, isLoading: docsLoading, refetch } = useDocuments(kbId, params);
  const docs = data?.items ?? [];
  useDocumentPolling(shouldPoll(docs), refetch);

  const schemeList = schemePage?.items ?? [];
  const schemeOptions = schemeList.map((scheme) => ({ value: scheme.id, label: scheme.name }));
  const folderOptions = [
    { value: 0, label: "未分类" },
    ...folders.map((folder) => ({ value: folder.id, label: folder.path })),
  ];
  const metadataFieldOptions = metadataFields.map((field) => ({
    value: field.id,
    label: field.name,
  }));
  const selectedMetadataField = metadataFields.find((field) => field.id === metadataFieldId);

  const editable = kb?.editable ?? false;
  const totalCount = kb?.doc_count ?? 0;
  const uncategorizedCount = Math.max(
    0,
    totalCount - folders.reduce((sum, folder) => sum + folder.doc_count, 0),
  );

  const { failed: failedCount, processing: processingCount } = processingCounts(data?.status_counts);

  const selectedDocs = docs.filter((doc) => selectedKeys.includes(doc.id));

  const invalidateSelection = () => setSelectedKeys([]);

  const handleEnable = (doc: DocumentOut, enabled: boolean) =>
    updateDocument.mutate(
      { docId: doc.id, body: { enabled } },
      {
        onSuccess: () => message.success(enabled ? "已启用" : "已停用"),
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );

  const handleRetry = (doc: DocumentOut) =>
    retry.mutate(doc.id, {
      onSuccess: () => message.success("已重新排队"),
      onError: (error) => message.error(getErrorMessage(error)),
    });

  const handleBatch = (action: Parameters<typeof buildBatchPayload>[0], docsToRun: DocumentOut[], extra?: Parameters<typeof buildBatchPayload>[2]) => {
    if (docsToRun.length === 0) {
      message.info("请先选择文档");
      return;
    }
    batch.mutate(buildBatchPayload(action, docsToRun.map((doc) => doc.id), extra), {
      onSuccess: (result) => {
        message.success(`已完成 ${result.succeeded.length} 个${result.failed.length > 0 ? `，失败 ${result.failed.length} 个` : ""}`);
        invalidateSelection();
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });
  };

  const confirmDelete = () => {
    if (!deleteDocs) {
      return;
    }
    if (deleteDocs.length === 1) {
      deleteDocument.mutate(deleteDocs[0]!.id, {
        onSuccess: () => {
          message.success("已删除");
          setDeleteDocs(null);
          invalidateSelection();
        },
        onError: (error) => message.error(getErrorMessage(error)),
      });
      return;
    }
    handleBatch("delete", deleteDocs);
    setDeleteDocs(null);
  };

  const renderActions = (doc: DocumentOut): ReactNode => {
    const items: MenuProps["items"] = [];
    if (editable) {
      items.push({ key: "meta", label: "设置元数据", onClick: () => setMetaDocs([doc]) });
      items.push({ key: "move", label: "移动到文件夹", onClick: () => openMove([doc]) });
      items.push({
        key: "toggle",
        label: doc.enabled ? "停用" : "启用",
        onClick: () => handleEnable(doc, !doc.enabled),
      });
      items.push({ key: "delete", label: "删除", danger: true, onClick: () => setDeleteDocs([doc]) });
    }
    return (
      <Space size="middle">
        <Link to={`/kb/${doc.kb_id}/documents/${doc.id}`}>查看</Link>
        {editable ? <a onClick={() => setReparseDocs([doc])}>重新解析</a> : null}
        {items.length > 0 ? (
          <Dropdown menu={{ items }} trigger={["click"]}>
            <Button type="text" size="small" aria-label="更多操作" icon={<MoreOutlined />} />
          </Dropdown>
        ) : null}
      </Space>
    );
  };

  const openMove = (targets: DocumentOut[]) => {
    setMoveDocs(targets);
    setMoveFolderId(targets[0]?.folder?.id ?? null);
  };

  const columns: TableProps<DocumentOut>["columns"] = [
    {
      title: "文件名",
      render: (_value, doc) => (
        <Space size={4} wrap>
          <Link to={`/kb/${doc.kb_id}/documents/${doc.id}`}>{doc.name}</Link>
          {doc.edited ? <Tag color="gold">已人工修改</Tag> : null}
        </Space>
      ),
    },
    {
      title: "入库方案",
      render: (_value, doc) => doc.scheme?.name ?? doc.scheme_snapshot_name ?? "—",
    },
    {
      title: "状态",
      render: (_value, doc) => {
        const view = documentStatusView(doc);
        return (
          <Space direction="vertical" size={0}>
            <Tag color={view.tagColor}>{view.label}</Tag>
            {view.error ? (
              <Typography.Text type="danger" style={{ fontSize: 12 }}>
                {view.error}
              </Typography.Text>
            ) : null}
            {doc.status === "failed" && editable ? <a onClick={() => handleRetry(doc)}>重试</a> : null}
          </Space>
        );
      },
    },
    {
      title: "分块",
      render: (_value, doc) => (doc.chunk_count > 0 ? doc.chunk_count : "—"),
    },
    { title: "元数据", render: (_value, doc) => metadataTags(doc) },
    {
      title: "启用",
      render: (_value, doc) => (
        <Switch
          size="small"
          checked={doc.enabled}
          disabled={!editable}
          onChange={(checked) => handleEnable(doc, checked)}
        />
      ),
    },
    { title: "操作", render: (_value, doc) => renderActions(doc) },
  ];

  const bulkItems: MenuProps["items"] = [
    { key: "meta", label: "设置元数据", onClick: () => setMetaDocs(selectedDocs) },
    { key: "move", label: "移动到文件夹", onClick: () => openMove(selectedDocs) },
    {
      key: "retry",
      label: "重试失败的",
      onClick: () => handleBatch("retry", selectedDocs.filter((doc) => doc.status === "failed")),
    },
    { key: "reparse", label: "重新解析", onClick: () => setReparseDocs(selectedDocs) },
    { key: "enable", label: "启用", onClick: () => handleBatch("enable", selectedDocs) },
    { key: "disable", label: "停用", onClick: () => handleBatch("disable", selectedDocs) },
    {
      key: "delete",
      label: "删除",
      danger: true,
      onClick: () => setDeleteDocs(selectedDocs),
    },
  ];

  if (isLoading || !kb) {
    return null;
  }

  return (
    <div>
      <KbHeader kb={kb} active="documents" />

      {editable ? (
        <div style={{ marginBottom: 16 }}>
          <Button type="primary" onClick={() => setUploadOpen(true)}>
            上传文档
          </Button>
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <FolderTree
          kbId={kb.id}
          folders={folders}
          selectedFolderId={selectedFolderId}
          onSelect={(folderId) => {
            setSelectedFolderId(folderId);
            setPage(1);
            invalidateSelection();
          }}
          totalCount={totalCount}
          uncategorizedCount={uncategorizedCount}
          editable={editable}
        />

        <Card size="small" style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索文件名"
              style={{ width: 200 }}
              onPressEnter={(event) => {
                setKeyword((event.target as HTMLInputElement).value);
                setPage(1);
              }}
              onChange={(event) => {
                if (!event.target.value) {
                  setKeyword("");
                  setPage(1);
                }
              }}
            />
            <Select
              value={statusFilter}
              style={{ width: 140 }}
              options={STATUS_FILTER_OPTIONS}
              onChange={(value: DocumentStatusFilter) => {
                setStatusFilter(value);
                setPage(1);
              }}
            />
            <Select
              value={fileType}
              style={{ width: 140 }}
              options={[{ value: "all", label: "全部类型" }, ...FILE_TYPE_OPTIONS]}
              onChange={(value: FileType | "all") => {
                setFileType(value);
                setPage(1);
              }}
            />
            <Select
              value={schemeId}
              style={{ width: 180 }}
              options={[{ value: "all", label: "全部入库方案" }, ...schemeOptions]}
              onChange={(value: number | "all") => {
                setSchemeId(value);
                setPage(1);
              }}
            />
            <Select
              allowClear
              value={metadataFieldId}
              placeholder="元数据字段"
              style={{ width: 150 }}
              options={metadataFieldOptions}
              onChange={(value: number | undefined) => {
                setMetadataFieldId(value);
                setMetadataValue("");
                setPage(1);
              }}
            />
            {metadataFieldId != null ? (
              selectedMetadataField?.type === "select" ? (
                <Select
                  value={metadataValue || undefined}
                  placeholder="全部"
                  style={{ width: 140 }}
                  options={(selectedMetadataField.options ?? []).map((option) => ({
                    value: option,
                    label: option,
                  }))}
                  onChange={(value: string | undefined) => {
                    setMetadataValue(value ?? "");
                    setPage(1);
                  }}
                />
              ) : (
                <Input
                  value={metadataValue}
                  placeholder="全部"
                  style={{ width: 140 }}
                  onChange={(event) => {
                    setMetadataValue(event.target.value);
                    setPage(1);
                  }}
                />
              )
            ) : null}

            <span style={{ marginLeft: "auto" }}>
              <Dropdown menu={{ items: bulkItems }} trigger={["click"]} disabled={selectedKeys.length === 0}>
                <Button disabled={selectedKeys.length === 0}>批量操作 ▾</Button>
              </Dropdown>
            </span>
          </div>

          <Table<DocumentOut>
            rowKey="id"
            columns={columns}
            dataSource={docs}
            loading={docsLoading}
            rowSelection={{
              selectedRowKeys: selectedKeys,
              onChange: (keys: Key[]) => setSelectedKeys(keys.map(Number)),
            }}
            pagination={{
              current: page,
              pageSize,
              total: data?.total ?? 0,
              showSizeChanger: true,
              showTotal: (count) => `共 ${count} 条`,
              onChange: (nextPage, nextPageSize) => {
                if (nextPageSize !== pageSize) {
                  setPageSize(nextPageSize);
                  setPage(1);
                  invalidateSelection();
                  return;
                }
                setPage(nextPage);
                invalidateSelection();
              },
            }}
          />
          <Typography.Text type="secondary">
            共 {data?.total ?? 0} 条 · 失败 {failedCount} · 处理中 {processingCount}
          </Typography.Text>
        </Card>
      </div>

      <UploadModal
        open={uploadOpen}
        kbId={kb.id}
        kbName={kb.name}
        folders={folders}
        defaultFolderId={typeof selectedFolderId === "number" ? selectedFolderId : null}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => void refetch()}
      />

      <DocumentMetaModal
        open={metaDocs !== null}
        kbId={kb.id}
        docs={metaDocs ?? []}
        fields={metadataFields}
        onClose={() => setMetaDocs(null)}
      />

      <ReparseModal
        open={reparseDocs !== null}
        kbId={kb.id}
        docs={reparseDocs ?? []}
        schemes={schemeList}
        onClose={() => setReparseDocs(null)}
      />

      <Modal
        open={moveDocs !== null}
        title={`移动到文件夹 · 已选 ${moveDocs?.length ?? 0} 个文档`}
        okText="移动"
        cancelText="取消"
        confirmLoading={batch.isPending}
        onOk={() => {
          handleBatch("move", moveDocs ?? [], { folderId: moveFolderId });
          setMoveDocs(null);
        }}
        onCancel={() => setMoveDocs(null)}
        destroyOnHidden
      >
        <Select
          style={{ width: "100%" }}
          value={moveFolderId ?? 0}
          options={folderOptions}
          onChange={(value: number) => setMoveFolderId(value === 0 ? null : value)}
        />
      </Modal>

      <Modal
        open={deleteDocs !== null}
        title={`删除 ${deleteDocs?.length ?? 0} 个文档？`}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={deleteDocument.isPending || batch.isPending}
        onOk={confirmDelete}
        onCancel={() => setDeleteDocs(null)}
        destroyOnHidden
      >
        <Typography.Paragraph style={{ marginBottom: 0 }}>
          删除文档同时删除原文件、解析结果、分块和索引，不可恢复。
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  ChevronDown,
  ExternalLink,
  FileText,
  FileUp,
  Layers,
  RefreshCw,
  RotateCcw,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { documentApi, fetchDocumentSource, knowledgeApi, modelApi } from '../../api/client';
import type { DocumentItem } from '../../api/types';
import { useApi, useDocumentTitle, usePolling } from '../../hooks';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  ErrorState,
  Field,
  IconButton,
  Input,
  LoadingState,
  Modal,
  PageHeader,
  ProgressBar,
  SearchInput,
  Select,
  StatusBadge,
  Tabs,
  Textarea,
  VisibilityBadge,
  useConfirm,
} from '../../components/ui';
import {
  DOCUMENT_STATUS_LABELS,
  isPendingDocument,
} from '../../utils/permissions';
import {
  errorMessage,
  formatBytes,
  formatDateTime,
  formatNumber,
  normalizeProgress,
} from '../../utils/format';
import { KnowledgeParsingTab, KnowledgeRetrievalTab } from './KnowledgeConfigTabs';
import { KnowledgeSearchTab } from './KnowledgeSearchTab';
import { KnowledgeGrantsTab } from './KnowledgeGrantsTab';
import { KnowledgeSettingsTab } from './KnowledgeSettingsTab';

type DetailTab = 'docs' | 'parsing' | 'retrieval' | 'search' | 'grants' | 'settings';

const ACCEPT = '.pdf,.docx,.pptx,.xlsx,.html,.htm,.md,.txt,.csv';
const MAX_FILE_SIZE = 20 * 1024 * 1024;

export function KnowledgeDetailPage() {
  const { id = '' } = useParams();
  const { currentOrg, can, user } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const orgId = currentOrg?.id;
  const [tab, setTab] = useState<DetailTab>('docs');
  const [docSearch, setDocSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadMode, setUploadMode] = useState<'file' | 'text'>('file');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [textName, setTextName] = useState('制度说明.md');
  const [textContent, setTextContent] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<unknown>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const replaceInputRef = useRef<HTMLInputElement>(null);
  const replaceTargetRef = useRef<string | null>(null);

  const kbState = useApi(() => knowledgeApi.get(id), [id, orgId], { enabled: Boolean(id) });
  const docsState = useApi(() => knowledgeApi.documents(id), [id, orgId], { enabled: Boolean(id) });
  const modelsState = useApi(() => modelApi.list().catch(() => []), [orgId]);
  const roleCanGrant = can('knowledge:grant');
  const effectiveCanGrant = kbState.data?.access ? kbState.data.access.grant : roleCanGrant;
  const grantsState = useApi(() => knowledgeApi.grants(id), [id, orgId, effectiveCanGrant], {
    enabled: Boolean(id) && effectiveCanGrant,
  });
  const usersState = useApi(() => knowledgeApi.grantCandidates(id), [id, orgId, effectiveCanGrant], {
    enabled: Boolean(id) && effectiveCanGrant,
  });

  const kb = kbState.data;
  const documents = docsState.data ?? [];
  const canWrite = kb?.access ? kb.access.edit : can('knowledge:write');
  const canGrant = kb?.access ? kb.access.grant : roleCanGrant;
  const embeddingName = useMemo(() => {
    if (!kb?.embeddingModelId) return '未指定';
    return modelsState.data?.find((model) => model.id === kb.embeddingModelId)?.name ?? kb.embeddingModelId;
  }, [kb, modelsState.data]);

  useDocumentTitle(kb?.name ?? '知识库详情');

  const hasPending = documents.some((document) => isPendingDocument(document.status));
  usePolling(
    () => {
      docsState.reload();
      kbState.reload();
    },
    3000,
    hasPending,
  );

  useEffect(() => {
    setUploadFile(null);
    setUploadError(null);
    setTextContent('');
    setDragging(false);
  }, [uploadOpen]);

  const filteredDocs = useMemo(() => {
    const keyword = docSearch.trim().toLowerCase();
    return documents.filter((document) => {
      if (statusFilter === 'pending' && !isPendingDocument(document.status)) return false;
      if (statusFilter === 'ready' && document.status !== 'READY') return false;
      if (statusFilter === 'failed' && document.status !== 'FAILED') return false;
      if (!keyword) return true;
      return document.name.toLowerCase().includes(keyword);
    });
  }, [documents, docSearch, statusFilter]);

  const stats = useMemo(
    () => ({
      total: documents.length,
      ready: documents.filter((document) => document.status === 'READY').length,
      pending: documents.filter((document) => isPendingDocument(document.status)).length,
      failed: documents.filter((document) => document.status === 'FAILED').length,
    }),
    [documents],
  );

  const refreshAll = () => {
    kbState.reload();
    docsState.reload();
  };

  const pickFile = (file: File | null) => {
    setUploadError(null);
    if (!file) {
      setUploadFile(null);
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      setUploadError(new Error(`文件大小 ${formatBytes(file.size)}，超过 20 MB 限制。`));
      setUploadFile(null);
      return;
    }
    setUploadFile(file);
  };

  const submitUpload = async () => {
    if (!kb) return;
    if (uploadMode === 'file' && !uploadFile) {
      setUploadError(new Error('请选择要上传的文件。'));
      return;
    }
    if (uploadMode === 'text' && (!textName.trim() || !textContent.trim())) {
      setUploadError(new Error('请填写文档名称与正文内容。'));
      return;
    }
    setUploading(true);
    setUploadError(null);
    try {
      if (uploadMode === 'file' && uploadFile) {
        await knowledgeApi.uploadDocument(kb.id, uploadFile);
      } else {
        await knowledgeApi.createTextDocument(kb.id, {
          name: textName.trim(),
          content: textContent,
        });
      }
      toast.success('文档已加入处理队列，进度会自动刷新');
      setUploadOpen(false);
      refreshAll();
    } catch (error) {
      setUploadError(error);
    } finally {
      setUploading(false);
    }
  };

  const triggerReplace = (document: DocumentItem) => {
    replaceTargetRef.current = document.id;
    replaceInputRef.current?.click();
  };

  const handleReplaceFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    const documentId = replaceTargetRef.current;
    event.target.value = '';
    replaceTargetRef.current = null;
    if (!file || !documentId) return;
    if (file.size > MAX_FILE_SIZE) {
      toast.error(`文件大小 ${formatBytes(file.size)}，超过 20 MB 限制。`);
      return;
    }
    const target = documents.find((document) => document.id === documentId);
    if (
      !(await confirm({
        title: '替换文档版本？',
        description: `将用「${file.name}」替换「${target?.name ?? documentId}」，新版本会重新解析与建索引，旧版本保留到新版本就绪。`,
        confirmLabel: '替换并处理',
      }))
    ) {
      return;
    }
    try {
      await documentApi.replace(documentId, file);
      toast.success('文件已替换，新版本正在处理');
      refreshAll();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const removeDocument = async (document: DocumentItem) => {
    if (
      !(await confirm({
        title: '删除文档？',
        description: `「${document.name}」及其切片会从知识库移除，相关引用将失效。此操作不可撤销。`,
        confirmLabel: '删除文档',
        danger: true,
      }))
    ) {
      return;
    }
    try {
      await documentApi.remove(document.id);
      toast.success('文档已删除');
      refreshAll();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const reprocess = async (document: DocumentItem) => {
    try {
      await documentApi.reprocess(document.id);
      toast.success('已重新加入处理队列');
      refreshAll();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const openSource = async (document: DocumentItem) => {
    try {
      const blob = await fetchDocumentSource(document.id);
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement('a');
      anchor.href = url;
      anchor.download = document.name || 'source';
      anchor.rel = 'noopener';
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      toast.info('原文件已作为下载处理，不会在浏览器同源页面中执行。');
    } catch (error) {
      toast.error(`无法打开原文：${errorMessage(error)}`);
    }
  };

  if (kbState.loading && !kb) {
    return (
      <div className="page">
        <LoadingState label="正在读取知识库…" />
      </div>
    );
  }

  if (kbState.error || !kb) {
    return (
      <div className="page">
        <PageHeader
          title="知识库"
          description="无法读取该知识库。"
          back={
            <Link to="/knowledge" className="back-link">
              <ArrowLeft size={15} /> 返回知识库
            </Link>
          }
        />
        <ErrorState error={kbState.error ?? new Error('知识库不存在')} onRetry={kbState.reload} />
      </div>
    );
  }

  return (
    <div className="page kb-detail-page">
      <PageHeader
        back={
          <Link to="/knowledge" className="back-link">
            <ArrowLeft size={15} /> 返回知识库
          </Link>
        }
        eyebrow={
          <span className="breadcrumb">
            资源库 <ChevronDown size={12} className="breadcrumb-sep" /> 知识库
          </span>
        }
        title={
          <span className="title-with-badge">
            {kb.name}
            <VisibilityBadge visibility={kb.visibility} />
          </span>
        }
        description={
          <span className="kb-meta-line">
            {formatNumber(kb.documentCount)} 个文档 · {formatNumber(kb.chunkCount)} 个切片 · Embedding：{embeddingName} · 创建于{' '}
            {formatDateTime(kb.createdAt)}
          </span>
        }
        actions={
          <>
            <Button variant="ghost" icon={<RefreshCw size={15} />} onClick={refreshAll} loading={kbState.loading || docsState.loading}>
              刷新
            </Button>
            {canWrite && (
              <Button variant="primary" icon={<Upload size={15} />} onClick={() => setUploadOpen(true)}>
                上传文档
              </Button>
            )}
          </>
        }
      />

      {!canWrite && (
        <Callout tone="info">
          你对这个知识库只有查看权限。上传、替换、编辑分块与修改配置需要「knowledge:write」权限。
        </Callout>
      )}

      <div className="kb-tabbar">
        <Tabs
          tabs={[
            { value: 'docs', label: '文档', count: documents.length },
            { value: 'parsing', label: '解析与分块' },
            { value: 'retrieval', label: '检索配置' },
            { value: 'search', label: '检索测试' },
            { value: 'grants', label: '权限' },
            { value: 'settings', label: '设置' },
          ]}
          value={tab}
          onChange={setTab}
        />
      </div>

      {tab === 'docs' && (
        <Card padded={false} className="kb-docs-card">
          <div className="card-toolbar">
            <div className="card-toolbar-stat">
              <FileText size={16} />
              <span>
                共 <strong>{stats.total}</strong> 个文档
              </span>
              {stats.pending > 0 && <Badge tone="blue">{stats.pending} 个处理中</Badge>}
              {stats.failed > 0 && <Badge tone="red">{stats.failed} 个失败</Badge>}
              {stats.ready > 0 && <Badge tone="green">{stats.ready} 个就绪</Badge>}
            </div>
            <div className="card-toolbar-actions">
              <SearchInput value={docSearch} onChange={setDocSearch} placeholder="搜索文档名称" />
              <div className="select-inline">
                <Select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="按状态筛选">
                  <option value="all">全部状态</option>
                  <option value="pending">处理中</option>
                  <option value="ready">已完成</option>
                  <option value="failed">失败</option>
                </Select>
              </div>
            </div>
          </div>

          {docsState.loading && !docsState.data ? (
            <LoadingState label="正在读取文档…" />
          ) : docsState.error ? (
            <div className="card-pad">
              <ErrorState error={docsState.error} onRetry={docsState.reload} compact />
            </div>
          ) : filteredDocs.length === 0 ? (
            <EmptyState
              icon={<FileUp size={23} />}
              title={documents.length === 0 ? '还没有文档' : '没有匹配的文档'}
              description={
                documents.length === 0
                  ? '上传 PDF、Word、PPT、Excel、HTML、Markdown、TXT 或 CSV 文件，系统会保留标题层级与表格位置。'
                  : '调整搜索词或状态筛选后再试。'
              }
              action={
                documents.length === 0 && canWrite ? (
                  <Button variant="primary" icon={<Upload size={15} />} onClick={() => setUploadOpen(true)}>
                    上传第一份文档
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="table-wrap">
              <table className="table doc-table">
                <thead>
                  <tr>
                    <th>文件</th>
                    <th>版本</th>
                    <th>状态</th>
                    <th>切片</th>
                    <th>创建时间</th>
                    <th className="align-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredDocs.map((document) => (
                    <tr key={document.id} className={document.status === 'FAILED' ? 'row-failed' : ''}>
                      <td>
                        <div className="doc-name-cell">
                          <span className="doc-icon">
                            <FileText size={16} />
                          </span>
                          <div>
                            <Link to={`/knowledge/${kb.id}/documents/${document.id}`} className="table-link">
                              {document.name}
                            </Link>
                            <div className="doc-subline">
                              {document.mime || '未知类型'} · {formatBytes(document.size)}
                            </div>
                            {document.status === 'FAILED' && document.error && (
                              <div className="doc-error" title={document.error}>
                                {document.error}
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                      <td>
                        <Badge tone="neutral">v{document.version}</Badge>
                      </td>
                      <td>
                        <div className="doc-status-cell">
                          <StatusBadge status={document.status} />
                          {isPendingDocument(document.status) && (
                            <ProgressBar value={normalizeProgress(document.progress)} />
                          )}
                          {isPendingDocument(document.status) && (
                            <span className="doc-status-hint">{DOCUMENT_STATUS_LABELS[document.status]}</span>
                          )}
                        </div>
                      </td>
                      <td>
                        <Link to={`/knowledge/${kb.id}/documents/${document.id}`} className="chunk-count-link">
                          <Layers size={13} />
                          {formatNumber(document.chunkCount)}
                        </Link>
                      </td>
                      <td className="muted-cell">{formatDateTime(document.createdAt)}</td>
                      <td>
                        <div className="row-actions">
                          <button type="button" className="row-action" onClick={() => openSource(document)}>
                            <ExternalLink size={13} /> 原文
                          </button>
                          <Link to={`/knowledge/${kb.id}/documents/${document.id}`} className="row-action">
                            <Layers size={13} /> 分块
                          </Link>
                          {canWrite && (
                            <>
                              <button type="button" className="row-action" onClick={() => triggerReplace(document)}>
                                <RotateCcw size={13} /> 替换
                              </button>
                              <button type="button" className="row-action" onClick={() => reprocess(document)}>
                                <RefreshCw size={13} /> 重试
                              </button>
                              <button
                                type="button"
                                className="row-action row-action-danger"
                                onClick={() => removeDocument(document)}
                              >
                                <Trash2 size={13} /> 删除
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="card-foot-note">
            处理进度与失败原因由页面定时刷新。失败文档可查看原因并重试；替换会生成新版本，旧索引在新版本就绪前保留。
          </div>
        </Card>
      )}

      {tab === 'parsing' && (
        <KnowledgeParsingTab
          kb={kb}
          documents={documents}
          canWrite={canWrite}
          onSaved={refreshAll}
        />
      )}

      {tab === 'retrieval' && (
        <KnowledgeRetrievalTab
          kb={kb}
          models={modelsState.data ?? []}
          canWrite={canWrite}
          onSaved={refreshAll}
        />
      )}

      {tab === 'search' && (
        <KnowledgeSearchTab
          kb={kb}
          models={modelsState.data ?? []}
          onEditConfig={() => setTab('retrieval')}
        />
      )}

      {tab === 'grants' && (
        <KnowledgeGrantsTab
          kb={kb}
          grantsState={grantsState}
          usersState={usersState}
          canGrant={canGrant}
          currentUserId={user?.id}
          onChanged={() => {
            grantsState.reload();
            usersState.reload();
          }}
        />
      )}

      {tab === 'settings' && (
        <KnowledgeSettingsTab
          kb={kb}
          models={modelsState.data ?? []}
          canWrite={canWrite}
          canDelete={canWrite}
          onSaved={refreshAll}
          onDeleted={() => navigate('/knowledge')}
        />
      )}

      <input ref={replaceInputRef} type="file" accept={ACCEPT} hidden onChange={handleReplaceFile} />

      <Modal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        title="上传文档"
        description="单个文件不超过 20 MB。文件会在服务端解析，进度与失败原因可在文档列表查看。"
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setUploadOpen(false)}>
              取消
            </Button>
            <Button variant="primary" loading={uploading} onClick={submitUpload} icon={<Upload size={15} />}>
              加入处理队列
            </Button>
          </>
        }
      >
        <div className="upload-tabs">
          <button
            type="button"
            className={uploadMode === 'file' ? 'is-active' : ''}
            onClick={() => setUploadMode('file')}
          >
            <FileUp size={15} /> 上传文件
          </button>
          <button
            type="button"
            className={uploadMode === 'text' ? 'is-active' : ''}
            onClick={() => setUploadMode('text')}
          >
            <FileText size={15} /> 粘贴文本
          </button>
        </div>

        {uploadMode === 'file' ? (
          <div
            className={`drop-zone${dragging ? ' is-dragging' : ''}${uploadFile ? ' has-file' : ''}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              pickFile(event.dataTransfer.files?.[0] ?? null);
            }}
            onClick={() => fileInputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') fileInputRef.current?.click();
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPT}
              hidden
              onChange={(event) => pickFile(event.target.files?.[0] ?? null)}
            />
            {uploadFile ? (
              <div className="drop-file">
                <FileText size={26} />
                <div>
                  <strong>{uploadFile.name}</strong>
                  <span>{formatBytes(uploadFile.size)}</span>
                </div>
                <IconButton
                  label="移除文件"
                  onClick={(event) => {
                    event.stopPropagation();
                    setUploadFile(null);
                  }}
                >
                  <X size={15} />
                </IconButton>
              </div>
            ) : (
              <>
                <FileUp size={28} />
                <strong>拖拽文件到此处，或点击选择</strong>
                <span>支持 PDF、DOCX、PPTX、XLSX、HTML、Markdown、TXT、CSV</span>
              </>
            )}
          </div>
        ) : (
          <div className="form-stack">
            <Field label="文档名称" htmlFor="upload-text-name" required>
              <Input
                id="upload-text-name"
                value={textName}
                onChange={(event) => setTextName(event.target.value)}
                placeholder="例如：差旅管理办法.md"
              />
            </Field>
            <Field label="正文内容" htmlFor="upload-text-content" required hint="Markdown 标题会被保留为章节路径。">
              <Textarea
                id="upload-text-content"
                rows={12}
                value={textContent}
                onChange={(event) => setTextContent(event.target.value)}
                placeholder={'# 一级标题\n\n正文段落…'}
              />
            </Field>
          </div>
        )}

        {uploadError != null && (
          <Callout tone="danger" title="无法加入队列">
            {errorMessage(uploadError)}
          </Callout>
        )}
      </Modal>
    </div>
  );
}

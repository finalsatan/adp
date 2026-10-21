import { useMemo, useState, type ChangeEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FileText,
  Layers,
  Merge,
  Pencil,
  RefreshCw,
  Scissors,
  Search,
  SlidersHorizontal,
  Trash2,
} from 'lucide-react';
import { documentApi, fetchDocumentSource, knowledgeApi } from '../../api/client';
import type { Chunk } from '../../api/types';
import { useApi, useDocumentTitle, usePolling } from '../../hooks';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import {
  Badge,
  Button,
  Callout,
  Card,
  CopyButton,
  EmptyState,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Modal,
  PageHeader,
  ProgressBar,
  SearchInput,
  StatusBadge,
  Textarea,
  useConfirm,
} from '../../components/ui';
import {
  isPendingDocument,
  sourceRefLabel,
  STRATEGY_LABELS,
} from '../../utils/permissions';
import {
  errorMessage,
  formatBytes,
  formatDateTime,
  formatNumber,
  normalizeProgress,
} from '../../utils/format';

export function DocumentPage() {
  const { id: kbId = '', documentId = '' } = useParams();
  const { currentOrg, can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const orgId = currentOrg?.id;
  const [chunkSearch, setChunkSearch] = useState('');
  const [hidingDisabled, setHidingDisabled] = useState(false);
  const [editChunk, setEditChunk] = useState<Chunk | null>(null);
  const [editContent, setEditContent] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);
  const [splitChunk, setSplitChunk] = useState<Chunk | null>(null);
  const [splitOffset, setSplitOffset] = useState(0);
  const [splitting, setSplitting] = useState(false);
  const [busyChunkId, setBusyChunkId] = useState<string | null>(null);

  const docState = useApi(() => documentApi.get(documentId), [documentId, orgId], {
    enabled: Boolean(documentId),
  });
  const chunksState = useApi(() => documentApi.chunks(documentId), [documentId, orgId], {
    enabled: Boolean(documentId),
  });
  const kbState = useApi(() => knowledgeApi.get(kbId), [kbId, orgId], { enabled: Boolean(kbId) });

  const documentItem = docState.data;
  const chunks = chunksState.data ?? [];
  const kb = kbState.data;
  const canWrite = kb?.access ? kb.access.edit : can('knowledge:write');
  const pending = documentItem ? isPendingDocument(documentItem.status) : false;

  useDocumentTitle(documentItem?.name ?? '文档分块');

  usePolling(
    () => {
      docState.reload();
      chunksState.reload();
    },
    3000,
    pending,
  );

  const sortedChunks = useMemo(
    () => [...chunks].sort((a, b) => (a.position ?? 0) - (b.position ?? 0)),
    [chunks],
  );

  const filteredChunks = useMemo(() => {
    const keyword = chunkSearch.trim().toLowerCase();
    return sortedChunks.filter((chunk) => {
      if (hidingDisabled && !chunk.enabled) return false;
      if (!keyword) return true;
      return (
        chunk.content.toLowerCase().includes(keyword) ||
        chunk.headingPath?.some((heading) => heading.toLowerCase().includes(keyword))
      );
    });
  }, [sortedChunks, chunkSearch, hidingDisabled]);

  const refresh = () => {
    docState.reload();
    chunksState.reload();
  };

  const reloadAfterMutation = () => {
    refresh();
  };

  const openSource = async () => {
    if (!documentItem) return;
    try {
      const blob = await fetchDocumentSource(documentItem.id);
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement('a');
      anchor.href = url;
      anchor.download = documentItem.name || 'source';
      anchor.rel = 'noopener';
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      toast.info('原文件已作为下载处理，不会在浏览器同源页面中执行。');
    } catch (error) {
      toast.error(`无法打开原文：${errorMessage(error)}`);
    }
  };

  const reprocess = async () => {
    if (!documentItem) return;
    try {
      await documentApi.reprocess(documentItem.id);
      toast.success('已重新加入处理队列');
      refresh();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const removeDocument = async () => {
    if (!documentItem) return;
    if (
      !(await confirm({
        title: '删除文档？',
        description: `「${documentItem.name}」及全部分块会被删除，相关引用将失效。此操作不可撤销。`,
        confirmLabel: '删除文档',
        danger: true,
      }))
    ) {
      return;
    }
    try {
      await documentApi.remove(documentItem.id);
      toast.success('文档已删除');
      navigate(`/knowledge/${kbId}`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const toggleChunk = async (chunk: Chunk) => {
    setBusyChunkId(chunk.id);
    try {
      await documentApi.updateChunk(chunk.id, { enabled: !chunk.enabled });
      toast.success(chunk.enabled ? '分块已禁用，将不再被检索' : '分块已启用');
      reloadAfterMutation();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusyChunkId(null);
    }
  };

  const openEdit = (chunk: Chunk) => {
    setEditChunk(chunk);
    setEditContent(chunk.content);
  };

  const saveEdit = async () => {
    if (!editChunk) return;
    if (!editContent.trim()) {
      toast.error('分块内容不能为空');
      return;
    }
    setSavingEdit(true);
    try {
      await documentApi.updateChunk(editChunk.id, { content: editContent });
      toast.success('分块已更新，重新索引由服务端处理');
      setEditChunk(null);
      reloadAfterMutation();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSavingEdit(false);
    }
  };

  const openSplit = (chunk: Chunk) => {
    setSplitChunk(chunk);
    setSplitOffset(Math.max(1, Math.floor(chunk.content.length / 2)));
  };

  const submitSplit = async () => {
    if (!splitChunk) return;
    if (splitOffset <= 0 || splitOffset >= splitChunk.content.length) {
      toast.error(`拆分位置需在 1 到 ${splitChunk.content.length - 1} 之间`);
      return;
    }
    setSplitting(true);
    try {
      await documentApi.splitChunk(splitChunk.id, splitOffset);
      toast.success('分块已拆分');
      setSplitChunk(null);
      reloadAfterMutation();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSplitting(false);
    }
  };

  const mergeNext = async (chunk: Chunk) => {
    const index = sortedChunks.findIndex((item) => item.id === chunk.id);
    const next = sortedChunks[index + 1];
    if (!next) return;
    if (
      !(await confirm({
        title: '合并相邻分块？',
        description: `将当前分块与下一块合并，合并后内容按顺序拼接，并重新计算位置。`,
        confirmLabel: '合并',
      }))
    ) {
      return;
    }
    setBusyChunkId(chunk.id);
    try {
      await documentApi.mergeChunk(chunk.id, next.id);
      toast.success('分块已合并');
      reloadAfterMutation();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusyChunkId(null);
    }
  };

  if (docState.loading && !documentItem) {
    return (
      <div className="page">
        <LoadingState label="正在读取文档…" />
      </div>
    );
  }

  if (docState.error || !documentItem) {
    return (
      <div className="page">
        <PageHeader
          title="文档"
          description="无法读取该文档。"
          back={
            <Link to={`/knowledge/${kbId}`} className="back-link">
              <ArrowLeft size={15} /> 返回知识库
            </Link>
          }
        />
        <ErrorState error={docState.error ?? new Error('文档不存在')} onRetry={docState.reload} />
      </div>
    );
  }

  return (
    <div className="page document-page">
      <PageHeader
        back={
          <Link to={`/knowledge/${kbId}`} className="back-link">
            <ArrowLeft size={15} /> 返回知识库
          </Link>
        }
        eyebrow={<span className="breadcrumb">{kb?.name ?? '知识库'} / 文档</span>}
        title={
          <span className="title-with-badge">
            {documentItem.name}
            <StatusBadge status={documentItem.status} />
            <Badge tone="neutral">v{documentItem.version}</Badge>
          </span>
        }
        description={
          <span className="kb-meta-line">
            {documentItem.mime || '未知类型'} · {formatBytes(documentItem.size)} · {formatNumber(documentItem.chunkCount)} 个切片 · 创建于{' '}
            {formatDateTime(documentItem.createdAt)}
          </span>
        }
        actions={
          <>
            <Button variant="secondary" icon={<ExternalLink size={15} />} onClick={openSource}>
              查看原文件
            </Button>
            {canWrite && (
              <Button variant="secondary" icon={<RefreshCw size={15} />} onClick={reprocess}>
                重新处理
              </Button>
            )}
            {canWrite && (
              <Button variant="ghost" icon={<Trash2 size={15} />} onClick={removeDocument}>
                删除
              </Button>
            )}
          </>
        }
      />

      {pending && (
        <Callout tone="info" title={`文档正在处理：${documentItem.status === 'QUEUED' ? '排队中' : documentItem.status === 'PARSING' ? '解析中' : '向量化中'}`}>
          <div className="doc-processing">
            <ProgressBar value={normalizeProgress(documentItem.progress)} label={`${Math.round(normalizeProgress(documentItem.progress))}%`} />
            <span>页面每 3 秒刷新。处理完成后会自动显示解析文本与分块。</span>
          </div>
        </Callout>
      )}

      {documentItem.status === 'FAILED' && (
        <Callout
          tone="danger"
          title="文档处理失败"
          action={
            canWrite ? (
              <Button size="sm" variant="danger" icon={<RefreshCw size={14} />} onClick={reprocess}>
                重试
              </Button>
            ) : undefined
          }
        >
          {documentItem.error || '服务端未返回具体失败原因，请检查文件是否可解析或查看入库链路。'}
        </Callout>
      )}

      <div className="doc-split">
        <Card padded={false} className="parsed-card">
          <div className="card-toolbar">
            <div className="card-toolbar-stat">
              <FileText size={16} />
              <span>解析文本</span>
              {kb?.chunkConfig && (
                <Badge tone="neutral">
                  {STRATEGY_LABELS[kb.chunkConfig.strategy] ?? kb.chunkConfig.strategy} · {kb.chunkConfig.maxChars} 字符
                </Badge>
              )}
            </div>
            <div className="card-toolbar-actions">
              {documentItem.parsedText && <CopyButton value={documentItem.parsedText} label="复制全文" />}
            </div>
          </div>
          {docState.loading && !documentItem.parsedText ? (
            <LoadingState label="读取解析文本…" />
          ) : documentItem.parsedText ? (
            <pre className="parsed-text">{documentItem.parsedText}</pre>
          ) : (
            <EmptyState
              icon={<FileText size={22} />}
              title={pending ? '等待解析完成' : '没有可用的解析文本'}
              description={
                pending
                  ? '解析完成后，保留的标题层级、表格与页码会显示在这里。'
                  : '如果是扫描件或受保护文件，服务端可能无法提取文字。请查看失败原因或更换文件。'
              }
            />
          )}
          <div className="card-foot-note">
            左侧为服务端解析结果；右侧分块内容可直接编辑、拆分、合并或禁用。禁用分块不会被检索。
          </div>
        </Card>

        <Card padded={false} className="chunks-card">
          <div className="card-toolbar">
            <div className="card-toolbar-stat">
              <Layers size={16} />
              <span>
                分块 <strong>{filteredChunks.length}</strong>
                {filteredChunks.length !== sortedChunks.length && <span className="muted-text"> / {sortedChunks.length}</span>}
              </span>
            </div>
            <div className="card-toolbar-actions">
              <SearchInput value={chunkSearch} onChange={setChunkSearch} placeholder="搜索分块内容" />
              <button
                type="button"
                className={`filter-toggle${hidingDisabled ? ' is-active' : ''}`}
                onClick={() => setHidingDisabled((value) => !value)}
              >
                <SlidersHorizontal size={13} />
                {hidingDisabled ? '显示已禁用' : '隐藏已禁用'}
              </button>
            </div>
          </div>

          {chunksState.loading && !chunksState.data ? (
            <LoadingState label="正在读取分块…" />
          ) : chunksState.error ? (
            <div className="card-pad">
              <ErrorState error={chunksState.error} onRetry={chunksState.reload} compact />
            </div>
          ) : filteredChunks.length === 0 ? (
            <EmptyState
              icon={<Search size={22} />}
              title={sortedChunks.length === 0 ? '暂无分块' : '没有匹配的分块'}
              description={
                sortedChunks.length === 0
                  ? pending
                    ? '文档处理完成后会自动生成分块。'
                    : '该文档没有生成分块，请检查解析结果或重新处理。'
                  : '尝试更换搜索词，或显示已禁用的分块。'
              }
            />
          ) : (
            <div className="chunk-list">
              {filteredChunks.map((chunk, index) => {
                const next = sortedChunks[sortedChunks.findIndex((item) => item.id === chunk.id) + 1];
                return (
                  <ChunkCard
                    key={chunk.id}
                    chunk={chunk}
                    displayIndex={index + 1}
                    nextExists={Boolean(next)}
                    canWrite={canWrite}
                    busy={busyChunkId === chunk.id}
                    onEdit={() => openEdit(chunk)}
                    onSplit={() => openSplit(chunk)}
                    onMerge={() => mergeNext(chunk)}
                    onToggle={() => toggleChunk(chunk)}
                  />
                );
              })}
            </div>
          )}
        </Card>
      </div>

      <Modal
        open={editChunk != null}
        onClose={() => setEditChunk(null)}
        title="编辑分块内容"
        description="修改会保留手动编辑标记；重新处理文档可能覆盖编辑结果。"
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditChunk(null)}>
              取消
            </Button>
            <Button variant="primary" loading={savingEdit} onClick={saveEdit}>
              保存分块
            </Button>
          </>
        }
      >
        <div className="form-stack">
          <div className="chunk-edit-meta">
            <Badge tone="neutral">#{editChunk?.position}</Badge>
            {editChunk?.headingPath?.length ? <span>{editChunk.headingPath.join(' › ')}</span> : null}
            {editChunk?.sourceRef && <span>{sourceRefLabel(editChunk.sourceRef)}</span>}
          </div>
          <Textarea
            value={editContent}
            onChange={(event) => setEditContent(event.target.value)}
            rows={14}
            aria-label="分块内容"
          />
          <span className="field-hint">{editContent.length} 个字符</span>
        </div>
      </Modal>

      <Modal
        open={splitChunk != null}
        onClose={() => setSplitChunk(null)}
        title="拆分分块"
        description="选择一个字符偏移位置。选中编辑框中的文字可自动带入偏移。"
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setSplitChunk(null)}>
              取消
            </Button>
            <Button
              variant="primary"
              icon={<Scissors size={15} />}
              loading={splitting}
              onClick={submitSplit}
              disabled={!splitChunk || splitOffset <= 0 || splitOffset >= (splitChunk?.content.length ?? 0)}
            >
              在偏移 {splitOffset} 处拆分
            </Button>
          </>
        }
      >
        {splitChunk && (
          <div className="form-stack">
            <div className="split-meta">
              <Badge tone="neutral">#{splitChunk.position}</Badge>
              <span>{splitChunk.content.length} 个字符</span>
              <span>{sourceRefLabel(splitChunk.sourceRef)}</span>
            </div>
            <fieldset className="split-fieldset">
              <legend>点击或选中文本以设置拆分位置</legend>
              <textarea
                className="split-textarea"
                value={splitChunk.content}
                onChange={() => undefined}
                onSelect={(event: ChangeEvent<HTMLTextAreaElement>) =>
                  setSplitOffset(event.currentTarget.selectionStart ?? splitOffset)
                }
                onClick={(event) => setSplitOffset(event.currentTarget.selectionStart ?? splitOffset)}
                rows={10}
                aria-label="待拆分内容"
              />
            </fieldset>
            <Field label="拆分偏移" htmlFor="split-offset" hint={`需在 1 到 ${splitChunk.content.length - 1} 之间。`}>
              <Input
                id="split-offset"
                type="number"
                min={1}
                max={splitChunk.content.length - 1}
                value={splitOffset}
                onChange={(event) => setSplitOffset(Number(event.target.value))}
              />
            </Field>
            <div className="split-preview">
              <div>
                <span>前段 · {Math.max(0, splitOffset)} 字符</span>
                <pre>{splitChunk.content.slice(0, Math.max(0, splitOffset)) || '（空）'}</pre>
              </div>
              <div>
                <span>后段 · {Math.max(0, splitChunk.content.length - splitOffset)} 字符</span>
                <pre>{splitChunk.content.slice(Math.max(0, splitOffset)) || '（空）'}</pre>
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

function ChunkCard({
  chunk,
  displayIndex,
  nextExists,
  canWrite,
  busy,
  onEdit,
  onSplit,
  onMerge,
  onToggle,
}: {
  chunk: Chunk;
  displayIndex: number;
  nextExists: boolean;
  canWrite: boolean;
  busy: boolean;
  onEdit: () => void;
  onSplit: () => void;
  onMerge: () => void;
  onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const long = chunk.content.length > 900;
  const content = expanded || !long ? chunk.content : `${chunk.content.slice(0, 900)}…`;

  return (
    <article className={`chunk-card${chunk.enabled ? '' : ' is-disabled'}${chunk.parentId ? ' is-child' : ''}`}>
      <div className="chunk-head">
        <span className="chunk-position">#{chunk.position}</span>
        <div className="chunk-badges">
          {chunk.isParent && <Badge tone="purple">父块</Badge>}
          {chunk.parentId && <Badge tone="indigo">子块</Badge>}
          {chunk.edited && <Badge tone="amber">已编辑</Badge>}
          {!chunk.enabled && <Badge tone="red">已禁用</Badge>}
          <Badge tone="neutral">{formatNumber(chunk.content.length)} 字符</Badge>
        </div>
        <span className="chunk-order">显示 #{displayIndex}</span>
      </div>
      {chunk.headingPath?.length > 0 && (
        <div className="chunk-path">
          {chunk.headingPath.map((heading, index) => (
            <span key={`${heading}-${index}`}>
              {index > 0 && <i>›</i>}
              {heading}
            </span>
          ))}
        </div>
      )}
      <div className="chunk-content">{content}</div>
      {long && (
        <button type="button" className="chunk-expand" onClick={() => setExpanded((value) => !value)}>
          {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {expanded ? '收起' : `展开剩余 ${chunk.content.length - 900} 字符`}
        </button>
      )}
      <div className="chunk-foot">
        <span className="chunk-source">{sourceRefLabel(chunk.sourceRef)}</span>
        {chunk.parentId && <span className="chunk-parent">父块 {chunk.parentId.slice(0, 8)}</span>}
        {canWrite && (
          <div className="chunk-actions">
            <button type="button" className="row-action" onClick={onEdit}>
              <Pencil size={13} /> 编辑
            </button>
            <button type="button" className="row-action" onClick={onSplit}>
              <Scissors size={13} /> 拆分
            </button>
            <button type="button" className="row-action" onClick={onMerge} disabled={!nextExists || busy}>
              <Merge size={13} /> 合并下一块
            </button>
            <button type="button" className="row-action" onClick={onToggle} disabled={busy}>
              {chunk.enabled ? <AlertTriangle size={13} /> : <RefreshCw size={13} />}
              {chunk.enabled ? '禁用' : '启用'}
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

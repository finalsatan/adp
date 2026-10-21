import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  ArrowRight,
  ChevronDown,
  FileText,
  Hash,
  Layers,
  Search,
  SlidersHorizontal,
} from 'lucide-react';
import type { Hit, KnowledgeBase, Model, SearchResponse, SearchStage } from '../../api/types';
import { knowledgeApi } from '../../api/client';
import { useAuth } from '../../contexts/AuthContext';
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  ErrorState,
  Input,
  SectionHeader,
  Spinner,
} from '../../components/ui';
import { RETRIEVAL_MODE_LABELS, sourceRefLabel } from '../../utils/permissions';
import { formatDuration, formatNumber } from '../../utils/format';

export function KnowledgeSearchTab({
  kb,
  models,
  onEditConfig,
}: {
  kb: KnowledgeBase;
  models: Model[];
  onEditConfig: () => void;
}) {
  const { can } = useAuth();
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [submittedQuery, setSubmittedQuery] = useState('');

  const config = kb.retrievalConfig;
  const rerankName = config?.rerankModelId
    ? models.find((model) => model.id === config.rerankModelId)?.name ?? config.rerankModelId
    : null;
  const rewriteName = config?.rewriteModelId
    ? models.find((model) => model.id === config.rewriteModelId)?.name ?? config.rewriteModelId
    : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = query.trim();
    if (!text) return;
    setLoading(true);
    setError(null);
    setSubmittedQuery(text);
    try {
      const response = await knowledgeApi.search(kb.id, { query: text });
      setResult(response);
    } catch (err) {
      setError(err);
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="tab-stack search-tab">
      <Card>
        <SectionHeader
          title="检索测试"
          description="使用知识库保存的检索配置，查看分阶段召回、融合与重排结果；不会生成回答。"
          actions={
            <Button variant="secondary" icon={<SlidersHorizontal size={15} />} onClick={onEditConfig}>
              编辑检索配置
            </Button>
          }
        />
        <div className="config-summary-row">
          <Badge tone="indigo">{config?.mode ? RETRIEVAL_MODE_LABELS[config.mode] : '混合检索'}</Badge>
          <span>topK {config?.topK ?? 5}</span>
          <span>阈值 {config?.threshold ?? 0}</span>
          <span>重排：{rerankName ?? '未启用'}</span>
          <span>改写：{rewriteName ?? '未启用'}</span>
          <Link to={`/knowledge/${kb.id}`} className="config-summary-edit" onClick={(event) => {
            event.preventDefault();
            onEditConfig();
          }}>
            调整 <ArrowRight size={12} />
          </Link>
        </div>

        <form className="search-query-form" onSubmit={submit}>
          <div className="search-query-input">
            <Search size={18} />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="输入一个真实业务问题，例如：一线城市经理出差住宿标准是多少？"
              aria-label="检索测试问题"
            />
          </div>
          <Button type="submit" variant="primary" loading={loading} disabled={!query.trim()} icon={<Search size={15} />}>
            执行检索
          </Button>
        </form>
        <p className="search-hint">
          检索测试直接调用知识库 Search API，阶段名称、耗时与各类得分来自服务端返回。
        </p>
      </Card>

      {loading && (
        <Card>
          <div className="search-loading">
            <Spinner size={20} />
            <div>
              <strong>正在检索知识库…</strong>
              <span>关键词、向量与融合过程可能需要数秒。</span>
            </div>
          </div>
        </Card>
      )}

      {error != null && <ErrorState error={error} compact />}

      {!loading && !error && result && (
        <>
          <div className="search-result-head">
            <div>
              <span className="page-eyebrow">检索问题</span>
              <h2>{submittedQuery}</h2>
              <p>
                最终命中 <strong>{result.items?.length ?? 0}</strong> 个分块 · 共 {result.stages?.length ?? 0} 个阶段
              </p>
            </div>
            <div className="search-result-actions">
              {result.traceId && can('observe:read') && (
                <Link to={`/traces/${result.traceId}`} className="btn btn-secondary btn-sm">
                  <Activity size={14} />
                  <span>查看链路</span>
                </Link>
              )}
              <Badge tone={result.items?.length ? 'green' : 'amber'} dot>
                {result.items?.length ? '有命中' : '无命中'}
              </Badge>
            </div>
          </div>

          {result.items?.length === 0 ? (
            <Card>
              <EmptyState
                icon={<Search size={22} />}
                title="没有召回任何分块"
                description="可能是问题与知识库内容不相关、阈值过高，或相关文档仍在处理中。可降低阈值或更换问题后再试。"
              />
            </Card>
          ) : (
            <>
              <div className="stage-list">
                {(result.stages ?? []).map((stage, index) => (
                  <StageCard key={`${stage.name}-${index}`} stage={stage} kbId={kb.id} defaultOpen={index === 0} />
                ))}
              </div>

              <Card>
                <SectionHeader
                  title="最终命中"
                  description="服务端返回的顺序与得分。点击文档可查看分块与原文定位。"
                />
                <div className="hit-list">
                  {result.items.map((hit, index) => (
                    <HitRow key={`${hit.id}-${index}`} hit={hit} rank={index + 1} kbId={kb.id} />
                  ))}
                </div>
              </Card>
            </>
          )}
        </>
      )}

      {!loading && !error && !result && (
        <Card>
          <EmptyState
            icon={<Search size={24} />}
            title="输入问题开始检索"
            description="检索测试不会调用大模型生成答案，只展示召回与排序依据，便于判断知识库是否真的能支撑问答。"
          />
        </Card>
      )}

      {config?.mode === 'vector' && (
        <Callout tone="info" title="语义检索依赖 Embedding 模型">
          如果知识库没有可用的 Embedding 模型，向量召回可能为空。关键词检索仍可作为基础验证。
        </Callout>
      )}
    </div>
  );
}

function StageCard({ stage, kbId, defaultOpen }: { stage: SearchStage; kbId: string; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const items = stage.items ?? [];
  return (
    <Card padded={false} className="stage-card">
      <button type="button" className="stage-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="stage-index">
          <Hash size={13} />
        </span>
        <span className="stage-name">{stageLabel(stage.name)}</span>
        <span className="stage-meta">{stage.durationMs != null ? formatDuration(stage.durationMs) : '—'}</span>
        <Badge tone="neutral">{formatNumber(items.length)} 项</Badge>
        <ChevronDown size={16} className={`stage-chevron${open ? ' is-open' : ''}`} />
      </button>
      {open && (
        <div className="stage-body">
          {items.length === 0 ? (
            <p className="muted-text">该阶段没有输出。若为向量召回，请检查 Embedding 模型与文档向量化状态。</p>
          ) : (
            <div className="stage-items">
              {items.map((hit, index) => (
                <HitRow key={`${hit.id}-${index}`} hit={hit} rank={index + 1} kbId={kbId} compact />
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function HitRow({ hit, rank, kbId, compact = false }: { hit: Hit; rank: number; kbId: string; compact?: boolean }) {
  const chunkId = hit.id;
  const path = Array.isArray(hit.headingPath) ? hit.headingPath.join(' › ') : '';
  return (
    <div className={`hit-row${compact ? ' hit-compact' : ''}`}>
      <span className="hit-rank">{rank}</span>
      <div className="hit-main">
        <div className="hit-title-line">
          <Link to={`/knowledge/${kbId}/documents/${hit.documentId}`} className="table-link">
            <FileText size={13} />
            {hit.documentName || '未命名文档'}
          </Link>
          {path && <span className="hit-path">{path}</span>}
          <span className="hit-source">{sourceRefLabel(hit.sourceRef)}</span>
        </div>
        <p className="hit-snippet">{hit.content}</p>
        <div className="score-row">
          <span className="score-main">得分 {formatScore(hit.score)}</span>
          {hit.keywordScore != null && <span>关键词 {formatScore(hit.keywordScore)}</span>}
          {hit.vectorScore != null && <span>向量 {formatScore(hit.vectorScore)}</span>}
          {hit.rerankScore != null && <span>重排 {formatScore(hit.rerankScore)}</span>}
          {chunkId && <span className="score-chunk"><Layers size={11} /> {chunkId.slice(0, 8)}</span>}
        </div>
      </div>
    </div>
  );
}

function formatScore(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toFixed(4);
}

function stageLabel(name: string): string {
  const map: Record<string, string> = {
    normalize: '查询预处理',
    rewrite: '查询改写',
    keyword: '关键词召回',
    bm25: 'BM25 召回',
    vector: '向量召回',
    embedding: '向量召回',
    fusion: 'RRF 融合',
    rrf: 'RRF 融合',
    filter: '阈值过滤',
    rerank: '重排',
    final: '最终结果',
  };
  return map[name] ?? name;
}

import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Bot,
  ChevronDown,
  Clock,
  Code2,
  Database,
  FlaskConical,
  RefreshCw,
  Search,
  Server,
} from 'lucide-react';
import type { Span, TraceKind } from '../../api/types';
import { traceApi } from '../../api/client';
import { useApi, useDocumentTitle, usePolling } from '../../hooks';
import { useAuth } from '../../contexts/AuthContext';
import {
  Badge,
  Button,
  Callout,
  Card,
  CopyButton,
  EmptyState,
  ErrorState,
  JsonBlock,
  LoadingState,
  PageHeader,
  PermissionDenied,
  SearchInput,
  Select,
  StatusBadge,
} from '../../components/ui';
import { formatDateTime, formatDuration, formatNumber } from '../../utils/format';

const KIND_LABELS: Record<string, string> = {
  ingest: '文档入库',
  search: '知识检索',
  chat: '知识问答',
  eval: '效果评测',
};

const KIND_ICONS: Record<string, typeof Activity> = {
  ingest: Database,
  search: Search,
  chat: Bot,
  eval: FlaskConical,
};

const KIND_TONE: Record<string, 'indigo' | 'green' | 'amber' | 'purple'> = {
  ingest: 'green',
  search: 'indigo',
  chat: 'purple',
  eval: 'amber',
};

export function TracesPage() {
  useDocumentTitle('链路追踪');
  const { can } = useAuth();
  const [kind, setKind] = useState<'all' | TraceKind>('all');
  const [status, setStatus] = useState('all');
  const [search, setSearch] = useState('');

  const tracesState = useApi(() => traceApi.list(), []);
  const traces = tracesState.data ?? [];

  const filtering = useMemo(
    () =>
      traces.filter((trace) => {
        if (kind !== 'all' && trace.kind !== kind) return false;
        if (status === 'ok' && !['ok', 'success'].includes(trace.status.toLowerCase())) return false;
        if (status === 'error' && !['error', 'failed'].includes(trace.status.toLowerCase())) return false;
        if (status === 'running' && trace.status.toLowerCase() !== 'running') return false;
        if (search.trim() && !trace.name.toLowerCase().includes(search.trim().toLowerCase())) return false;
        return true;
      }),
    [traces, kind, status, search],
  );

  const counts = useMemo(
    () => ({
      chat: traces.filter((trace) => trace.kind === 'chat').length,
      search: traces.filter((trace) => trace.kind === 'search').length,
      ingest: traces.filter((trace) => trace.kind === 'ingest').length,
      eval: traces.filter((trace) => trace.kind === 'eval').length,
      errors: traces.filter((trace) => ['error', 'failed'].includes(trace.status.toLowerCase())).length,
    }),
    [traces],
  );

  usePolling(
    () => tracesState.reload(),
    4000,
    traces.some((trace) => trace.status.toLowerCase() === 'running'),
  );

  if (!can('observe:read')) {
    return (
      <div className="page">
        <PageHeader title="链路追踪" description="回放每次检索与问答的输入输出。" />
        <Card>
          <PermissionDenied permission="observe:read" />
        </Card>
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="应用观测"
        title="链路追踪"
        description="回放文档入库、检索、问答与评测的改写、召回、融合、重排、上下文和模型调用。"
        actions={
          <Button
            variant="secondary"
            icon={<RefreshCw size={15} />}
            onClick={tracesState.reload}
            loading={tracesState.loading}
          >
            刷新
          </Button>
        }
      />

      <div className="stat-grid stat-grid-compact">
        <div className="stat-card">
          <div className="stat-icon"><Bot size={17} /></div>
          <div className="stat-copy"><span>问答调用</span><strong>{formatNumber(counts.chat)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Search size={17} /></div>
          <div className="stat-copy"><span>检索调用</span><strong>{formatNumber(counts.search)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Database size={17} /></div>
          <div className="stat-copy"><span>入库任务</span><strong>{formatNumber(counts.ingest)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><FlaskConical size={17} /></div>
          <div className="stat-copy"><span>评测调用</span><strong>{formatNumber(counts.eval)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Server size={17} /></div>
          <div className="stat-copy"><span>失败调用</span><strong>{formatNumber(counts.errors)}</strong></div>
        </div>
      </div>

      <div className="toolbar toolbar-wrap">
        <div className="filter-group">
          <Select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)} aria-label="按类型筛选">
            <option value="all">全部类型</option>
            <option value="chat">知识问答</option>
            <option value="search">知识检索</option>
            <option value="ingest">文档入库</option>
            <option value="eval">效果评测</option>
          </Select>
          <Select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="按状态筛选">
            <option value="all">全部状态</option>
            <option value="ok">成功</option>
            <option value="error">失败</option>
            <option value="running">运行中</option>
          </Select>
        </div>
        <SearchInput value={search} onChange={setSearch} placeholder="搜索调用名称" className="grow-search" />
      </div>

      <Card padded={false}>
        {tracesState.loading && !tracesState.data ? (
          <LoadingState label="正在读取链路…" />
        ) : tracesState.error ? (
          <div className="card-pad">
            <ErrorState error={tracesState.error} onRetry={tracesState.reload} compact />
          </div>
        ) : filtering.length === 0 ? (
          <EmptyState
            icon={<Activity size={23} />}
            title={traces.length === 0 ? '还没有调用记录' : '没有匹配的调用'}
            description={
              traces.length === 0
                ? '完成一次文档入库、检索测试或知识问答后，可在这里回放完整过程。'
                : '调整类型、状态或搜索词后再试。'
            }
            action={
              traces.length === 0 ? (
                <Link to={can('apps:read') ? '/apps' : '/knowledge'} className="btn btn-secondary btn-sm">
                  去发起一次调用
                </Link>
              ) : undefined
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="table trace-table">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>调用</th>
                  <th>状态</th>
                  <th>耗时</th>
                  <th>Token</th>
                  <th>时间</th>
                  <th className="align-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtering.map((trace) => {
                  const Icon = KIND_ICONS[trace.kind] ?? Activity;
                  return (
                    <tr key={trace.id}>
                      <td>
                        <Badge tone={KIND_TONE[trace.kind] ?? 'neutral'}>
                          <Icon size={12} />
                          {KIND_LABELS[trace.kind] ?? trace.kind}
                        </Badge>
                      </td>
                      <td>
                        <Link to={`/traces/${trace.id}`} className="table-link">
                          {trace.name || '未命名调用'}
                        </Link>
                        <div className="cell-sub mono-text">{trace.id.slice(0, 16)}…</div>
                      </td>
                      <td><StatusBadge status={trace.status} /></td>
                      <td className="mono-cell">{formatDuration(trace.durationMs)}</td>
                      <td className="mono-cell">{trace.tokenCount != null ? formatNumber(trace.tokenCount) : '—'}</td>
                      <td className="muted-cell">{formatDateTime(trace.createdAt)}</td>
                      <td className="align-right">
                        <Link to={`/traces/${trace.id}`} className="row-action">
                          回放 <ArrowRight size={13} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="card-foot-note">
          链路记录不包含 API Key 明文。输入与输出可能包含业务文本，请按内部数据规范管理访问权限。
        </div>
      </Card>
    </div>
  );
}

export function TraceDetailPage() {
  useDocumentTitle('链路回放');
  const { id = '' } = useParams();
  const { can } = useAuth();
  const traceState = useApi(() => traceApi.get(id), [id], { enabled: Boolean(id) });
  const trace = traceState.data;
  const spans = trace?.spans ?? [];
  const maxDuration = Math.max(...spans.map((span) => span.durationMs ?? 0), 1);

  if (!can('observe:read')) {
    return (
      <div className="page">
        <PageHeader title="链路回放" />
        <Card><PermissionDenied permission="observe:read" /></Card>
      </div>
    );
  }

  if (traceState.loading && !trace) {
    return <div className="page"><LoadingState label="正在读取链路…" /></div>;
  }

  if (traceState.error || !trace) {
    return (
      <div className="page">
        <PageHeader
          title="链路回放"
          back={
            <Link to="/traces" className="back-link">
              <ArrowLeft size={15} /> 返回链路列表
            </Link>
          }
        />
        <ErrorState error={traceState.error ?? new Error('链路不存在')} onRetry={traceState.reload} />
      </div>
    );
  }

  const Icon = KIND_ICONS[trace.kind] ?? Activity;

  return (
    <div className="page trace-detail-page">
      <PageHeader
        back={
          <Link to="/traces" className="back-link">
            <ArrowLeft size={15} /> 返回链路追踪
          </Link>
        }
        eyebrow={
          <span className="breadcrumb">
            <Icon size={13} /> {KIND_LABELS[trace.kind] ?? trace.kind}
          </span>
        }
        title={
          <span className="title-with-badge">
            {trace.name || '未命名调用'}
            <StatusBadge status={trace.status} />
          </span>
        }
        description={`Trace ${trace.id} · ${formatDateTime(trace.createdAt)}`}
        actions={
          <>
            <CopyButton value={trace.id} label="复制 Trace ID" />
            <Button variant="secondary" icon={<RefreshCw size={15} />} onClick={traceState.reload} loading={traceState.loading}>
              刷新
            </Button>
          </>
        }
      />

      <div className="stat-grid stat-grid-compact trace-stat-grid">
        <div className="stat-card">
          <div className="stat-icon"><Clock size={17} /></div>
          <div className="stat-copy"><span>总耗时</span><strong>{formatDuration(trace.durationMs)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Code2 size={17} /></div>
          <div className="stat-copy"><span>Token</span><strong>{trace.tokenCount != null ? formatNumber(trace.tokenCount) : '未记录'}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Activity size={17} /></div>
          <div className="stat-copy"><span>Span 数量</span><strong>{formatNumber(spans.length)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Bot size={17} /></div>
          <div className="stat-copy">
            <span>关联应用</span>
            <strong className="stat-small">
              {trace.appId ? (
                can('apps:read') ? (
                  <Link to={`/apps/${trace.appId}`} className="text-link">
                    {trace.appId.slice(0, 10)}…
                  </Link>
                ) : (
                  `${trace.appId.slice(0, 10)}…`
                )
              ) : (
                '—'
              )}
            </strong>
          </div>
        </div>
      </div>

      <Callout tone="info" title="回放内容">
        每个 Span 展示服务端记录的输入与输出，包括改写查询、召回候选、融合与重排结果、最终上下文和模型输出。
        比例与耗时来自真实调用，不包含 API Key。
      </Callout>

      <div className="span-timeline">
        {spans.length === 0 ? (
          <Card>
            <EmptyState
              icon={<Activity size={22} />}
              title="该链路没有 Span"
              description="服务端尚未记录阶段明细，或该调用类型不产生子步骤。"
            />
          </Card>
        ) : (
          spans.map((span, index) => (
            <SpanCard key={span.id ?? index} span={span} index={index} maxDuration={maxDuration} />
          ))
        )}
      </div>
    </div>
  );
}

function SpanCard({ span, index, maxDuration }: { span: Span; index: number; maxDuration: number }) {
  const width = Math.max(2, Math.min(100, ((span.durationMs ?? 0) / maxDuration) * 100));
  return (
    <Card padded={false} className="span-card">
      <details>
        <summary className="span-head">
          <span className="span-index">{String(index + 1).padStart(2, '0')}</span>
          <span className="span-dot" />
          <span className="span-name">{span.name}</span>
          <Badge tone="neutral">{span.kind}</Badge>
          <span className="span-bar">
            <span style={{ width: `${width}%` }} />
          </span>
          <span className="span-duration">{formatDuration(span.durationMs)}</span>
          <StatusBadge status={span.status} />
          <ChevronDown size={15} className="span-chevron" />
        </summary>
        <div className="span-body">
          <div className="span-io">
            <div className="span-io-label">输入</div>
            <JsonBlock value={span.input} />
          </div>
          <div className="span-io">
            <div className="span-io-label">输出</div>
            <JsonBlock value={span.output} />
          </div>
        </div>
      </details>
    </Card>
  );
}
